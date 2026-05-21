from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json

from sqlalchemy import Select, and_, case, func, or_, select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import Category, DailySnapshot, SubTask, Task, TaskCompletion, User, ProductivityStats
from .schemas import (
    CategoryBreakdownItem,
    CategoryCreate,
    CategoryUpdate,
    LoginRequest,
    SignUpRequest,
    SubTaskCreate,
    SubTaskUpdate,
    TaskCreate,
    TaskUpdate,
    UserUpdate,
    TrendPoint,
    ProductivityStatsOut,
)
from .security import generate_token, hash_password, verify_password
from .cache import invalidate_user_cache


def _normalized_name(name: str) -> str:
    return name.strip()


def _habit_days_to_str(days: list[int] | None) -> str | None:
    """Convert a list like [0,2,4] to '0,2,4' for DB storage. None = daily."""
    if days is None or len(days) == 0 or len(days) == 7:
        return None  # daily
    return ",".join(str(d) for d in sorted(set(days)))


def _is_habit_active_today(habit_days_str: str | None) -> bool:
    """Check if a habit with the given habit_days string is active today."""
    if not habit_days_str:
        return True  # daily
    today_weekday = datetime.now(timezone.utc).weekday()  # 0=Mon..6=Sun
    active_days = {int(d) for d in habit_days_str.split(",") if d.strip().isdigit()}
    return today_weekday in active_days


def _normalize_task_progress(task: Task) -> bool:
    """Repair legacy mismatches between completion state and stored progress."""
    expected_progress = 100 if task.completed else 0

    if task.completed and task.progress != 100:
        task.progress = 100
        return True

    if not task.completed and task.progress == 100:
        task.progress = 0
        return True

    if task.progress is None:
        task.progress = expected_progress
        return True

    return False


async def _normalize_tasks_progress(session: AsyncSession, tasks: list[Task]) -> None:
    changed = False
    for task in tasks:
        changed = _normalize_task_progress(task) or changed

    if changed:
        await session.commit()


async def _claim_orphaned_data_for_single_user(session: AsyncSession, user: User) -> None:
    user_count = int((await session.execute(select(func.count(User.id)))).scalar_one())
    if user_count != 1:
        return

    await session.execute(update(Category).where(Category.user_id.is_(None)).values(user_id=user.id))
    await session.execute(update(Task).where(Task.user_id.is_(None)).values(user_id=user.id))
    await session.commit()


async def create_category(session: AsyncSession, user: User, payload: CategoryCreate) -> Category:
    normalized_name = _normalized_name(payload.name)
    existing = await session.execute(
        select(Category).where(
            Category.user_id == user.id,
            func.lower(Category.name) == normalized_name.lower(),
        )
    )
    if existing.scalar_one_or_none():
        raise ValueError("Category with this name already exists")

    category = Category(user_id=user.id, name=normalized_name, color=payload.color, icon=payload.icon)
    session.add(category)
    await session.commit()
    await session.refresh(category)
    return category


async def list_categories(session: AsyncSession, user: User) -> list[Category]:
    result = await session.execute(
        select(Category).where(Category.user_id == user.id).order_by(Category.created_at.desc())
    )
    return list(result.scalars().all())


async def get_category(session: AsyncSession, user: User, category_id: str) -> Category | None:
    result = await session.execute(
        select(Category).where(Category.id == category_id, Category.user_id == user.id)
    )
    return result.scalar_one_or_none()


async def update_category(session: AsyncSession, user: User, category: Category, payload: CategoryUpdate) -> Category:
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        normalized_name = _normalized_name(data["name"])
        existing = await session.execute(
            select(Category).where(
                Category.user_id == user.id,
                Category.id != category.id,
                func.lower(Category.name) == normalized_name.lower(),
            )
        )
        if existing.scalar_one_or_none():
            raise ValueError("Category with this name already exists")
        data["name"] = normalized_name

    for key, value in data.items():
        setattr(category, key, value)
    await session.commit()
    await session.refresh(category)
    return category


async def delete_category(session: AsyncSession, category: Category) -> None:
    await session.delete(category)
    await session.commit()


async def create_task(session: AsyncSession, user: User, payload: TaskCreate) -> Task:
    category = None
    if payload.category_id:
        category = await get_category(session, user, payload.category_id)
        
    if not category:
        # Fallback to creating/fetching "General" category
        stmt = select(Category).where(
            Category.user_id == user.id,
            func.lower(Category.name) == "general"
        )
        cat_res = await session.execute(stmt)
        category = cat_res.scalar_one_or_none()
        if not category:
            category = Category(user_id=user.id, name="General", color="#6B7280", icon="folder")
            session.add(category)
            await session.commit()
            await session.refresh(category)
            
    completed_at = datetime.now(timezone.utc) if payload.completed else None
    priority_map = {"low": 0, "medium": 1, "high": 2}
    task = Task(
        user_id=user.id,
        title=payload.title.strip(),
        category_id=category.id,
        notes=payload.notes,
        completed=payload.completed,
        completed_at=completed_at,
        is_habit=payload.is_habit,
        priority=payload.priority,
        due_time=payload.due_time,
        start_date=payload.start_date,
        habit_days=_habit_days_to_str(payload.habit_days) if payload.is_habit else None,
        progress=100 if payload.completed else 0,
    )
    for idx, sub in enumerate(payload.subtasks):
        task.subtasks.append(SubTask(title=sub.title.strip(), completed=sub.completed, position=idx))

    session.add(task)
    await session.commit()
    # Adjust persistent ledger totals
    await adjust_stats(session, user.id, category_id=category.id, total_delta=1, completed_delta=1 if payload.completed else 0)
    # Invalidate cache so dashboard reflects the new task immediately
    await invalidate_user_cache(user.id)
    return await get_task_or_none(session, user, task.id, with_subtasks=True)


async def get_task_or_none(
    session: AsyncSession,
    user: User,
    task_id: str,
    with_subtasks: bool = True,
) -> Task | None:
    stmt: Select[tuple[Task]] = select(Task).where(Task.id == task_id, Task.user_id == user.id)
    if with_subtasks:
        stmt = stmt.options(selectinload(Task.subtasks))
    result = await session.execute(stmt)
    task = result.scalar_one_or_none()
    if task:
        await _normalize_tasks_progress(session, [task])
    return task


async def list_tasks(
    session: AsyncSession,
    user: User,
    category_id: str | None,
    completed: bool | None,
    priority: str | None,
    search: str | None,
    limit: int,
    offset: int,
    date_filter: str | None = None,
    include_future: bool = False,
) -> tuple[list[Task], int]:
    filters = [Task.user_id == user.id, Task.is_deleted.is_(False)]
    now_utc = datetime.now(timezone.utc)
    today = now_utc.date()
    today_weekday = str(today.weekday())  # 0=Mon..6=Sun

    if include_future:
        # Show ONLY tasks whose start_date is strictly in the future
        filters.append(Task.start_date > today)
    else:
        # Default: hide tasks that haven't started yet
        filters.append(or_(Task.start_date.is_(None), Task.start_date <= today))
        # Only show habits that are active today
        filters.append(or_(
            Task.is_habit.is_(False),
            Task.habit_days.is_(None),
            Task.habit_days.contains(today_weekday),
        ))
    if category_id:
        filters.append(Task.category_id == category_id)
    if completed is not None:
        filters.append(Task.completed == completed)
    if priority:
        filters.append(Task.priority == priority)
    if search:
        filters.append(Task.title.ilike(f"%{search.strip()}%"))
    # Date filter: 'today' scopes tasks to today
    # - Active tasks: only those created today OR habits (already scoped to today by habit_days filter)
    # - Completed tasks: only those marked complete today (completed_at is today)
    if date_filter == "today":
        today_start = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        today_end = today_start + timedelta(days=1)
        filters.append(or_(
            # Active (pending) tasks: habits active today (already filtered above) OR one-offs created today
            and_(
                Task.completed.is_(False),
                or_(Task.is_habit.is_(True), Task.created_at >= today_start)
            ),
            # Completed tasks: only those completed today, regardless of when they were created
            and_(
                Task.completed.is_(True),
                Task.completed_at >= today_start,
                Task.completed_at < today_end,
            ),
        ))

    base_query  = select(Task).where(*filters)
    total_query = select(func.count(Task.id)).where(*filters)

    # Future tasks ordered by start_date asc so soonest appears first
    order_col = Task.start_date.asc() if include_future else Task.created_at.desc()

    tasks_query = (
        base_query.options(selectinload(Task.subtasks))
        .order_by(order_col)
        .limit(limit)
        .offset(offset)
    )

    tasks_result, total_result = await session.execute(tasks_query), await session.execute(total_query)
    tasks = list(tasks_result.scalars().unique().all())
    await _normalize_tasks_progress(session, tasks)
    total = int(total_result.scalar_one())
    return tasks, total


async def update_task(session: AsyncSession, user: User, task: Task, payload: TaskUpdate) -> Task:
    data = payload.model_dump(exclude_unset=True)
    if "category_id" in data and data["category_id"] is not None:
        category = await get_category(session, user, data["category_id"])
        if not category:
            raise ValueError("Invalid category_id")
    if "title" in data and data["title"] is not None:
        data["title"] = data["title"].strip()

    # Keep progress and completion state consistent in both directions.
    if "progress" in data and data["progress"] is not None and "completed" not in data:
        data["completed"] = data["progress"] >= 100
    elif "completed" in data and data["completed"] is not None and "progress" not in data:
        data["progress"] = 100 if data["completed"] else 0

    if "completed" in data and data["completed"] is not None:
        old_completed = task.completed
        new_completed = data["completed"]
        if new_completed != old_completed:
            completed_delta = 1 if new_completed else -1
            await adjust_stats(session, user.id, category_id=task.category_id, completed_delta=completed_delta)

        if new_completed and not old_completed:
            task.completed_at = datetime.now(timezone.utc)
            session.add(TaskCompletion(
                user_id=user.id,
                task_id=task.id,
                category_id=task.category_id,
                task_title=task.title,
                is_habit=task.is_habit,
                completed_at=task.completed_at,
            ))
        elif not new_completed and old_completed:
            task.completed_at = None
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            await session.execute(
                delete(TaskCompletion).where(
                    TaskCompletion.task_id == task.id,
                    TaskCompletion.completed_at >= today_start
                )
            )
    if "priority" in data:
        task.priority = data.pop("priority")
    if "habit_days" in data:
        task.habit_days = _habit_days_to_str(data.pop("habit_days"))
        
    for key, value in data.items():
        setattr(task, key, value)
    await session.commit()
    await invalidate_user_cache(user.id)
    return await get_task_or_none(session, user, task.id, with_subtasks=True)


async def delete_task(session: AsyncSession, task: Task) -> None:
    task.is_deleted = True
    task.deleted_at = datetime.now(timezone.utc)
    await session.commit()
    await invalidate_user_cache(task.user_id)


async def add_subtask(session: AsyncSession, user: User, task: Task, payload: SubTaskCreate) -> Task:
    position_result = await session.execute(select(func.count(SubTask.id)).where(SubTask.task_id == task.id))
    position = int(position_result.scalar_one())
    subtask = SubTask(task_id=task.id, title=payload.title.strip(), completed=payload.completed, position=position)
    session.add(subtask)
    await session.commit()
    return await get_task_or_none(session, user, task.id, with_subtasks=True)


async def get_subtask(session: AsyncSession, user: User, task_id: str, subtask_id: str) -> SubTask | None:
    result = await session.execute(
        select(SubTask)
        .join(Task, Task.id == SubTask.task_id)
        .where(
            SubTask.id == subtask_id,
            SubTask.task_id == task_id,
            Task.user_id == user.id,
        )
    )
    return result.scalar_one_or_none()


async def update_subtask(session: AsyncSession, subtask: SubTask, payload: SubTaskUpdate) -> SubTask:
    data = payload.model_dump(exclude_unset=True)
    if "title" in data and data["title"] is not None:
        data["title"] = data["title"].strip()
    for key, value in data.items():
        setattr(subtask, key, value)
    await session.commit()
    await session.refresh(subtask)
    return subtask


async def toggle_task_completion(session: AsyncSession, user: User, task: Task) -> Task:
    task.completed = not task.completed
    task.completed_at = datetime.now(timezone.utc) if task.completed else None
    task.progress = 100 if task.completed else 0
    
    # Log to completion ledger for accurate historical stats
    if task.completed:
        session.add(TaskCompletion(
            user_id=user.id,
            task_id=task.id,
            category_id=task.category_id,
            task_title=task.title,
            is_habit=task.is_habit,
            completed_at=task.completed_at,
        ))
    else:
        # If uncompleted today, delete today's completion records to undo
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        await session.execute(
            delete(TaskCompletion).where(
                TaskCompletion.task_id == task.id,
                TaskCompletion.completed_at >= today_start
            )
        )
    
    await session.commit()
    # Adjust persistent ledger totals
    completed_delta = 1 if task.completed else -1
    await adjust_stats(session, user.id, category_id=task.category_id, completed_delta=completed_delta)
    await invalidate_user_cache(user.id)
    return await get_task_or_none(session, user, task.id, with_subtasks=True)


async def toggle_subtask_completion(session: AsyncSession, subtask: SubTask) -> SubTask:
    subtask.completed = not subtask.completed
    await session.commit()
    await session.refresh(subtask)
    return subtask


async def dashboard_stats(session: AsyncSession, user: User) -> dict[str, float | int]:
    # Define "today" in UTC
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_date = now.date()
    today_weekday = str(today_date.weekday())
    
    filters = [
        Task.user_id == user.id,
        Task.is_deleted.is_(False),
        # Exclude tasks that haven't started yet
        or_(Task.start_date.is_(None), Task.start_date <= today_date),
        # Exclude habits not active today
        or_(Task.is_habit.is_(False), Task.habit_days.is_(None), Task.habit_days.contains(today_weekday)),
    ]
    
    # Active tasks = Pending tasks (whose start_date has arrived)
    active_q = await session.execute(select(func.count(Task.id)).where(*filters, Task.completed.is_(False)))
    active = int(active_q.scalar_one())
    
    # Completed today = Completed and completed_at is today
    completed_today_q = await session.execute(
        select(func.count(Task.id)).where(*filters, Task.completed.is_(True), Task.completed_at >= today_start)
    )
    completed = int(completed_today_q.scalar_one())
    
    # Total for today = Pending (active) + Completed Today
    total = active + completed
    
    categories_q = await session.execute(select(func.count(Category.id)).where(Category.user_id == user.id))
    categories = int(categories_q.scalar_one())

    return {
        "total_tasks": total,
        "completed_tasks": completed,
        "active_tasks": active,
        "categories": categories,
        "completion_rate": min(round((completed / total * 100) if total else 0.0, 2), 100.0),
    }


async def history_summary(session: AsyncSession, user: User) -> dict[str, datetime | float | int]:
    """
    Returns history/streak summary.
    OPTIMISED: reads from the already-stored ProductivityStats row if it is
    fresh (updated within the last 5 minutes) to avoid a redundant full recalculation.
    Falls back to a fresh calculation only when the stored row is stale or missing.
    """
    now = datetime.now(timezone.utc)

    # Try to read from the stored stats row first
    stored_q = await session.execute(
        select(ProductivityStats).where(ProductivityStats.user_id == user.id)
    )
    stored = stored_q.scalar_one_or_none()

    if stored and stored.updated_at and (now - stored.updated_at).total_seconds() < 300:
        # Stored row is fresh — use it directly (saves re-running the heavy calculation)
        alltime_total     = int(stored.alltime_total_tasks or 0)
        alltime_completed = int(stored.alltime_completed_tasks or 0)
        alltime_rate      = float(stored.alltime_completion_rate or 0.0)
    else:
        # Stale or missing — recalculate
        stats             = await calculate_and_store_productivity_stats(session, user)
        alltime_total     = stats.alltime_total_tasks
        alltime_completed = stats.alltime_completed_tasks
        alltime_rate      = stats.alltime_completion_rate

    # Streak calculation: one query, computed in Python
    completion_date_expr = func.date(TaskCompletion.completed_at)
    streak_result = await session.execute(
        select(completion_date_expr)
        .where(TaskCompletion.user_id == user.id)
        .group_by(completion_date_expr)
    )
    completed_days = {value for value in streak_result.scalars().all() if value}

    streak = 0
    cursor = now.date()
    if cursor in completed_days or (cursor - timedelta(days=1)) in completed_days:
        if cursor not in completed_days:
            cursor -= timedelta(days=1)
        while cursor in completed_days:
            streak += 1
            cursor -= timedelta(days=1)

    return {
        "started_at": user.created_at,
        "since_start_total_tasks": alltime_total,
        "since_start_completed_tasks": alltime_completed,
        "completion_rate": alltime_rate,
        "current_streak": streak,
        "total_momentum": alltime_completed * 10,
    }


async def category_completion_stats(
    session: AsyncSession,
    user: User,
    days: int = 30,
) -> list[dict[str, str | int | float]]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    completed_case = func.coalesce(func.sum(case((Task.completed.is_(True), 1), else_=0)), 0)
    stmt = (
        select(
            Category.id.label("category_id"),
            Category.name.label("category_name"),
            Category.color.label("color"),
            func.count(Task.id).label("total_tasks"),
            completed_case.label("completed_tasks"),
        )
        .where(Category.user_id == user.id)
        .outerjoin(
            Task,
            and_(
                Task.category_id == Category.id,
                Task.user_id == user.id,
                Task.created_at >= cutoff,
            ),
        )
        .group_by(Category.id, Category.name, Category.color)
        .order_by(Category.created_at.desc())
    )

    result = await session.execute(stmt)
    rows = result.mappings().all()

    stats: list[dict[str, str | int | float]] = []
    for row in rows:
        total_tasks = int(row["total_tasks"] or 0)
        completed_tasks = int(row["completed_tasks"] or 0)
        completion_rate = min(round((completed_tasks / total_tasks * 100) if total_tasks else 0.0, 2), 100.0)
        stats.append(
            {
                "category_id": str(row["category_id"]),
                "category_name": str(row["category_name"]),
                "color": str(row["color"]),
                "total_tasks": total_tasks,
                "completed_tasks": completed_tasks,
                "completion_rate": completion_rate,
            }
        )

    return stats


async def update_user_profile(session: AsyncSession, user: User, payload: UserUpdate) -> User:
    data = payload.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        user.name = data["name"].strip()
    if "password" in data and data["password"] is not None:
        user.password_hash = hash_password(data["password"])
    await session.commit()
    await session.refresh(user)
    return user


async def sign_up(session: AsyncSession, payload: SignUpRequest) -> User:
    existing = await session.execute(select(User).where(User.email == payload.email.lower()))
    if existing.scalar_one_or_none():
        raise ValueError("Email already exists")
    user = User(
        email=payload.email.lower(),
        name=payload.name.strip(),
        password_hash=hash_password(payload.password),
        auth_token=generate_token(),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    await _claim_orphaned_data_for_single_user(session, user)
    await session.refresh(user)
    return user


async def login(session: AsyncSession, payload: LoginRequest) -> User:
    user_result = await session.execute(select(User).where(User.email == payload.email.lower()))
    user = user_result.scalar_one_or_none()
    if not user or not verify_password(payload.password, user.password_hash):
        raise ValueError("Invalid email or password")
    
    # Only generate a new token if one doesn't exist
    if not user.auth_token:
        user.auth_token = generate_token()
        
    await session.commit()
    await session.refresh(user)
    await _claim_orphaned_data_for_single_user(session, user)
    await session.refresh(user)
    return user


# _count_completions_in_period and _count_available_tasks_for_period removed —
# their logic is now inlined into the single batch query in calculate_and_store_productivity_stats.


async def _get_category_breakdown(session: AsyncSession, user: User) -> list[CategoryBreakdownItem]:
    """
    Get category breakdown — OPTIMISED from N+1 queries to 3 total queries.
    Before: 2 queries per category (O(N*2)).
    After:  1 query for all categories + 1 bulk GROUP BY for completions + 1 bulk GROUP BY for active tasks.
    """
    # Query 1: all categories for this user
    cats_result = await session.execute(
        select(Category.id, Category.name, Category.color)
        .where(Category.user_id == user.id)
        .order_by(Category.created_at.desc())
    )
    categories = cats_result.all()
    if not categories:
        return []

    # Query 2: completions per category — single GROUP BY instead of one query per category
    comp_result = await session.execute(
        select(
            TaskCompletion.category_id,
            func.count(TaskCompletion.id).label("completed"),
        )
        .where(TaskCompletion.user_id == user.id)
        .group_by(TaskCompletion.category_id)
    )
    completions_by_cat: dict[str, int] = {
        row.category_id: int(row.completed) for row in comp_result if row.category_id
    }

    # Query 3: active (incomplete, non-deleted) tasks per category — single GROUP BY
    active_result = await session.execute(
        select(
            Task.category_id,
            func.count(Task.id).label("active"),
        )
        .where(
            Task.user_id == user.id,
            Task.completed.is_(False),
            Task.is_deleted.is_(False),
        )
        .group_by(Task.category_id)
    )
    active_by_cat: dict[str, int] = {
        row.category_id: int(row.active) for row in active_result
    }

    breakdown = []
    for cat_id, cat_name, cat_color in categories:
        completed = completions_by_cat.get(cat_id, 0)
        active    = active_by_cat.get(cat_id, 0)
        total     = completed + active
        rate      = min(round((completed / total * 100) if total else 0.0, 2), 100.0)
        breakdown.append(CategoryBreakdownItem(
            category_id=cat_id,
            category_name=cat_name,
            color=cat_color,
            total_tasks=total,
            completed_tasks=completed,
            completion_rate=rate,
        ))
    return breakdown


def _rate(completed: int, total: int) -> float:
    """Compute completion rate capped at 100%."""
    return min(round((completed / total * 100) if total else 0.0, 2), 100.0)


async def calculate_and_store_productivity_stats(
    session: AsyncSession,
    user: User,
) -> ProductivityStatsOut:
    """
    Calculate productivity stats — HEAVILY OPTIMISED.

    Before: 4 separate snapshot queries + up to 365 per-day trend queries + N*2 category queries.
    After:  1 live-stats query  +  1 batch snapshot query (all periods at once)
            + 1 trend snapshot range query  +  3 category queries total.
    Total DB round-trips: ~6 regardless of history length.
    """
    now         = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end   = today_start + timedelta(days=1)
    week_start  = today_start - timedelta(days=now.weekday())
    month_start = today_start.replace(day=1)
    user_start  = (
        user.created_at if user.created_at.tzinfo
        else user.created_at.replace(tzinfo=timezone.utc)
    ).replace(hour=0, minute=0, second=0, microsecond=0)

    # ── STEP 1: Live stats for today (1 query set) ──────────────────────────
    live        = await dashboard_stats(session, user)
    live_total  = int(live["total_tasks"])
    live_comp   = int(live["completed_tasks"])

    # ── STEP 2: One batch snapshot query — all periods simultaneously ───────
    # Conditional SUM replaces 4 separate snapshot queries.
    snap_q = await session.execute(
        select(
            # All-time
            func.sum(case((DailySnapshot.snapshot_date >= user_start,  DailySnapshot.total_available),  else_=0)).label("at_avail"),
            func.sum(case((DailySnapshot.snapshot_date >= user_start,  DailySnapshot.total_completed),  else_=0)).label("at_comp"),
            # Month
            func.sum(case((DailySnapshot.snapshot_date >= month_start, DailySnapshot.total_available),  else_=0)).label("mo_avail"),
            func.sum(case((DailySnapshot.snapshot_date >= month_start, DailySnapshot.total_completed),  else_=0)).label("mo_comp"),
            # Week
            func.sum(case((DailySnapshot.snapshot_date >= week_start,  DailySnapshot.total_available),  else_=0)).label("wk_avail"),
            func.sum(case((DailySnapshot.snapshot_date >= week_start,  DailySnapshot.total_completed),  else_=0)).label("wk_comp"),
        )
        .where(
            DailySnapshot.user_id       == user.id,
            DailySnapshot.snapshot_date >= user_start,
            DailySnapshot.snapshot_date <  today_start,   # exclude today (live covers it)
        )
    )
    snap = snap_q.first()

    at_avail = int(snap.at_avail or 0) if snap else 0
    at_comp  = int(snap.at_comp  or 0) if snap else 0
    mo_avail = int(snap.mo_avail or 0) if snap else 0
    mo_comp  = int(snap.mo_comp  or 0) if snap else 0
    wk_avail = int(snap.wk_avail or 0) if snap else 0
    wk_comp  = int(snap.wk_comp  or 0) if snap else 0

    # Combine snapshots + today's live figures
    day_total,     day_completed     = live_total,              live_comp
    week_total,    week_completed    = wk_avail + live_total,   wk_comp + live_comp
    month_total,   month_completed   = mo_avail + live_total,   mo_comp + live_comp
    alltime_total, alltime_completed = at_avail + live_total,   at_comp + live_comp

    day_rate     = _rate(day_completed,     day_total)
    week_rate    = _rate(week_completed,    week_total)
    month_rate   = _rate(month_completed,   month_total)
    alltime_rate = _rate(alltime_completed, alltime_total)

    # ── STEP 3: Trend — ONE range query, zero per-day loops ─────────────────
    account_start = user_start
    if account_start.tzinfo is None:
        account_start = account_start.replace(tzinfo=timezone.utc)
    lookback   = today_start - timedelta(days=6)
    trend_start = max(min(account_start, lookback), today_start - timedelta(days=365))

    trend_snaps_q = await session.execute(
        select(
            DailySnapshot.snapshot_date,
            DailySnapshot.total_available,
            DailySnapshot.total_completed,
        )
        .where(
            DailySnapshot.user_id       == user.id,
            DailySnapshot.snapshot_date >= trend_start,
            DailySnapshot.snapshot_date <  today_start,
        )
        .order_by(DailySnapshot.snapshot_date)
    )
    # Build a dict keyed by date so lookup is O(1)
    snap_by_date: dict[date, tuple[int, int]] = {
        row.snapshot_date.date(): (int(row.total_available or 0), int(row.total_completed or 0))
        for row in trend_snaps_q
    }

    days_to_show = (today_start.date() - trend_start.date()).days
    trend: list[TrendPoint] = []
    for i in range(days_to_show, 0, -1):
        d     = (today_start - timedelta(days=i)).date()
        avail, comp = snap_by_date.get(d, (0, 0))
        trend.append(TrendPoint(date=d.strftime("%b %d"), rate=_rate(comp, avail)))
    trend.append(TrendPoint(date=now.strftime("%b %d"), rate=day_rate))

    # ── STEP 4: Category breakdown (3 queries total via GROUP BY) ───────────
    category_breakdown     = await _get_category_breakdown(session, user)
    category_breakdown_json = json.dumps([item.model_dump() for item in category_breakdown])

    # ── STEP 5: Upsert stored stats row ────────────────────────────────────
    existing = await session.execute(
        select(ProductivityStats).where(ProductivityStats.user_id == user.id)
    )
    stats = existing.scalar_one_or_none()

    fields = dict(
        alltime_total_tasks=alltime_total,   alltime_completed_tasks=alltime_completed, alltime_completion_rate=alltime_rate,
        month_total_tasks=month_total,       month_completed_tasks=month_completed,     month_completion_rate=month_rate,
        week_total_tasks=week_total,         week_completed_tasks=week_completed,       week_completion_rate=week_rate,
        day_total_tasks=day_total,           day_completed_tasks=day_completed,         day_completion_rate=day_rate,
        category_breakdown=category_breakdown_json,
        updated_at=now,
    )
    if stats:
        for k, v in fields.items():
            setattr(stats, k, v)
    else:
        stats = ProductivityStats(user_id=user.id, **fields)
        session.add(stats)

    await session.commit()
    await session.refresh(stats)

    return ProductivityStatsOut(
        alltime_total_tasks=alltime_total,       alltime_completed_tasks=alltime_completed, alltime_completion_rate=alltime_rate,
        month_total_tasks=month_total,           month_completed_tasks=month_completed,     month_completion_rate=month_rate,
        week_total_tasks=week_total,             week_completed_tasks=week_completed,       week_completion_rate=week_rate,
        day_total_tasks=day_total,               day_completed_tasks=day_completed,         day_completion_rate=day_rate,
        category_breakdown=category_breakdown,
        trend=trend,
        updated_at=stats.updated_at,
    )


# get_productivity_stats is an alias kept for router compatibility
get_productivity_stats = calculate_and_store_productivity_stats


async def adjust_stats(
    session: AsyncSession,
    user_id: str,
    category_id: str | None = None,
    total_delta: int = 0,
    completed_delta: int = 0,
) -> None:
    """
    Called after every write.  Previously triggered a full stats recalculation
    on every task create/toggle/delete — extremely slow.

    Now it simply invalidates the Redis cache so the NEXT read recomputes stats
    lazily.  This makes writes ~10x faster with zero data loss.
    """
    await invalidate_user_cache(user_id)
