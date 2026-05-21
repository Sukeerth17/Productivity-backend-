from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ..cache import (
    get_cache, set_cache, is_cache_available,
    key_dashboard, key_history, key_productivity, key_category_completion,
    CACHE_TTL,
)
from ..database import get_session
from ..dependencies import get_current_user
from ..models import User
from ..schemas import CategoryCompletionStats, DashboardStats, HistorySummary, ProductivityStatsOut
from ..services import category_completion_stats, dashboard_stats, history_summary, get_productivity_stats

router = APIRouter(prefix="/stats", tags=["stats"])


def _cache_response(data: dict, cache_hit: bool, elapsed_ms: float) -> JSONResponse:
    """Wrap data in a JSONResponse with cache-status and timing headers."""
    return JSONResponse(
        content=data,
        headers={
            "X-Cache": "HIT" if cache_hit else "MISS",
            "X-Response-Time-Ms": str(round(elapsed_ms, 2)),
            "X-Cache-TTL": str(CACHE_TTL),
        },
    )


@router.get("/dashboard", response_model=DashboardStats)
async def get_dashboard_stats(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    t0 = time.perf_counter()
    cache_key = key_dashboard(current_user.id)
    cached = await get_cache(cache_key)
    if cached is not None:
        return _cache_response(cached, cache_hit=True, elapsed_ms=(time.perf_counter() - t0) * 1000)

    data = await dashboard_stats(session, current_user)
    await set_cache(cache_key, data)
    return _cache_response(data, cache_hit=False, elapsed_ms=(time.perf_counter() - t0) * 1000)


@router.get("/history-summary", response_model=HistorySummary)
async def get_history_summary(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    t0 = time.perf_counter()
    cache_key = key_history(current_user.id)
    cached = await get_cache(cache_key)
    if cached is not None:
        return _cache_response(cached, cache_hit=True, elapsed_ms=(time.perf_counter() - t0) * 1000)

    data = await history_summary(session, current_user)
    await set_cache(cache_key, data)
    return _cache_response(data, cache_hit=False, elapsed_ms=(time.perf_counter() - t0) * 1000)


@router.get("/category-completion", response_model=list[CategoryCompletionStats])
async def get_category_completion(
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    t0 = time.perf_counter()
    cache_key = key_category_completion(current_user.id, days)
    cached = await get_cache(cache_key)
    if cached is not None:
        return _cache_response(cached, cache_hit=True, elapsed_ms=(time.perf_counter() - t0) * 1000)

    data = await category_completion_stats(session, current_user, days=days)
    await set_cache(cache_key, data)
    return _cache_response(data, cache_hit=False, elapsed_ms=(time.perf_counter() - t0) * 1000)


@router.get("/productivity", response_model=ProductivityStatsOut)
async def get_productivity(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    t0 = time.perf_counter()
    cache_key = key_productivity(current_user.id)
    cached = await get_cache(cache_key)
    if cached is not None:
        return _cache_response(cached, cache_hit=True, elapsed_ms=(time.perf_counter() - t0) * 1000)

    data = await get_productivity_stats(session, current_user)
    # ProductivityStatsOut is a Pydantic model — serialise it
    data_dict = data.model_dump(mode="json")
    await set_cache(cache_key, data_dict)
    return _cache_response(data_dict, cache_hit=False, elapsed_ms=(time.perf_counter() - t0) * 1000)

