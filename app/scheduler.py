from __future__ import annotations

from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import and_, delete, func, select, or_, not_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import sessionmaker, selectinload

from .database import engine
from .models import DailySnapshot, Task, TaskCompletion, User


async def _write_daily_snapshots() -> None:
    """Write DailySnapshots for all users for any missing past days (up to 30 days back)."""
    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        # Snapshots are written at 00:00 UTC for the day that just ended (yesterday).
        now = datetime.now(timezone.utc)
        yesterday = now - timedelta(days=1)
        yesterday_midnight = yesterday.replace(hour=0, minute=0, second=0, microsecond=0)
        
        users_result = await session.execute(select(User))
        users = users_result.scalars().all()

        for user in users:
            # Backfill from user's registration date up to yesterday, capping at 30 days to avoid performance issues
            user_start = user.created_at.replace(hour=0, minute=0, second=0, microsecond=0)
            max_backfill = yesterday_midnight - timedelta(days=30)
            start_date = max(user_start, max_backfill)

            cursor = start_date
            while cursor <= yesterday_midnight:
                # Check if snapshot already exists for this cursor date
                existing = await session.execute(
                    select(DailySnapshot).where(
                        DailySnapshot.user_id == user.id,
                        func.date(DailySnapshot.snapshot_date) == cursor.date(),
                    )
                )
                if existing.scalar_one_or_none():
                    cursor += timedelta(days=1)
                    continue

                today_weekday = str(cursor.date().weekday())
                next_day_start = cursor + timedelta(days=1)
                
                # 1. Count completions from ledger
                completed_count = await session.execute(
                    select(func.count(TaskCompletion.id)).where(
                        TaskCompletion.user_id == user.id,
                        TaskCompletion.completed_at >= cursor,
                        TaskCompletion.completed_at < next_day_start,
                    )
                )
                total_completed = int(completed_count.scalar_one() or 0)

                # 2. Count pending one-offs (active on cursor day but not completed today)
                pending_oneoff_q = select(func.count(Task.id)).where(
                    Task.user_id == user.id,
                    Task.is_habit.is_(False),
                    Task.created_at < next_day_start,
                    or_(Task.completed.is_(False), Task.completed_at >= next_day_start),
                    or_(
                        Task.is_deleted.is_(False),
                        and_(Task.is_deleted.is_(True), Task.deleted_at >= next_day_start)
                    ),
                    or_(Task.start_date.is_(None), Task.start_date <= cursor.date())
                )
                pending_oneoffs = int((await session.execute(pending_oneoff_q)).scalar_one() or 0)

                # 3. Count pending habits (active today but not completed today)
                # Fetch task ids of habits completed on this cursor day
                habit_comp_subq = (
                    select(TaskCompletion.task_id)
                    .where(
                        TaskCompletion.user_id == user.id,
                        TaskCompletion.completed_at >= cursor,
                        TaskCompletion.completed_at < next_day_start,
                        TaskCompletion.task_id.is_not(None)
                    )
                ).subquery()

                pending_habit_q = select(func.count(Task.id)).where(
                    Task.user_id == user.id,
                    Task.is_habit.is_(True),
                    Task.created_at < next_day_start,
                    or_(
                        Task.is_deleted.is_(False),
                        and_(Task.is_deleted.is_(True), Task.deleted_at >= next_day_start)
                    ),
                    or_(
                        Task.habit_days.is_(None),
                        Task.habit_days.contains(today_weekday)
                    ),
                    or_(Task.start_date.is_(None), Task.start_date <= cursor.date()),
                    not_(Task.id.in_(select(habit_comp_subq.c.task_id)))
                )
                pending_habits = int((await session.execute(pending_habit_q)).scalar_one() or 0)

                total_available = total_completed + pending_oneoffs + pending_habits

                session.add(DailySnapshot(
                    user_id=user.id,
                    snapshot_date=cursor,
                    total_available=total_available,
                    total_completed=total_completed,
                ))
                cursor += timedelta(days=1)

        await session.commit()
        print(f"[SCHEDULER] Completed daily snapshots backfill and update for {len(users)} users at {now}")


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
    """Write daily snapshots FIRST, then reset habit tasks that are active today."""
    # Write snapshots BEFORE resetting (captures today's state accurately)
    await _write_daily_snapshots()

    async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with async_session() as session:
        now_utc = datetime.now(timezone.utc)
        today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        today_weekday = now_utc.weekday()  # 0=Mon..6=Sun

        # Get all completed habit tasks that were completed BEFORE today.
        # This makes the reset idempotent; if it runs multiple times today,
        # it won't reset tasks the user has already completed TODAY.
        stmt = (
            select(Task)
            .options(selectinload(Task.subtasks))
            .where(
                and_(
                    Task.is_habit.is_(True),
                    Task.completed.is_(True),
                    Task.is_deleted.is_(False),
                    or_(Task.completed_at.is_(None), Task.completed_at < today_start)
                )
            )
        )

        result = await session.execute(stmt)
        tasks = result.scalars().all()

        reset_count = 0
        for task in tasks:
            # Check if habit is active today (the new day)
            if task.habit_days:
                active_days = {int(d) for d in task.habit_days.split(",") if d.strip().isdigit()}
                if today_weekday not in active_days:
                    # Skip reset if it's not active today. 
                    # This means it will stay 'completed' from yesterday until its next active day.
                    # This is correct because the frontend filters out habits not active today anyway.
                    continue 

            # Reset the task
            task.completed = False
            task.completed_at = None
            
            # ALSO RESET SUBTASKS
            for subtask in task.subtasks:
                subtask.completed = False
            
            reset_count += 1

        await session.commit()
        print(f"[SCHEDULER] Reset {reset_count} habit tasks and their subtasks at {now_utc}")


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
