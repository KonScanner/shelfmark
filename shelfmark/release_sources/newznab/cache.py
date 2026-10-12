"""
Newznab release cache.

Stores search results so the handler can look up releases by source_id.
"""

import time
from threading import Lock

from shelfmark.core.logger import setup_logger

logger = setup_logger(__name__)

RELEASE_CACHE_TTL = 3600  # 1 hour

_cache: dict[str, tuple] = {}
_cache_lock = Lock()

# Most cached releases are never looked up again, so expiry on read alone would let the
# cache grow for as long as Shelfmark runs. cache_release prunes, but at most this often:
# it runs once per search result, and a full scan per insert would be quadratic.
_PRUNE_INTERVAL_SECONDS = 60
_prune_state = {"last": 0.0}


def _prune_expired_locked(now: float) -> int:
    """Drop expired entries. The caller holds ``_cache_lock``."""
    expired_ids = [
        sid for sid, (_, cached_at) in _cache.items() if now - cached_at > RELEASE_CACHE_TTL
    ]
    for sid in expired_ids:
        del _cache[sid]
    return len(expired_ids)


def cache_release(source_id: str, release_data: dict) -> None:
    now = time.time()
    with _cache_lock:
        if now - _prune_state["last"] >= _PRUNE_INTERVAL_SECONDS:
            _prune_expired_locked(now)
            _prune_state["last"] = now
        _cache[source_id] = (release_data, now)


def get_release(source_id: str) -> dict | None:
    with _cache_lock:
        if source_id not in _cache:
            logger.debug("Newznab release not in cache: %s", source_id)
            return None

        release_data, cached_at = _cache[source_id]
        if time.time() - cached_at > RELEASE_CACHE_TTL:
            del _cache[source_id]
            logger.debug("Newznab release expired: %s", source_id)
            return None

        return release_data


def remove_release(source_id: str) -> None:
    with _cache_lock:
        if source_id in _cache:
            del _cache[source_id]
            logger.debug("Removed Newznab release from cache: %s", source_id)


def cleanup_expired() -> int:
    with _cache_lock:
        removed = _prune_expired_locked(time.time())
    if removed:
        logger.debug("Cleaned up %d expired Newznab cache entries", removed)
    return removed
