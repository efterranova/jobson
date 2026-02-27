from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from jobson.models import normalize_record
from jobson.storage.base import BaseRepository


class SQLiteRepository(BaseRepository):
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @property
    def backend_name(self) -> str:
        return "sqlite"

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS linkedin_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_type TEXT NOT NULL,
                    source_id TEXT,
                    title TEXT,
                    company TEXT,
                    author TEXT,
                    summary TEXT,
                    content TEXT,
                    seniority TEXT,
                    apply_type TEXT,
                    url TEXT,
                    keyword TEXT NOT NULL,
                    search_mode TEXT NOT NULL,
                    scraped_at TEXT NOT NULL,
                    dedupe_key TEXT NOT NULL UNIQUE
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_linkedin_results_scraped_at ON linkedin_results(scraped_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_linkedin_results_source_type ON linkedin_results(source_type)"
            )

    def upsert_results(self, records: list[dict[str, Any]], keyword: str, search_mode: str) -> dict[str, int]:
        normalized = [normalize_record(record, keyword, search_mode) for record in records]
        unique_records = {item["dedupe_key"]: item for item in normalized}

        if not unique_records:
            return {"received": 0, "inserted": 0, "updated": 0}

        keys = list(unique_records.keys())
        existing_keys: set[str] = set()

        with self._connect() as conn:
            placeholders = ",".join(["?"] * len(keys))
            query = f"SELECT dedupe_key FROM linkedin_results WHERE dedupe_key IN ({placeholders})"
            for row in conn.execute(query, keys).fetchall():
                existing_keys.add(row["dedupe_key"])

            for item in unique_records.values():
                conn.execute(
                    """
                    INSERT INTO linkedin_results (
                        source_type, source_id, title, company, author, summary, content,
                        seniority, apply_type, url, keyword, search_mode, scraped_at, dedupe_key
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(dedupe_key) DO UPDATE SET
                        source_type=excluded.source_type,
                        source_id=excluded.source_id,
                        title=excluded.title,
                        company=excluded.company,
                        author=excluded.author,
                        summary=excluded.summary,
                        content=excluded.content,
                        seniority=excluded.seniority,
                        apply_type=excluded.apply_type,
                        url=excluded.url,
                        keyword=excluded.keyword,
                        search_mode=excluded.search_mode,
                        scraped_at=excluded.scraped_at
                    """,
                    (
                        item["source_type"],
                        item["source_id"],
                        item["title"],
                        item["company"],
                        item["author"],
                        item["summary"],
                        item["content"],
                        item["seniority"],
                        item["apply_type"],
                        item["url"],
                        item["keyword"],
                        item["search_mode"],
                        item["scraped_at"],
                        item["dedupe_key"],
                    ),
                )

        inserted = len(unique_records) - len(existing_keys)
        updated = len(unique_records) - inserted
        return {"received": len(records), "inserted": inserted, "updated": updated}

    def list_results(
        self,
        limit: int = 200,
        source_type: str | None = None,
        search_text: str | None = None,
    ) -> list[dict[str, Any]]:
        clauses = []
        params: list[Any] = []

        if source_type:
            clauses.append("source_type = ?")
            params.append(source_type)

        if search_text:
            needle = f"%{search_text.strip()}%"
            clauses.append(
                "(" + " OR ".join(
                    [
                        "COALESCE(title, '') LIKE ?",
                        "COALESCE(company, '') LIKE ?",
                        "COALESCE(author, '') LIKE ?",
                        "COALESCE(summary, '') LIKE ?",
                        "COALESCE(content, '') LIKE ?",
                    ]
                ) + ")"
            )
            params.extend([needle] * 5)

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"SELECT * FROM linkedin_results {where_sql} ORDER BY scraped_at DESC LIMIT ?"
        params.append(max(1, min(limit, 1000)))

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]
