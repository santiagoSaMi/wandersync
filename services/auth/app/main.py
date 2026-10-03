"""Servicio de identidad de WanderSync.

Seguridad implementada (Requisito Transversal de Seguridad):
  * Contrasenas con ARGON2ID (parametros RFC 9106 "low memory"), con re-hash automatico si
    los parametros se endurecen. El hash se calcula en un hilo aparte (no bloquea el event loop).
  * Sesiones SERVER-SIDE en Redis: el navegador solo guarda un identificador opaco de 256 bits
    (cookie HttpOnly). En Redis la clave es SHA-256(id): si Redis se filtrara, los ids no sirven.
  * MITIGACION DE SESSION FIXATION: al autenticarse SIEMPRE se destruye la sesion previa
    (anonima o no) y se emite un identificador NUEVO. Un id "plantado" por un atacante muere en el login.
  * Expiracion: inactividad (deslizante) y absoluta.
  * Anti fuerza bruta: bloqueo temporal por cuenta tras N fallos + verificacion "dummy" cuando el
    usuario no existe (mismo tiempo de respuesta => sin enumeracion de usuarios) + mensajes genericos.
Solo accesible desde la red interna con X-Internal-Token.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import secrets
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import redis.asyncio as aioredis
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import DateTime, String, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("auth")

DATABASE_URL = os.environ["DATABASE_URL"]
REDIS_URL = os.environ["REDIS_URL"]
INTERNAL_TOKEN = os.environ["INTERNAL_TOKEN"]
SESSION_IDLE_TTL = int(os.getenv("SESSION_IDLE_TTL_SECONDS", "1800"))          # 30 min de inactividad
SESSION_ABSOLUTE_TTL = int(os.getenv("SESSION_ABSOLUTE_TTL_SECONDS", "28800"))  # 8 h maximo
LOGIN_MAX_FAILURES = int(os.getenv("LOGIN_MAX_FAILURES", "5"))
LOCKOUT_SECONDS = int(os.getenv("LOGIN_LOCKOUT_SECONDS", "900"))

hasher = PasswordHasher()          # Argon2id por defecto (time_cost=3, memory_cost=64 MiB, parallelism=4)
db_engine = create_async_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5, max_overflow=5)
Session = async_sessionmaker(db_engine, expire_on_commit=False)
rds: aioredis.Redis = aioredis.from_url(REDIS_URL, decode_responses=True)
DUMMY_HASH = ""


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# ------------------------------------------------------------------ esquemas
class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)
    full_name: str = Field(min_length=1, max_length=120)


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    current_session_id: str | None = Field(default=None, max_length=128)


class SessionIn(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)


class UserOut(BaseModel):
    id: str
    email: str
    full_name: str


# ------------------------------------------------------------------ utilidades
async def internal_auth(x_internal_token: str = Header(default="")) -> None:
    if not secrets.compare_digest(x_internal_token.encode(), INTERNAL_TOKEN.encode()):
        raise HTTPException(status_code=401, detail="invalid internal token")


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _skey(session_id: str) -> str:
    return "sess:" + _sha(session_id)


async def _hash(password: str) -> str:
    return await asyncio.to_thread(hasher.hash, password)


async def _verify(password_hash: str, password: str) -> bool:
    def _v() -> bool:
        try:
            return hasher.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            return False
    return await asyncio.to_thread(_v)


def check_password_policy(password: str, email: str) -> None:
    local = email.split("@")[0].lower()
    if len(set(password)) < 5:
        raise HTTPException(422, "weak_password")
    if len(local) >= 4 and local in password.lower():
        raise HTTPException(422, "weak_password")
    if re.fullmatch(r"[a-zA-Z]+|\d+", password):
        raise HTTPException(422, "weak_password")


async def create_session(user: UserOut | None) -> str:
    session_id = secrets.token_urlsafe(32)                 # 256 bits de entropia
    key = _skey(session_id)
    mapping = {"created_at": str(int(time.time())), "user_id": "", "email": "", "full_name": ""}
    if user:
        mapping.update(user_id=user.id, email=user.email, full_name=user.full_name)
    await rds.hset(key, mapping=mapping)
    await rds.expire(key, SESSION_IDLE_TTL)
    return session_id


async def load_session(session_id: str) -> dict | None:
    key = _skey(session_id)
    data = await rds.hgetall(key)
    if not data:
        return None
    if time.time() - int(data.get("created_at", "0")) > SESSION_ABSOLUTE_TTL:
        await rds.delete(key)
        return None
    await rds.expire(key, SESSION_IDLE_TTL)                # expiracion deslizante
    return data


async def destroy_session(session_id: str) -> None:
    await rds.delete(_skey(session_id))


# ------------------------------------------------------------------ app
@asynccontextmanager
async def lifespan(_: FastAPI):
    global DUMMY_HASH
    for attempt in range(1, 31):
        try:
            async with db_engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            await rds.ping()
            break
        except Exception as exc:
            log.warning("dependencias no listas (%s), reintento %d/30", type(exc).__name__, attempt)
            await asyncio.sleep(2)
    else:
        raise RuntimeError("no se pudo conectar a la base de datos / Redis")
    DUMMY_HASH = await _hash(secrets.token_urlsafe(16))
    yield
    await rds.aclose()
    await db_engine.dispose()


app = FastAPI(title="WanderSync auth", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


@app.get("/health")
async def health() -> dict:
    async with Session() as s:
        await s.execute(text("select 1"))
    await rds.ping()
    return {"status": "ok", "service": "auth"}


@app.post("/register", status_code=201, response_model=UserOut, dependencies=[Depends(internal_auth)])
async def register(body: RegisterIn):
    email = str(body.email).lower()
    check_password_policy(body.password, email)
    password_hash = await _hash(body.password)
    async with Session() as s:
        s.add(User(email=email, full_name=body.full_name.strip(), password_hash=password_hash))
        try:
            await s.commit()
        except IntegrityError:
            await s.rollback()
            raise HTTPException(status_code=409, detail="email_taken")
        user = (await s.execute(select(User).where(User.email == email))).scalar_one()
    log.info("usuario registrado id=%s", user.id)
    return UserOut(id=user.id, email=user.email, full_name=user.full_name)


@app.post("/login", dependencies=[Depends(internal_auth)])
async def login(body: LoginIn):
    email = str(body.email).lower()
    lock_key = "login_fail:" + _sha(email)
    fails = int(await rds.get(lock_key) or 0)
    if fails >= LOGIN_MAX_FAILURES:
        retry = max(await rds.ttl(lock_key), 1)
        raise HTTPException(status_code=429, detail="too_many_attempts", headers={"Retry-After": str(retry)})

    async with Session() as s:
        user = (await s.execute(select(User).where(User.email == email))).scalar_one_or_none()
    if user is not None:
        valid = await _verify(user.password_hash, body.password)
    else:
        await _verify(DUMMY_HASH, body.password)           # igualar tiempos: no se revela si el usuario existe
        valid = False

    if not valid:
        n = await rds.incr(lock_key)
        if n == 1:
            await rds.expire(lock_key, LOCKOUT_SECONDS)
        raise HTTPException(status_code=401, detail="invalid_credentials")

    await rds.delete(lock_key)
    if hasher.check_needs_rehash(user.password_hash):      # parametros endurecidos desde el ultimo login
        new_hash = await _hash(body.password)
        async with Session() as s:
            db_user = await s.get(User, user.id)
            db_user.password_hash = new_hash
            await s.commit()

    # === MITIGACION DE SESSION FIXATION ===================================================
    # Nunca se "eleva" una sesion existente: se destruye la anterior y se emite un id nuevo.
    if body.current_session_id:
        await destroy_session(body.current_session_id)
    out = UserOut(id=user.id, email=user.email, full_name=user.full_name)
    session_id = await create_session(out)
    log.info("login OK user=%s (sesion rotada)", user.id)
    return {"session_id": session_id, "user": out.model_dump()}


@app.post("/logout", dependencies=[Depends(internal_auth)])
async def logout(body: SessionIn) -> dict:
    await destroy_session(body.session_id)
    return {"ok": True}


@app.post("/sessions/anonymous", dependencies=[Depends(internal_auth)])
async def anonymous_session() -> dict:
    """Sesion pre-autenticacion (necesaria para demostrar que el login la ROTA)."""
    return {"session_id": await create_session(None)}


@app.post("/sessions/resolve", dependencies=[Depends(internal_auth)])
async def resolve_session(body: SessionIn) -> dict:
    data = await load_session(body.session_id)
    if data is None:
        return {"valid": False, "user": None}
    user = None
    if data.get("user_id"):
        user = {"id": data["user_id"], "email": data["email"], "full_name": data["full_name"]}
    return {"valid": True, "user": user}
