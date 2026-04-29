from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .bootstrap import prepare_database
from .config import settings
from .database import check_db_health, engine
from .routers import auth, categories, stats, tasks
from .scheduler import start_scheduler


@asynccontextmanager
async def lifespan(_app: FastAPI):
    await prepare_database(engine)
    # Start background scheduler
    scheduler = start_scheduler()
    yield
    # Shutdown scheduler
    scheduler.shutdown()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health_check():
    db_ok = await check_db_health()
    return {"status": "ok" if db_ok else "degraded", "database": db_ok}


app.include_router(categories.router, prefix="/api/v1")
app.include_router(tasks.router, prefix="/api/v1")
app.include_router(stats.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")
