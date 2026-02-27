from __future__ import annotations

import csv
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jobson.scraper.linkedin import LinkedInScraper
from jobson.storage.base import BaseRepository


class SearchService:
    def __init__(self, scraper: LinkedInScraper, repository: BaseRepository, data_dir: Path):
        self.scraper = scraper
        self.repository = repository
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)

    def _save_csv(self, records: list[dict[str, Any]], mode: str, keywords: str) -> str | None:
        if not records:
            return None

        safe_keyword = re.sub(r"[^a-zA-Z0-9]+", "_", keywords).strip("_")[:40] or "busqueda"
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        path = self.data_dir / f"{mode}_linkedin_{safe_keyword}_{stamp}.csv"

        fieldnames = [
            "source_type",
            "source_id",
            "title",
            "company",
            "author",
            "summary",
            "content",
            "seniority",
            "apply_type",
            "url",
            "scraped_at",
        ]

        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(records)

        return str(path)

    async def run_search(
        self,
        mode: str,
        keywords: str,
        limit: int,
        days: int | None,
    ) -> dict[str, Any]:
        mode = mode.strip().lower()
        if mode not in {"jobs", "feed", "mixed"}:
            raise ValueError("Modo inválido. Usa jobs, feed o mixed.")

        if mode == "jobs":
            records = await self.scraper.scrape_jobs(keywords, limit, days)
        elif mode == "feed":
            records = await self.scraper.scrape_posts(keywords, limit, days)
        else:
            records = await self.scraper.scrape_mixed(keywords, limit, days)

        persistence = self.repository.upsert_results(records, keyword=keywords, search_mode=mode)
        csv_path = self._save_csv(records, mode=mode, keywords=keywords)

        jobs_count = sum(1 for row in records if row.get("source_type") == "jobs")
        feed_count = sum(1 for row in records if row.get("source_type") == "feed")

        return {
            "mode": mode,
            "keywords": keywords,
            "limit": limit,
            "days": days,
            "scraped_total": len(records),
            "scraped_jobs": jobs_count,
            "scraped_feed": feed_count,
            "persisted": persistence,
            "csv_path": csv_path,
            "storage_backend": self.repository.backend_name,
        }
