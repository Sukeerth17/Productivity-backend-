from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..dependencies import get_current_user
from ..models import User
from ..schemas import CategoryCompletionStats, DashboardStats, HistorySummary
from ..services import category_completion_stats, dashboard_stats, history_summary

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/dashboard", response_model=DashboardStats)
async def get_dashboard_stats(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    return await dashboard_stats(session, current_user)


@router.get("/history-summary", response_model=HistorySummary)
async def get_history_summary(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    return await history_summary(session, current_user)


@router.get("/category-completion", response_model=list[CategoryCompletionStats])
async def get_category_completion(
    days: int = Query(default=30, ge=1, le=365),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    return await category_completion_stats(session, current_user, days=days)
