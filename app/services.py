from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json

from sqlalchemy import Select, and_, case, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import Category, SubTask, Task, User, ProductivityStats
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
    category = await get_category(session, user, payload.category_id)
    if not category:
        raise ValueError("Invalid category_id")

    completed_at = datetime.now(timezone.utc) if payload.completed else None
    task = Task(
        user_id=user.id,
        title=payload.title.strip(),
        category_id=payload.category_id,
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
    # Recalculate productivity stats
    await calculate_and_store_productivity_stats(session, user)
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
    limit: int,
    offset: int,
) -> tuple[list[Task], int]:
    filters = [Task.user_id == user.id]
    if category_id:
        filters.append(Task.category_id == category_id)
    if completed is not None:
        filters.append(Task.completed == completed)
    if priority:
        filters.append(Task.priority == priority)

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
        if data["completed"] and not task.completed:
            task.completed_at = datetime.now(timezone.utc)
        elif not data["completed"]:
            task.completed_at = None
    for key, value in data.items():
        setattr(task, key, value)
    await session.commit()
    return await get_task_or_none(session, user, task.id, with_subtasks=True)


async def delete_task(session: AsyncSession, task: Task) -> None:
    await session.delete(task)
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
    await session.commit()
    # Recalculate productivity stats
    await calculate_and_store_productivity_stats(session, user)
    return await get_task_or_none(session, user, task.id, with_subtasks=True)


async def toggle_subtask_completion(session: AsyncSession, subtask: SubTask) -> SubTask:
    subtask.completed = not subtask.completed
    await session.commit()
    await session.refresh(subtask)
    return subtask


async def dashboard_stats(session: AsyncSession, user: User) -> dict[str, float | int]:
    filters = [Task.user_id == user.id]
    total_q = await session.execute(select(func.count(Task.id)).where(*filters))
    completed_q = await session.execute(select(func.count(Task.id)).where(*filters, Task.completed.is_(True)))
    categories_q = await session.execute(select(func.count(Category.id)).where(Category.user_id == user.id))

    total = int(total_q.scalar_one())
    completed = int(completed_q.scalar_one())
    categories = int(categories_q.scalar_one())
    active = total - completed

    return {
        "total_tasks": total,
        "completed_tasks": completed,
        "active_tasks": active,
        "categories": categories,
        "completion_rate": round((completed / total * 100) if total else 0.0, 2),
    }


async def history_summary(session: AsyncSession, user: User) -> dict[str, datetime | float | int]:
    created_filters = [Task.user_id == user.id, Task.created_at >= user.created_at]
    total_tasks = int((await session.execute(select(func.count(Task.id)).where(*created_filters))).scalar_one())
    completed_tasks = int(
        (
            await session.execute(
                select(func.count(Task.id)).where(*created_filters, Task.completed.is_(True))
            )
        ).scalar_one()
    )

    completion_date_expr = func.date(func.coalesce(Task.completed_at, Task.created_at))
    streak_result = await session.execute(
        select(completion_date_expr)
        .where(Task.user_id == user.id, Task.completed.is_(True))
        .group_by(completion_date_expr)
    )
    completed_days = {
        date.fromisoformat(str(value))
        for value in streak_result.scalars().all()
        if value
    }

    streak = 0
    cursor = datetime.now(timezone.utc).date()
    while cursor in completed_days:
        streak += 1
        cursor -= timedelta(days=1)

    return {
        "started_at": user.created_at,
        "since_start_total_tasks": total_tasks,
        "since_start_completed_tasks": completed_tasks,
        "completion_rate": round((completed_tasks / total_tasks * 100) if total_tasks else 0.0, 2),
        "current_streak": streak,
        "total_momentum": completed_tasks * 5,
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
    user.name = payload.name.strip()
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
    user.auth_token = generate_token()
    await session.commit()
    await session.refresh(user)
    await _claim_orphaned_data_for_single_user(session, user)
    await session.refresh(user)
    return user


async def _calculate_stats_for_period(
    session: AsyncSession,
    user: User,
    start_date: datetime,
    end_date: datetime | None = None,
) -> tuple[int, int, float]:
    """Calculate total, completed, and completion rate for a given time period."""
    filters = [
        Task.user_id == user.id,
        Task.created_at >= start_date,
    ]
    if end_date:
        filters.append(Task.created_at < end_date)
    
    total_q = await session.execute(select(func.count(Task.id)).where(*filters))
    completed_q = await session.execute(
        select(func.count(Task.id)).where(*filters, Task.completed.is_(True))
    )
    
    total = int(total_q.scalar_one())
    completed = int(completed_q.scalar_one())
    completion_rate = round((completed / total * 100) if total else 0.0, 2)
    
    return total, completed, completion_rate


async def _get_category_breakdown(session: AsyncSession, user: User) -> list[CategoryBreakdownItem]:
    """Get category breakdown for all-time stats."""
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
            ),
        )
        .group_by(Category.id, Category.name, Category.color)
        .order_by(Category.created_at.desc())
    )

    result = await session.execute(stmt)
    rows = result.mappings().all()

    breakdown = []
    for row in rows:
        total_tasks = int(row["total_tasks"] or 0)
        completed_tasks = int(row["completed_tasks"] or 0)
        completion_rate = round((completed_tasks / total_tasks * 100) if total_tasks else 0.0, 2)
        breakdown.append(
            CategoryBreakdownItem(
                category_id=str(row["category_id"]),
                category_name=str(row["category_name"]),
                color=str(row["color"]),
                total_tasks=total_tasks,
                completed_tasks=completed_tasks,
                completion_rate=completion_rate,
            )
        )
    
    return breakdown


async def calculate_and_store_productivity_stats(
    session: AsyncSession,
    user: User,
) -> ProductivityStats:
    """Calculate productivity stats and store/overwrite in database."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=now.weekday())
    month_start = today_start.replace(day=1)
    
    # Calculate stats for each period
    alltime_total, alltime_completed, alltime_rate = await _calculate_stats_for_period(
        session, user, user.created_at
    )
    
    day_total, day_completed, day_rate = await _calculate_stats_for_period(
        session, user, today_start
    )
    
    week_total, week_completed, week_rate = await _calculate_stats_for_period(
        session, user, week_start
    )
    
    month_total, month_completed, month_rate = await _calculate_stats_for_period(
        session, user, month_start
    )
    
    # Get category breakdown
    category_breakdown = await _get_category_breakdown(session, user)
    category_breakdown_json = json.dumps([item.model_dump() for item in category_breakdown])
    
    # Check if stats already exist for this user
    existing_stats = await session.execute(
        select(ProductivityStats).where(ProductivityStats.user_id == user.id)
    )
    stats = existing_stats.scalar_one_or_none()
    
    if stats:
        # Update existing stats (overwrite)
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
        # Create new stats
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
    return stats


async def get_productivity_stats(session: AsyncSession, user: User) -> ProductivityStatsOut:
    """Get stored productivity stats."""
    # First calculate and store/update stats
    stats = await calculate_and_store_productivity_stats(session, user)
    
    # Parse category breakdown
    category_breakdown = None
    if stats.category_breakdown:
        try:
            breakdown_data = json.loads(stats.category_breakdown)
            category_breakdown = [CategoryBreakdownItem(**item) for item in breakdown_data]
        except (json.JSONDecodeError, TypeError):
            category_breakdown = None
    
    return ProductivityStatsOut(
        alltime_total_tasks=stats.alltime_total_tasks,
        alltime_completed_tasks=stats.alltime_completed_tasks,
        alltime_completion_rate=stats.alltime_completion_rate,
        day_total_tasks=stats.day_total_tasks,
        day_completed_tasks=stats.day_completed_tasks,
        day_completion_rate=stats.day_completion_rate,
        week_total_tasks=stats.week_total_tasks,
        week_completed_tasks=stats.week_completed_tasks,
        week_completion_rate=stats.week_completion_rate,
        month_total_tasks=stats.month_total_tasks,
        month_completed_tasks=stats.month_completed_tasks,
        month_completion_rate=stats.month_completion_rate,
        category_breakdown=category_breakdown,
        updated_at=stats.updated_at,
    )
