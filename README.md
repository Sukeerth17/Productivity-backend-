# Productvity FastAPI Backend

High-performance FastAPI backend for categories, tasks, and subtasks.

## Performance defaults

- Async FastAPI + async SQLAlchemy (`aiosqlite` by default).
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
