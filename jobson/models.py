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
    base = "|".join(
        [
            _clean_text(record.get("source_type")),
            _clean_text(record.get("source_id")),
            _clean_text(record.get("url")),
            _clean_text(record.get("title")),
            _clean_text(record.get("company")),
            _clean_text(record.get("author")),
            _clean_text(record.get("content"))[:180],
        ]
    )
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def normalize_record(record: dict[str, Any], keyword: str, search_mode: str) -> dict[str, Any]:
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
        "keyword": _clean_text(keyword),
        "search_mode": _clean_text(search_mode),
        "scraped_at": _clean_text(record.get("scraped_at")) or now_iso(),
    }
    normalized["dedupe_key"] = build_dedupe_key(normalized)
    return normalized
