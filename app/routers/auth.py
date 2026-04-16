from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_session
from ..schemas import AuthResponse, LoginRequest, SignUpRequest
from ..services import login, sign_up

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
