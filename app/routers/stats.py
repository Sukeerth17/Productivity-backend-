from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..schemas import CategoryCompletionStats, DashboardStats
from ..services import category_completion_stats, dashboard_stats

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/dashboard", response_model=DashboardStats)
async def get_dashboard_stats(session: AsyncSession = Depends(get_session)):
    return await dashboard_stats(session)


@router.get("/category-completion", response_model=list[CategoryCompletionStats])
async def get_category_completion(
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
):
    return await category_completion_stats(session, days=days)
