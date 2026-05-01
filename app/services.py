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


def _normalized_name(name: str) -> str:
    return name.strip()


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
    )
    for idx, sub in enumerate(payload.subtasks):
        task.subtasks.append(SubTask(title=sub.title.strip(), completed=sub.completed, position=idx))

    session.add(task)
    await session.commit()
    # Adjust persistent ledger totals
    await adjust_stats(session, user.id, category_id=category.id, total_delta=1, completed_delta=1 if payload.completed else 0)
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
    return result.scalar_one_or_none()


async def list_tasks(
    session: AsyncSession,
    user: User,
    category_id: str | None,
    completed: bool | None,
    priority: str | None,
    search: str | None,
    limit: int,
    offset: int,
) -> tuple[list[Task], int]:
    filters = [Task.user_id == user.id, Task.is_deleted.is_(False)]
    if category_id:
        filters.append(Task.category_id == category_id)
    if completed is not None:
        filters.append(Task.completed == completed)
    if priority:
        filters.append(Task.priority == priority)
    if search:
        filters.append(Task.title.ilike(f"%{search.strip()}%"))

    base_query = select(Task).where(*filters)
    total_query = select(func.count(Task.id)).where(*filters)

    tasks_query = (
        base_query.options(selectinload(Task.subtasks))
        .order_by(Task.created_at.desc())
        .limit(limit)
        .offset(offset)
    )

    tasks_result, total_result = await session.execute(tasks_query), await session.execute(total_query)
    tasks = list(tasks_result.scalars().unique().all())
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
    if "completed" in data and data["completed"] is not None:
        old_completed = task.completed
        new_completed = data["completed"]
        if new_completed != old_completed:
            completed_delta = 1 if new_completed else -1
            await adjust_stats(session, user.id, category_id=task.category_id, completed_delta=completed_delta)
        
        if new_completed and not old_completed:
            task.completed_at = datetime.now(timezone.utc)
            # Log to completion ledger for accurate historical stats
            session.add(TaskCompletion(
                user_id=user.id,
                task_id=task.id,
                category_id=task.category_id,
                task_title=task.title,
                is_habit=task.is_habit,
                completed_at=task.completed_at,
            ))
        elif not new_completed:
            task.completed_at = None
            # If uncompleted today, delete today's completion records to undo
            today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            await session.execute(
                delete(TaskCompletion).where(
                    TaskCompletion.task_id == task.id,
                    TaskCompletion.completed_at >= today_start
                )
            )
    if "priority" in data:
        task.priority = data.pop("priority")
        
    for key, value in data.items():
        setattr(task, key, value)
    await session.commit()
    return await get_task_or_none(session, user, task.id, with_subtasks=True)


async def delete_task(session: AsyncSession, task: Task) -> None:
    task.is_deleted = True
    task.deleted_at = datetime.now(timezone.utc)
    await session.commit()


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
    
    filters = [Task.user_id == user.id, Task.is_deleted.is_(False)]
    
    # Active tasks = Pending tasks
    active_q = await session.execute(select(func.count(Task.id)).where(*filters, Task.completed.is_(False)))
    active = int(active_q.scalar_one())
    
    # Completed today = Completed and completed_at is today
    completed_today_q = await session.execute(
        select(func.count(Task.id)).where(*filters, Task.completed.is_(True), Task.completed_at >= today_start)
    )
    completed = int(completed_today_q.scalar_one())
    
    # Total for today = Active + Completed Today
    total = active + completed
    
    categories_q = await session.execute(select(func.count(Category.id)).where(Category.user_id == user.id))
    categories = int(categories_q.scalar_one())

    return {
        "total_tasks": total,
        "completed_tasks": completed,
        "active_tasks": active,
        "categories": categories,
        "completion_rate": round((completed / total * 100) if total else 0.0, 2),
    }


async def history_summary(session: AsyncSession, user: User) -> dict[str, datetime | float | int]:
    # Use the ProductivityStats table which we already keep synced
    stats = await calculate_and_store_productivity_stats(session, user)
    
    # Calculate streak from ledger
    completion_date_expr = func.date(TaskCompletion.completed_at)
    streak_result = await session.execute(
        select(completion_date_expr)
        .where(TaskCompletion.user_id == user.id)
        .group_by(completion_date_expr)
    )
    completed_days = {
        value for value in streak_result.scalars().all()
        if value
    }

    streak = 0
    cursor = datetime.now(timezone.utc).date()
    # Check if they did something today or yesterday to continue streak
    if cursor in completed_days or (cursor - timedelta(days=1)) in completed_days:
        if cursor not in completed_days:
            cursor -= timedelta(days=1)
        while cursor in completed_days:
            streak += 1
            cursor -= timedelta(days=1)

    return {
        "started_at": user.created_at,
        "since_start_total_tasks": int(stats.alltime_total_tasks),
        "since_start_completed_tasks": int(stats.alltime_completed_tasks),
        "completion_rate": float(stats.alltime_completion_rate),
        "current_streak": streak,
        "total_momentum": int(stats.alltime_completed_tasks * 10), # 10 momentum per task
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
        completion_rate = round((completed_tasks / total_tasks * 100) if total_tasks else 0.0, 2)
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


async def _count_completions_in_period(
    session: AsyncSession,
    user: User,
    start_date: datetime,
    end_date: datetime | None = None,
) -> int:
    """Count total task completions in a time window using the TaskCompletion ledger."""
    stmt = select(func.count(TaskCompletion.id)).where(
        TaskCompletion.user_id == user.id,
        TaskCompletion.completed_at >= start_date,
    )
    if end_date:
        stmt = stmt.where(TaskCompletion.completed_at < end_date)
    
    res = await session.execute(stmt)
    count = int(res.scalar_one() or 0)
    
    # Fallback: also count completed one-offs in the live Task table that aren't in the ledger
    # (Only needed if the ledger is missing old data, but good for robustness)
    task_filters = [
        Task.user_id == user.id,
        Task.is_habit.is_(False),
        Task.completed.is_(True),
        Task.completed_at >= start_date,
    ]
    if end_date:
        task_filters.append(Task.completed_at < end_date)
    
    # We only count those NOT in the ledger to avoid double-counting
    # But since we're using a ledger now, we'll assume most are there.
    # To be perfectly safe, we could check which IDs aren't in TaskCompletion, 
    # but a simpler fallback is to just count tasks with completed_at if they are one-offs.
    # For now, let's just rely on the ledger for habits and use a distinct count for one-offs 
    # if we really need to, but the current ledger tracks everything.
    
    return count


async def _count_available_tasks_for_period(
    session: AsyncSession,
    user: User,
    period_start: datetime,
    period_end: datetime,
) -> int:
    """
    Count the total 'possible' tasks for a period:
    - Habits: count each habit that existed for each day in the period
    - One-off: count one-off tasks that existed during the period (created before period_end, not deleted before period_start)
    """
    # One-off tasks created before period_end and completed_at is either null or >= period_start
    # (if completed before period_start they're already gone / irrelevant to this window)
    oneoff_q = await session.execute(
        select(func.count(Task.id)).where(
            Task.user_id == user.id,
            Task.is_habit.is_(False),
            Task.created_at < period_end,
        )
    )
    oneoff_total = int(oneoff_q.scalar_one() or 0)

    # Habits: count distinct habits that existed, multiplied by the number of days they were active in the window
    habits_result = await session.execute(
        select(Task.id, Task.created_at).where(
            Task.user_id == user.id,
            Task.is_habit.is_(True),
            Task.created_at < period_end,
        )
    )
    habit_rows = habits_result.all()

    # How many full days in this period?
    days_in_period = max(1, (period_end.date() - period_start.date()).days)

    habit_total = 0
    for _, habit_created_at in habit_rows:
        created = habit_created_at if habit_created_at.tzinfo else habit_created_at.replace(tzinfo=timezone.utc)
        # Days this habit was active within the period
        habit_start_in_period = max(period_start, created.replace(hour=0, minute=0, second=0, microsecond=0))
        days_active = max(1, (period_end.date() - habit_start_in_period.date()).days)
        habit_total += min(days_active, days_in_period)

    return oneoff_total + habit_total


async def _get_category_breakdown(session: AsyncSession, user: User) -> list[CategoryBreakdownItem]:
    """Get category breakdown for all-time stats using TaskCompletion ledger."""
    categories_result = await session.execute(select(Category).where(Category.user_id == user.id))
    categories = categories_result.scalars().all()
    
    breakdown = []
    for cat in categories:
        # Count all-time completions for this category from TaskCompletion ledger
        # We look at category_id directly in TaskCompletion for persistence after task deletion
        completions_q = await session.execute(
            select(func.count(TaskCompletion.id)).where(
                TaskCompletion.user_id == user.id,
                TaskCompletion.category_id == cat.id
            )
        )
        completed = int(completions_q.scalar_one() or 0)
        
        # Total tasks for a category = (Current Tasks in DB) + (Completions in Ledger)
        # Note: This is an approximation since a task might have multiple completions (if habit)
        # But for breakdown, we want to show volume.
        current_total_q = await session.execute(
            select(func.count(Task.id)).where(
                Task.user_id == user.id, 
                Task.category_id == cat.id,
                Task.completed.is_(False) # Only count active tasks toward current "total"
            )
        )
        active = int(current_total_q.scalar_one() or 0)
        
        total = completed + active
        rate = round((completed / total * 100) if total else 0.0, 2)
        
        breakdown.append(CategoryBreakdownItem(
            category_id=cat.id,
            category_name=cat.name,
            color=cat.color,
            total_tasks=total,
            completed_tasks=completed,
            completion_rate=rate
        ))
    return breakdown


async def calculate_and_store_productivity_stats(
    session: AsyncSession,
    user: User,
) -> ProductivityStats:
    """Calculate productivity stats using a hybrid approach for perfect accuracy across time."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timedelta(days=1)
    week_start = today_start - timedelta(days=now.weekday())
    month_start = today_start.replace(day=1)

    user_start = user.created_at if user.created_at.tzinfo else user.created_at.replace(tzinfo=timezone.utc)
    
    async def get_stats_for_period(start_date: datetime, end_date: datetime | None = None):
        filters = [TaskCompletion.user_id == user.id, TaskCompletion.completed_at >= start_date]
        if end_date:
            filters.append(TaskCompletion.completed_at < end_date)
        completed_q = await session.execute(select(func.count(TaskCompletion.id)).where(*filters))
        completed = completed_q.scalar() or 0

        avail_filters = [Task.user_id == user.id]
        if end_date:
            avail_filters.append(Task.created_at < end_date)
        avail_filters.append(or_(Task.is_deleted.is_(False), Task.deleted_at >= start_date))
        avail_filters.append(or_(Task.is_habit.is_(True), Task.completed.is_(False), Task.completed_at >= start_date))
        
        available_q = await session.execute(select(func.count(Task.id)).where(*avail_filters))
        available = available_q.scalar() or 0
        return available, completed

    # === DAY stats ===
    day_total, day_completed = await get_stats_for_period(today_start, today_end)
    day_rate = round((day_completed / day_total * 100) if day_total else 0.0, 2)
    

    # === ALL-TIME stats ===
    alltime_total, alltime_completed = await get_stats_for_period(user_start, None)
    alltime_rate = round((alltime_completed / alltime_total * 100) if alltime_total else 0.0, 2)

    # === WEEK stats ===
    week_total, week_completed = await get_stats_for_period(week_start, today_end)
    week_rate = round((week_completed / week_total * 100) if week_total else 0.0, 2)

    # === MONTH stats ===
    month_total, month_completed = await get_stats_for_period(month_start, today_end)
    month_rate = round((month_completed / month_total * 100) if month_total else 0.0, 2)

    # === TREND stats ===
    trend = []
    lookback = today_start - timedelta(days=6)
    account_start = user.created_at.replace(hour=0, minute=0, second=0, microsecond=0)
    if account_start.tzinfo is None and today_start.tzinfo is not None:
        account_start = account_start.replace(tzinfo=timezone.utc)
    
    trend_start = min(account_start, lookback)
    trend_start = max(trend_start, today_start - timedelta(days=365))
    days_to_show = (today_start.date() - trend_start.date()).days
    
    for i in range(days_to_show, 0, -1):
        target_start = today_start - timedelta(days=i)
        target_end = target_start + timedelta(days=1)
        avail, comp = await get_stats_for_period(target_start, target_end)
        rate = round((comp / avail * 100) if avail else 0.0, 2)
        trend.append(TrendPoint(date=target_start.date().strftime("%b %d"), rate=rate))
    
    trend.append(TrendPoint(date=now.strftime("%b %d"), rate=day_rate))

    # Category breakdown
    category_breakdown = await _get_category_breakdown(session, user)
    category_breakdown_json = json.dumps([item.model_dump() for item in category_breakdown])

    existing_stats = await session.execute(
        select(ProductivityStats).where(ProductivityStats.user_id == user.id)
    )
    stats = existing_stats.scalar_one_or_none()

    if stats:
        stats.alltime_total_tasks = alltime_total
        stats.alltime_completed_tasks = alltime_completed
        stats.alltime_completion_rate = alltime_rate
        stats.day_total_tasks = day_total
        stats.day_completed_tasks = day_completed
        stats.day_completion_rate = day_rate
        stats.week_total_tasks = week_total
        stats.week_completed_tasks = week_completed
        stats.week_completion_rate = week_rate
        stats.month_total_tasks = month_total
        stats.month_completed_tasks = month_completed
        stats.month_completion_rate = month_rate
        stats.category_breakdown = category_breakdown_json
        stats.updated_at = now
    else:
        stats = ProductivityStats(
            user_id=user.id,
            alltime_total_tasks=alltime_total,
            alltime_completed_tasks=alltime_completed,
            alltime_completion_rate=alltime_rate,
            day_total_tasks=day_total,
            day_completed_tasks=day_completed,
            day_completion_rate=day_rate,
            week_total_tasks=week_total,
            week_completed_tasks=week_completed,
            week_completion_rate=week_rate,
            month_total_tasks=month_total,
            month_completed_tasks=month_completed,
            month_completion_rate=month_rate,
            category_breakdown=category_breakdown_json,
        )
        session.add(stats)

    await session.commit()
    await session.refresh(stats)
    
    return ProductivityStatsOut(
        alltime_total_tasks=int(stats.alltime_total_tasks or 0),
        alltime_completed_tasks=int(stats.alltime_completed_tasks or 0),
        alltime_completion_rate=float(stats.alltime_completion_rate or 0.0),
        month_total_tasks=int(stats.month_total_tasks or 0),
        month_completed_tasks=int(stats.month_completed_tasks or 0),
        month_completion_rate=float(stats.month_completion_rate or 0.0),
        week_total_tasks=int(stats.week_total_tasks or 0),
        week_completed_tasks=int(stats.week_completed_tasks or 0),
        week_completion_rate=float(stats.week_completion_rate or 0.0),
        day_total_tasks=int(stats.day_total_tasks or 0),
        day_completed_tasks=int(stats.day_completed_tasks or 0),
        day_completion_rate=float(stats.day_completion_rate or 0.0),
        category_breakdown=category_breakdown,
        trend=trend,
        updated_at=stats.updated_at
    )


async def get_productivity_stats(session: AsyncSession, user: User) -> ProductivityStatsOut:
    """Get stored productivity stats, recalculating if needed."""
    return await calculate_and_store_productivity_stats(session, user)


async def adjust_stats(
    session: AsyncSession,
    user_id: str,
    category_id: str | None = None,
    total_delta: int = 0,
    completed_delta: int = 0,
) -> None:
    """Adjust persistent ledger totals for a user by recalculating from the source of truth."""
    user_res = await session.execute(select(User).where(User.id == user_id))
    user = user_res.scalar_one_or_none()
    if user:
        await calculate_and_store_productivity_stats(session, user)
