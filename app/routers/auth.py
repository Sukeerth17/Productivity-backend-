from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..dependencies import get_current_user
from ..models import User
from ..schemas import AuthResponse, LoginRequest, SignUpRequest, UserOut, UserUpdate
from ..services import login, sign_up, update_user_profile

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=AuthResponse, status_code=status.HTTP_201_CREATED)
async def post_signup(payload: SignUpRequest, session: AsyncSession = Depends(get_session)):
    try:
        user = await sign_up(session, payload)
        return {"token": user.auth_token, "user": user}
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/login", response_model=AuthResponse)
async def post_login(payload: LoginRequest, session: AsyncSession = Depends(get_session)):
    try:
        user = await login(session, payload)
        return {"token": user.auth_token, "user": user}
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


@router.get("/me", response_model=UserOut)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.patch("/me", response_model=UserOut)
async def patch_me(
    payload: UserUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
):
    return await update_user_profile(session, current_user, payload)
