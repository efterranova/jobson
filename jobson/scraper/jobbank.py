from __future__ import annotations

import html
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any, Callable
from urllib.parse import quote_plus

import requests

logger = logging.getLogger(__name__)

BASE_URL = "https://www.jobbank.gc.ca"
SEARCH_URL = f"{BASE_URL}/jobsearch/jobsearch"
POSTING_BASE = f"{BASE_URL}/jobsearch/jobposting"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"
)

# --- Patrones del listado de resultados (verificados en HTML real) ---
ARTICLE_RE = re.compile(r'<article id="article-(\d+)".*?</article>', re.DOTALL)
NOCTITLE_RE = re.compile(r'<span class="noctitle">\s*(.*?)\s*</span>', re.DOTALL)
BUSINESS_RE = re.compile(r'<li class="business">\s*(.*?)\s*</li>', re.DOTALL)
LOCATION_RE = re.compile(r'<li class="location">(.*?)</li>', re.DOTALL)
# "Location" es un label sólo-lectores (<span class="wb-inv">); lo removemos.
LOCATION_LABEL_RE = re.compile(r"^location\s+", re.IGNORECASE)
DATE_RE = re.compile(r'<li class="date">\s*(.*?)\s*</li>', re.DOTALL)
SALARY_RE = re.compile(r'<li class="salary">(.*?)</li>', re.DOTALL)
TELEWORK_RE = re.compile(r'<span class="telework">\s*(.*?)\s*</span>', re.DOTALL)

# --- Patrones del detalle del aviso ---
H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.DOTALL)
DESCRIPTION_RE = re.compile(
    r'property="description"[^>]*>(.*?)</div>', re.DOTALL
)
APPLY_METHOD_RE = re.compile(r"<h4>\s*By\s+([^<\n]+)", re.IGNORECASE)
EMAIL_RE = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
EXTERNAL_URL_RE = re.compile(r'https?://[^"\'<>\s)]+', re.IGNORECASE)


def _strip_html(value: str) -> str:
    if not value:
        return ""
    text = re.sub(r"<[^>]+>", " ", value)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


class JobBankScraper:
    """Scraper de jobbank.gc.ca (Job Bank, gobierno de Canadá — sin login).

    Solo capturamos avisos POSTEADOS DIRECTAMENTE en Job Bank (marca
    `postedonJB` en la card). Esos son los que exponen el método de aplicación
    real (email corporativo, web de la empresa, teléfono) detrás de un botón
    "Show how to apply" que se revela con un POST JSF (ViewState stateless).
    Los avisos agregados (CareerBeacon, Talent.com, etc.) solo enlazan a
    terceros y no aportan contacto, así que los descartamos.

    API async para mantener el contrato compatible con LinkedInScraper/TPE.
    Internamente usa `requests` + regex; no necesita navegador real.
    """

    def __init__(self, rate_limit_seconds: float = 1.2):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-CA,en;q=0.9,fr-CA;q=0.7",
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
        location: str | None = None,
    ) -> list[dict[str, Any]]:
        """Async wrapper. El trabajo bloqueante corre en un thread."""
        import asyncio
        return await asyncio.to_thread(
            self._scrape_jobs_sync, keywords, limit, antiquity_days, on_record, location
        )

    async def scrape_posts(self, *args, **kwargs) -> list[dict[str, Any]]:
        # Job Bank no tiene concepto de "feed". Siempre vacío por compatibilidad.
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

    def _get(self, url: str, params: dict[str, Any] | None = None) -> str:
        self._wait_rate_limit()
        try:
            response = self.session.get(url, params=params, timeout=30, allow_redirects=True)
            response.raise_for_status()
            return response.text
        except Exception as exc:
            logger.warning("JobBank GET falló en %s: %s", url, exc)
            return ""

    def _build_search_params(self, keyword: str, location: str | None, page: int) -> dict[str, str]:
        params = {"sort": "M", "page": str(page)}  # sort=M → más recientes primero
        kw = (keyword or "").strip()
        if kw:
            params["searchstring"] = kw
        loc = (location or "").strip()
        if loc:
            params["locationstring"] = loc
        return params

    def _parse_cards(self, html_text: str) -> list[dict[str, Any]]:
        """Devuelve solo las cards de avisos directos de Job Bank."""
        cards: list[dict[str, Any]] = []
        for match in ARTICLE_RE.finditer(html_text):
            block = match.group(0)
            # Solo avisos posteados directamente en Job Bank (traen contacto real).
            if "postedonJB" not in block:
                continue
            job_id = match.group(1)
            title = _strip_html((NOCTITLE_RE.search(block) or [None, ""])[1]) if NOCTITLE_RE.search(block) else ""
            business = _strip_html((BUSINESS_RE.search(block) or [None, ""])[1]) if BUSINESS_RE.search(block) else ""
            location = ""
            loc_m = LOCATION_RE.search(block)
            if loc_m:
                location = LOCATION_LABEL_RE.sub("", _strip_html(loc_m.group(1))).strip()
            date_label = _strip_html((DATE_RE.search(block) or [None, ""])[1]) if DATE_RE.search(block) else ""
            salary = ""
            sal_m = SALARY_RE.search(block)
            if sal_m:
                salary = _strip_html(sal_m.group(1)).replace("Salary", "").strip()
            telework_m = TELEWORK_RE.search(block)
            telework = _strip_html(telework_m.group(1)) if telework_m else ""
            cards.append({
                "source_id": job_id,
                "title": title,
                "company": business,
                "location_text": location or None,
                "date_label": date_label or None,
                "salary_text": salary or None,
                "telework": telework or None,
            })
        return cards

    def _parse_date(self, raw: str | None) -> datetime | None:
        if not raw:
            return None
        raw = raw.strip()
        for fmt in ("%B %d, %Y", "%b %d, %Y"):
            try:
                return datetime.strptime(raw, fmt).replace(tzinfo=UTC)
            except ValueError:
                continue
        return None

    def _reveal_apply(self, job_id: str, posting_url: str) -> tuple[list[str], list[str], str | None]:
        """Replica el botón 'Show how to apply' (POST JSF parcial, ViewState
        stateless) y devuelve (emails, external_urls, apply_method)."""
        data = {
            "seekeractivity": "seekeractivity",
            "seekeractivity:jobid": str(job_id),
            "seekeractivity_SUBMIT": "1",
            "jakarta.faces.ViewState": "stateless",
            "jakarta.faces.source": "applynowbutton",
            "jakarta.faces.partial.event": "action",
            "jakarta.faces.behavior.event": "action",
            "jakarta.faces.partial.ajax": "true",
            "jakarta.faces.partial.execute": "seekeractivity:jobid",
            "jakarta.faces.partial.render": "applynow markappliedgroup",
            "jsJobId": str(job_id),
            "action": "applynowbutton",
        }
        headers = {
            "Faces-Request": "partial/ajax",
            "X-Requested-With": "XMLHttpRequest",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Referer": posting_url,
        }
        self._wait_rate_limit()
        try:
            resp = self.session.post(posting_url, data=data, headers=headers, timeout=30)
            resp.raise_for_status()
            txt = resp.text
        except Exception as exc:
            logger.warning("JobBank reveal falló en %s: %s", job_id, exc)
            return [], [], None

        emails: list[str] = []
        seen_e: set[str] = set()
        for raw in EMAIL_RE.findall(txt):
            e = raw.strip().rstrip(".,;:")
            low = e.lower()
            if low in seen_e:
                continue
            seen_e.add(low)
            emails.append(e)

        urls: list[str] = []
        seen_u: set[str] = set()
        for raw in EXTERNAL_URL_RE.findall(txt):
            u = html.unescape(raw).rstrip(".,);:!?\"'")
            if "jobbank.gc.ca" in u:
                continue
            low = u.lower()
            if low in seen_u:
                continue
            seen_u.add(low)
            urls.append(u)

        method_m = APPLY_METHOD_RE.search(txt)
        method = _strip_html(method_m.group(1)) if method_m else None
        return emails, urls, method

    def _enrich_with_detail(self, record: dict[str, Any], card: dict[str, Any]) -> None:
        job_id = record["source_id"]
        url = record["url"]
        html_text = self._get(url)

        description = ""
        if html_text:
            h1 = H1_RE.search(html_text)
            if h1:
                h1_text = _strip_html(h1.group(1))
                if h1_text:
                    record["title"] = h1_text
            desc_m = DESCRIPTION_RE.search(html_text)
            if desc_m:
                description = _strip_html(desc_m.group(1))

        emails, external_urls, method = self._reveal_apply(job_id, url)

        # Determinar apply_type humano-legible.
        if emails:
            record["apply_type"] = "Email"
        elif external_urls:
            record["apply_type"] = "Web"
        elif method:
            record["apply_type"] = method

        # La primera URL externa cruda va como apply_url_external candidato;
        # contact_filter la valida contra el blacklist (agregadores → null).
        if external_urls:
            record["apply_url_external"] = external_urls[0]

        # Construir el content que el contact_filter va a procesar. Inyectamos
        # emails y URLs en texto plano para que los detecte aunque vengan del
        # reveal (no del cuerpo del aviso).
        parts: list[str] = []
        if description:
            parts.append(description)
        if card.get("salary_text"):
            parts.append(f"Salario: {card['salary_text']}")
        if card.get("location_text"):
            parts.append(f"Ubicación: {card['location_text']}")
        if card.get("telework"):
            parts.append(f"Modalidad: {card['telework']}")
        if method:
            parts.append(f"Cómo aplicar: {method}")
        if external_urls:
            parts.append("Aplicar: " + ", ".join(external_urls))
        if emails:
            parts.append("Contacto: " + ", ".join(emails))

        full_content = "\n".join(parts).strip()
        record["content"] = full_content
        record["summary"] = full_content[:260] + ("..." if len(full_content) > 260 else "")

    def _scrape_jobs_sync(
        self,
        keywords: str,
        limit: int,
        antiquity_days: int | None,
        on_record: Callable[[dict[str, Any]], Any] | None,
        location: str | None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        cutoff: datetime | None = None
        if antiquity_days and antiquity_days > 0:
            cutoff = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
            cutoff = cutoff - timedelta(days=antiquity_days)

        results: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        max_pages = 25

        for page in range(1, max_pages + 1):
            if len(results) >= limit:
                break
            params = self._build_search_params(keywords, location, page)
            html_text = self._get(SEARCH_URL, params=params)
            if not html_text:
                break

            # Fin REAL de resultados = página sin ningún <article>. Una página
            # puede traer 25 avisos pero todos agregados (0 directos): en ese
            # caso NO cortamos — los directos suelen estar en páginas siguientes
            # (ej. "director" tiene 0 directos en page=1 pero 57 en pages 2-8).
            all_article_ids = ARTICLE_RE.findall(html_text)
            if not all_article_ids:
                logger.info("JobBank: page=%d sin resultados → fin", page)
                break
            cards = self._parse_cards(html_text)
            if not cards:
                logger.info(
                    "JobBank: page=%d con %d avisos pero 0 directos → sigo",
                    page, len(all_article_ids),
                )
                continue

            for card in cards:
                if len(results) >= limit:
                    break
                source_id = card["source_id"]
                if source_id in seen_ids:
                    continue
                seen_ids.add(source_id)

                # Filtro de antigüedad por la fecha de la card (sort=M ya viene
                # ordenado desc, así que en cuanto pasamos el cutoff cortamos).
                if cutoff:
                    card_dt = self._parse_date(card.get("date_label"))
                    if card_dt and card_dt < cutoff:
                        continue

                record: dict[str, Any] = {
                    "source_type": "jobbank",
                    "source_id": source_id,
                    "title": card.get("title") or "Sin título",
                    "company": card.get("company") or "",
                    "author": "",
                    "summary": "",
                    "content": "",
                    "seniority": None,
                    "apply_type": None,
                    "url": f"{POSTING_BASE}/{source_id}",
                    "location_text": card.get("location_text"),
                    "scraped_at": datetime.now(UTC).isoformat(),
                }

                self._enrich_with_detail(record, card)

                results.append(record)
                if on_record is not None:
                    try:
                        on_record(record)
                    except Exception:
                        pass

        return results
