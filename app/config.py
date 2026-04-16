from __future__ import annotations

import os


class Settings:
    app_name: str = os.getenv("APP_NAME", "Productvity Backend")
    app_version: str = "1.0.0"
    debug: bool = os.getenv("DEBUG", "false").lower() == "true"
    database_url: str = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./productvity.db")
    allowed_origins: list[str] = [
        origin.strip()
        for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
        if origin.strip()
    ]
    default_page_size: int = int(os.getenv("DEFAULT_PAGE_SIZE", "50"))
    max_page_size: int = int(os.getenv("MAX_PAGE_SIZE", "200"))


settings = Settings()
