from __future__ import annotations

import asyncio
from pathlib import Path

from flask import Flask, jsonify, render_template, request

from jobson.config import Settings, load_settings
from jobson.scraper.linkedin import LinkedInScraper
from jobson.service import SearchService
from jobson.storage.base import BaseRepository
from jobson.storage.factory import build_repository

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def build_service(settings: Settings) -> tuple[SearchService, BaseRepository]:
    repository = build_repository(settings)
    scraper = LinkedInScraper(settings.storage_state_path)
    service = SearchService(scraper=scraper, repository=repository, data_dir=settings.data_dir)
    return service, repository


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or load_settings()
    service, repository = build_service(settings)

    app = Flask(__name__, template_folder=str(TEMPLATES_DIR))
    app.config["service"] = service
    app.config["repository"] = repository

    @app.get("/")
    def index():
        return render_template(
            "index.html",
            backend_name=repository.backend_name,
            app_role=settings.app_role,
        )

    @app.get("/api/results")
    def get_results():
        repo: BaseRepository = app.config["repository"]

        mode = (request.args.get("mode") or "all").strip().lower()
        query = (request.args.get("q") or "").strip()
        limit_raw = request.args.get("limit") or "200"

        try:
            limit = int(limit_raw)
        except ValueError:
            return jsonify({"error": "limit debe ser número"}), 400

        source_type = mode if mode in {"jobs", "feed"} else None
        try:
            rows = repo.list_results(limit=limit, source_type=source_type, search_text=query)
        except Exception as exc:
            return (
                jsonify(
                    {
                        "error": (
                            "No se pudo leer desde la base de datos. "
                            "Revisa SUPABASE_URL, SUPABASE_KEY y permisos de la tabla."
                        ),
                        "detail": str(exc),
                    }
                ),
                502,
            )

        summary = {
            "total": len(rows),
            "jobs": sum(1 for row in rows if row.get("source_type") == "jobs"),
            "feed": sum(1 for row in rows if row.get("source_type") == "feed"),
        }
        return jsonify({"records": rows, "summary": summary})

    @app.post("/api/search")
    def run_search():
        if settings.app_role == "viewer":
            return (
                jsonify(
                    {
                        "error": (
                            "Este servidor está en modo visor. "
                            "El scraping se ejecuta desde tu equipo local."
                        )
                    }
                ),
                403,
            )

        payload = request.get_json(silent=True) or {}
        keywords = (payload.get("keywords") or "").strip()
        mode = (payload.get("mode") or "mixed").strip().lower()
        limit_raw = payload.get("limit", 20)
        days_raw = payload.get("days", None)

        if not keywords:
            return jsonify({"error": "Debes escribir palabras clave."}), 400

        if mode not in {"jobs", "feed", "mixed"}:
            return jsonify({"error": "Modo inválido. Usa jobs, feed o mixed."}), 400

        try:
            limit = int(limit_raw)
            if limit < 1:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"error": "El límite debe ser entero mayor que 0."}), 400

        days: int | None
        if days_raw in (None, ""):
            days = None
        else:
            try:
                days = int(days_raw)
                if days <= 0:
                    days = None
            except (TypeError, ValueError):
                return jsonify({"error": "El campo días debe ser entero."}), 400

        service: SearchService = app.config["service"]

        try:
            result = asyncio.run(
                service.run_search(
                    mode=mode,
                    keywords=keywords,
                    limit=limit,
                    days=days,
                )
            )
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": f"Error durante scraping: {exc}"}), 500

    return app
