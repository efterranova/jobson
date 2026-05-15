from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import requests

from jobson.models import normalize_record
from jobson.storage.base import BaseRepository


class SupabaseRepository(BaseRepository):
    def __init__(self, url: str, key: str, table: str, reviewers_table: str = "reviewers"):
        self.url = url.rstrip("/")
        self.key = key
        self.table = table
        self.reviewers_table = reviewers_table
        self.endpoint = f"{self.url}/rest/v1/{self.table}"
        self.reviewers_endpoint = f"{self.url}/rest/v1/{self.reviewers_table}"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "apikey": self.key,
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            }
        )

    @property
    def backend_name(self) -> str:
        return "supabase"

    def _get_existing(self, keys: list[str]) -> dict[str, dict[str, Any]]:
        """Devuelve {dedupe_key: {dedupe_key, first_seen_at, wp_status}} para los keys dados."""
        existing: dict[str, dict[str, Any]] = {}
        chunk_size = 120
        for index in range(0, len(keys), chunk_size):
            chunk = keys[index:index + chunk_size]
            in_filter = f"({','.join(chunk)})"
            response = self.session.get(
                self.endpoint,
                params={"select": "dedupe_key,first_seen_at,wp_status", "dedupe_key": f"in.{in_filter}"},
                timeout=30,
            )
            response.raise_for_status()
            for row in response.json():
                key = row.get("dedupe_key")
                if key:
                    existing[key] = row
        return existing

    def upsert_results(self, records: list[dict[str, Any]], keyword: str, search_mode: str) -> dict[str, int]:
        normalized = [normalize_record(record, keyword, search_mode) for record in records]
        unique_records = {item["dedupe_key"]: item for item in normalized}

        if not unique_records:
            return {"received": 0, "inserted": 0, "updated": 0}

        keys = list(unique_records.keys())
        existing = self._get_existing(keys)
        now = datetime.now(UTC).isoformat()

        # Preservar first_seen_at y wp_status terminal cuando el record ya existe.
        payload_rows: list[dict[str, Any]] = []
        for key, item in unique_records.items():
            row = dict(item)
            # Postgres jsonb: pasar listas como están (no JSON-string)
            row["extracted_emails"] = list(item.get("extracted_emails") or [])
            row["extracted_urls"] = list(item.get("extracted_urls") or [])

            if key in existing:
                prev = existing[key]
                # Preservar first_seen_at (nunca cambia tras INSERT inicial)
                row["first_seen_at"] = prev.get("first_seen_at") or now
                # Preservar wp_status si está en estado terminal
                if prev.get("wp_status") in ("synced", "failed", "discarded"):
                    row["wp_status"] = prev["wp_status"]
            else:
                row["first_seen_at"] = now
            row["last_seen_at"] = now
            payload_rows.append(row)

        response = self.session.post(
            self.endpoint,
            params={"on_conflict": "dedupe_key"},
            headers={"Prefer": "resolution=merge-duplicates,return=minimal"},
            json=payload_rows,
            timeout=60,
        )
        if response.status_code >= 400:
            raise RuntimeError(f"Supabase upsert {response.status_code}: {response.text[:400]}")

        inserted = len(unique_records) - len(existing)
        updated = len(unique_records) - inserted
        return {"received": len(records), "inserted": inserted, "updated": updated}

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
        params: dict[str, Any] = {
            "select": "*",
            "order": "first_seen_at.desc.nullslast,scraped_at.desc",
            "limit": max(1, min(limit, 1000)),
        }

        if source_type:
            params["source_type"] = f"eq.{source_type}"

        if user_status:
            params["user_status"] = f"eq.{user_status}"
        elif only_followups:
            params["user_status"] = "not.is.null"

        if review_status:
            params["review_status"] = f"eq.{review_status}"

        if wp_status:
            params["wp_status"] = f"eq.{wp_status}"

        if contact_status:
            params["contact_status"] = f"eq.{contact_status}"

        if only_new_today:
            today = datetime.now(UTC).date().isoformat()
            params["first_seen_at"] = f"gte.{today}T00:00:00+00:00"

        if search_text and search_text.strip():
            query = search_text.strip().replace("%", "")
            params["or"] = (
                f"(title.ilike.*{query}*,company.ilike.*{query}*,author.ilike.*{query}*,"
                f"summary.ilike.*{query}*,content.ilike.*{query}*,"
                f"apply_email.ilike.*{query}*,apply_url_external.ilike.*{query}*)"
            )

        response = self.session.get(self.endpoint, params=params, timeout=30)
        response.raise_for_status()
        rows = response.json()
        for row in rows:
            ai = row.get("ai_normalized_json")
            if isinstance(ai, (dict, list)):
                row["ai_normalized_json"] = json.dumps(ai, ensure_ascii=False)
        return rows

    def update_result_status(self, dedupe_key: str, user_status: str | None) -> dict[str, Any] | None:
        payload: dict[str, Any] = {
            "user_status": user_status,
            "status_updated_at": datetime.now(UTC).isoformat(),
        }
        response = self.session.patch(
            self.endpoint,
            params={"dedupe_key": f"eq.{dedupe_key}"},
            headers={"Prefer": "return=representation"},
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        rows = response.json()
        if not rows:
            return None
        return rows[0]

    # --- AI cache ---
    def save_ai_normalization(self, dedupe_key: str, ai_json: str, prompt_version: str) -> None:
        try:
            parsed = json.loads(ai_json) if isinstance(ai_json, str) else ai_json
        except json.JSONDecodeError:
            parsed = ai_json
        payload = {
            "ai_normalized_json": parsed,
            "ai_prompt_version":  prompt_version,
            "ai_normalized_at":   datetime.now(UTC).isoformat(),
        }
        resp = self.session.patch(
            self.endpoint,
            params={"dedupe_key": f"eq.{dedupe_key}"},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()

    # --- Review workflow ---
    def set_review_status(
        self, dedupe_key: str, review_status: str, reviewer_id: str | None = None
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {
            "review_status": review_status,
            "reviewed_at":   datetime.now(UTC).isoformat(),
        }
        if reviewer_id:
            payload["reviewed_by"] = reviewer_id
        resp = self.session.patch(
            self.endpoint,
            params={"dedupe_key": f"eq.{dedupe_key}"},
            headers={"Prefer": "return=representation"},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None

    def mark_published(
        self, dedupe_key: str, post_id: int, wp_url: str | None = None
    ) -> dict[str, Any] | None:
        payload: dict[str, Any] = {
            "review_status":     "published",
            "published_post_id": int(post_id),
            "published_at":      datetime.now(UTC).isoformat(),
        }
        if wp_url:
            payload["published_wp_url"] = wp_url
        resp = self.session.patch(
            self.endpoint,
            params={"dedupe_key": f"eq.{dedupe_key}"},
            headers={"Prefer": "return=representation"},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None

    def mark_wp_synced(self, dedupe_key: str, post_id: int, wp_url: str | None = None) -> dict[str, Any] | None:
        """Marca el lado técnico WP + el lado humano review como published en un solo PATCH."""
        now = datetime.now(UTC).isoformat()
        payload: dict[str, Any] = {
            "wp_status":         "synced",
            "wp_post_id":        int(post_id),
            "wp_synced_at":      now,
            "wp_last_error":     None,
            "review_status":     "published",
            "published_post_id": int(post_id),
            "published_at":      now,
        }
        if wp_url:
            payload["published_wp_url"] = wp_url
        resp = self.session.patch(
            self.endpoint,
            params={"dedupe_key": f"eq.{dedupe_key}"},
            headers={"Prefer": "return=representation"},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None

    def mark_wp_failed(self, dedupe_key: str, error: str) -> dict[str, Any] | None:
        resp = self.session.patch(
            self.endpoint,
            params={"dedupe_key": f"eq.{dedupe_key}"},
            headers={"Prefer": "return=representation"},
            json={"wp_status": "failed", "wp_last_error": (error or "")[:800]},
            timeout=30,
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None

    def update_wp_status(
        self,
        dedupe_key: str,
        wp_status: str,
        wp_post_id: int | None = None,
        wp_last_error: str | None = None,
    ) -> dict[str, Any] | None:
        valid = {"pending", "synced", "skipped", "failed", "discarded"}
        if wp_status not in valid:
            raise ValueError(f"wp_status inválido: {wp_status}")
        payload: dict[str, Any] = {"wp_status": wp_status, "wp_last_error": wp_last_error}
        if wp_post_id is not None:
            payload["wp_post_id"] = int(wp_post_id)
        if wp_status == "synced":
            payload["wp_synced_at"] = datetime.now(UTC).isoformat()
        resp = self.session.patch(
            self.endpoint,
            params={"dedupe_key": f"eq.{dedupe_key}"},
            headers={"Prefer": "return=representation"},
            json=payload,
            timeout=30,
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None

    # --- Reviewers ---
    def get_reviewer_by_email(self, email: str) -> dict[str, Any] | None:
        resp = self.session.get(
            self.reviewers_endpoint,
            params={"select": "*", "email": f"eq.{email.lower().strip()}", "active": "eq.true", "limit": 1},
            timeout=15,
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None

    def get_reviewer(self, reviewer_id: str) -> dict[str, Any] | None:
        resp = self.session.get(
            self.reviewers_endpoint,
            params={"select": "*", "id": f"eq.{reviewer_id}", "limit": 1},
            timeout=15,
        )
        resp.raise_for_status()
        rows = resp.json()
        return rows[0] if rows else None

    def create_reviewer(
        self, email: str, password_hash: str, name: str, role: str = "reviewer"
    ) -> dict[str, Any]:
        payload = {
            "email":         email.lower().strip(),
            "password_hash": password_hash,
            "name":          name.strip(),
            "role":          role if role in ("admin", "reviewer") else "reviewer",
        }
        resp = self.session.post(
            self.reviewers_endpoint,
            headers={"Prefer": "return=representation"},
            json=payload,
            timeout=15,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"No se pudo crear reviewer: {resp.status_code} {resp.text[:200]}")
        rows = resp.json()
        return rows[0] if rows else {}

    def touch_reviewer_login(self, reviewer_id: str) -> None:
        self.session.patch(
            self.reviewers_endpoint,
            params={"id": f"eq.{reviewer_id}"},
            json={"last_login_at": datetime.now(UTC).isoformat()},
            timeout=15,
        )
