from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .bootstrap import prepare_database
from .cache import connect_redis, disconnect_redis, get_redis_info
from .config import settings
from .database import check_db_health, engine
from .routers import auth, categories, stats, tasks
from .scheduler import start_scheduler


import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info(f"Allowed Origins: {settings.allowed_origins}")
    await prepare_database(engine)

    # Connect to Redis cache (graceful — won't crash if unavailable)
    redis_ok = await connect_redis()
    logger.info(f"[CACHE] Redis available: {redis_ok}")

    # Start background scheduler
    scheduler = start_scheduler()

    # Run a catch-up reset on startup in case the server was down at midnight
    try:
        from .scheduler import reset_habit_tasks
        logger.info("Running startup habit reset catch-up...")
        await reset_habit_tasks()
    except Exception as e:
        logger.error(f"Failed to run startup habit reset: {e}")

    yield

    # Shutdown Redis and scheduler
    await disconnect_redis()
    scheduler.shutdown()


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    debug=settings.debug,
    lifespan=lifespan,
)

# More permissive CORS for troubleshooting
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)

from fastapi import Request
@app.middleware("http")
async def db_session_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        logger.info(f"Preflight request for {request.url.path} from {request.headers.get('origin')}")
    response = await call_next(request)
    return response


@app.get("/health")
async def health_check():
    db_ok = await check_db_health()
    redis_info = await get_redis_info()
    return {
        "status": "ok" if db_ok else "degraded",
        "database": db_ok,
        "redis": redis_info,
    }


app.include_router(categories.router, prefix="/api/v1")
app.include_router(tasks.router, prefix="/api/v1")
app.include_router(stats.router, prefix="/api/v1")
app.include_router(auth.router, prefix="/api/v1")

# Benchmark router (speed testing)
from .routers import benchmark as benchmark_router
app.include_router(benchmark_router.router, prefix="/api/v1")

