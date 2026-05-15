from __future__ import annotations

import asyncio
import hashlib
import inspect
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

from jobson.contact_filter import (
    DEFAULT_PORTAL_BLACKLIST,
    DEFAULT_SOCIAL_BLACKLIST,
    domain_of,
    is_portal_domain,
)

logger = logging.getLogger(__name__)


class LinkedInScraper:
    def __init__(self, session_path: Path):
        self.session_path = session_path
        self.session_path.parent.mkdir(parents=True, exist_ok=True)
        self.base_url = "https://www.linkedin.com"

    async def _is_logged_in(self, page) -> bool:
        current_url = page.url.lower()
        if "login" in current_url or "checkpoint" in current_url:
            return False

        sign_in = page.locator(
            'button:has-text("Sign in"), a:has-text("Sign in"), '
            'button:has-text("Iniciar sesión"), a:has-text("Iniciar sesión")'
        )
        return await sign_in.count() == 0

    async def _wait_for_manual_login(self, context, page) -> None:
        print("\n" + "!" * 65)
        print("[!] No se detectó sesión de LinkedIn guardada.")
        print("[!] Inicia sesión manualmente en la ventana del navegador.")
        print("[!] Cuando termines y entres al feed o jobs, el scraper continúa solo.")
        print("!" * 65 + "\n")

        for _ in range(300):
            await asyncio.sleep(2)
            url = page.url.lower()
            if "login" not in url and ("/feed" in url or "/jobs" in url or "/in/" in url):
                await context.storage_state(path=str(self.session_path))
                logger.info("Sesion guardada en %s", self.session_path)
                return

        raise RuntimeError("No se detectó login manual dentro del tiempo esperado.")

    async def _get_authenticated_page(self):
        playwright = await async_playwright().start()
        storage_state = str(self.session_path) if self.session_path.exists() else None
        headless = bool(storage_state)

        logger.info("Iniciando navegador LinkedIn (headless=%s)", headless)
        browser = await playwright.chromium.launch(headless=headless)
        context = await browser.new_context(storage_state=storage_state)
        page = await context.new_page()

        await page.goto(f"{self.base_url}/jobs/", wait_until="load", timeout=60000)
        if await self._is_logged_in(page):
            return playwright, browser, context, page

        logger.info("Sesion no válida. Reabriendo navegador para login manual.")
        await browser.close()
        browser = await playwright.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()
        await page.goto(f"{self.base_url}/login", wait_until="load", timeout=60000)
        await self._wait_for_manual_login(context, page)
        return playwright, browser, context, page

    def _estimate_seniority(self, title: str, description: str) -> str:
        text = f"{title} {description}".lower()
        if any(word in text for word in ["director", "vp", "vice president", "head of", "principal"]):
            return "Lead/Director"
        if any(word in text for word in ["senior", "sr", "lead", "staff"]):
            return "Senior"
        if any(word in text for word in ["junior", "jr", "entry", "trainee", "intern", "pasante"]):
            return "Junior"
        return "Mid"

    def _detect_apply_type(self, html_fragment: str) -> str:
        if "Easy Apply" in html_fragment or "Solicitud sencilla" in html_fragment:
            return "Easy Apply"
        if "Apply" in html_fragment or "Solicitar" in html_fragment:
            return "External Apply"
        return "Unknown"

    async def _first_visible_text(self, card, selectors: list[str]) -> str:
        for selector in selectors:
            try:
                element = card.locator(selector).first
                if await element.is_visible(timeout=600):
                    return (await element.inner_text(timeout=1500)).strip()
            except Exception:
                continue
        return ""

    async def _extract_external_apply_href(self, page) -> str:
        """Best-effort capture of the *external* apply URL without clicking.

        LinkedIn often hides the real URL behind a click that opens a new tab.
        We do NOT click here (resolving with click is opt-in via flag in the
        service). We just inspect the apply button's href / data attributes.

        Many "About the company" panels expose social links (YouTube, X, FB)
        with similar attributes. We discard those — they are NOT apply URLs.
        """
        selectors = [
            ".jobs-apply-button[href]",
            "a.jobs-apply-button",
            ".jobs-s-apply a[href]",
            ".jobs-apply-button--top-card[href]",
            "a[href*='offsite_apply']",
            "a[data-job-apply-url]",
            "a[data-application-url]",
        ]
        attrs = ("href", "data-job-apply-url", "data-application-url")
        for selector in selectors:
            try:
                button = page.locator(selector).first
                if not await button.count():
                    continue
                for attr in attrs:
                    value = await button.get_attribute(attr)
                    if not value:
                        continue
                    value = value.strip()
                    if not value or value.startswith("javascript:") or value == "#":
                        continue
                    if value.startswith("/"):
                        value = self.base_url + value
                    domain = domain_of(value)
                    if is_portal_domain(domain, DEFAULT_SOCIAL_BLACKLIST):
                        # Social link masquerading as apply button → skip.
                        continue
                    return value
            except Exception:
                continue
        return ""

    async def _notify_record(self, on_record, record: dict[str, Any]) -> None:
        if on_record is None:
            return
        try:
            maybe_awaitable = on_record(record)
            if inspect.isawaitable(maybe_awaitable):
                await maybe_awaitable
        except Exception:
            # Progress hooks should never break scraping.
            return

    def _extract_job_id(self, raw_id: str | None, url: str | None = None) -> str:
        if raw_id:
            if ":" in raw_id:
                return raw_id.split(":")[-1]
            return raw_id

        if not url:
            return ""

        match = re.search(r"/jobs/view/(\d+)", url)
        return match.group(1) if match else ""

    def _normalize_linkedin_url(self, href: str | None) -> str:
        if not href:
            return ""
        href = href.strip()
        if not href:
            return ""
        if href.startswith("/"):
            href = f"{self.base_url}{href}"
        if "linkedin.com" not in href:
            return ""
        # Remove tracking query params for cleaner, stable URLs.
        return href.split("?")[0]

    def _extract_post_url_from_post_id(self, post_id: str | None) -> str:
        if not post_id:
            return ""

        activity_match = re.search(r"activity:(\d+)", post_id)
        if activity_match:
            activity_id = activity_match.group(1)
            return f"{self.base_url}/feed/update/urn:li:activity:{activity_id}/"

        ugc_match = re.search(r"ugcPost:(\d+)", post_id)
        if ugc_match:
            ugc_id = ugc_match.group(1)
            return f"{self.base_url}/feed/update/urn:li:ugcPost:{ugc_id}/"

        return ""

    async def _extract_post_link_from_card(self, card) -> str:
        hrefs: list[str] = []
        try:
            raw_hrefs = await card.locator("a[href]").evaluate_all("els => els.map(el => el.getAttribute('href'))")
            hrefs = [self._normalize_linkedin_url(item) for item in raw_hrefs if item]
            hrefs = [item for item in hrefs if item]
        except Exception:
            hrefs = []

        if not hrefs:
            return ""

        priority_patterns = [
            "/feed/update/urn:li:activity:",
            "/feed/update/urn:li:ugcPost:",
            "/feed/update/",
            "/posts/",
            "/pulse/",
        ]
        for pattern in priority_patterns:
            for href in hrefs:
                if pattern in href:
                    return href

        # Discard likely non-post links (profile/company/search).
        for href in hrefs:
            if "/search/results/" in href:
                continue
            if "/in/" in href:
                continue
            if "/company/" in href:
                continue
            if "/jobs/" in href:
                continue
            return href

        return ""

    async def scrape_jobs(
        self,
        keywords: str,
        limit: int,
        antiquity_days: int | None = None,
        on_record=None,
    ) -> list[dict[str, Any]]:
        playwright, browser, _, page = await self._get_authenticated_page()
        results: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        try:
            tpr = ""
            if antiquity_days:
                if antiquity_days <= 1:
                    tpr = "&f_TPR=r86400"
                elif antiquity_days <= 7:
                    tpr = "&f_TPR=r604800"
                else:
                    tpr = "&f_TPR=r2592000"

            search_url = f"{self.base_url}/jobs/search/?keywords={quote_plus(keywords)}{tpr}"
            logger.info("Buscando jobs: %s", search_url)
            await page.goto(search_url, wait_until="load", timeout=60000)
            await asyncio.sleep(4)

            no_new_rounds = 0
            while len(results) < limit and no_new_rounds <= 8:
                cards = await page.locator(
                    ".job-card-container, .jobs-search-results__list-item, .jobs-search-results-list__list-item"
                ).all()

                if not cards:
                    no_new_rounds += 1
                    await page.evaluate("window.scrollBy(0, 900)")
                    await asyncio.sleep(2)
                    continue

                before = len(results)
                for card in cards:
                    if len(results) >= limit:
                        break

                    try:
                        raw_id = await card.get_attribute("data-job-id")
                        if not raw_id:
                            raw_id = await card.get_attribute("data-entity-urn")

                        card_link = ""
                        try:
                            href = await card.locator("a[href*='/jobs/view/']").first.get_attribute("href")
                            if href:
                                card_link = href if href.startswith("http") else f"{self.base_url}{href}"
                        except Exception:
                            card_link = ""

                        job_id = self._extract_job_id(raw_id, card_link)
                        if not job_id or job_id in seen_ids:
                            continue
                        seen_ids.add(job_id)

                        title = await self._first_visible_text(
                            card,
                            [
                                ".job-card-list__title",
                                ".base-search-card__title",
                                ".artdeco-entity-lockup__title",
                                "h3",
                                "h4",
                            ],
                        )

                        company = await self._first_visible_text(
                            card,
                            [
                                ".job-card-container__primary-description",
                                ".job-card-container__company-name",
                                ".base-search-card__subtitle",
                                ".artdeco-entity-lockup__subtitle",
                            ],
                        )

                        location_text = await self._first_visible_text(
                            card,
                            [
                                ".job-card-container__metadata-item",
                                ".job-card-container__metadata-wrapper li",
                                ".artdeco-entity-lockup__caption",
                                ".job-card__location",
                                ".base-search-card__metadata",
                            ],
                        )

                        detail_text = ""
                        detail_html = ""
                        try:
                            await card.click(timeout=2000)
                            await asyncio.sleep(1.4)

                            # Click "Ver más" / "Show more" para expandir descripción
                            # truncada — muchas empresas esconden el email tras el
                            # botón de expandir.
                            for see_more_sel in [
                                'button:has-text("Ver más")',
                                'button:has-text("ver más")',
                                'button:has-text("Show more")',
                                'button:has-text("show more")',
                                ".jobs-description__footer-button",
                                ".feed-shared-inline-show-more-text__see-more-less-toggle",
                            ]:
                                try:
                                    btn = page.locator(see_more_sel).first
                                    if await btn.is_visible(timeout=600):
                                        await btn.click(timeout=1500)
                                        await asyncio.sleep(0.6)
                                        break
                                except Exception:
                                    continue

                            detail = page.locator(
                                ".jobs-search__job-details, .jobs-description-content, .jobs-details__main-content"
                            ).first
                            if await detail.is_visible(timeout=3000):
                                detail_text = (await detail.inner_text(timeout=4000)).strip()
                                detail_html = await detail.inner_html(timeout=4000)
                        except Exception:
                            pass

                        apply_url_external = await self._extract_external_apply_href(page)

                        full_url = card_link or f"{self.base_url}/jobs/view/{job_id}/"
                        summary = (detail_text[:260] + "...") if len(detail_text) > 260 else detail_text

                        record = {
                            "source_type": "jobs",
                            "source_id": job_id,
                            "title": title or "Sin título",
                            "company": company or "Sin empresa",
                            "author": "",
                            "summary": summary,
                            "content": detail_text,
                            "seniority": self._estimate_seniority(title, detail_text),
                            "apply_type": self._detect_apply_type(detail_html),
                            "url": full_url,
                            "location_text": location_text or None,
                            "apply_url_external": apply_url_external or None,
                            "scraped_at": datetime.now(UTC).isoformat(),
                        }
                        results.append(record)
                        await self._notify_record(on_record, record)
                    except Exception:
                        continue

                if len(results) == before:
                    no_new_rounds += 1
                else:
                    no_new_rounds = 0

                await page.evaluate(
                    """
                    () => {
                        const list = document.querySelector('.jobs-search-results-list') ||
                                     document.querySelector('.jobs-search-results-list__list');
                        if (list) {
                            list.scrollBy(0, 1200);
                        } else {
                            window.scrollBy(0, 1200);
                        }
                    }
                    """
                )
                await asyncio.sleep(2)

            return results
        finally:
            await browser.close()
            await playwright.stop()

    async def scrape_posts(
        self,
        keywords: str,
        limit: int,
        antiquity_days: int | None = None,
        on_record=None,
    ) -> list[dict[str, Any]]:
        """LinkedIn search/results/content — scraping moderno (post-2025 SDUI).

        LinkedIn migró a Server-Driven UI con clases CSS hasheadas inestables.
        Las únicas señales confiables son:
          - aria-label="Abrir el menú de controles para la publicación de NOMBRE"
            → identifica un post + su autor.
          - [data-testid="expandable-text-box"] → contiene el texto del post.
          - [data-testid="expandable-text-button"] → botón "Ver más".
        """
        playwright, browser, _, page = await self._get_authenticated_page()
        results: list[dict[str, Any]] = []
        seen_ids: set[str] = set()

        try:
            date_filter = ""
            if antiquity_days:
                if antiquity_days <= 1:
                    date_filter = '&datePublished=%22past-24h%22'
                elif antiquity_days <= 7:
                    date_filter = '&datePublished=%22past-week%22'

            search_url = (
                f"{self.base_url}/search/results/content/?keywords={quote_plus(keywords)}"
                f"{date_filter}&sortBy=%22date_posted%22"
            )
            logger.info("Buscando posts/feed: %s", search_url)
            await page.goto(search_url, wait_until="load", timeout=60000)
            await asyncio.sleep(4)

            no_new_rounds = 0
            processed_aria: set[str] = set()

            while len(results) < limit and no_new_rounds <= 8:
                # Aria-labels en español e inglés.
                handles = await page.locator(
                    '[aria-label^="Abrir el menú de controles para la publicación"], '
                    '[aria-label^="Open control menu for post"]'
                ).all()

                if not handles:
                    no_new_rounds += 1
                    if no_new_rounds == 4:
                        logger.warning(
                            "feed: 0 posts visibles después de 4 rondas. "
                            "Posible que la sesión perdió permisos o LinkedIn "
                            "movió aria-labels. URL=%s",
                            search_url,
                        )
                    await page.evaluate("window.scrollBy(0, 1200)")
                    await asyncio.sleep(2)
                    continue

                before = len(results)
                for handle in handles:
                    if len(results) >= limit:
                        break

                    try:
                        aria = (await handle.get_attribute("aria-label")) or ""
                        if aria in processed_aria:
                            continue
                        processed_aria.add(aria)

                        # Autor: lo que viene tras "publicación de" o "post by".
                        author = ""
                        m = re.search(
                            r"(?:publicaci[oó]n de|post by)\s+(.+?)\s*$",
                            aria,
                            re.IGNORECASE,
                        )
                        if m:
                            author = m.group(1).strip()

                        # Subimos al primer ancestor que también contiene un
                        # expandable-text-box: ese es el contenedor del post.
                        container = handle.locator(
                            'xpath=ancestor::*[descendant::*[@data-testid="expandable-text-box"]][1]'
                        )
                        if not await container.count():
                            continue

                        # Click "Ver más" para expandir el contenido truncado.
                        try:
                            btn = container.locator(
                                '[data-testid="expandable-text-button"]'
                            ).first
                            if await btn.is_visible(timeout=400):
                                await btn.click(timeout=1000)
                                await asyncio.sleep(0.4)
                        except Exception:
                            pass

                        # Contenido del post.
                        content = ""
                        try:
                            box = container.locator(
                                '[data-testid="expandable-text-box"]'
                            ).first
                            if await box.count():
                                content = (await box.inner_text(timeout=2000)).strip()
                        except Exception:
                            content = ""

                        # URL: buscar primer href que apunte al post específico.
                        link = ""
                        try:
                            hrefs = await container.locator("a[href]").evaluate_all(
                                "els => els.map(e => e.href)"
                            )
                            for href in hrefs:
                                low = (href or "").lower()
                                if "urn:li:activity" in low or "/feed/update/" in low:
                                    link = href
                                    break
                            # Fallback: primer link a /posts/ del autor.
                            if not link:
                                for href in hrefs:
                                    if "/posts/" in (href or "").lower():
                                        link = href
                                        break
                        except Exception:
                            pass

                        # source_id estable.
                        post_id = ""
                        if link:
                            urn_match = re.search(r"urn:li:activity:(\d+)", link)
                            if urn_match:
                                post_id = f"activity:{urn_match.group(1)}"
                            else:
                                ugc_match = re.search(r"urn:li:ugcPost:(\d+)", link)
                                if ugc_match:
                                    post_id = f"ugcPost:{ugc_match.group(1)}"
                        if not post_id:
                            seed = f"{author}|{content[:300]}".strip()
                            digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:20]
                            post_id = f"feed-item:{digest}"

                        if post_id in seen_ids:
                            continue
                        seen_ids.add(post_id)

                        record = {
                            "source_type": "feed",
                            "source_id": post_id,
                            "title": "",
                            "company": "",
                            "author": author or "Autor desconocido",
                            "summary": (content[:260] + "...") if len(content) > 260 else content,
                            "content": content,
                            "seniority": self._estimate_seniority("", content),
                            "apply_type": "N/A",
                            "url": link,
                            "scraped_at": datetime.now(UTC).isoformat(),
                        }
                        results.append(record)
                        await self._notify_record(on_record, record)
                    except Exception as exc:
                        logger.debug("feed: salto post por error: %s", exc)
                        continue

                if len(results) == before:
                    no_new_rounds += 1
                else:
                    no_new_rounds = 0

                await page.evaluate("window.scrollBy(0, 1200)")
                await asyncio.sleep(2)

            return results
        finally:
            await browser.close()
            await playwright.stop()

    async def scrape_mixed(
        self,
        keywords: str,
        limit: int,
        antiquity_days: int | None = None,
        on_record=None,
    ) -> list[dict[str, Any]]:
        jobs_limit = max(1, limit // 2)
        feed_limit = max(1, limit - jobs_limit)

        jobs = await self.scrape_jobs(
            keywords=keywords,
            limit=jobs_limit,
            antiquity_days=antiquity_days,
            on_record=on_record,
        )
        feed = await self.scrape_posts(
            keywords=keywords,
            limit=feed_limit,
            antiquity_days=antiquity_days,
            on_record=on_record,
        )
        return jobs + feed
