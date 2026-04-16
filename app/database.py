from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import settings


def _normalize_database_url(database_url: str) -> str:
    # Supabase/Render often expose postgres:// or postgresql:// DSNs.
    # SQLAlchemy async engine needs postgresql+asyncpg:// for async driver support.
    if database_url.startswith("postgresql+asyncpg://"):
        return database_url
    if database_url.startswith("postgres://"):
        return database_url.replace("postgres://", "postgresql+asyncpg://", 1)
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return database_url


database_url = _normalize_database_url(settings.database_url)
is_sqlite = database_url.startswith("sqlite")

connect_args: dict[str, object] = {}
if is_sqlite:
    connect_args = {"check_same_thread": False}

engine = create_async_engine(
    database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    connect_args=connect_args,
)


@event.listens_for(engine.sync_engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, _connection_record):
    if not is_sqlite:
        return
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA synchronous=NORMAL;")
    cursor.execute("PRAGMA temp_store=MEMORY;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.close()


AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def check_db_health() -> bool:
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
