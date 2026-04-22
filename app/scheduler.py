from __future__ import annotations

from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import and_, delete, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from .config import settings
from .database import engine
from .models import Task


async def cleanup_old_oneoff_tasks() -> None:
    """Delete one-off tasks that are completed and older than 2 days."""
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        cutoff_time = datetime.now(timezone.utc) - timedelta(days=2)
        
        # Delete completed one-off tasks older than 2 days
        stmt = delete(Task).where(
            and_(
                Task.is_habit.is_(False),
                Task.completed.is_(True),
                Task.completed_at <= cutoff_time,
            )
        )
        
        await session.execute(stmt)
        await session.commit()
        print(f"[SCHEDULER] Cleaned up old one-off tasks at {datetime.now(timezone.utc)}")


async def reset_habit_tasks() -> None:
    """Reset all habit tasks to incomplete every day at midnight."""
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Get all completed habit tasks from today
        stmt = select(Task).where(
            and_(
                Task.is_habit.is_(True),
                Task.completed.is_(True),
                Task.completed_at >= today_start,
            )
        )
        
        result = await session.execute(stmt)
        tasks = result.scalars().all()
        
        # Reset them to incomplete
        for task in tasks:
            task.completed = False
            task.completed_at = None
        
        await session.commit()
        print(f"[SCHEDULER] Reset {len(tasks)} habit tasks at {datetime.now(timezone.utc)}")


def start_scheduler() -> AsyncIOScheduler:
    """Start the background scheduler."""
    scheduler = AsyncIOScheduler()
    
    # Run cleanup every hour
    scheduler.add_job(
        cleanup_old_oneoff_tasks,
        "interval",
        hours=1,
        id="cleanup_old_tasks",
        name="Cleanup old one-off tasks",
    )
    
    # Reset habits at midnight every day
    scheduler.add_job(
        reset_habit_tasks,
        "cron",
        hour=0,
        minute=0,
        second=0,
        id="reset_habits",
        name="Reset habit tasks daily",
        timezone="UTC",
    )
    
    scheduler.start()
    print("[SCHEDULER] Background scheduler started")
    
    return scheduler
