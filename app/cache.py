"""
Redis cache layer for the Productivity backend.

Strategy:
  - Cache READ-heavy stat endpoints (dashboard, productivity, history, category completion).
  - On any WRITE (create/toggle/delete task), invalidate the user's cached keys immediately.
  - If Redis is unavailable, the app falls back to direct DB queries transparently.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

import redis.asyncio as aioredis
from redis.asyncio import Redis

from .config import settings

logger = logging.getLogger(__name__)

# Global Redis client — initialised in `connect_redis()` at startup
_redis: Redis | None = None

# Cache TTL: stats are valid for 60 seconds
CACHE_TTL = 60

# Key prefixes
PREFIX_DASHBOARD = "dashboard"
PREFIX_HISTORY = "history"
PREFIX_PRODUCTIVITY = "productivity"
PREFIX_CATEGORY = "category_completion"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------

async def connect_redis() -> bool:
    """Create the global Redis connection pool. Returns True if successful."""
    global _redis
    try:
        client = aioredis.from_url(
            settings.redis_url,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        # Verify connectivity
        await client.ping()
        _redis = client
        logger.info(f"[CACHE] Redis connected → {settings.redis_url}")
        return True
    except Exception as exc:
        logger.warning(f"[CACHE] Redis unavailable — running without cache. ({exc})")
        _redis = None
        return False


async def disconnect_redis() -> None:
    """Close the Redis connection pool on shutdown."""
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None
        logger.info("[CACHE] Redis connection closed.")


def is_cache_available() -> bool:
    return _redis is not None


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

async def get_cache(key: str) -> Any | None:
    """Return the cached value for *key*, or None on miss / error."""
    if not _redis:
        return None
    try:
        raw = await _redis.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as exc:
        logger.warning(f"[CACHE] get error for key={key}: {exc}")
        return None


async def set_cache(key: str, data: Any, ttl: int = CACHE_TTL) -> None:
    """Serialise *data* to JSON and store it under *key* with a TTL."""
    if not _redis:
        return
    try:
        await _redis.setex(key, ttl, json.dumps(data, default=str))
    except Exception as exc:
        logger.warning(f"[CACHE] set error for key={key}: {exc}")


async def invalidate_user_cache(user_id: str) -> None:
    """
    Delete ALL cached keys for a specific user.
    Called after any write operation (create / toggle / delete / update task).
    """
    if not _redis:
        return
    try:
        pattern = f"*:{user_id}*"
        keys = await _redis.keys(pattern)
        if keys:
            await _redis.delete(*keys)
            logger.debug(f"[CACHE] Invalidated {len(keys)} keys for user={user_id}")
    except Exception as exc:
        logger.warning(f"[CACHE] invalidate error for user={user_id}: {exc}")


# ---------------------------------------------------------------------------
# Key builders
# ---------------------------------------------------------------------------

def key_dashboard(user_id: str) -> str:
    return f"{PREFIX_DASHBOARD}:{user_id}"

def key_history(user_id: str) -> str:
    return f"{PREFIX_HISTORY}:{user_id}"

def key_productivity(user_id: str) -> str:
    return f"{PREFIX_PRODUCTIVITY}:{user_id}"

def key_category_completion(user_id: str, days: int) -> str:
    return f"{PREFIX_CATEGORY}:{user_id}:{days}"


# ---------------------------------------------------------------------------
# Benchmark helper
# ---------------------------------------------------------------------------

async def measure_redis_latency(iterations: int = 5) -> dict[str, float]:
    """
    Write and read a dummy key *iterations* times and return timing stats (ms).
    """
    if not _redis:
        return {"available": False}

    write_times: list[float] = []
    read_times: list[float] = []
    bench_key = "__benchmark__"
    bench_val = json.dumps({"x": "a" * 512})  # ~512 byte payload

    for _ in range(iterations):
        t0 = time.perf_counter()
        await _redis.setex(bench_key, 10, bench_val)
        write_times.append((time.perf_counter() - t0) * 1000)

        t0 = time.perf_counter()
        await _redis.get(bench_key)
        read_times.append((time.perf_counter() - t0) * 1000)

    await _redis.delete(bench_key)

    return {
        "available": True,
        "iterations": iterations,
        "avg_write_ms": round(sum(write_times) / iterations, 3),
        "avg_read_ms": round(sum(read_times) / iterations, 3),
        "min_read_ms": round(min(read_times), 3),
        "max_read_ms": round(max(read_times), 3),
    }


async def get_redis_info() -> dict:
    """Return Redis server info for the /health endpoint."""
    if not _redis:
        return {"connected": False}
    try:
        info = await _redis.info("server")
        return {
            "connected": True,
            "version": info.get("redis_version"),
            "uptime_seconds": info.get("uptime_in_seconds"),
        }
    except Exception:
        return {"connected": False}
