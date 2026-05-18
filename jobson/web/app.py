from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

from datetime import timedelta

from flask import Flask, flash, jsonify, make_response, redirect, render_template, request, url_for

from jobson.config import Settings, load_settings
from jobson.storage.base import BaseRepository
from jobson.storage.factory import build_repository
from jobson.web.auth import (
    admin_required,
    authenticate,
    current_user,
    get_secret_key,
    login_required,
    login_user,
    logout_user,
)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
ALLOWED_TRACKING_STATUS = {"me_interesa", "no_me_interesa", "ya_aplique"}
ALLOWED_STATUS_FILTERS = {"all", "seguimiento", *ALLOWED_TRACKING_STATUS}
ALLOWED_REVIEW_STATUS = {"pending", "approved", "rejected", "published"}
ALLOWED_WP_STATUS = {"pending", "synced", "skipped", "failed", "discarded"}
ALLOWED_CONTACT_STATUS = {"tiene_email", "tiene_web", "tiene_ambos", "sin_contacto"}


def build_service(settings: Settings):
    """Construye el repositorio siempre y el servicio de scraping SOLO si APP_ROLE='full'.

    En modo viewer/serverless (Vercel) no se importa Playwright — el viewer solo
    lee de Supabase.
    """
    repository = build_repository(settings)
    if settings.app_role == "viewer":
        return None, repository
    # Lazy imports solo en modo full (Mac local)
    from jobson.scraper.linkedin import LinkedInScraper  # noqa: I001
    from jobson.scraper.tuportalempleo import TuPortalEmpleoScraper  # noqa: I001
    from jobson.service import SearchService  # noqa: I001

    scrapers = {
        "linkedin": LinkedInScraper(settings.storage_state_path),
        "tpe":      TuPortalEmpleoScraper(),
    }
    service = SearchService(scrapers=scrapers, repository=repository, data_dir=settings.data_dir)
    return service, repository


def _split_csv_tokens(value: str) -> list[str]:
    return [token.strip() for token in re.split(r"[,\n;|]+", value) if token and token.strip()]


def _parse_string_list(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        tokens = _split_csv_tokens(value)
    elif isinstance(value, list):
        tokens = []
        for item in value:
            if item is None:
                continue
            if isinstance(item, str):
                tokens.extend(_split_csv_tokens(item))
            else:
                tokens.extend(_split_csv_tokens(str(item)))
    else:
        tokens = _split_csv_tokens(str(value))

    unique: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        normalized = re.sub(r"\s+", " ", token).strip()
        lowered = normalized.lower()
        if not normalized or lowered in seen:
            continue
        seen.add(lowered)
        unique.append(normalized)
    return unique


def create_app(settings: Settings | None = None) -> Flask:
    settings = settings or load_settings()
    service, repository = build_service(settings)

    # SearchJobRunner + LoginJobRunner solo aplican en modo full (Mac local).
    job_runner = None
    login_runner = None
    if service is not None:
        from jobson.web.search_jobs import SearchJobRunner  # noqa: I001
        from jobson.linkedin_session import LoginJobRunner  # noqa: I001
        job_runner = SearchJobRunner(service)
        login_runner = LoginJobRunner(settings.storage_state_path)

    app = Flask(__name__, template_folder=str(TEMPLATES_DIR))
    app.config["service"] = service
    app.config["repository"] = repository
    app.config["job_runner"] = job_runner
    app.config["login_runner"] = login_runner
    app.config["session_path"] = settings.storage_state_path
    app.config["SECRET_KEY"] = get_secret_key()
    app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(days=14)
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

    @app.context_processor
    def inject_user() -> dict:
        return {"current_user": current_user()}

    @app.get("/")
    def index():
        # En producción (Vercel, APP_ROLE=viewer) la raíz lleva al panel revisor.
        # En local (APP_ROLE=full) la raíz muestra el scraper.
        if settings.app_role == "viewer":
            return redirect(url_for("login_page") if not current_user() else url_for("review_page"))
        rendered = render_template(
            "index.html",
            backend_name=repository.backend_name,
            app_role=settings.app_role,
        )
        response = make_response(rendered)
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
        return response

    @app.get("/health")
    def health():
        return "<!doctype html><html><body><h1>OK</h1><p>JobsOn vivo.</p></body></html>"

    # --- LinkedIn session (solo modo full) ---
    @app.get("/api/linkedin/session/status")
    def linkedin_session_status():
        if settings.app_role == "viewer" or app.config.get("login_runner") is None:
            return jsonify({"available": False, "error": "Servidor en modo visor."}), 403
        from jobson.linkedin_session import get_session_status
        status = get_session_status(app.config["session_path"])
        runner = app.config["login_runner"]
        status["active_login"] = runner.get_active()
        return jsonify(status)

    @app.post("/api/linkedin/session/login")
    def linkedin_session_login():
        if settings.app_role == "viewer" or app.config.get("login_runner") is None:
            return jsonify({"error": "Servidor en modo visor. Login se hace en el equipo local."}), 403
        runner = app.config["login_runner"]
        try:
            job_id = runner.start_login()
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        return jsonify({"job_id": job_id, "status": "queued"})

    @app.get("/api/linkedin/session/login/<job_id>")
    def linkedin_session_login_status(job_id: str):
        runner = app.config.get("login_runner")
        if runner is None:
            return jsonify({"error": "Servidor en modo visor."}), 403
        job = runner.get_job(job_id)
        if not job:
            return jsonify({"error": "Job no encontrado"}), 404
        return jsonify(job)

    # ---------- Auth ----------
    @app.get("/login")
    def login_page():
        return render_template("login.html", next=request.args.get("next", ""))

    @app.post("/login")
    def login_submit():
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        nxt = (request.form.get("next") or "").strip() or url_for("review_page")
        if not email or not password:
            flash("Email y password son obligatorios.", "error")
            return redirect(url_for("login_page", next=nxt))
        user = authenticate(repository, email, password)
        if not user:
            flash("Credenciales inválidas.", "error")
            return redirect(url_for("login_page", next=nxt))
        login_user(user)
        try:
            repository.touch_reviewer_login(str(user.get("id")))
        except Exception:
            pass
        return redirect(nxt)

    @app.post("/logout")
    def logout():
        logout_user()
        return redirect(url_for("login_page"))

    # ---------- Review UI ----------
    @app.get("/review")
    @login_required
    def review_page():
        status = (request.args.get("status") or "pending").lower()
        if status not in ALLOWED_REVIEW_STATUS:
            status = "pending"
        rendered = render_template(
            "review.html",
            current_status=status,
            backend_name=repository.backend_name,
        )
        resp = make_response(rendered)
        # Forzar no-caché — vital en Vercel donde Cloudflare + browser cachean HTML
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp

    @app.get("/api/review")
    @login_required
    def review_list():
        status = (request.args.get("status") or "pending").lower()
        if status not in ALLOWED_REVIEW_STATUS:
            return jsonify({"error": "review_status inválido"}), 400

        source_type = (request.args.get("source_type") or "").lower()
        source_type = source_type if source_type in {"jobs", "feed", "tuportalempleo"} else None

        wp_status = (request.args.get("wp_status") or "").lower() or None
        if wp_status and wp_status not in {"pending", "synced", "skipped", "failed", "discarded"}:
            wp_status = None
        contact_status = (request.args.get("contact_status") or "").lower() or None
        if contact_status and contact_status not in {"tiene_email", "tiene_web", "tiene_ambos", "sin_contacto"}:
            contact_status = None
        only_new_today = (request.args.get("only_new_today") or "").lower() in {"1", "true", "yes"}

        try:
            limit = int(request.args.get("limit") or "100")
        except ValueError:
            limit = 100
        query = (request.args.get("q") or "").strip()
        try:
            rows = repository.list_results(
                limit=max(1, min(limit, 500)),
                source_type=source_type,
                search_text=query or None,
                review_status=status,
                wp_status=wp_status,
                contact_status=contact_status,
                only_new_today=only_new_today,
            )
        except Exception as exc:
            return jsonify({"error": f"No se pudo leer: {exc}"}), 502
        return jsonify({"records": rows, "count": len(rows)})

    @app.post("/api/review/<dedupe_key>")
    @login_required
    def review_set(dedupe_key: str):
        payload = request.get_json(silent=True) or {}
        new_status = (payload.get("review_status") or "").lower().strip()
        if new_status not in {"pending", "approved", "rejected"}:
            return jsonify({"error": "review_status inválido"}), 400
        user = current_user() or {}
        try:
            row = repository.set_review_status(
                dedupe_key=dedupe_key,
                review_status=new_status,
                reviewer_id=user.get("id"),
            )
        except Exception as exc:
            return jsonify({"error": f"No se pudo actualizar: {exc}"}), 500
        if not row:
            return jsonify({"error": "Registro no encontrado"}), 404
        return jsonify({"record": row})

    # ---------- Sync to WP (admin only) ----------
    @app.post("/api/sync-approved")
    @admin_required
    def sync_approved():
        from jobson.ai_normalizer import AINormalizer
        from jobson.wp_publisher import WPPublisher, load_wp_config

        try:
            cfg = load_wp_config()
        except SystemExit as exc:
            return jsonify({"error": str(exc)}), 400

        ai = AINormalizer.from_env()
        publisher = WPPublisher(
            repository=repository,
            cfg=cfg,
            dry_run=False,
            ai_normalizer=ai,
        )
        try:
            limit = int((request.get_json(silent=True) or {}).get("limit", 50))
        except ValueError:
            limit = 50

        records = repository.list_results(
            limit=max(1, min(limit, 200)),
            review_status="approved",
        )
        records = [r for r in records if (r.get("title") or "").strip()]

        results: dict = {"total": len(records), "created": 0, "updated": 0, "errors": []}
        for rec in records:
            try:
                out = publisher.publish_one(rec, status=cfg.default_status)
            except Exception as exc:
                results["errors"].append({"dedupe_key": rec.get("dedupe_key"), "error": str(exc)})
                continue
            action = out.get("action")
            if action == "created":
                results["created"] += 1
            elif action == "updated":
                results["updated"] += 1
            if out.get("id"):
                try:
                    repository.mark_published(
                        dedupe_key=rec["dedupe_key"],
                        post_id=int(out["id"]),
                        wp_url=out.get("link"),
                    )
                except Exception:
                    pass
        return jsonify(results)

    @app.get("/api/results")
    def get_results():
        repo: BaseRepository = app.config["repository"]
        mode = (request.args.get("mode") or "all").strip().lower()
        query = (request.args.get("q") or "").strip()
        wp_status = (request.args.get("wp_status") or "all").strip().lower()
        contact_status = (request.args.get("contact_status") or "all").strip().lower()
        review_status = (request.args.get("review_status") or "all").strip().lower()
        only_new_today = (request.args.get("new_today") or "").strip().lower() in {"1", "true", "yes"}
        # Compat con el endpoint viejo (user_status filter)
        status_filter = (request.args.get("status") or "all").strip().lower()
        limit_raw = request.args.get("limit") or "200"

        try:
            limit = int(limit_raw)
        except ValueError:
            return jsonify({"error": "limit debe ser número"}), 400
        if wp_status != "all" and wp_status not in ALLOWED_WP_STATUS:
            return jsonify({"error": "wp_status inválido"}), 400
        if contact_status != "all" and contact_status not in ALLOWED_CONTACT_STATUS:
            return jsonify({"error": "contact_status inválido"}), 400
        if review_status != "all" and review_status not in ALLOWED_REVIEW_STATUS:
            return jsonify({"error": "review_status inválido"}), 400
        if status_filter not in ALLOWED_STATUS_FILTERS:
            return jsonify({"error": "status inválido"}), 400

        # source_type acepta jobs/feed/tpe/tuportalempleo
        if mode in {"jobs", "feed"}:
            source_type = mode
        elif mode in {"tpe", "tuportalempleo"}:
            source_type = "tuportalempleo"
        else:
            source_type = None

        wp_filter = wp_status if wp_status != "all" else None
        contact_filter = contact_status if contact_status != "all" else None
        review_filter = review_status if review_status != "all" else None
        user_status = None if status_filter in {"all", "seguimiento"} else status_filter
        only_followups = status_filter == "seguimiento"

        try:
            rows = repo.list_results(
                limit=limit,
                source_type=source_type,
                search_text=query,
                user_status=user_status,
                only_followups=only_followups,
                review_status=review_filter,
                wp_status=wp_filter,
                contact_status=contact_filter,
                only_new_today=only_new_today,
            )
        except Exception as exc:
            return (
                jsonify({
                    "error": "No se pudo leer desde la base de datos.",
                    "detail": str(exc),
                }),
                502,
            )

        summary = {
            "total":          len(rows),
            "jobs":           sum(1 for r in rows if r.get("source_type") == "jobs"),
            "feed":           sum(1 for r in rows if r.get("source_type") == "feed"),
            "tpe":            sum(1 for r in rows if r.get("source_type") == "tuportalempleo"),
            "wp_pending":     sum(1 for r in rows if r.get("wp_status") == "pending"),
            "wp_synced":      sum(1 for r in rows if r.get("wp_status") == "synced"),
            "wp_skipped":     sum(1 for r in rows if r.get("wp_status") == "skipped"),
            "wp_failed":      sum(1 for r in rows if r.get("wp_status") == "failed"),
            "wp_discarded":   sum(1 for r in rows if r.get("wp_status") == "discarded"),
            "review_pending":   sum(1 for r in rows if r.get("review_status") == "pending"),
            "review_approved":  sum(1 for r in rows if r.get("review_status") == "approved"),
            "review_rejected":  sum(1 for r in rows if r.get("review_status") == "rejected"),
            "review_published": sum(1 for r in rows if r.get("review_status") == "published"),
            "tiene_email":    sum(1 for r in rows if r.get("contact_status") == "tiene_email"),
            "tiene_web":      sum(1 for r in rows if r.get("contact_status") == "tiene_web"),
            "tiene_ambos":    sum(1 for r in rows if r.get("contact_status") == "tiene_ambos"),
            "sin_contacto":   sum(1 for r in rows if r.get("contact_status") == "sin_contacto"),
            "with_country":   sum(1 for r in rows if r.get("country")),
            "with_city":      sum(1 for r in rows if r.get("city")),
            # legado
            "me_interesa":    sum(1 for r in rows if r.get("user_status") == "me_interesa"),
            "no_me_interesa": sum(1 for r in rows if r.get("user_status") == "no_me_interesa"),
            "ya_aplique":     sum(1 for r in rows if r.get("user_status") == "ya_aplique"),
        }
        return jsonify({"records": rows, "summary": summary})

    @app.post("/api/results/wp-status")
    def update_wp_status_endpoint():
        payload = request.get_json(silent=True) or {}
        dedupe_key = (payload.get("dedupe_key") or "").strip()
        wp_status = (payload.get("wp_status") or "").strip().lower()
        wp_post_id = payload.get("wp_post_id")
        wp_last_error = payload.get("wp_last_error")
        if not dedupe_key:
            return jsonify({"error": "dedupe_key es obligatorio"}), 400
        if wp_status not in ALLOWED_WP_STATUS:
            return jsonify({"error": "wp_status inválido"}), 400
        if wp_post_id is not None:
            try:
                wp_post_id = int(wp_post_id)
            except (TypeError, ValueError):
                return jsonify({"error": "wp_post_id debe ser entero"}), 400
        try:
            row = repository.update_wp_status(
                dedupe_key=dedupe_key,
                wp_status=wp_status,
                wp_post_id=wp_post_id,
                wp_last_error=wp_last_error,
            )
        except Exception as exc:
            return jsonify({"error": f"No se pudo actualizar wp_status: {exc}"}), 500
        if not row:
            return jsonify({"error": "Registro no encontrado"}), 404
        return jsonify({"record": row})

    @app.post("/api/results/status")
    def update_result_status():
        payload = request.get_json(silent=True) or {}
        dedupe_key = (payload.get("dedupe_key") or "").strip()
        user_status = payload.get("user_status")
        user_status = user_status.strip().lower() if isinstance(user_status, str) else None

        if not dedupe_key:
            return jsonify({"error": "dedupe_key es obligatorio"}), 400

        if user_status is not None and user_status not in ALLOWED_TRACKING_STATUS:
            return jsonify({"error": "user_status inválido"}), 400

        repo: BaseRepository = app.config["repository"]
        try:
            row = repo.update_result_status(dedupe_key=dedupe_key, user_status=user_status)
        except Exception as exc:
            return jsonify({"error": f"No se pudo actualizar estado: {exc}"}), 500

        if not row:
            return jsonify({"error": "Registro no encontrado"}), 404
        return jsonify({"record": row})

    @app.post("/api/search")
    def run_search():
        if settings.app_role == "viewer" or app.config.get("service") is None:
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
        sources_raw = payload.get("sources") or ["linkedin"]
        limit_raw = payload.get("limit", 20)
        days_raw = payload.get("days", None)
        exclude_keywords = _parse_string_list(payload.get("exclude_keywords"))
        languages = _parse_string_list(payload.get("languages"))
        location = (payload.get("location") or "").strip() or None

        if mode not in {"jobs", "feed", "mixed"}:
            return jsonify({"error": "Modo LinkedIn inválido. Usa jobs, feed o mixed."}), 400

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

        service = app.config["service"]

        try:
            result = asyncio.run(
                service.run_search(
                    keywords=keywords,
                    limit=limit,
                    days=days,
                    sources=sources_raw if isinstance(sources_raw, list) else _parse_string_list(sources_raw),
                    linkedin_mode=mode,
                    exclude_keywords=exclude_keywords,
                    languages=languages,
                    location=location,
                )
            )
            return jsonify(result)
        except Exception as exc:
            return jsonify({"error": f"Error durante scraping: {exc}"}), 500

    @app.post("/api/search/start")
    def start_search_job():
        if settings.app_role == "viewer" or app.config.get("job_runner") is None:
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
        # Sources (nuevo modelo): linkedin / tpe
        sources_raw = payload.get("sources") or []
        if isinstance(sources_raw, str):
            sources_raw = [s.strip() for s in sources_raw.split(",") if s.strip()]
        sources = [s for s in (str(x).strip().lower() for x in sources_raw) if s in {"linkedin", "tpe"}]
        # Compat: si vienen vacíos pero hay "mode", asumimos linkedin
        linkedin_mode = (payload.get("linkedin_mode") or payload.get("mode") or "mixed").strip().lower()
        if linkedin_mode not in {"jobs", "feed", "mixed"}:
            linkedin_mode = "mixed"
        if not sources:
            sources = ["linkedin"]

        if not keywords and "linkedin" in sources and "tpe" not in sources:
            return jsonify({"error": "LinkedIn requiere palabras clave. Escribe alguna o desmarca LinkedIn."}), 400

        limit_raw = payload.get("limit", 50)
        days_raw = payload.get("days", None)
        exclude_keywords = _parse_string_list(payload.get("exclude_keywords"))
        languages = _parse_string_list(payload.get("languages"))
        location = (payload.get("location") or "").strip() or None

        try:
            limit = int(limit_raw)
            if limit < 1:
                raise ValueError
        except (TypeError, ValueError):
            return jsonify({"error": "El límite debe ser entero mayor que 0."}), 400

        if days_raw in (None, ""):
            days = None
        else:
            try:
                days = int(days_raw)
                if days <= 0:
                    days = None
            except (TypeError, ValueError):
                return jsonify({"error": "El campo días debe ser entero."}), 400

        runner = app.config["job_runner"]
        try:
            job_id = runner.start_job(
                keywords=keywords,
                limit=limit,
                days=days,
                sources=sources,
                linkedin_mode=linkedin_mode,
                exclude_keywords=exclude_keywords,
                languages=languages,
                location=location,
            )
        except RuntimeError as exc:
            return jsonify({"error": str(exc)}), 409
        except Exception as exc:  # pragma: no cover
            return jsonify({"error": f"No se pudo iniciar búsqueda: {exc}"}), 500

        return jsonify({"job_id": job_id, "status": "queued"})

    @app.get("/api/search/status/<job_id>")
    def search_job_status(job_id: str):
        runner = app.config.get("job_runner")
        if runner is None:
            return jsonify({"error": "Servidor en modo visor."}), 403
        job = runner.get_job(job_id)
        if not job:
            return jsonify({"error": "Job no encontrado"}), 404
        return jsonify(job)

    @app.get("/api/search/active")
    def active_search_job():
        runner = app.config.get("job_runner")
        if runner is None:
            return jsonify({"active": None})
        active = runner.get_active_job()
        if not active:
            return jsonify({"active": None})
        return jsonify({"active": active})

    return app
