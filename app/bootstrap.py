from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from .models import Base


def _has_column(sync_conn, table_name: str, column_name: str) -> bool:
    inspector = inspect(sync_conn)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


async def _ensure_user_id_column(conn: AsyncConnection, table_name: str) -> None:
    has_user_id = await conn.run_sync(lambda sync_conn: _has_column(sync_conn, table_name, "user_id"))
    if has_user_id:
        return
    await conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN user_id VARCHAR(36)"))


async def _backfill_existing_owner(conn: AsyncConnection) -> None:
    user_ids = list((await conn.execute(text("SELECT id FROM users ORDER BY created_at ASC"))).scalars().all())
    if len(user_ids) != 1:
        return

    user_id = user_ids[0]
    await conn.execute(text("UPDATE categories SET user_id = :user_id WHERE user_id IS NULL"), {"user_id": user_id})
    await conn.execute(text("UPDATE tasks SET user_id = :user_id WHERE user_id IS NULL"), {"user_id": user_id})


async def _ensure_indexes(conn: AsyncConnection) -> None:
    statements = [
        "CREATE INDEX IF NOT EXISTS ix_categories_user_id ON categories (user_id)",
        "CREATE UNIQUE INDEX IF NOT EXISTS ix_categories_user_name ON categories (user_id, name)",
        "CREATE INDEX IF NOT EXISTS ix_tasks_user_id ON tasks (user_id)",
        "CREATE INDEX IF NOT EXISTS ix_tasks_user_category_completed_created ON tasks (user_id, category_id, completed, created_at)",
    ]
    for statement in statements:
        await conn.execute(text(statement))


async def _ensure_extra_columns(conn: AsyncConnection) -> None:
    # Ensure task_completions has category_id and task_id can be null
    has_cat_id = await conn.run_sync(lambda sync_conn: _has_column(sync_conn, "task_completions", "category_id"))
    if not has_cat_id:
        await conn.execute(text("ALTER TABLE task_completions ADD COLUMN category_id VARCHAR(36)"))
        await conn.execute(text("ALTER TABLE task_completions ADD CONSTRAINT fk_task_completions_category FOREIGN KEY (category_id) REFERENCES categories(id) ON DELETE SET NULL"))

    # Ensure task_completions task_id is nullable (it was previously non-nullable in some versions)
    await conn.execute(text("ALTER TABLE task_completions ALTER COLUMN task_id DROP NOT NULL"))
    
    # Ensure task_completions task_id has SET NULL instead of CASCADE
    try:
        await conn.execute(text("ALTER TABLE task_completions DROP CONSTRAINT task_completions_task_id_fkey"))
    except Exception:
        pass
    try:
        await conn.execute(text("ALTER TABLE task_completions ADD CONSTRAINT task_completions_task_id_fkey FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE SET NULL"))
    except Exception:
        pass

    # Ensure tasks table has is_deleted and deleted_at
    has_is_deleted = await conn.run_sync(lambda sync_conn: _has_column(sync_conn, "tasks", "is_deleted"))
    if not has_is_deleted:
        await conn.execute(text("ALTER TABLE tasks ADD COLUMN is_deleted BOOLEAN DEFAULT FALSE"))
        await conn.execute(text("ALTER TABLE tasks ADD COLUMN deleted_at TIMESTAMP WITH TIME ZONE"))
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_tasks_is_deleted ON tasks (is_deleted)"))


async def prepare_database(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_user_id_column(conn, "categories")
        await _ensure_user_id_column(conn, "tasks")
        await _ensure_extra_columns(conn)
        await _backfill_existing_owner(conn)
        await _ensure_indexes(conn)
