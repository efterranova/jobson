from __future__ import annotations

import csv
import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

try:
    from langdetect import DetectorFactory, LangDetectException, detect

    DetectorFactory.seed = 0
except Exception:  # pragma: no cover
    DetectorFactory = None
    LangDetectException = Exception
    detect = None

from jobson.contact_filter import (
    DEFAULT_PORTAL_BLACKLIST,
    enrich_record_with_contact,
)
from jobson.geo_extractor import extract_geo
from jobson.job_type_classifier import classify_job_type, detect_remote
from jobson.storage.base import BaseRepository

VALID_SOURCES = ("linkedin", "tpe", "jobbank")
VALID_LINKEDIN_MODES = ("jobs", "feed", "mixed")


class SearchService:
    _LANGUAGE_ALIASES = {
        "es": "es", "spa": "es", "spanish": "es", "espanol": "es", "español": "es",
        "en": "en", "eng": "en", "english": "en", "ingles": "en", "inglés": "en",
        "pt": "pt", "por": "pt", "portuguese": "pt", "portugues": "pt", "português": "pt",
        "fr": "fr", "fre": "fr", "french": "fr", "frances": "fr", "francés": "fr",
        "de": "de", "ger": "de", "german": "de", "aleman": "de", "alemán": "de",
        "it": "it", "ita": "it", "italian": "it", "italiano": "it",
    }

    _FALLBACK_STOPWORDS: dict[str, set[str]] = {
        "es": {"de", "la", "el", "que", "y", "en", "con", "para", "remoto", "trabajo", "años"},
        "en": {"the", "and", "with", "for", "remote", "experience", "years", "role", "job"},
        "pt": {"de", "que", "com", "para", "trabalho", "remoto", "anos", "vaga"},
        "fr": {"de", "la", "et", "avec", "pour", "travail", "ans", "poste"},
        "de": {"der", "die", "und", "mit", "fur", "für", "arbeit", "jahre", "stelle"},
        "it": {"di", "che", "con", "per", "lavoro", "anni", "ruolo", "posizione"},
    }

    def __init__(
        self,
        scrapers: dict[str, Any],
        repository: BaseRepository,
        data_dir: Path,
        portals_blacklist: tuple[str, ...] | list[str] = DEFAULT_PORTAL_BLACKLIST,
    ):
        self.scrapers = scrapers
        self.repository = repository
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.portals_blacklist = tuple(portals_blacklist) or DEFAULT_PORTAL_BLACKLIST

    # ------------------------------------------------------------------ utils

    def _save_csv(self, records: list[dict[str, Any]], label: str, keywords: str) -> str | None:
        if not records:
            return None
        safe_keyword = re.sub(r"[^a-zA-Z0-9]+", "_", keywords).strip("_")[:40] or "busqueda"
        stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
        path = self.data_dir / f"{label}_jobs_wp_{safe_keyword}_{stamp}.csv"
        fieldnames = [
            "source_type", "source_id", "title", "company", "author",
            "location_text", "is_remote", "job_type_guess",
            "apply_email", "apply_url_external", "company_website",
            "contact_status", "wp_status",
            "seniority", "apply_type", "url", "scraped_at",
        ]
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(records)
        return str(path)

    def _strip_accents(self, value: str) -> str:
        normalized = unicodedata.normalize("NFKD", value)
        return "".join(ch for ch in normalized if not unicodedata.combining(ch))

    def _split_csv_tokens(self, value: str) -> list[str]:
        return [token.strip() for token in re.split(r"[,\n;|]+", value) if token and token.strip()]

    def _normalize_keywords(self, keywords: str | list[str] | None) -> list[str]:
        """Multi-keyword: separar por coma | salto | punto y coma | pipe."""
        if not keywords:
            return []
        raw_items: list[str] = []
        if isinstance(keywords, str):
            raw_items = self._split_csv_tokens(keywords)
        else:
            for item in keywords:
                if item is None:
                    continue
                raw_items.extend(self._split_csv_tokens(str(item)))
        seen: set[str] = set()
        out: list[str] = []
        for item in raw_items:
            normalized = re.sub(r"\s+", " ", item).strip()
            if not normalized:
                continue
            key = normalized.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(normalized)
        return out

    def _normalize_sources(self, sources: list[str] | str | None) -> list[str]:
        if not sources:
            return ["linkedin"]
        if isinstance(sources, str):
            sources = self._split_csv_tokens(sources)
        out: list[str] = []
        for s in sources:
            key = (s or "").strip().lower()
            if key in VALID_SOURCES and key not in out:
                out.append(key)
        return out or ["linkedin"]

    def _normalize_exclude_keywords(self, exclude_keywords: str | list[str] | None) -> list[str]:
        if exclude_keywords is None:
            return []
        raw_items: list[str] = []
        if isinstance(exclude_keywords, str):
            raw_items = self._split_csv_tokens(exclude_keywords)
        else:
            for item in exclude_keywords:
                if item is None:
                    continue
                raw_items.extend(self._split_csv_tokens(str(item)))
        normalized: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            token = re.sub(r"\s+", " ", item).strip().lower()
            if not token or token in seen:
                continue
            seen.add(token)
            normalized.append(token)
        return normalized

    def _normalize_languages(self, languages: str | list[str] | None) -> list[str]:
        if languages is None:
            return []
        raw_items: list[str] = []
        if isinstance(languages, str):
            raw_items = self._split_csv_tokens(languages)
        else:
            for item in languages:
                if item is None:
                    continue
                raw_items.extend(self._split_csv_tokens(str(item)))
        normalized: list[str] = []
        seen: set[str] = set()
        for item in raw_items:
            token = item.strip().lower()
            if not token:
                continue
            simple = self._strip_accents(token)
            if simple in self._LANGUAGE_ALIASES:
                code = self._LANGUAGE_ALIASES[simple]
            elif re.fullmatch(r"[a-z]{2}", simple):
                code = simple
            elif re.fullmatch(r"[a-z]{2}[-_][a-z]{2}", simple):
                code = simple[:2]
            else:
                continue
            if code in seen:
                continue
            seen.add(code)
            normalized.append(code)
        return normalized

    def _record_text_blob(self, record: dict[str, Any]) -> str:
        return " ".join(
            [
                str(record.get("title") or ""),
                str(record.get("company") or ""),
                str(record.get("author") or ""),
                str(record.get("summary") or ""),
                str(record.get("content") or ""),
            ]
        ).strip()

    def _detect_language_fallback(self, text: str) -> str | None:
        cleaned = self._strip_accents(text.lower())
        words = re.findall(r"[a-z]{2,}", cleaned)
        if not words:
            return None
        scores: dict[str, int] = {}
        for lang, stopwords in self._FALLBACK_STOPWORDS.items():
            score = sum(1 for word in words if word in stopwords)
            if score:
                scores[lang] = score
        if not scores:
            return None
        winner = max(scores, key=scores.get)
        if scores[winner] < 2:
            return None
        return winner

    def _detect_record_language(self, record: dict[str, Any]) -> str | None:
        text = self._record_text_blob(record)
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 25:
            return None
        if detect is not None:
            try:
                detected = str(detect(text[:5000])).lower().strip()
                if re.fullmatch(r"[a-z]{2}", detected):
                    return detected
                if re.fullmatch(r"[a-z]{2}[-_][a-z]{2}", detected):
                    return detected[:2]
            except LangDetectException:
                pass
            except Exception:
                pass
        return self._detect_language_fallback(text)

    def _evaluate_record_filters(
        self,
        record: dict[str, Any],
        exclude_keywords: list[str],
        allowed_languages: list[str],
    ) -> tuple[bool, str | None]:
        blob = self._record_text_blob(record).lower()
        if exclude_keywords and blob:
            for term in exclude_keywords:
                if term in blob:
                    return False, "excluded_keywords"
        if allowed_languages:
            detected = self._detect_record_language(record)
            if not detected or detected not in allowed_languages:
                return False, "language"
        return True, None

    def _enrich_for_wpjm(self, record: dict[str, Any]) -> dict[str, Any]:
        title = str(record.get("title") or "")
        content = str(record.get("content") or "")
        summary = str(record.get("summary") or "")
        location = str(record.get("location_text") or "")
        if record.get("job_type_guess") is None:
            record["job_type_guess"] = classify_job_type(title, content, summary)
        if record.get("is_remote") is None:
            record["is_remote"] = detect_remote(title, content, summary, location)
        # Geo: ciudad, provincia, país, modalidad de trabajo.
        geo = extract_geo(location, content, record.get("source_type"))
        for k in ("city", "state", "country", "work_mode"):
            if record.get(k) is None and geo.get(k):
                record[k] = geo[k]
        # Sincronizar is_remote con work_mode si nos vino remoto explícito.
        if record.get("work_mode") == "remoto" and not record.get("is_remote"):
            record["is_remote"] = True
        enrich_record_with_contact(record, blacklist=self.portals_blacklist)
        return record

    def _count_pipeline(self, records: list[dict[str, Any]]) -> dict[str, int]:
        return {
            "wp_pending": sum(1 for r in records if r.get("wp_status") == "pending"),
            "wp_skipped": sum(1 for r in records if r.get("wp_status") == "skipped"),
            "tiene_email": sum(1 for r in records if r.get("contact_status") == "tiene_email"),
            "tiene_web": sum(1 for r in records if r.get("contact_status") == "tiene_web"),
            "tiene_ambos": sum(1 for r in records if r.get("contact_status") == "tiene_ambos"),
            "sin_contacto": sum(1 for r in records if r.get("contact_status") == "sin_contacto"),
        }

    def _count_per_source(self, records: list[dict[str, Any]]) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in records:
            st = r.get("source_type") or "unknown"
            out[st] = out.get(st, 0) + 1
        return out

    # ----------------------------------------------------------------- search

    async def _scrape_one_source_keyword(
        self,
        source: str,
        keyword: str,
        limit: int,
        days: int | None,
        linkedin_mode: str,
        on_record,
        location: str | None = None,
    ) -> list[dict[str, Any]]:
        scraper = self.scrapers.get(source)
        if scraper is None:
            return []

        if source == "linkedin":
            mode = linkedin_mode if linkedin_mode in VALID_LINKEDIN_MODES else "mixed"
            if mode == "jobs":
                return await scraper.scrape_jobs(keyword, limit, days, on_record=on_record, location=location)
            if mode == "feed":
                return await scraper.scrape_posts(keyword, limit, days, on_record=on_record)
            return await scraper.scrape_mixed(keyword, limit, days, on_record=on_record, location=location)

        if source == "tpe":
            return await scraper.scrape_jobs(keyword, limit, days, on_record=on_record)

        if source == "jobbank":
            return await scraper.scrape_jobs(
                keyword, limit, days, on_record=on_record, location=location
            )

        return []

    async def run_search(
        self,
        keywords: str | list[str],
        limit: int,
        days: int | None,
        sources: list[str] | None = None,
        linkedin_mode: str = "mixed",
        exclude_keywords: str | list[str] | None = None,
        languages: str | list[str] | None = None,
        progress_callback=None,
        location: str | None = None,
    ) -> dict[str, Any]:
        normalized_sources = self._normalize_sources(sources)
        normalized_keywords = self._normalize_keywords(keywords)
        if not normalized_keywords:
            # Sin keywords: solo TPE puede correr (modo masivo del listado).
            # LinkedIn requiere keyword sí o sí.
            if "linkedin" in normalized_sources and "tpe" not in normalized_sources:
                raise ValueError(
                    "LinkedIn requiere palabras clave. Quita LinkedIn de las "
                    "fuentes o escribe al menos una keyword."
                )
            normalized_keywords = [""]  # señal de scrape masivo para TPE
        normalized_excludes = self._normalize_exclude_keywords(exclude_keywords)
        normalized_languages = self._normalize_languages(languages)

        records: list[dict[str, Any]] = []
        seen_dedupe: set[str] = set()

        # Counters
        scraped_total = 0
        scraped_by_source: dict[str, int] = {}
        matched_by_source: dict[str, int] = {}
        filtered_excluded = 0
        filtered_language = 0
        persisted_received = 0
        persisted_inserted = 0
        persisted_updated = 0

        def emit_progress(last_record: dict[str, Any] | None = None) -> None:
            if progress_callback is None:
                return
            title = ""
            if last_record:
                title = (
                    last_record.get("title")
                    or last_record.get("author")
                    or last_record.get("company")
                    or ""
                )
            title = re.sub(r"\s+", " ", title).strip()
            progress_callback(
                {
                    "scraped_total": scraped_total,
                    "scraped_by_source": dict(scraped_by_source),
                    "matched_total": len(records),
                    "matched_by_source": dict(matched_by_source),
                    "filtered_out": {
                        "excluded_keywords": filtered_excluded,
                        "language": filtered_language,
                    },
                    "persisted": {
                        "received": persisted_received,
                        "inserted": persisted_inserted,
                        "updated": persisted_updated,
                    },
                    "pipeline": self._count_pipeline(records),
                    "criteria": {
                        "exclude_keywords": normalized_excludes,
                        "languages": normalized_languages,
                        "sources": normalized_sources,
                        "keywords": normalized_keywords,
                        "linkedin_mode": linkedin_mode,
                    },
                    "last_item": title[:120],
                }
            )

        import logging as _logging
        _log = _logging.getLogger(__name__)

        def make_handler(source: str, keyword: str):
            def handle_record(record: dict[str, Any]) -> None:
                nonlocal scraped_total, filtered_excluded, filtered_language
                nonlocal persisted_received, persisted_inserted, persisted_updated

                try:
                    scraped_total += 1
                    scraped_by_source[source] = scraped_by_source.get(source, 0) + 1

                    keep, reason = self._evaluate_record_filters(
                        record=record,
                        exclude_keywords=normalized_excludes,
                        allowed_languages=normalized_languages,
                    )
                    if not keep:
                        if reason == "excluded_keywords":
                            filtered_excluded += 1
                        elif reason == "language":
                            filtered_language += 1
                        emit_progress(record)
                        return

                    try:
                        self._enrich_for_wpjm(record)
                    except Exception:
                        _log.exception("enrich falló (record continúa con campos vacíos)")

                    key = f"{record.get('source_type')}|{record.get('source_id')}"
                    if key in seen_dedupe:
                        emit_progress(record)
                        return
                    seen_dedupe.add(key)

                    records.append(record)
                    matched_by_source[source] = matched_by_source.get(source, 0) + 1

                    if progress_callback is not None:
                        try:
                            persistence = self.repository.upsert_results(
                                [record], keyword=keyword, search_mode=source
                            )
                            persisted_received += persistence.get("received", 0)
                            persisted_inserted += persistence.get("inserted", 0)
                            persisted_updated += persistence.get("updated", 0)
                        except Exception:
                            _log.exception("upsert falló para %s", record.get("source_id"))
                        emit_progress(record)
                except Exception:
                    # Cualquier fallo en el handler NO debe abortar el scraper
                    _log.exception("handle_record falló para %s", record.get("source_id"))

            return handle_record

        # Loop principal: por keyword × fuente
        for keyword in normalized_keywords:
            for source in normalized_sources:
                # Sin keyword + LinkedIn: lo saltamos (LinkedIn lo requiere).
                if not keyword and source == "linkedin":
                    continue
                handler = make_handler(source, keyword) if progress_callback is not None else None
                try:
                    scraped = await self._scrape_one_source_keyword(
                        source=source,
                        keyword=keyword,
                        limit=limit,
                        days=days,
                        linkedin_mode=linkedin_mode,
                        on_record=handler,
                        location=location,
                    )
                except Exception as exc:  # pragma: no cover
                    # No abortar: si una fuente falla, seguimos con la otra.
                    import logging
                    logging.getLogger(__name__).exception(
                        "Falló scraping de %s con keyword %r: %s", source, keyword, exc
                    )
                    continue

                # Modo sin progress_callback: el scraper devolvió todo de una.
                if progress_callback is None:
                    for record in scraped:
                        scraped_total += 1
                        scraped_by_source[source] = scraped_by_source.get(source, 0) + 1
                        keep, reason = self._evaluate_record_filters(
                            record=record,
                            exclude_keywords=normalized_excludes,
                            allowed_languages=normalized_languages,
                        )
                        if not keep:
                            if reason == "excluded_keywords":
                                filtered_excluded += 1
                            elif reason == "language":
                                filtered_language += 1
                            continue
                        self._enrich_for_wpjm(record)
                        key = f"{record.get('source_type')}|{record.get('source_id')}"
                        if key in seen_dedupe:
                            continue
                        seen_dedupe.add(key)
                        records.append(record)
                        matched_by_source[source] = matched_by_source.get(source, 0) + 1

        # Persistencia final si no hubo callback en vivo
        if progress_callback is None:
            persistence = self.repository.upsert_results(
                records, keyword=", ".join(normalized_keywords), search_mode=",".join(normalized_sources)
            )
            persisted_received = persistence.get("received", 0)
            persisted_inserted = persistence.get("inserted", 0)
            persisted_updated = persistence.get("updated", 0)

        csv_label = "_".join(normalized_sources)
        csv_path = self._save_csv(records, label=csv_label, keywords=", ".join(normalized_keywords))
        pipeline = self._count_pipeline(records)

        return {
            "sources": normalized_sources,
            "linkedin_mode": linkedin_mode,
            "keywords": normalized_keywords,
            "exclude_keywords": normalized_excludes,
            "languages": normalized_languages,
            "limit": limit,
            "days": days,
            "scraped_total": scraped_total,
            "scraped_by_source": scraped_by_source,
            "matched_total": len(records),
            "matched_by_source": matched_by_source,
            "filtered_out": {
                "excluded_keywords": filtered_excluded,
                "language": filtered_language,
            },
            "persisted": {
                "received": persisted_received,
                "inserted": persisted_inserted,
                "updated": persisted_updated,
            },
            "pipeline": pipeline,
            "csv_path": csv_path,
            "storage_backend": self.repository.backend_name,
        }
