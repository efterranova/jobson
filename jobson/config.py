from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = ROOT_DIR / ".env"
load_dotenv(ENV_PATH)


@dataclass(frozen=True)
class Settings:
    root_dir: Path
    data_dir: Path
    sessions_dir: Path
    logs_dir: Path
    storage_state_path: Path
    sqlite_path: Path
    supabase_url: str
    supabase_key: str
    supabase_table: str
    app_role: str
    web_host: str
    web_port: int


def load_settings() -> Settings:
    data_dir = ROOT_DIR / "data"
    sessions_dir = ROOT_DIR / "sessions"
    logs_dir = ROOT_DIR / "logs"

    data_dir.mkdir(parents=True, exist_ok=True)
    sessions_dir.mkdir(parents=True, exist_ok=True)
    logs_dir.mkdir(parents=True, exist_ok=True)

    sqlite_env = os.getenv("SQLITE_PATH", "").strip()
    sqlite_path = Path(sqlite_env) if sqlite_env else data_dir / "jobson.db"

    return Settings(
        root_dir=ROOT_DIR,
        data_dir=data_dir,
        sessions_dir=sessions_dir,
        logs_dir=logs_dir,
        storage_state_path=sessions_dir / "storage_state.json",
        sqlite_path=sqlite_path,
        supabase_url=os.getenv("SUPABASE_URL", "").strip(),
        supabase_key=os.getenv("SUPABASE_KEY", "").strip(),
        supabase_table=os.getenv("SUPABASE_TABLE", "linkedin_results").strip() or "linkedin_results",
        app_role=os.getenv("APP_ROLE", "full").strip().lower() or "full",
        web_host=os.getenv("WEB_HOST", "127.0.0.1").strip() or "127.0.0.1",
        web_port=int(os.getenv("WEB_PORT", "5050")),
    )
