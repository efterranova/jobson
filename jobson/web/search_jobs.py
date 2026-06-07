from __future__ import annotations

import asyncio
import copy
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from jobson.service import SearchService


class SearchJobRunner:
    """Runs one scraping job at a time and exposes in-memory status."""

    def __init__(self, service: SearchService):
        self.service = service
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._active_job_id: str | None = None

    def start_job(
        self,
        keywords: str = "",
        limit: int = 50,
        days: int | None = None,
        sources: list[str] | None = None,
        linkedin_mode: str = "mixed",
        exclude_keywords: list[str] | None = None,
        languages: list[str] | None = None,
        mode: str | None = None,  # alias legacy de linkedin_mode
        location: str | None = None,
    ) -> str:
        sources = sources or ["linkedin"]
        mode = mode or linkedin_mode
        with self._lock:
            if self._active_job_id:
                active = self._jobs.get(self._active_job_id)
                if active and active.get("status") in {"queued", "running"}:
                    raise RuntimeError("Ya hay una búsqueda en ejecución. Espera a que termine.")

            job_id = str(uuid.uuid4())
            self._jobs[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "mode": mode,
                "sources": sources,
                "keywords": keywords,
                "limit": limit,
                "days": days,
                "exclude_keywords": exclude_keywords or [],
                "languages": languages or [],
                "location": location,
                "created_at": datetime.now(UTC).isoformat(),
                "started_at": None,
                "finished_at": None,
                "result": None,
                "error": None,
                "progress": {
                    "scraped_total": 0,
                    "scraped_jobs": 0,
                    "scraped_feed": 0,
                    "matched_total": 0,
                    "matched_jobs": 0,
                    "matched_feed": 0,
                    "filtered_out": {"excluded_keywords": 0, "language": 0},
                    "persisted": {"received": 0, "inserted": 0, "updated": 0},
                    "criteria": {
                        "exclude_keywords": exclude_keywords or [],
                        "languages": languages or [],
                    },
                    "last_item": "",
                },
            }
            self._active_job_id = job_id

        worker = threading.Thread(
            target=self._run_job,
            args=(job_id, mode, keywords, limit, days, exclude_keywords or [], languages or [], sources, location),
            daemon=True,
        )
        worker.start()
        return job_id

    def _run_job(
        self,
        job_id: str,
        mode: str,
        keywords: str,
        limit: int,
        days: int | None,
        exclude_keywords: list[str],
        languages: list[str],
        sources: list[str],
        location: str | None = None,
    ) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job["status"] = "running"
            job["started_at"] = datetime.now(UTC).isoformat()

        try:
            def on_progress(progress: dict[str, Any]) -> None:
                with self._lock:
                    current = self._jobs.get(job_id)
                    if not current:
                        return
                    by_source = dict(progress.get("scraped_by_source") or {})
                    matched_by_source = dict(progress.get("matched_by_source") or {})
                    pipeline = dict(progress.get("pipeline") or {})
                    # LinkedIn scraper genera source_type 'jobs' o 'feed'.
                    # TPE genera source_type 'tuportalempleo'.
                    # El service nos pasa por source key del scraper ('linkedin'/'tpe').
                    current["progress"] = {
                        "scraped_total": int(progress.get("scraped_total", 0)),
                        "scraped_by_source": by_source,
                        "scraped_jobs": int(by_source.get("linkedin", 0)),
                        "scraped_feed": 0,
                        "scraped_tpe": int(by_source.get("tpe", 0)),
                        "scraped_jobbank": int(by_source.get("jobbank", 0)),
                        "matched_total": int(progress.get("matched_total", 0)),
                        "matched_by_source": matched_by_source,
                        "matched_jobs": int(matched_by_source.get("linkedin", 0)),
                        "matched_feed": 0,
                        "matched_tpe": int(matched_by_source.get("tpe", 0)),
                        "matched_jobbank": int(matched_by_source.get("jobbank", 0)),
                        "pipeline": {
                            "wp_pending":   int(pipeline.get("wp_pending", 0)),
                            "wp_skipped":   int(pipeline.get("wp_skipped", 0)),
                            "tiene_email":  int(pipeline.get("tiene_email", 0)),
                            "tiene_web":    int(pipeline.get("tiene_web", 0)),
                            "tiene_ambos":  int(pipeline.get("tiene_ambos", 0)),
                            "sin_contacto": int(pipeline.get("sin_contacto", 0)),
                        },
                        "filtered_out": {
                            "excluded_keywords": int(
                                (progress.get("filtered_out") or {}).get("excluded_keywords", 0)
                            ),
                            "language": int((progress.get("filtered_out") or {}).get("language", 0)),
                        },
                        "persisted": {
                            "received": int((progress.get("persisted") or {}).get("received", 0)),
                            "inserted": int((progress.get("persisted") or {}).get("inserted", 0)),
                            "updated": int((progress.get("persisted") or {}).get("updated", 0)),
                        },
                        "criteria": {
                            "exclude_keywords": list((progress.get("criteria") or {}).get("exclude_keywords", [])),
                            "languages": list((progress.get("criteria") or {}).get("languages", [])),
                            "sources": list((progress.get("criteria") or {}).get("sources", [])),
                        },
                        "last_item": str(progress.get("last_item") or ""),
                    }

            result = asyncio.run(
                self.service.run_search(
                    keywords=keywords,
                    limit=limit,
                    days=days,
                    sources=sources,
                    linkedin_mode=mode,
                    exclude_keywords=exclude_keywords,
                    languages=languages,
                    progress_callback=on_progress,
                    location=location,
                )
            )
            with self._lock:
                job = self._jobs[job_id]
                job["status"] = "success"
                job["result"] = result
                by_source = dict(result.get("scraped_by_source") or {})
                matched_by_source = dict(result.get("matched_by_source") or {})
                pipeline = dict(result.get("pipeline") or {})
                job["progress"] = {
                    "scraped_total": int(result.get("scraped_total", 0)),
                    "scraped_by_source": by_source,
                    "scraped_jobs": int(by_source.get("linkedin", 0)),
                    "scraped_feed": 0,
                    "scraped_tpe": int(by_source.get("tpe", 0)),
                    "scraped_jobbank": int(by_source.get("jobbank", 0)),
                    "matched_total": int(result.get("matched_total", 0)),
                    "matched_by_source": matched_by_source,
                    "matched_jobs": int(matched_by_source.get("linkedin", 0)),
                    "matched_feed": 0,
                    "matched_tpe": int(matched_by_source.get("tpe", 0)),
                    "matched_jobbank": int(matched_by_source.get("jobbank", 0)),
                    "pipeline": {
                        "wp_pending":   int(pipeline.get("wp_pending", 0)),
                        "wp_skipped":   int(pipeline.get("wp_skipped", 0)),
                        "tiene_email":  int(pipeline.get("tiene_email", 0)),
                        "tiene_web":    int(pipeline.get("tiene_web", 0)),
                        "tiene_ambos":  int(pipeline.get("tiene_ambos", 0)),
                        "sin_contacto": int(pipeline.get("sin_contacto", 0)),
                    },
                    "filtered_out": {
                        "excluded_keywords": int((result.get("filtered_out") or {}).get("excluded_keywords", 0)),
                        "language": int((result.get("filtered_out") or {}).get("language", 0)),
                    },
                    "persisted": {
                        "received": int((result.get("persisted") or {}).get("received", 0)),
                        "inserted": int((result.get("persisted") or {}).get("inserted", 0)),
                        "updated": int((result.get("persisted") or {}).get("updated", 0)),
                    },
                    "criteria": {
                        "exclude_keywords": list(result.get("exclude_keywords") or []),
                        "languages": list(result.get("languages") or []),
                        "sources": list(result.get("sources") or []),
                    },
                    "last_item": "",
                }
        except Exception as exc:  # pragma: no cover
            with self._lock:
                job = self._jobs[job_id]
                job["status"] = "error"
                job["error"] = str(exc)
        finally:
            with self._lock:
                job = self._jobs[job_id]
                job["finished_at"] = datetime.now(UTC).isoformat()
                if self._active_job_id == job_id:
                    self._active_job_id = None

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            return copy.deepcopy(job)

    def get_active_job(self) -> dict[str, Any] | None:
        with self._lock:
            if not self._active_job_id:
                return None
            job = self._jobs.get(self._active_job_id)
            return copy.deepcopy(job) if job else None
