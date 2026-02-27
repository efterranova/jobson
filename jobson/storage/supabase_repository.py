from __future__ import annotations

from typing import Any

import requests

from jobson.models import normalize_record
from jobson.storage.base import BaseRepository


class SupabaseRepository(BaseRepository):
    def __init__(self, url: str, key: str, table: str):
        self.url = url.rstrip("/")
        self.key = key
        self.table = table
        self.endpoint = f"{self.url}/rest/v1/{self.table}"
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

    def _get_existing_keys(self, keys: list[str]) -> set[str]:
        existing: set[str] = set()
        chunk_size = 120

        for index in range(0, len(keys), chunk_size):
            chunk = keys[index:index + chunk_size]
            in_filter = f"({','.join(chunk)})"
            response = self.session.get(
                self.endpoint,
                params={"select": "dedupe_key", "dedupe_key": f"in.{in_filter}"},
                timeout=30,
            )
            response.raise_for_status()
            for row in response.json():
                dedupe_key = row.get("dedupe_key")
                if dedupe_key:
                    existing.add(dedupe_key)
        return existing

    def upsert_results(self, records: list[dict[str, Any]], keyword: str, search_mode: str) -> dict[str, int]:
        normalized = [normalize_record(record, keyword, search_mode) for record in records]
        unique_records = {item["dedupe_key"]: item for item in normalized}

        if not unique_records:
            return {"received": 0, "inserted": 0, "updated": 0}

        keys = list(unique_records.keys())
        existing_keys = self._get_existing_keys(keys)

        response = self.session.post(
            self.endpoint,
            params={"on_conflict": "dedupe_key"},
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
            json=list(unique_records.values()),
            timeout=60,
        )
        response.raise_for_status()

        inserted = len(unique_records) - len(existing_keys)
        updated = len(unique_records) - inserted
        return {"received": len(records), "inserted": inserted, "updated": updated}

    def list_results(
        self,
        limit: int = 200,
        source_type: str | None = None,
        search_text: str | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {
            "select": "*",
            "order": "scraped_at.desc",
            "limit": max(1, min(limit, 1000)),
        }

        if source_type:
            params["source_type"] = f"eq.{source_type}"

        if search_text and search_text.strip():
            query = search_text.strip().replace("%", "")
            params[
                "or"
            ] = (
                f"(title.ilike.*{query}*,company.ilike.*{query}*,author.ilike.*{query}*,"
                f"summary.ilike.*{query}*,content.ilike.*{query}*)"
            )

        response = self.session.get(self.endpoint, params=params, timeout=30)
        response.raise_for_status()
        return response.json()
