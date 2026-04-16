from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..schemas import CategoryCreate, CategoryOut, CategoryUpdate
from ..services import create_category, delete_category, get_category, list_categories, update_category

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=list[CategoryOut])
async def get_categories(session: AsyncSession = Depends(get_session)):
    return await list_categories(session)


@router.post("", response_model=CategoryOut, status_code=status.HTTP_201_CREATED)
async def post_category(payload: CategoryCreate, session: AsyncSession = Depends(get_session)):
    try:
        return await create_category(session, payload)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.patch("/{category_id}", response_model=CategoryOut)
async def patch_category(category_id: str, payload: CategoryUpdate, session: AsyncSession = Depends(get_session)):
    category = await get_category(session, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    return await update_category(session, category, payload)


@router.delete("/{category_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_category(category_id: str, session: AsyncSession = Depends(get_session)):
    category = await get_category(session, category_id)
    if not category:
        raise HTTPException(status_code=404, detail="Category not found")
    await delete_category(session, category)
