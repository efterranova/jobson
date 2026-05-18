"""Publica registros scrapeados al WordPress de erecruit.ca (WP Job Manager + Cariera).

Usa el endpoint custom /wp-json/jobson/v1/upsert provisto por el mu-plugin
`deploy/wordpress/jobson-rest.php`. Idempotente vía `_jobson_dedupe_key`.

Envs requeridos en .env:
    WP_BASE_URL=https://staging.erecruit.ca
    WP_USER=erick
    WP_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx
    WP_DEFAULT_STATUS=draft        # publish | draft | pending  (default draft, conservador)
    WP_EXPIRES_DAYS=30
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import requests
from requests.auth import HTTPBasicAuth

from jobson.ai_normalizer import AINormalizer
from jobson.storage.base import BaseRepository
from jobson.wp_normalize import (
    JOB_TYPE_BY_LANG,
    clean_company,
    normalize_record as _normalize,
    pick_featured_image_url,
)

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class WPConfig:
    base_url: str
    user: str
    app_password: str
    default_status: str
    expires_days: int

    @property
    def auth(self) -> HTTPBasicAuth:
        return HTTPBasicAuth(self.user, self.app_password)

    @property
    def upsert_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/wp-json/jobson/v1/upsert"

    @property
    def find_url(self) -> str:
        return f"{self.base_url.rstrip('/')}/wp-json/jobson/v1/find"


def load_wp_config() -> WPConfig:
    base = os.getenv("WP_BASE_URL", "").strip()
    user = os.getenv("WP_USER", "").strip()
    pwd  = os.getenv("WP_APP_PASSWORD", "").strip()
    if not (base and user and pwd):
        raise SystemExit(
            "Faltan envs: define WP_BASE_URL, WP_USER y WP_APP_PASSWORD en .env "
            "(genera la Application Password en WP-Admin → Users → Profile)."
        )
    status = os.getenv("WP_DEFAULT_STATUS", "draft").strip().lower() or "draft"
    if status not in {"publish", "draft", "pending", "private"}:
        status = "draft"
    try:
        expires = int(os.getenv("WP_EXPIRES_DAYS", "30"))
    except ValueError:
        expires = 30
    return WPConfig(base_url=base, user=user, app_password=pwd, default_status=status, expires_days=expires)


def _clean_linkedin_url(url: str) -> str:
    """Quita query params de tracking (?eBP, ?refId, ?trackingId, ?trk) de URLs de LinkedIn."""
    if not url:
        return url
    if "linkedin.com" not in url.lower():
        return url
    from urllib.parse import urlparse, urlunparse

    p = urlparse(url)
    return urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", "", ""))


def _resolve_application_target(record: dict[str, Any]) -> str:
    """Devuelve el valor a guardar en _application según prioridad:
    1) apply_email (WP Job Manager acepta email en _application)
    2) apply_url_external (URL externa NO-portal)
    3) company_website (sitio oficial de la empresa)
    4) URL original limpia (fallback — LinkedIn sin tracking)
    """
    email = (record.get("apply_email") or "").strip()
    if email:
        return email
    external = (record.get("apply_url_external") or "").strip()
    if external:
        return external
    website = (record.get("company_website") or "").strip()
    if website:
        return website
    return _clean_linkedin_url((record.get("url") or "").strip())


def _expires_iso(record: dict[str, Any], days: int) -> str:
    # Calcula desde hoy para no publicar ofertas ya vencidas (los registros
    # pueden ser viejos del scraper). Mantiene el record param por consistencia.
    _ = record  # noqa: F841 (reservado por si en el futuro queremos lógica per-record)
    return (datetime.now(UTC) + timedelta(days=days)).date().isoformat()


def _apply_ai_overrides(norm: dict[str, Any], ai_data: dict[str, Any]) -> dict[str, Any]:
    """Sobrescribe campos de `norm` con valores limpios de IA cuando aplica."""
    if not ai_data:
        return norm

    if ai_data.get("title"):
        norm["title"] = str(ai_data["title"]).strip() or norm["title"]
    if ai_data.get("location"):
        norm["location"] = str(ai_data["location"]).strip()
    if isinstance(ai_data.get("is_remote"), bool):
        norm["remote"] = ai_data["is_remote"]
    lang = ai_data.get("language")
    if lang in ("es", "en"):
        norm["language"] = lang
    jt = ai_data.get("job_type")
    if jt and jt in JOB_TYPE_BY_LANG.get(norm["language"], {}):
        norm["job_types"] = [JOB_TYPE_BY_LANG[norm["language"]][jt]]
    if ai_data.get("description_html"):
        norm["content"] = str(ai_data["description_html"])
    return norm


def record_to_payload(
    record: dict[str, Any],
    cfg: WPConfig,
    status: str | None = None,
    test_batch_id: str | None = None,
    test_label: str = "[JobsOn TEST]",
    ai_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    # Si tenemos category_hints de IA, los inyectamos como "keyword" para que
    # wp_normalize.pick_categories tenga señal más fuerte que el keyword del scraper.
    synth_record = dict(record)
    if ai_data and ai_data.get("category_hints"):
        hints = " ".join(str(h) for h in ai_data["category_hints"] if h)
        if hints:
            synth_record["keyword"] = hints + " " + (record.get("keyword") or "")
    if ai_data and ai_data.get("company"):
        synth_record["company"] = str(ai_data["company"]).strip() or record.get("company")

    norm = _normalize(synth_record)
    norm = _apply_ai_overrides(norm, ai_data or {})

    title = norm["title"]
    dedupe_key = record["dedupe_key"]

    if test_batch_id:
        title = f"{test_label} {title}"
        dedupe_key = f"{dedupe_key}__test_{test_batch_id}"

    content_html = norm["content"]
    company = (synth_record.get("company") or record.get("company") or "").strip()
    application_target = _resolve_application_target(record)
    company_website = (record.get("company_website") or "").strip()
    clean_source_url = _clean_linkedin_url((record.get("url") or "").strip())

    # Inyecta nombre de empresa como <h2> al inicio del content — siempre visible
    # sin depender del theme. Sin emoji ni styling extra (causa rendering raro en Cariera).
    if company:
        company_header = f"<h2>{company}</h2>"
        if company_website:
            company_header += (
                f'\n<p><a href="{company_website}" target="_blank" rel="noopener">{company_website}</a></p>'
            )
        content_html = company_header + "\n" + content_html

    # Location: prefiere campos estructurados sobre la inferencia de IA si están
    location = (
        record.get("location_text")
        or norm.get("location")
        or ", ".join(filter(None, [record.get("city"), record.get("country")]))
        or ""
    )

    payload: dict[str, Any] = {
        "dedupe_key":   dedupe_key,
        "title":        title,
        "content":      content_html,
        "status":       (status or cfg.default_status),
        "company_name": company,
        "meta": {
            "_company_name":       company,
            "_company_website":    company_website,
            "_application":        application_target,
            "_job_location":       location,
            "_job_expires":        _expires_iso(record, cfg.expires_days),
            "_remote_position":    bool(record.get("is_remote")) if record.get("is_remote") is not None else norm["remote"],
            "_featured":           False,
            "_filled":             False,
            "_jobson_source_url":  clean_source_url,
            "_jobson_source_type": (record.get("source_type") or "").strip(),
        },
        # Slugs (no nombres) — el mu-plugin los buscará por slug y NO creará nuevos.
        "taxonomy_slugs": {
            "job-types":      norm["job_types"],
            "job-categories": norm["categories"],
        },
        "language": norm["language"],
        # Featured image: si el record trajo logo de la empresa, úsalo; si no,
        # cae a la imagen genérica por categoría (mu-plugin la sideloada 1 vez).
        "featured_image_url": (
            (record.get("company_logo_url") or "").strip()
            or pick_featured_image_url(norm["categories"], norm["language"])
        ),
    }
    if test_batch_id:
        payload["meta"]["_jobson_test_batch"] = test_batch_id
    return payload


class WPPublisher:
    def __init__(
        self,
        repository: BaseRepository,
        cfg: WPConfig,
        dry_run: bool = False,
        ai_normalizer: AINormalizer | None = None,
        ai_disabled: bool = False,
        ai_force: bool = False,
    ):
        self.repo = repository
        self.cfg = cfg
        self.dry_run = dry_run
        self.ai = ai_normalizer if not ai_disabled else None
        self.ai_force = ai_force
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "Content-Type": "application/json"})

    def _resolve_ai(self, record: dict[str, Any]) -> dict[str, Any] | None:
        """Devuelve dict normalizado por IA usando cache si está fresco. None si IA off o falla."""
        if self.ai is None:
            return None

        cached_json    = record.get("ai_normalized_json")
        cached_version = record.get("ai_prompt_version")
        if cached_json and cached_version == self.ai.prompt_version and not self.ai_force:
            try:
                return json.loads(cached_json)
            except (TypeError, json.JSONDecodeError):
                LOG.warning("Cache AI inválido para %s, reintentando…", record.get("dedupe_key"))

        fresh = self.ai.normalize(record)
        if fresh is None:
            return None

        try:
            self.repo.save_ai_normalization(
                dedupe_key=record["dedupe_key"],
                ai_json=json.dumps(fresh, ensure_ascii=False),
                prompt_version=self.ai.prompt_version,
            )
        except Exception:  # noqa: BLE001
            LOG.exception("No se pudo guardar cache AI para %s", record.get("dedupe_key"))
        return fresh

    def publish_one(
        self,
        record: dict[str, Any],
        status: str | None = None,
        test_batch_id: str | None = None,
    ) -> dict[str, Any]:
        ai_data = self._resolve_ai(record)
        payload = record_to_payload(
            record, self.cfg,
            status=status,
            test_batch_id=test_batch_id,
            ai_data=ai_data,
        )
        if self.dry_run:
            return {
                "action": "dry-run",
                "title":  payload["title"],
                "payload": payload,
                "ai_used": ai_data is not None,
            }

        resp = self.session.post(
            self.cfg.upsert_url,
            json=payload,
            auth=self.cfg.auth,
            timeout=30,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"WP upsert {resp.status_code} para dedupe_key={payload['dedupe_key']}: {resp.text[:400]}"
            )
        result = resp.json()
        result["title"] = payload["title"]
        result["ai_used"] = ai_data is not None
        return result

    def publish_batch(
        self,
        limit: int = 50,
        user_status_filter: str | None = "me_interesa",
        status: str | None = None,
        source_type: str | None = None,
        test_batch_id: str | None = None,
        review_status: str | None = None,
        wp_status: str | None = "pending",
    ) -> dict[str, Any]:
        records = self.repo.list_results(
            limit=limit,
            user_status=user_status_filter,
            source_type=source_type,
            only_followups=False,
            review_status=review_status,
            wp_status=wp_status,
        )
        # Excluye registros sin título (típicamente posts de feed sin estructura)
        records = [r for r in records if (r.get("title") or "").strip()]

        # Purga: filtra company-name contaminados (portal-as-company sin email
        # corporativo). Los descartados se marcan en Supabase para reproceso.
        discarded: list[dict[str, Any]] = []
        publishable: list[dict[str, Any]] = []
        for rec in records:
            co_clean, dom = clean_company(
                rec.get("company") or "",
                rec.get("apply_email") or "",
            )
            if not co_clean:
                discarded.append(rec)
                continue
            # Muta el record para que el resto del pipeline use el nombre limpio.
            rec["company"] = co_clean
            if dom and not (rec.get("company_website") or "").strip():
                rec["company_website"] = f"https://{dom}"
            publishable.append(rec)

        # Los descartados se skipean en memoria (no mutamos wp_status para no
        # perder historial de sync previo y mantenerlos elegibles cuando el
        # scraper se arregle y traiga un email corporativo). El conteo se
        # reporta en summary['discarded'].
        records = publishable
        if discarded:
            LOG.info(
                "publish_batch: %d records descartados por portal-leak (company=portal sin email corporativo)",
                len(discarded),
            )

        results: dict[str, Any] = {
            "total": len(records),
            "created": 0,
            "updated": 0,
            "discarded": len(discarded),
            "errors": [],
            "items": [],
            "test_batch_id": test_batch_id,
        }
        for rec in records:
            try:
                out = self.publish_one(rec, status=status, test_batch_id=test_batch_id)
            except Exception as exc:  # noqa: BLE001
                LOG.exception("Falló publicar %s", rec.get("dedupe_key"))
                # Marca el record como failed en la DB (no para test batches)
                if not test_batch_id and not self.dry_run:
                    try:
                        self.repo.mark_wp_failed(rec.get("dedupe_key"), str(exc))
                    except Exception:
                        LOG.exception("Tambien fallo mark_wp_failed")
                results["errors"].append({"dedupe_key": rec.get("dedupe_key"), "error": str(exc)})
                continue

            action = out.get("action")
            if action == "created":
                results["created"] += 1
            elif action == "updated":
                results["updated"] += 1

            # Marca wp_status=synced + review_status=published (no para test batches/dry-run)
            if not test_batch_id and not self.dry_run and out.get("id"):
                try:
                    self.repo.mark_wp_synced(
                        dedupe_key=rec["dedupe_key"],
                        post_id=int(out["id"]),
                        wp_url=out.get("link"),
                    )
                except Exception:
                    LOG.exception("Falló mark_wp_synced para %s", rec.get("dedupe_key"))

            results["items"].append({
                "dedupe_key": rec.get("dedupe_key"),
                "title":      out.get("title") or rec.get("title"),
                "action":     action,
                "link":       out.get("link"),
                "edit":       out.get("edit"),
                "id":         out.get("id"),
                "ai_used":    out.get("ai_used", False),
                "unmatched":  out.get("unmatched") or [],
            })

        return results

    def delete_post(self, post_id: int, force: bool = True) -> dict[str, Any]:
        url = f"{self.cfg.base_url.rstrip('/')}/wp-json/wp/v2/job-listings/{int(post_id)}"
        params = {"force": "true"} if force else {}
        resp = self.session.delete(url, auth=self.cfg.auth, params=params, timeout=30)
        if resp.status_code >= 400:
            raise RuntimeError(f"WP delete {resp.status_code} id={post_id}: {resp.text[:300]}")
        return resp.json()

    def cleanup_test_batch(self, manifest_items: list[dict[str, Any]], force: bool = True) -> dict[str, Any]:
        result = {"deleted": 0, "errors": []}
        for item in manifest_items:
            pid = item.get("id")
            if not pid:
                continue
            try:
                self.delete_post(int(pid), force=force)
                result["deleted"] += 1
            except Exception as exc:  # noqa: BLE001
                LOG.exception("Falló borrar post id=%s", pid)
                result["errors"].append({"id": pid, "error": str(exc)})
        return result
