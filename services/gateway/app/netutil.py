"""IP real del cliente, sin confiar ciegamente en cabeceras que puede falsificar cualquiera."""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from collections.abc import Awaitable, Callable, Mapping

log = logging.getLogger("gateway.netutil")


def parse_cidrs(csv: str) -> list:
    nets = []
    for part in csv.split(","):
        part = part.strip()
        if part:
            nets.append(ipaddress.ip_network(part, strict=False))
    return nets


async def _dns_resolve(host: str) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


class TrustedProxies:
    """Proxies de confianza = redes fijas (CIDR) + hosts por NOMBRE (p. ej. el contenedor 'frontend',
    cuya IP asigna Docker y cambia al recrearlo). Los nombres se resuelven por DNS y se cachean `ttl_s`;
    si aun no resuelven (el proxy no ha arrancado), se reintenta cada `retry_s`.
    Confiar en rangos privados enteros NO sirve: el trafico del host al puerto publicado llega desde la IP
    del bridge de Docker (172.x), y cualquiera podria falsificar X-Real-IP para saltarse el rate limiting."""

    def __init__(self, cidrs: list, hosts: list[str], ttl_s: float = 30.0, retry_s: float = 5.0,
                 resolver: Callable[[str], Awaitable[list[str]]] = _dns_resolve,
                 clock: Callable[[], float] = time.monotonic):
        self.cidrs, self.hosts = list(cidrs), list(hosts)
        self.ttl_s, self.retry_s = ttl_s, retry_s
        self._resolver, self._clock = resolver, clock
        self._resolved: list = []
        self._expires = float("-inf")

    async def networks(self) -> list:
        if self.hosts and self._clock() >= self._expires:
            self._resolved = await self._resolve()
            self._expires = self._clock() + (self.ttl_s if self._resolved else self.retry_s)
        return self.cidrs + self._resolved

    async def _resolve(self) -> list:
        nets = []
        for host in self.hosts:
            try:
                nets += [ipaddress.ip_network(ip) for ip in await self._resolver(host)]   # /32 o /128
            except (OSError, ValueError) as exc:
                log.warning("proxy de confianza %r no resuelve (%s): su X-Real-IP se ignora por ahora", host, exc)
        return nets


def client_ip(peer: str | None, headers: Mapping[str, str], trusted_proxies: list) -> str:
    """Solo se usa X-Real-IP si la conexion viene de un proxy de confianza (nginx interno).
    Si alguien llega directo al gateway, su X-Real-IP se ignora."""
    if not peer:
        return "unknown"
    try:
        peer_ip = ipaddress.ip_address(peer)
    except ValueError:
        return peer
    if any(peer_ip in net for net in trusted_proxies):
        try:
            return str(ipaddress.ip_address((headers.get("x-real-ip") or "").strip()))
        except ValueError:
            pass
    return str(peer_ip)
