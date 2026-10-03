"""Singletons de infraestructura del gateway (se cierran en el lifespan de main.py)."""
from .clients import AuthApi, CatalogClient, OrdersApi
from .config import settings
from .netutil import TrustedProxies
from .ratelimit import RateLimiter

catalog = CatalogClient(settings.supabase_url, settings.supabase_anon_key)
auth_api = AuthApi(settings.auth_url, settings.internal_token)
orders_api = OrdersApi(settings.orders_url, settings.internal_token)
limiter = RateLimiter(settings.redis_url)
trusted_proxies = TrustedProxies(settings.trusted_proxy_cidrs, settings.trusted_proxy_hosts)
