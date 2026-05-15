from __future__ import annotations

import html
import logging
import re
import time
from datetime import UTC, datetime
from typing import Any, Callable
from urllib.parse import quote_plus, urljoin

import requests

from jobson.cloudflare_email import decode_cfemail

logger = logging.getLogger(__name__)

BASE_URL = "https://tuportalempleo.com"
LISTING_BASE = f"{BASE_URL}/buscar-ofertas-de-empleo-actuales"
SEARCH_BASE = f"{BASE_URL}/buscar-ofertas-de-empleo"
DETAIL_PREFIX = f"{BASE_URL}/ofertas-de-empleo/"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)

# Patrones HTML del portal (todos verificados en muestras reales)
CARD_RE = re.compile(
    r'<div class="oferta">\s*<a\s+href="([^"]+)">(.*?)</a>\s*</div>',
    re.DOTALL,
)
TITLE_RE = re.compile(r"<h3[^>]*>(.*?)</h3>", re.DOTALL)
SHORT_DESC_RE = re.compile(r'<span class="no_movil"[^>]*>(.*?)</span>', re.DOTALL)
ICON_ROW_RE = re.compile(
    r'<div class="b">\s*<img[^>]*icon/([a-z0-9_-]+)\.png[^>]*>\s*</div>\s*<div class="b">([^<]*)<',
    re.DOTALL,
)
ID_FROM_URL_RE = re.compile(r"/(\d+)/?$")
H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.DOTALL)
INPUT_READONLY_RE = re.compile(
    r'<input[^>]*value="([^"]+)"[^>]*readonly[^>]*>',
    re.IGNORECASE,
)
TITLE_ATTR_RE = re.compile(r'title="([^"]+:[^"]+)"')
CFEMAIL_RE = re.compile(r'data-cfemail="([0-9a-fA-F]+)"')
PLAIN_EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")

# Mapeo de "Duración del contrato" del portal → job_type_guess estándar
_TPE_JOB_TYPE_MAP = {
    "tiempo completo": "full-time",
    "medio tiempo": "part-time",
    "horas extras": "part-time",
    "por horas": "part-time",
    "pasantia": "internship",
    "pasantía": "internship",
    "contrato": "contract",
    "freelance": "contract",
    "honorarios": "contract",
    "temporal": "temporary",
    "eventual": "temporary",
}


def _strip_html(value: str) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_id(url: str) -> str:
    if not url:
        return ""
    m = ID_FROM_URL_RE.search(url.split("?")[0])
    return m.group(1) if m else ""


def _normalize_job_type(raw: str) -> str | None:
    if not raw:
        return None
    lowered = raw.strip().lower()
    for key, mapped in _TPE_JOB_TYPE_MAP.items():
        if key in lowered:
            return mapped
    return None


def _parse_card(href: str, body: str) -> dict[str, Any]:
    """Parse one search-result card. Returns minimal record."""
    title = _strip_html((TITLE_RE.search(body) or [None, ""])[1] if TITLE_RE.search(body) else "")
    short_desc_m = SHORT_DESC_RE.search(body)
    short_desc = _strip_html(short_desc_m.group(1)) if short_desc_m else ""

    location = ""
    publication = ""
    category = ""
    for icon, value in ICON_ROW_RE.findall(body):
        v = value.strip()
        if not v:
            continue
        if "calendario" in icon:
            publication = v
        elif "location" in icon:
            location = v
        elif "categoria" in icon:
            category = v

    return {
        "url": href,
        "source_id": _parse_id(href),
        "title": title,
        "short_description": short_desc,
        "location_text": location or None,
        "publication_label": publication or None,
        "category_label": category or None,
    }


class TuPortalEmpleoScraper:
    """Scraper de tuportalempleo.com (Ecuador, sin login).

    Async API to keep contract compatible with LinkedInScraper. Internally
    uses `requests` + regex; we don't need a real browser here.
    """

    def __init__(self, rate_limit_seconds: float = 1.5):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "es-EC,es;q=0.9,en;q=0.8",
        })
        self.rate_limit_seconds = max(0.0, float(rate_limit_seconds))
        self._last_request_at = 0.0

    # ---------------------------------------------------------------- public

    async def scrape_jobs(
        self,
        keywords: str,
        limit: int,
        antiquity_days: int | None = None,
        on_record: Callable[[dict[str, Any]], Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Async wrapper. The blocking work runs in a thread."""
        import asyncio
        return await asyncio.to_thread(
            self._scrape_jobs_sync, keywords, limit, antiquity_days, on_record
        )

    async def scrape_posts(self, *args, **kwargs) -> list[dict[str, Any]]:
        # TPE doesn't have a "feed" concept. Always empty for compatibility.
        return []

    async def scrape_mixed(self, *args, **kwargs) -> list[dict[str, Any]]:
        return await self.scrape_jobs(*args, **kwargs)

    # --------------------------------------------------------------- private

    def _wait_rate_limit(self) -> None:
        if self.rate_limit_seconds <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.rate_limit_seconds:
            time.sleep(self.rate_limit_seconds - elapsed)
        self._last_request_at = time.monotonic()

    def _get(self, url: str) -> str:
        self._wait_rate_limit()
        try:
            response = self.session.get(url, timeout=30, allow_redirects=True)
            response.raise_for_status()
            return response.text
        except Exception as exc:
            logger.warning("TPE GET fallo en %s: %s", url, exc)
            return ""

    def _build_search_url(self, keyword: str) -> str:
        kw = (keyword or "").strip()
        if not kw:
            # Modo masivo: barre el listado completo de empleos vigentes.
            return LISTING_BASE
        return f"{SEARCH_BASE}?buscar={quote_plus(kw)}"

    def _iter_listing_pages(self, base_url: str, max_pages: int = 20):
        """Yield HTML of consecutive listing pages until empty or max_pages."""
        for page in range(1, max_pages + 1):
            url = base_url if page == 1 else f"{base_url.rstrip('/')}/{page}"
            html_text = self._get(url)
            if not html_text:
                return
            yield html_text, page

    def _parse_listing_cards(self, html_text: str) -> list[dict[str, Any]]:
        cards = []
        for href, body in CARD_RE.findall(html_text):
            href = href.strip()
            if not href.startswith("http"):
                href = urljoin(BASE_URL, href)
            if not href.startswith(DETAIL_PREFIX):
                continue
            cards.append(_parse_card(href, body))
        return cards

    def _parse_publication_date(self, raw: str | None) -> datetime | None:
        if not raw:
            return None
        raw = raw.strip()
        # Formato visto: "2026-05-04 22:42:01"
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
            except ValueError:
                continue
        return None

    def _guess_company_from_body(self, body_text: str, emails: list[str]) -> str | None:
        """Heurística para encontrar la empresa real del aviso.

        Orden:
        1. Patrones explícitos: "Empresa: X", "Razón social: X", "Compañía: X".
        2. Dominio del primer email (foo@empresa.com → "empresa.com" → "empresa").
        """
        if body_text:
            patterns = [
                r"empresa\s*:\s*([A-ZÁÉÍÓÚÑ][^\n.|]{2,80})",
                r"razón\s*social\s*:\s*([A-ZÁÉÍÓÚÑ][^\n.|]{2,80})",
                r"razon\s*social\s*:\s*([A-ZÁÉÍÓÚÑ][^\n.|]{2,80})",
                r"compañía\s*:\s*([A-ZÁÉÍÓÚÑ][^\n.|]{2,80})",
                r"compania\s*:\s*([A-ZÁÉÍÓÚÑ][^\n.|]{2,80})",
                r"organización\s*:\s*([A-ZÁÉÍÓÚÑ][^\n.|]{2,80})",
            ]
            for pat in patterns:
                m = re.search(pat, body_text, re.IGNORECASE)
                if m:
                    name = m.group(1).strip(" \t-.,:;")
                    if name and len(name) > 2:
                        return name

        # Fallback: derivar del dominio del primer email "corporativo".
        # Ignoramos hotmail/gmail/yahoo y otros free-mailers.
        FREE_MAILERS = {
            "gmail.com", "hotmail.com", "yahoo.com", "outlook.com",
            "live.com", "icloud.com", "aol.com", "proton.me",
            "protonmail.com", "yahoo.com.mx", "yahoo.es",
        }
        for email in emails:
            domain = email.split("@", 1)[-1].lower()
            if domain in FREE_MAILERS:
                continue
            # foo@careers.empresa.com.mx → "empresa"
            parts = domain.split(".")
            if len(parts) >= 2:
                # Saltar TLD compuesto (.com.mx, .com.ec): nos quedamos con la
                # parte más a la izquierda no genérica.
                generic = {"com", "net", "org", "edu", "gob", "co", "ec", "mx"}
                for piece in parts:
                    if piece not in generic and len(piece) > 2:
                        return piece.capitalize()
        return None

    def _enrich_with_detail(self, record: dict[str, Any]) -> None:
        url = record.get("url")
        if not url:
            return
        html_text = self._get(url)
        if not html_text:
            return

        # Título preciso desde H1
        h1_match = H1_RE.search(html_text)
        if h1_match:
            h1_text = _strip_html(h1_match.group(1))
            if h1_text:
                record["title"] = h1_text

        # Metadatos como title="Label: Valor" e inputs readonly correlativos.
        meta_pairs: dict[str, str] = {}
        for m in TITLE_ATTR_RE.finditer(html_text):
            label_value = m.group(1)
            if ":" not in label_value:
                continue
            label, value = label_value.split(":", 1)
            meta_pairs[label.strip().lower()] = value.strip()

        loc = meta_pairs.get("lugar")
        if loc:
            record["location_text"] = loc
        salary = meta_pairs.get("salario")
        if salary:
            record["salary_text"] = salary
        sector = meta_pairs.get("sector empresarial")
        if sector:
            record["sector"] = sector
        category = meta_pairs.get("categoria laboral") or meta_pairs.get("categoría laboral")
        if category:
            record["category_label"] = category
        contract = meta_pairs.get("duración del contrato") or meta_pairs.get("duracion del contrato")
        if contract:
            record["job_type_guess"] = _normalize_job_type(contract)
            record["contract_label"] = contract
        tenure = meta_pairs.get("tiempo laboral")
        if tenure:
            record["tenure_label"] = tenure
        pub_date = meta_pairs.get("fecha de publicación actualizada") or meta_pairs.get(
            "fecha de publicacion actualizada"
        )
        if pub_date:
            record["publication_date"] = pub_date

        # Descripción / cuerpo del aviso → todo el texto entre H1 y el botón Postúlate
        body_match = re.search(
            r"<h1[^>]*>.*?</h1>(.*?)(?:Postúlate|Postulate|<button id=\"aplicar\")",
            html_text,
            re.DOTALL,
        )
        body_text = ""
        if body_match:
            body_text = _strip_html(body_match.group(1))

        # Emails: visibles en HTML + decodificados de Cloudflare
        emails: list[str] = []
        seen_emails: set[str] = set()
        for raw_email in PLAIN_EMAIL_RE.findall(html_text):
            email = raw_email.strip().rstrip(".,;:")
            lowered = email.lower()
            if lowered in seen_emails:
                continue
            seen_emails.add(lowered)
            emails.append(email)
        for cf_hex in CFEMAIL_RE.findall(html_text):
            decoded = decode_cfemail(cf_hex)
            if not decoded:
                continue
            lowered = decoded.lower()
            if lowered in seen_emails:
                continue
            seen_emails.add(lowered)
            emails.append(decoded)

        # Construye el content que el filtro de contacto va a procesar
        content_parts = []
        if body_text:
            content_parts.append(body_text)
        for label_key, label_human in (
            ("salary_text", "Salario"),
            ("sector", "Sector"),
            ("category_label", "Categoría"),
            ("contract_label", "Contrato"),
            ("tenure_label", "Tiempo laboral"),
            ("location_text", "Lugar"),
        ):
            if record.get(label_key):
                content_parts.append(f"{label_human}: {record[label_key]}")
        if emails:
            # Importante: incluir emails como texto plano para que el
            # contact_filter los detecte aunque Cloudflare los oculte.
            content_parts.append("Contacto: " + ", ".join(emails))

        full_content = "\n".join(content_parts).strip()
        record["content"] = full_content
        summary = full_content[:260] + ("..." if len(full_content) > 260 else "")
        record["summary"] = summary
        record["_raw_emails"] = emails

        # Empresa real: si la encontramos, sobrescribimos el publisher genérico.
        guessed_company = self._guess_company_from_body(body_text, emails)
        if guessed_company:
            record["company"] = guessed_company

    def _scrape_jobs_sync(
        self,
        keywords: str,
        limit: int,
        antiquity_days: int | None,
        on_record: Callable[[dict[str, Any]], Any] | None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        base_url = self._build_search_url(keywords)
        results: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        cutoff: datetime | None = None
        if antiquity_days and antiquity_days > 0:
            cutoff = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            from datetime import timedelta
            cutoff = cutoff - timedelta(days=antiquity_days)

        for html_text, page_num in self._iter_listing_pages(base_url, max_pages=20):
            cards = self._parse_listing_cards(html_text)
            if not cards:
                logger.info("TPE: sin más cards en page=%d → fin", page_num)
                break

            for card in cards:
                if len(results) >= limit:
                    break

                source_id = card.get("source_id")
                if not source_id or source_id in seen_ids:
                    continue
                seen_ids.add(source_id)

                # Inicializa shape estándar para que el resto del pipeline
                # (filtro contacto + repo) lo trate como el resto.
                record: dict[str, Any] = {
                    "source_type": "tpe",
                    "source_id": source_id,
                    "title": card.get("title") or "Sin título",
                    "company": "TuPortalEmpleo Ecuador",
                    "author": "",
                    "summary": card.get("short_description") or "",
                    "content": card.get("short_description") or "",
                    "seniority": None,
                    "apply_type": "Email",
                    "url": card.get("url"),
                    "location_text": card.get("location_text"),
                    "scraped_at": datetime.now(UTC).isoformat(),
                }

                # Enriquecer con el detalle (rate-limited).
                self._enrich_with_detail(record)

                # Filtro de antigüedad (requiere haber leído publication_date)
                if cutoff:
                    pub_dt = self._parse_publication_date(record.get("publication_date"))
                    if pub_dt and pub_dt < cutoff:
                        continue

                results.append(record)
                if on_record is not None:
                    try:
                        on_record(record)
                    except Exception:
                        pass

            if len(results) >= limit:
                break

        return results
