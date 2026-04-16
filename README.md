# Productvity FastAPI Backend

High-performance FastAPI backend for categories, tasks, and subtasks.

## Performance defaults

- Async FastAPI + async SQLAlchemy (`aiosqlite` for local dev, `asyncpg` for Postgres/Supabase).
- SQLite WAL mode + tuned PRAGMAs for better read/write concurrency.
- Indexed columns for common filters (`category_id`, `completed`, `priority`, `created_at`).
- Pagination on task listing endpoints.
- `selectinload` eager loading for subtasks to avoid N+1 query overhead.

## Quick start

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Open API docs: `http://localhost:8000/docs`

## Deploy (Render + Supabase)

1. Create a Supabase project and copy its connection string.
2. In Render, create a new Web Service from this repository (`backend`).
3. Build command: `pip install -r requirements.txt`
4. Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
5. Set environment variables in Render:
   - `DATABASE_URL` = your Supabase Postgres URL (`postgresql://...?...sslmode=require`)
   - `ALLOWED_ORIGINS` = your Vercel frontend URL (comma-separated if multiple)
   - `DEBUG=false`
   - Optional: `APP_NAME`, `DEFAULT_PAGE_SIZE`, `MAX_PAGE_SIZE`

You can also use `render.yaml` in this folder for Blueprint deploy.

## API overview

- `GET /health`
- `GET/POST /api/v1/categories`
- `PATCH/DELETE /api/v1/categories/{category_id}`
- `GET/POST /api/v1/tasks`
- `GET/PATCH/DELETE /api/v1/tasks/{task_id}`
- `POST /api/v1/tasks/{task_id}/toggle`
- `POST /api/v1/tasks/{task_id}/subtasks`
- `PATCH /api/v1/tasks/{task_id}/subtasks/{subtask_id}`
- `POST /api/v1/tasks/{task_id}/subtasks/{subtask_id}/toggle`
- `GET /api/v1/stats/dashboard`
- `POST /api/v1/auth/signup`
- `POST /api/v1/auth/login`

## Example request

```bash
curl -X POST http://localhost:8000/api/v1/categories \
  -H "Content-Type: application/json" \
  -d '{"name":"Work","color":"#22C55E"}'
```
