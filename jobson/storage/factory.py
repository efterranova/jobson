from __future__ import annotations

from jobson.config import Settings
from jobson.storage.base import BaseRepository
from jobson.storage.sqlite_repository import SQLiteRepository
from jobson.storage.supabase_repository import SupabaseRepository


def build_repository(settings: Settings) -> BaseRepository:
    if settings.supabase_url and settings.supabase_key:
        return SupabaseRepository(
            url=settings.supabase_url,
            key=settings.supabase_key,
            table=settings.supabase_table,
        )
    return SQLiteRepository(settings.sqlite_path)
