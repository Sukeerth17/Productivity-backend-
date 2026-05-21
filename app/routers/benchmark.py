"""
Benchmark router — compares DB vs Redis read/write speed.
GET /api/v1/benchmark
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..cache import measure_redis_latency, is_cache_available, get_cache, set_cache
from ..database import get_session
from ..dependencies import get_current_user
from ..models import User
from ..services import get_productivity_stats

router = APIRouter(prefix="/benchmark", tags=["benchmark"])


@router.get("")
async def run_benchmark(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    """
    Benchmark endpoint: runs productivity stats query 3 times against the DB,
    then serves the same data 3 times from Redis cache, and reports the speedup.
    """
    iterations = 3
    db_times: list[float] = []
    cache_times: list[float] = []
    bench_cache_key = f"__benchmark__{current_user.id}"

    # --- Phase 1: DB reads ---
    for i in range(iterations):
        t0 = time.perf_counter()
        result = await get_productivity_stats(session, current_user)
        db_times.append((time.perf_counter() - t0) * 1000)

    # Store result in cache for phase 2
    data_dict = result.model_dump(mode="json")
    await set_cache(bench_cache_key, data_dict, ttl=30)

    # --- Phase 2: Redis reads ---
    if is_cache_available():
        for i in range(iterations):
            t0 = time.perf_counter()
            await get_cache(bench_cache_key)
            cache_times.append((time.perf_counter() - t0) * 1000)

    # --- Phase 3: Redis raw read/write latency ---
    redis_raw = await measure_redis_latency(iterations=5)

    db_avg = round(sum(db_times) / iterations, 2)
    cache_avg = round(sum(cache_times) / iterations, 2) if cache_times else None
    speedup = round(db_avg / cache_avg, 1) if cache_avg and cache_avg > 0 else None

    return {
        "summary": {
            "db_avg_ms": db_avg,
            "redis_avg_ms": cache_avg,
            "speedup_factor": f"{speedup}x faster" if speedup else "Redis not available",
            "redis_available": is_cache_available(),
        },
        "db_reads": {
            "iterations": iterations,
            "times_ms": [round(t, 2) for t in db_times],
            "avg_ms": db_avg,
            "min_ms": round(min(db_times), 2),
            "max_ms": round(max(db_times), 2),
        },
        "redis_reads": {
            "iterations": iterations,
            "times_ms": [round(t, 2) for t in cache_times] if cache_times else [],
            "avg_ms": cache_avg,
        },
        "redis_raw_latency": redis_raw,
    }
