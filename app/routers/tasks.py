from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_session
from ..dependencies import get_current_user
from ..models import User
from ..schemas import (
    PaginatedTasks,
    SubTaskCreate,
    SubTaskOut,
    SubTaskUpdate,
    TaskCreate,
    TaskOut,
    TaskUpdate,
)
from ..services import (
    add_subtask,
    create_task,
    delete_task,
    get_subtask,
    get_task_or_none,
    list_tasks,
    toggle_subtask_completion,
    toggle_task_completion,
    update_subtask,
    update_task,
)

router = APIRouter(prefix="/tasks", tags=["tasks"])


@router.get("", response_model=PaginatedTasks)
async def get_tasks(
    category_id: str | None = None,
    completed: bool | None = None,
    priority: str | None = Query(default=None, pattern="^(low|medium|high)$"),
    limit: int = Query(default=settings.default_page_size, ge=1, le=settings.max_page_size),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    tasks, total = await list_tasks(session, current_user, category_id, completed, priority, limit, offset)
    return {"items": tasks, "total": total, "limit": limit, "offset": offset}


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED)
async def post_task(
    payload: TaskCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    try:
        return await create_task(session, current_user, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/{task_id}", response_model=TaskOut)
async def get_task(
    task_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    task = await get_task_or_none(session, current_user, task_id, with_subtasks=True)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return task


@router.patch("/{task_id}", response_model=TaskOut)
async def patch_task(
    task_id: str,
    payload: TaskUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    task = await get_task_or_none(session, current_user, task_id, with_subtasks=False)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    try:
        return await update_task(session, current_user, task, payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_task(
    task_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    task = await get_task_or_none(session, current_user, task_id, with_subtasks=False)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    await delete_task(session, task)


@router.post("/{task_id}/toggle", response_model=TaskOut)
async def toggle_task(
    task_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    task = await get_task_or_none(session, current_user, task_id, with_subtasks=False)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return await toggle_task_completion(session, current_user, task)


@router.post("/{task_id}/subtasks", response_model=TaskOut)
async def post_subtask(
    task_id: str,
    payload: SubTaskCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    task = await get_task_or_none(session, current_user, task_id, with_subtasks=False)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return await add_subtask(session, current_user, task, payload)


@router.patch("/{task_id}/subtasks/{subtask_id}", response_model=SubTaskOut)
async def patch_subtask(
    task_id: str,
    subtask_id: str,
    payload: SubTaskUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    subtask = await get_subtask(session, current_user, task_id, subtask_id)
    if not subtask:
        raise HTTPException(status_code=404, detail="Subtask not found")
    return await update_subtask(session, subtask, payload)


@router.post("/{task_id}/subtasks/{subtask_id}/toggle", response_model=SubTaskOut)
async def toggle_subtask(
    task_id: str,
    subtask_id: str,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    subtask = await get_subtask(session, current_user, task_id, subtask_id)
    if not subtask:
        raise HTTPException(status_code=404, detail="Subtask not found")
    return await toggle_subtask_completion(session, subtask)
