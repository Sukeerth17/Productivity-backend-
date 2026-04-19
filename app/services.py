from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import Select, and_, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from .models import Category, SubTask, Task, User
from .schemas import (
    CategoryCreate,
    CategoryUpdate,
    LoginRequest,
    SignUpRequest,
    SubTaskCreate,
    SubTaskUpdate,
    TaskCreate,
    TaskUpdate,
)
from .security import generate_token, hash_password, verify_password


async def create_category(session: AsyncSession, payload: CategoryCreate) -> Category:
    existing = await session.execute(select(Category).where(Category.name == payload.name.strip()))
    if existing.scalar_one_or_none():
        raise ValueError("Category with this name already exists")
    category = Category(name=payload.name.strip(), color=payload.color, icon=payload.icon)
    session.add(category)
    await session.commit()
    await session.refresh(category)
    return category


async def list_categories(session: AsyncSession) -> list[Category]:
    result = await session.execute(select(Category).order_by(Category.created_at.desc()))
    return list(result.scalars().all())


async def get_category(session: AsyncSession, category_id: str) -> Category | None:
    return await session.get(Category, category_id)


async def update_category(session: AsyncSession, category: Category, payload: CategoryUpdate) -> Category:
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(category, key, value)
    await session.commit()
    await session.refresh(category)
    return category


async def delete_category(session: AsyncSession, category: Category) -> None:
    await session.delete(category)
    await session.commit()


async def create_task(session: AsyncSession, payload: TaskCreate) -> Task:
    category = await session.get(Category, payload.category_id)
    if not category:
        raise ValueError("Invalid category_id")

    task = Task(
        title=payload.title.strip(),
        category_id=payload.category_id,
        notes=payload.notes,
        completed=payload.completed,
        completed_at=None,
        is_habit=payload.is_habit,
        priority=payload.priority,
        due_time=payload.due_time,
    )
    for idx, sub in enumerate(payload.subtasks):
        task.subtasks.append(SubTask(title=sub.title.strip(), completed=sub.completed, position=idx))

    session.add(task)
    await session.commit()
    return await get_task_or_none(session, task.id, with_subtasks=True)


async def get_task_or_none(session: AsyncSession, task_id: str, with_subtasks: bool = True) -> Task | None:
    stmt: Select[tuple[Task]] = select(Task).where(Task.id == task_id)
    if with_subtasks:
        stmt = stmt.options(selectinload(Task.subtasks))
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_tasks(
    session: AsyncSession,
    category_id: str | None,
    completed: bool | None,
    priority: str | None,
    limit: int,
    offset: int,
) -> tuple[list[Task], int]:
    filters = []
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


async def update_task(session: AsyncSession, task: Task, payload: TaskUpdate) -> Task:
    data = payload.model_dump(exclude_unset=True)
    if "category_id" in data and data["category_id"] is not None:
        category = await session.get(Category, data["category_id"])
        if not category:
            raise ValueError("Invalid category_id")
    for key, value in data.items():
        setattr(task, key, value)
    await session.commit()
    return await get_task_or_none(session, task.id, with_subtasks=True)


async def delete_task(session: AsyncSession, task: Task) -> None:
    await session.delete(task)
    await session.commit()


async def add_subtask(session: AsyncSession, task: Task, payload: SubTaskCreate) -> Task:
    position_result = await session.execute(select(func.count(SubTask.id)).where(SubTask.task_id == task.id))
    position = int(position_result.scalar_one())
    subtask = SubTask(task_id=task.id, title=payload.title.strip(), completed=payload.completed, position=position)
    session.add(subtask)
    await session.commit()
    return await get_task_or_none(session, task.id, with_subtasks=True)


async def get_subtask(session: AsyncSession, task_id: str, subtask_id: str) -> SubTask | None:
    result = await session.execute(select(SubTask).where(SubTask.id == subtask_id, SubTask.task_id == task_id))
    return result.scalar_one_or_none()


async def update_subtask(session: AsyncSession, subtask: SubTask, payload: SubTaskUpdate) -> SubTask:
    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        setattr(subtask, key, value)
    await session.commit()
    await session.refresh(subtask)
    return subtask


async def toggle_task_completion(session: AsyncSession, task: Task) -> Task:
    task.completed = not task.completed
    task.completed_at = datetime.now(timezone.utc) if task.completed else None
    await session.commit()
    return await get_task_or_none(session, task.id, with_subtasks=True)


async def toggle_subtask_completion(session: AsyncSession, subtask: SubTask) -> SubTask:
    subtask.completed = not subtask.completed
    await session.commit()
    await session.refresh(subtask)
    return subtask


async def dashboard_stats(session: AsyncSession) -> dict[str, float | int]:
    total_q = await session.execute(select(func.count(Task.id)))
    completed_q = await session.execute(select(func.count(Task.id)).where(Task.completed.is_(True)))
    categories_q = await session.execute(select(func.count(Category.id)))

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


async def category_completion_stats(session: AsyncSession, days: int = 30) -> list[dict[str, str | int | float]]:
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
        .outerjoin(
            Task,
            and_(
                Task.category_id == Category.id,
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
    return user


async def login(session: AsyncSession, payload: LoginRequest) -> User:
    user_result = await session.execute(select(User).where(User.email == payload.email.lower()))
    user = user_result.scalar_one_or_none()
    if not user or not verify_password(payload.password, user.password_hash):
        raise ValueError("Invalid email or password")
    user.auth_token = generate_token()
    await session.commit()
    await session.refresh(user)
    return user
