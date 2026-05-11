# Momentum Builder Backend

A high-performance, asynchronous FastAPI backend powering the Momentum Builder productivity suite.

## 🚀 Key Features

- **Asynchronous Architecture**: Built with FastAPI and `sqlalchemy[asyncio]` for maximum throughput.
- **Smart Task Management**: Support for categories, tasks, and nested subtasks with priority levels.
- **Habit Scheduling**: Advanced logic for habit-based tasks with configurable active days (e.g., Mon/Wed/Fri).
- **Insightful Analytics**: Dashboard endpoints providing completion rates, productivity trends, and historical statistics.
- **Robust Authentication**: Secure JWT-based user authentication and registration.
- **Optimized Persistence**: SQLite WAL mode for local development and PostgreSQL support for production (Supabase).
- **Eager Loading**: Optimized database queries using `selectinload` to eliminate N+1 issues.

## 🛠 Tech Stack

- **Framework**: FastAPI
- **Database**: SQLAlchemy 2.0 (Async), Pydantic v2
- **Migrations**: Alembic
- **Environment**: Python 3.10+

## 🚦 Quick Start

### 1. Clone & Setup Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

### 2. Run Database Migrations
```bash
alembic upgrade head
```

### 3. Start the Server
```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Access the interactive API documentation at: [http://localhost:8000/docs](http://localhost:8000/docs)

## 🌐 Deployment (Render + Supabase)

1. **Database**: Create a Supabase PostgreSQL instance.
2. **Web Service**: Create a new Web Service on Render from this repository.
3. **Configuration**:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
   - **Environment Variables**:
     - `DATABASE_URL`: Your Supabase connection string.
     - `ALLOWED_ORIGINS`: Your frontend URL (comma-separated).
     - `DEBUG`: `false`

## 📊 API Overview

- `POST /api/v1/auth/signup` - User registration
- `POST /api/v1/auth/login` - User authentication
- `GET /api/v1/stats/dashboard` - Productivity analytics
- `GET/POST /api/v1/tasks` - Task management
- `POST /api/v1/tasks/{id}/toggle` - Mark task as complete/incomplete
- `GET/POST /api/v1/categories` - Category organization

