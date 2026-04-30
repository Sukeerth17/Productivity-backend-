from __future__ import annotations

from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import and_, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker

from .database import engine
from .models import DailySnapshot, Task, TaskCompletion, User


async def _write_daily_snapshots() -> None:
    """Write a DailySnapshot for each user capturing today's available and completed counts."""
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        now = datetime.now(timezone.utc)
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)

        users_result = await session.execute(select(User))
        users = users_result.scalars().all()

        for user in users:
            # Check if snapshot already exists for today
            existing = await session.execute(
                select(DailySnapshot).where(
                    DailySnapshot.user_id == user.id,
                    func.date(DailySnapshot.snapshot_date) == today_start.date(),
                )
            )
            if existing.scalar_one_or_none():
                continue

            # Count today's available tasks: 
            # - Active habits + habits deleted specifically TODAY
            # - INCOMPLETE one-offs (not deleted)
            habit_count = await session.execute(
                select(func.count(Task.id)).where(
                    Task.user_id == user.id, 
                    Task.is_habit.is_(True),
                    (Task.is_deleted.is_(False)) | (Task.deleted_at >= today_start)
                )
            )
            oneoff_count = await session.execute(
                select(func.count(Task.id)).where(
                    Task.user_id == user.id,
                    Task.is_habit.is_(False),
                    Task.completed.is_(False),
                    Task.is_deleted.is_(False),
                )
            )
            total_available = int(habit_count.scalar_one() or 0) + int(oneoff_count.scalar_one() or 0)

            # Count today's completions from TaskCompletion ledger
            completed_count = await session.execute(
                select(func.count(TaskCompletion.id)).where(
                    TaskCompletion.user_id == user.id,
                    TaskCompletion.completed_at >= today_start,
                    TaskCompletion.completed_at < today_end,
                )
            )
            total_completed = int(completed_count.scalar_one() or 0)

            session.add(DailySnapshot(
                user_id=user.id,
                snapshot_date=today_start,
                total_available=total_available,
                total_completed=total_completed,
            ))

        await session.commit()
        print(f"[SCHEDULER] Wrote daily snapshots for {len(users)} users at {now}")


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
    """Write daily snapshots FIRST, then reset all habit tasks to incomplete."""
    # Write snapshots BEFORE resetting (captures today's state accurately)
    await _write_daily_snapshots()

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        # Get all completed habit tasks
        stmt = select(Task).where(
            and_(
                Task.is_habit.is_(True),
                Task.completed.is_(True),
                Task.is_deleted.is_(False),
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

    # Reset habits at midnight every day (also writes daily snapshots)
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
