"""Prowlarr release cache.

Stores search results so the handler can look up releases by source_id.
This keeps all Prowlarr-specific data within the plugin.
"""

import time
from threading import Lock

from shelfmark.core.logger import setup_logger

logger = setup_logger(__name__)

# Cache TTL in seconds (1 hour - releases should be downloaded within this time)
RELEASE_CACHE_TTL = 3600

# Internal cache storage: source_id -> (release_dict, timestamp)
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
        source_id
        for source_id, (_, cached_at) in _cache.items()
        if now - cached_at > RELEASE_CACHE_TTL
    ]
    for source_id in expired_ids:
        del _cache[source_id]
    return len(expired_ids)


def cache_release(source_id: str, release_data: dict) -> None:
    """Cache a release by its source_id.

    Args:
        source_id: The unique identifier for this release (GUID)
        release_data: The full Prowlarr API result dict

    """
    now = time.time()
    with _cache_lock:
        if now - _prune_state["last"] >= _PRUNE_INTERVAL_SECONDS:
            _prune_expired_locked(now)
            _prune_state["last"] = now
        _cache[source_id] = (release_data, now)


def get_release(source_id: str) -> dict | None:
    """Get a cached release by source_id.

    Args:
        source_id: The unique identifier for the release

    Returns:
        The cached release dict, or None if not found or expired

    """
    with _cache_lock:
        if source_id not in _cache:
            logger.debug("Prowlarr release not in cache: %s", source_id)
            return None

        release_data, cached_at = _cache[source_id]
        age = time.time() - cached_at

        if age > RELEASE_CACHE_TTL:
            # Expired - remove from cache
            del _cache[source_id]
            logger.debug("Prowlarr release expired: %s", source_id)
            return None

        return release_data


def remove_release(source_id: str) -> None:
    """Remove a release from the cache (e.g., after successful download).

    Args:
        source_id: The unique identifier for the release

    """
    with _cache_lock:
        if source_id in _cache:
            del _cache[source_id]
            logger.debug("Removed Prowlarr release from cache: %s", source_id)


def cleanup_expired() -> int:
    """Remove all expired entries from the cache.

    Returns:
        Number of entries removed

    """
    with _cache_lock:
        removed = _prune_expired_locked(time.time())

    if removed:
        logger.debug("Cleaned up %s expired Prowlarr cache entries", removed)

    return removed


def get_cache_stats() -> dict:
    """Get cache statistics for debugging.

    Returns:
        Dict with cache stats

    """
    with _cache_lock:
        return {
            "size": len(_cache),
            "entries": list(_cache.keys()),
        }
