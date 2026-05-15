from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _clean_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def build_dedupe_key(record: dict[str, Any]) -> str:
    source_type = _clean_text(record.get("source_type"))
    source_id = _clean_text(record.get("source_id"))

    if source_type and source_id:
        base = f"{source_type}|{source_id}"
    else:
        base = "|".join(
            [
                source_type,
                source_id,
                _clean_text(record.get("url")),
                _clean_text(record.get("title")),
                _clean_text(record.get("company")),
                _clean_text(record.get("author")),
                _clean_text(record.get("content"))[:180],
            ]
        )
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def normalize_record(record: dict[str, Any], keyword: str, search_mode: str) -> dict[str, Any]:
    """Estandariza el shape de un record. Los campos de enrichment (geo, contact,
    job_type) deben llenarse ANTES de llamar esta función — aquí solo passthrough.
    """
    extracted_emails = record.get("extracted_emails") or []
    extracted_urls = record.get("extracted_urls") or []

    normalized = {
        "source_type": _clean_text(record.get("source_type")),
        "source_id": _clean_text(record.get("source_id")) or None,
        "title": _clean_text(record.get("title")) or None,
        "company": _clean_text(record.get("company")) or None,
        "author": _clean_text(record.get("author")) or None,
        "summary": _clean_text(record.get("summary")) or None,
        "content": _clean_text(record.get("content")) or None,
        "seniority": _clean_text(record.get("seniority")) or None,
        "apply_type": _clean_text(record.get("apply_type")) or None,
        "url": _clean_text(record.get("url")) or None,

        # Ubicación estructurada (de geo_extractor)
        "location_text": _clean_text(record.get("location_text")) or None,
        "city": _clean_text(record.get("city")) or None,
        "state": _clean_text(record.get("state")) or None,
        "country": _clean_text(record.get("country")) or None,
        "work_mode": _clean_text(record.get("work_mode")) or None,
        "is_remote": (
            bool(record.get("is_remote")) if record.get("is_remote") is not None else None
        ),
        "job_type_guess": _clean_text(record.get("job_type_guess")) or None,

        # Contacto (de contact_filter)
        "apply_email": _clean_text(record.get("apply_email")) or None,
        "apply_url_external": _clean_text(record.get("apply_url_external")) or None,
        "company_website": _clean_text(record.get("company_website")) or None,
        "extracted_emails": list(extracted_emails) if extracted_emails else [],
        "extracted_urls": list(extracted_urls) if extracted_urls else [],
        "contact_status": _clean_text(record.get("contact_status")) or "sin_contacto",

        # Pipeline técnico WP (independiente de review_status humano)
        "wp_status": _clean_text(record.get("wp_status")) or "skipped",
        "wp_last_error": _clean_text(record.get("wp_last_error")) or None,

        # Búsqueda + tiempos
        "keyword": _clean_text(keyword),
        "search_mode": _clean_text(search_mode),
        "scraped_at": _clean_text(record.get("scraped_at")) or now_iso(),
    }
    normalized["dedupe_key"] = build_dedupe_key(normalized)
    return normalized
