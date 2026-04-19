from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator


Priority = Literal["low", "medium", "high"]


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    color: str = Field(default="#3B82F6", min_length=4, max_length=12)
    icon: str = Field(default="star", min_length=1, max_length=64)


class CategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    color: str | None = Field(default=None, min_length=4, max_length=12)
    icon: str | None = Field(default=None, min_length=1, max_length=64)


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    color: str
    icon: str
    created_at: datetime


class SubTaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    completed: bool = False


class SubTaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    completed: bool | None = None
    position: int | None = Field(default=None, ge=0)


class SubTaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    completed: bool
    position: int
    task_id: str


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=240)
    category_id: str
    notes: str | None = None
    completed: bool = False
    is_habit: bool = False
    priority: Priority | None = None
    due_time: str | None = Field(default=None, max_length=12)
    subtasks: list[SubTaskCreate] = Field(default_factory=list)

    @field_validator("due_time")
    @classmethod
    def validate_due_time(cls, value: str | None) -> str | None:
        if value is None:
            return value
        if len(value.split(":")) != 2:
            raise ValueError("due_time must be HH:MM")
        return value


class TaskUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    category_id: str | None = None
    notes: str | None = None
    completed: bool | None = None
    is_habit: bool | None = None
    priority: Priority | None = None
    due_time: str | None = Field(default=None, max_length=12)


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    category_id: str
    notes: str | None
    completed: bool
    is_habit: bool
    priority: Priority | None
    due_time: str | None
    created_at: datetime
    completed_at: datetime | None
    updated_at: datetime
    subtasks: list[SubTaskOut]


class PaginatedTasks(BaseModel):
    items: list[TaskOut]
    total: int
    limit: int
    offset: int


class DashboardStats(BaseModel):
    total_tasks: int
    completed_tasks: int
    active_tasks: int
    categories: int
    completion_rate: float


class CategoryCompletionStats(BaseModel):
    category_id: str
    category_name: str
    color: str
    total_tasks: int
    completed_tasks: int
    completion_rate: float


class SignUpRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserOut(BaseModel):
    id: str
    name: str
    email: EmailStr
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class AuthResponse(BaseModel):
    token: str
    user: UserOut
