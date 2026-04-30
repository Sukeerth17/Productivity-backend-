from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    password_hash: Mapped[str] = mapped_column(String(512))
    auth_token: Mapped[str | None] = mapped_column(String(128), unique=True, index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

    categories: Mapped[list[Category]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    tasks: Mapped[list[Task]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    productivity_stats: Mapped[ProductivityStats | None] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
    )
    task_completions: Mapped[list[TaskCompletion]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class Category(Base):
    __tablename__ = "categories"
    __table_args__ = (
        Index("ix_categories_user_name", "user_id", "name", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    name: Mapped[str] = mapped_column(String(120), index=True)
    color: Mapped[str] = mapped_column(String(12), default="#3B82F6")
    icon: Mapped[str] = mapped_column(String(64), default="star")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)

    user: Mapped[User | None] = relationship(back_populates="categories")
    tasks: Mapped[list[Task]] = relationship(
        back_populates="category",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        Index("ix_tasks_user_category_completed_created", "user_id", "category_id", "completed", "created_at"),
        Index("ix_tasks_due_time", "due_time"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    title: Mapped[str] = mapped_column(String(240))
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_habit: Mapped[bool] = mapped_column(Boolean, default=False)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    due_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    category_id: Mapped[str] = mapped_column(
        ForeignKey("categories.id", ondelete="CASCADE"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=now_utc,
        onupdate=now_utc,
    )
    
    # Soft deletion for history preservation
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User | None] = relationship(back_populates="tasks")
    category: Mapped[Category] = relationship(back_populates="tasks", lazy="joined")
    subtasks: Mapped[list[SubTask]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="SubTask.position",
        lazy="selectin",
    )
    completions: Mapped[list[TaskCompletion]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class SubTask(Base):
    __tablename__ = "subtasks"
    __table_args__ = (
        Index("ix_subtasks_task_position", "task_id", "position"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    title: Mapped[str] = mapped_column(String(240))
    completed: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)

    task_id: Mapped[str] = mapped_column(
        ForeignKey("tasks.id", ondelete="CASCADE"),
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc)

    task: Mapped[Task] = relationship(back_populates="subtasks")


class ProductivityStats(Base):
    __tablename__ = "productivity_stats"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    
    # All-time stats
    alltime_total_tasks: Mapped[int] = mapped_column(Integer, default=0)
    alltime_completed_tasks: Mapped[int] = mapped_column(Integer, default=0)
    alltime_completion_rate: Mapped[float] = mapped_column(default=0.0)
    
    # Month stats
    month_total_tasks: Mapped[int] = mapped_column(Integer, default=0)
    month_completed_tasks: Mapped[int] = mapped_column(Integer, default=0)
    month_completion_rate: Mapped[float] = mapped_column(default=0.0)
    
    # Week stats
    week_total_tasks: Mapped[int] = mapped_column(Integer, default=0)
    week_completed_tasks: Mapped[int] = mapped_column(Integer, default=0)
    week_completion_rate: Mapped[float] = mapped_column(default=0.0)
    
    # Day stats
    day_total_tasks: Mapped[int] = mapped_column(Integer, default=0)
    day_completed_tasks: Mapped[int] = mapped_column(Integer, default=0)
    day_completion_rate: Mapped[float] = mapped_column(default=0.0)
    
    # Category breakdown for all-time
    category_breakdown: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON string
    
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now_utc, onupdate=now_utc)
    
    user: Mapped[User] = relationship(back_populates="productivity_stats")


class TaskCompletion(Base):
    """Track each task completion for accurate statistics across habit resets."""
    __tablename__ = "task_completions"
    __table_args__ = (
        Index("ix_task_completions_user_completed_at", "user_id", "completed_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    task_id: Mapped[str | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    category_id: Mapped[str | None] = mapped_column(
        ForeignKey("categories.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )
    task_title: Mapped[str] = mapped_column(String(240))  # Store title for history after task deletion
    is_habit: Mapped[bool] = mapped_column(Boolean, default=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    
    user: Mapped[User] = relationship(back_populates="task_completions")
    task: Mapped[Task] = relationship(back_populates="completions")
    category: Mapped[Category | None] = relationship()


class DailySnapshot(Base):
    """Store daily stats so that alltime totals survive task deletion."""
    __tablename__ = "daily_snapshots"
    __table_args__ = (
        Index("ix_daily_snapshots_user_date", "user_id", "snapshot_date", unique=True),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    snapshot_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    total_available: Mapped[int] = mapped_column(Integer, default=0)
    total_completed: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped[User] = relationship()
