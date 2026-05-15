from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jobson.models import normalize_record
from jobson.storage.base import BaseRepository

VALID_WP_STATUSES = {"pending", "synced", "skipped", "failed", "discarded"}


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

    @staticmethod
    def _bool_to_int(value: Any) -> int | None:
        if value is None:
            return None
        return 1 if bool(value) else 0

    @staticmethod
    def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
        data = dict(row)
        for json_field in ("extracted_emails", "extracted_urls"):
            raw = data.get(json_field)
            if isinstance(raw, str):
                try:
                    data[json_field] = json.loads(raw)
                except json.JSONDecodeError:
                    data[json_field] = []
        if data.get("is_remote") is not None:
            data["is_remote"] = bool(data["is_remote"])
        return data

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
                    dedupe_key TEXT NOT NULL UNIQUE,
                    user_status TEXT,
                    status_updated_at TEXT,
                    location_text TEXT,
                    city TEXT,
                    state TEXT,
                    country TEXT,
                    work_mode TEXT,
                    is_remote INTEGER,
                    job_type_guess TEXT,
                    apply_email TEXT,
                    apply_url_external TEXT,
                    company_website TEXT,
                    extracted_emails TEXT NOT NULL DEFAULT '[]',
                    extracted_urls TEXT NOT NULL DEFAULT '[]',
                    contact_status TEXT NOT NULL DEFAULT 'sin_contacto',
                    wp_status TEXT NOT NULL DEFAULT 'skipped',
                    wp_post_id INTEGER,
                    wp_synced_at TEXT,
                    wp_last_error TEXT,
                    first_seen_at TEXT,
                    last_seen_at TEXT,
                    ai_normalized_json TEXT,
                    ai_prompt_version TEXT,
                    ai_normalized_at TEXT,
                    review_status TEXT DEFAULT 'pending',
                    reviewed_by TEXT,
                    reviewed_at TEXT,
                    published_post_id INTEGER,
                    published_wp_url TEXT,
                    published_at TEXT
                )
                """
            )
            existing = {row["name"] for row in conn.execute("PRAGMA table_info(linkedin_results)")}
            for col, ddl in [
                ("user_status",         "TEXT"),
                ("status_updated_at",   "TEXT"),
                ("location_text",       "TEXT"),
                ("city",                "TEXT"),
                ("state",               "TEXT"),
                ("country",             "TEXT"),
                ("work_mode",           "TEXT"),
                ("is_remote",           "INTEGER"),
                ("job_type_guess",      "TEXT"),
                ("apply_email",         "TEXT"),
                ("apply_url_external",  "TEXT"),
                ("company_website",     "TEXT"),
                ("extracted_emails",    "TEXT NOT NULL DEFAULT '[]'"),
                ("extracted_urls",      "TEXT NOT NULL DEFAULT '[]'"),
                ("contact_status",      "TEXT NOT NULL DEFAULT 'sin_contacto'"),
                ("wp_status",           "TEXT NOT NULL DEFAULT 'skipped'"),
                ("wp_post_id",          "INTEGER"),
                ("wp_synced_at",        "TEXT"),
                ("wp_last_error",       "TEXT"),
                ("first_seen_at",       "TEXT"),
                ("last_seen_at",        "TEXT"),
                ("ai_normalized_json",  "TEXT"),
                ("ai_prompt_version",   "TEXT"),
                ("ai_normalized_at",    "TEXT"),
                ("review_status",       "TEXT DEFAULT 'pending'"),
                ("reviewed_by",         "TEXT"),
                ("reviewed_at",         "TEXT"),
                ("published_post_id",   "INTEGER"),
                ("published_wp_url",    "TEXT"),
                ("published_at",        "TEXT"),
            ]:
                if col not in existing:
                    conn.execute(f"ALTER TABLE linkedin_results ADD COLUMN {col} {ddl}")

            for sql in (
                "CREATE INDEX IF NOT EXISTS idx_lr_scraped_at ON linkedin_results(scraped_at DESC)",
                "CREATE INDEX IF NOT EXISTS idx_lr_source_type ON linkedin_results(source_type)",
                "CREATE INDEX IF NOT EXISTS idx_lr_user_status ON linkedin_results(user_status)",
                "CREATE INDEX IF NOT EXISTS idx_lr_review_status ON linkedin_results(review_status)",
                "CREATE INDEX IF NOT EXISTS idx_lr_wp_status ON linkedin_results(wp_status)",
                "CREATE INDEX IF NOT EXISTS idx_lr_contact_status ON linkedin_results(contact_status)",
                "CREATE INDEX IF NOT EXISTS idx_lr_first_seen ON linkedin_results(first_seen_at DESC)",
                "CREATE INDEX IF NOT EXISTS idx_lr_last_seen ON linkedin_results(last_seen_at DESC)",
            ):
                conn.execute(sql)

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reviewers (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    name TEXT,
                    role TEXT NOT NULL DEFAULT 'reviewer',
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    last_login_at TEXT
                )
                """
            )

    # ------------------------------------------------------------------ upsert

    def upsert_results(self, records: list[dict[str, Any]], keyword: str, search_mode: str) -> dict[str, int]:
        normalized = [normalize_record(record, keyword, search_mode) for record in records]
        unique_records = {item["dedupe_key"]: item for item in normalized}
        if not unique_records:
            return {"received": 0, "inserted": 0, "updated": 0}

        keys = list(unique_records.keys())
        now = datetime.now(UTC).isoformat()

        with self._connect() as conn:
            placeholders = ",".join(["?"] * len(keys))
            existing_keys: set[str] = set()
            for row in conn.execute(
                f"SELECT dedupe_key FROM linkedin_results WHERE dedupe_key IN ({placeholders})", keys
            ):
                existing_keys.add(row["dedupe_key"])

            for item in unique_records.values():
                conn.execute(
                    """
                    INSERT INTO linkedin_results (
                        source_type, source_id, title, company, author, summary, content,
                        seniority, apply_type, url, keyword, search_mode, scraped_at, dedupe_key,
                        location_text, city, state, country, work_mode, is_remote, job_type_guess,
                        apply_email, apply_url_external, company_website,
                        extracted_emails, extracted_urls, contact_status,
                        wp_status, wp_last_error,
                        first_seen_at, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        scraped_at=excluded.scraped_at,
                        location_text=excluded.location_text,
                        city=excluded.city,
                        state=excluded.state,
                        country=excluded.country,
                        work_mode=excluded.work_mode,
                        is_remote=excluded.is_remote,
                        job_type_guess=excluded.job_type_guess,
                        apply_email=excluded.apply_email,
                        apply_url_external=excluded.apply_url_external,
                        company_website=excluded.company_website,
                        extracted_emails=excluded.extracted_emails,
                        extracted_urls=excluded.extracted_urls,
                        contact_status=excluded.contact_status,
                        wp_status=CASE
                            WHEN linkedin_results.wp_status IN ('synced','failed','discarded')
                                THEN linkedin_results.wp_status
                            ELSE excluded.wp_status
                        END,
                        wp_last_error=excluded.wp_last_error,
                        last_seen_at=excluded.last_seen_at
                    """,
                    (
                        item["source_type"], item["source_id"], item["title"], item["company"],
                        item["author"], item["summary"], item["content"], item["seniority"],
                        item["apply_type"], item["url"], item["keyword"], item["search_mode"],
                        item["scraped_at"], item["dedupe_key"],
                        item.get("location_text"), item.get("city"), item.get("state"),
                        item.get("country"), item.get("work_mode"),
                        self._bool_to_int(item.get("is_remote")),
                        item.get("job_type_guess"),
                        item.get("apply_email"), item.get("apply_url_external"), item.get("company_website"),
                        json.dumps(item.get("extracted_emails") or [], ensure_ascii=False),
                        json.dumps(item.get("extracted_urls") or [], ensure_ascii=False),
                        item.get("contact_status", "sin_contacto"),
                        item.get("wp_status", "skipped"),
                        item.get("wp_last_error"),
                        now,  # first_seen_at — solo se respeta en INSERT (no se actualiza)
                        now,  # last_seen_at — siempre se refresca
                    ),
                )

        inserted = len(unique_records) - len(existing_keys)
        updated = len(unique_records) - inserted
        return {"received": len(records), "inserted": inserted, "updated": updated}

    # ------------------------------------------------------------------ list

    def list_results(
        self,
        limit: int = 200,
        source_type: str | None = None,
        search_text: str | None = None,
        user_status: str | None = None,
        only_followups: bool = False,
        review_status: str | None = None,
        wp_status: str | None = None,
        contact_status: str | None = None,
        only_new_today: bool = False,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []

        if source_type:
            clauses.append("source_type = ?")
            params.append(source_type)

        if search_text and search_text.strip():
            needle = f"%{search_text.strip()}%"
            clauses.append(
                "(" + " OR ".join([
                    "COALESCE(title, '') LIKE ?",
                    "COALESCE(company, '') LIKE ?",
                    "COALESCE(author, '') LIKE ?",
                    "COALESCE(summary, '') LIKE ?",
                    "COALESCE(content, '') LIKE ?",
                    "COALESCE(apply_email, '') LIKE ?",
                    "COALESCE(apply_url_external, '') LIKE ?",
                ]) + ")"
            )
            params.extend([needle] * 7)

        if user_status:
            clauses.append("user_status = ?")
            params.append(user_status)
        elif only_followups:
            clauses.append("COALESCE(user_status, '') <> ''")

        if review_status:
            clauses.append("review_status = ?")
            params.append(review_status)

        if wp_status and wp_status in VALID_WP_STATUSES:
            clauses.append("wp_status = ?")
            params.append(wp_status)

        if contact_status:
            clauses.append("contact_status = ?")
            params.append(contact_status)

        if only_new_today:
            clauses.append("date(first_seen_at) = date('now')")

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            f"SELECT * FROM linkedin_results {where_sql} "
            "ORDER BY COALESCE(first_seen_at, scraped_at) DESC LIMIT ?"
        )
        params.append(max(1, min(limit, 1000)))

        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [self._row_to_dict(row) for row in rows]

    # ------------------------------------------------------------------ updates

    def update_result_status(self, dedupe_key: str, user_status: str | None) -> dict[str, Any] | None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE linkedin_results SET user_status=?, status_updated_at=? WHERE dedupe_key=?",
                (user_status, datetime.now(UTC).isoformat(), dedupe_key),
            )
            row = conn.execute("SELECT * FROM linkedin_results WHERE dedupe_key=?", (dedupe_key,)).fetchone()
        return self._row_to_dict(row) if row else None

    def save_ai_normalization(self, dedupe_key: str, ai_json: str, prompt_version: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE linkedin_results SET ai_normalized_json=?, ai_prompt_version=?, ai_normalized_at=? WHERE dedupe_key=?",
                (ai_json, prompt_version, datetime.now(UTC).isoformat(), dedupe_key),
            )

    def set_review_status(
        self, dedupe_key: str, review_status: str, reviewer_id: str | None = None
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE linkedin_results SET review_status=?, reviewed_by=?, reviewed_at=? WHERE dedupe_key=?",
                (review_status, reviewer_id, datetime.now(UTC).isoformat(), dedupe_key),
            )
            row = conn.execute("SELECT * FROM linkedin_results WHERE dedupe_key=?", (dedupe_key,)).fetchone()
        return self._row_to_dict(row) if row else None

    def mark_published(
        self, dedupe_key: str, post_id: int, wp_url: str | None = None
    ) -> dict[str, Any] | None:
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE linkedin_results
                SET review_status='published',
                    published_post_id=?, published_wp_url=?, published_at=?
                WHERE dedupe_key=?
                """,
                (int(post_id), wp_url, datetime.now(UTC).isoformat(), dedupe_key),
            )
            row = conn.execute("SELECT * FROM linkedin_results WHERE dedupe_key=?", (dedupe_key,)).fetchone()
        return self._row_to_dict(row) if row else None

    def mark_wp_synced(self, dedupe_key: str, post_id: int, wp_url: str | None = None) -> dict[str, Any] | None:
        """Marca el lado técnico WP: pasó la elegibilidad y se publicó OK.
        Lo combinamos con mark_published (review_status=published)."""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE linkedin_results
                SET wp_status='synced',
                    wp_post_id=?,
                    wp_synced_at=?,
                    wp_last_error=NULL,
                    review_status='published',
                    published_post_id=?,
                    published_wp_url=?,
                    published_at=?
                WHERE dedupe_key=?
                """,
                (int(post_id), datetime.now(UTC).isoformat(), int(post_id), wp_url,
                 datetime.now(UTC).isoformat(), dedupe_key),
            )
            row = conn.execute("SELECT * FROM linkedin_results WHERE dedupe_key=?", (dedupe_key,)).fetchone()
        return self._row_to_dict(row) if row else None

    def mark_wp_failed(self, dedupe_key: str, error: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE linkedin_results SET wp_status='failed', wp_last_error=? WHERE dedupe_key=?",
                (error[:800], dedupe_key),
            )
            row = conn.execute("SELECT * FROM linkedin_results WHERE dedupe_key=?", (dedupe_key,)).fetchone()
        return self._row_to_dict(row) if row else None

    def update_wp_status(
        self,
        dedupe_key: str,
        wp_status: str,
        wp_post_id: int | None = None,
        wp_last_error: str | None = None,
    ) -> dict[str, Any] | None:
        if wp_status not in VALID_WP_STATUSES:
            raise ValueError(f"wp_status inválido: {wp_status}")
        synced_at = datetime.now(UTC).isoformat() if wp_status == "synced" else None
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE linkedin_results
                SET wp_status = ?,
                    wp_post_id = COALESCE(?, wp_post_id),
                    wp_synced_at = COALESCE(?, wp_synced_at),
                    wp_last_error = ?
                WHERE dedupe_key = ?
                """,
                (wp_status, wp_post_id, synced_at, wp_last_error, dedupe_key),
            )
            row = conn.execute("SELECT * FROM linkedin_results WHERE dedupe_key=?", (dedupe_key,)).fetchone()
        return self._row_to_dict(row) if row else None

    # ------------------------------------------------------------------ reviewers

    def get_reviewer_by_email(self, email: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM reviewers WHERE email=? AND active=1", (email.lower().strip(),),
            ).fetchone()
        return dict(row) if row else None

    def get_reviewer(self, reviewer_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM reviewers WHERE id=?", (reviewer_id,)).fetchone()
        return dict(row) if row else None

    def create_reviewer(
        self, email: str, password_hash: str, name: str, role: str = "reviewer"
    ) -> dict[str, Any]:
        rid = str(uuid.uuid4())
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO reviewers (id, email, password_hash, name, role, active, created_at)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    rid, email.lower().strip(), password_hash, name.strip(),
                    role if role in ("admin", "reviewer") else "reviewer",
                    datetime.now(UTC).isoformat(),
                ),
            )
            row = conn.execute("SELECT * FROM reviewers WHERE id=?", (rid,)).fetchone()
        return dict(row) if row else {}

    def touch_reviewer_login(self, reviewer_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE reviewers SET last_login_at=? WHERE id=?",
                (datetime.now(UTC).isoformat(), reviewer_id),
            )
