from __future__ import annotations

import asyncio
import copy
import logging
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

LOGIN_TIMEOUT_SECONDS = 600  # 10 minutos para que el usuario logre loggearse


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _human_age(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    return f"{days}d"


def get_session_status(session_path: Path) -> dict[str, Any]:
    """Lightweight status of LinkedIn session storage_state.

    We can't verify without hitting LinkedIn (slow + detectable). Best we
    can do locally: file presence + mtime as proxy of freshness.
    """
    if not session_path.exists():
        return {
            "exists": False,
            "path": str(session_path),
            "age_seconds": None,
            "age_human": None,
            "modified_at": None,
        }
    stat = session_path.stat()
    age = max(0.0, datetime.now(UTC).timestamp() - stat.st_mtime)
    return {
        "exists": True,
        "path": str(session_path),
        "age_seconds": int(age),
        "age_human": _human_age(age),
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
    }


async def _login_flow(session_path: Path, status_setter) -> None:
    """Open a visible browser, wait for the user to log in, then save state."""
    session_path.parent.mkdir(parents=True, exist_ok=True)

    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=False)
    context = await browser.new_context()
    page = await context.new_page()

    try:
        await page.goto("https://www.linkedin.com/login", wait_until="load", timeout=60000)
        status_setter("waiting_user", "Ventana de Chromium abierta. Inicia sesión en LinkedIn.")

        deadline = LOGIN_TIMEOUT_SECONDS // 2  # we sleep 2s per iter
        for _ in range(deadline):
            await asyncio.sleep(2)
            try:
                url = page.url.lower()
            except Exception:
                # Page closed by user
                raise RuntimeError("La ventana del navegador se cerró antes del login.")
            if "login" not in url and (
                "/feed" in url or "/jobs" in url or "/in/" in url or "/mynetwork" in url
            ):
                await context.storage_state(path=str(session_path))
                logger.info("Sesión LinkedIn guardada en %s", session_path)
                return

        raise TimeoutError(f"Timeout: no se detectó login en {LOGIN_TIMEOUT_SECONDS}s.")
    finally:
        try:
            await browser.close()
        except Exception:
            pass
        try:
            await playwright.stop()
        except Exception:
            pass


class LoginJobRunner:
    """Owns a single in-flight login attempt; thread-safe."""

    def __init__(self, session_path: Path):
        self.session_path = session_path
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}
        self._active_job_id: str | None = None

    def start_login(self) -> str:
        with self._lock:
            if self._active_job_id:
                active = self._jobs.get(self._active_job_id)
                if active and active.get("status") in {"queued", "running", "waiting_user"}:
                    raise RuntimeError("Ya hay un proceso de login en curso.")

            job_id = str(uuid.uuid4())
            self._jobs[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "message": "",
                "created_at": _now_iso(),
                "started_at": None,
                "finished_at": None,
                "error": None,
            }
            self._active_job_id = job_id

        threading.Thread(
            target=self._run_login,
            args=(job_id,),
            daemon=True,
        ).start()
        return job_id

    def _set_status(self, job_id: str, status: str, message: str = "") -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job["status"] = status
            if message:
                job["message"] = message

    def _run_login(self, job_id: str) -> None:
        self._set_status(job_id, "running", "Lanzando navegador...")
        with self._lock:
            self._jobs[job_id]["started_at"] = _now_iso()

        def setter(status: str, message: str) -> None:
            self._set_status(job_id, status, message)

        try:
            asyncio.run(_login_flow(self.session_path, setter))
            self._set_status(job_id, "success", "Sesión LinkedIn guardada correctamente.")
        except Exception as exc:
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None:
                    job["status"] = "error"
                    job["error"] = str(exc)
                    job["message"] = str(exc)
        finally:
            with self._lock:
                job = self._jobs.get(job_id)
                if job is not None:
                    job["finished_at"] = _now_iso()
                if self._active_job_id == job_id:
                    self._active_job_id = None

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return copy.deepcopy(job) if job else None

    def get_active(self) -> dict[str, Any] | None:
        with self._lock:
            if not self._active_job_id:
                return None
            job = self._jobs.get(self._active_job_id)
            return copy.deepcopy(job) if job else None
