from __future__ import annotations

import copy
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from jobson.storage.base import BaseRepository
from jobson.wp_publisher import WPConfig, WPPublisher


class SyncJobRunner:
    """Runs one WP sync job at a time and exposes in-memory progress."""

    def __init__(self, repository: BaseRepository):
        self.repository = repository
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._active_job_id: str | None = None

    def start_job(self, cfg: WPConfig, ai_normalizer, limit: int = 100) -> str:
        with self._lock:
            if self._active_job_id:
                active = self._jobs.get(self._active_job_id)
                if active and active.get("status") in {"queued", "running"}:
                    raise RuntimeError("Ya hay una sincronización en ejecución. Espera a que termine.")

            records = self.repository.list_results(
                limit=max(1, min(limit, 500)),
                review_status="approved",
            )
            records = [r for r in records if (r.get("title") or "").strip()]

            job_id = str(uuid.uuid4())
            self._jobs[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "created_at": datetime.now(UTC).isoformat(),
                "started_at": None,
                "finished_at": None,
                "total": len(records),
                "processed": 0,
                "created": 0,
                "updated": 0,
                "errors": [],
                "last_item": "",
                "current_dedupe_key": None,
            }
            self._active_job_id = job_id

        worker = threading.Thread(
            target=self._run_job,
            args=(job_id, cfg, ai_normalizer, records),
            daemon=True,
        )
        worker.start()
        return job_id

    def _run_job(self, job_id: str, cfg: WPConfig, ai_normalizer, records: list[dict]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "running"
            job["started_at"] = datetime.now(UTC).isoformat()

        publisher = WPPublisher(
            repository=self.repository,
            cfg=cfg,
            dry_run=False,
            ai_normalizer=ai_normalizer,
        )

        for rec in records:
            with self._lock:
                job = self._jobs[job_id]
                job["current_dedupe_key"] = rec.get("dedupe_key")
                job["last_item"] = (rec.get("title") or "")[:120]

            try:
                out = publisher.publish_one(rec, status=cfg.default_status)
            except Exception as exc:
                with self._lock:
                    job = self._jobs[job_id]
                    job["errors"].append({
                        "dedupe_key": rec.get("dedupe_key"),
                        "title": (rec.get("title") or "")[:120],
                        "error": str(exc),
                    })
                    job["processed"] += 1
                continue

            action = out.get("action")
            if out.get("id"):
                try:
                    self.repository.mark_published(
                        dedupe_key=rec["dedupe_key"],
                        post_id=int(out["id"]),
                        wp_url=out.get("link"),
                    )
                except Exception:
                    pass

            with self._lock:
                job = self._jobs[job_id]
                if action == "created":
                    job["created"] += 1
                elif action == "updated":
                    job["updated"] += 1
                job["processed"] += 1

        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "success"
            job["finished_at"] = datetime.now(UTC).isoformat()
            job["current_dedupe_key"] = None
            if self._active_job_id == job_id:
                self._active_job_id = None

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return copy.deepcopy(job) if job else None

    def get_active_job(self) -> dict[str, Any] | None:
        with self._lock:
            if not self._active_job_id:
                return None
            job = self._jobs.get(self._active_job_id)
            return copy.deepcopy(job) if job else None
