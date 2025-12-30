"""Persistent HTTP cache for yfinance (requests-cache).

- Uses SQLite-backed CachedSession (e.g. `.http_cache.sqlite`)
- Plugs the session into yfinance via `yf.shared._requests = session`
"""

from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class CacheConfig:
    cache_name: str = ".http_cache"              # creates .http_cache.sqlite
    backend: str = "sqlite"
    expire_after_seconds: int = 24 * 60 * 60     # default TTL: 24h
    stale_if_error: bool = True                  # serve stale cache on 429/500

def create_cached_session(cfg: CacheConfig):
    from requests_cache import CachedSession  # requires requests-cache installed
    return CachedSession(
        cache_name=cfg.cache_name,
        backend=cfg.backend,
        expire_after=cfg.expire_after_seconds,
        stale_if_error=cfg.stale_if_error,
    )

def try_enable_yfinance_cache(cfg: Optional[CacheConfig] = None) -> bool:
    if cfg is None:
        cfg = CacheConfig()
    try:
        session = create_cached_session(cfg)
    except Exception:
        return False

    # Do not cache Yahoo "crumb" auth failures (prevents poisoning the cache)
    def _no_cache_on_invalid_crumb(r, *args, **kwargs):
        try:
            txt = (r.text or "")
        except Exception:
            txt = ""
        if r.status_code == 401 and "Invalid Crumb" in txt:
            # requests-cache respects this flag: don't store this response
            r.from_cache = False  # just in case
            r.cache_control = "no-store"
        return r

    try:
        session.hooks["response"].append(_no_cache_on_invalid_crumb)
    except Exception:
        pass

    try:
        import yfinance as yf
        yf.shared._requests = session
    except Exception:
        return False

    return True
