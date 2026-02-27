from __future__ import annotations

import asyncio
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

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

    def _extract_job_id(self, raw_id: str | None, url: str | None = None) -> str:
        if raw_id:
            if ":" in raw_id:
                return raw_id.split(":")[-1]
            return raw_id

        if not url:
            return ""

        match = re.search(r"/jobs/view/(\d+)", url)
        return match.group(1) if match else ""

    async def scrape_jobs(self, keywords: str, limit: int, antiquity_days: int | None = None) -> list[dict[str, Any]]:
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

                        detail_text = ""
                        detail_html = ""
                        try:
                            await card.click(timeout=2000)
                            await asyncio.sleep(1.4)
                            detail = page.locator(
                                ".jobs-search__job-details, .jobs-description-content, .jobs-details__main-content"
                            ).first
                            if await detail.is_visible(timeout=3000):
                                detail_text = (await detail.inner_text(timeout=4000)).strip()
                                detail_html = await detail.inner_html(timeout=4000)
                        except Exception:
                            pass

                        full_url = card_link or f"{self.base_url}/jobs/view/{job_id}/"
                        summary = (detail_text[:260] + "...") if len(detail_text) > 260 else detail_text

                        results.append(
                            {
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
                                "scraped_at": datetime.now(UTC).isoformat(),
                            }
                        )
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

    async def scrape_posts(self, keywords: str, limit: int, antiquity_days: int | None = None) -> list[dict[str, Any]]:
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
            while len(results) < limit and no_new_rounds <= 8:
                cards = await page.locator(
                    ".feed-shared-update-v2, .search-content-entity-lockup, .search-results-container [data-urn]"
                ).all()

                if not cards:
                    no_new_rounds += 1
                    await page.evaluate("window.scrollBy(0, 1000)")
                    await asyncio.sleep(2)
                    continue

                before = len(results)
                for card in cards:
                    if len(results) >= limit:
                        break

                    try:
                        post_id = await card.get_attribute("data-urn")
                        if not post_id:
                            post_id = await card.get_attribute("data-id")

                        if not post_id or post_id in seen_ids:
                            continue
                        seen_ids.add(post_id)

                        author = await self._first_visible_text(
                            card,
                            [
                                ".update-components-actor__name",
                                ".feed-shared-actor__name",
                                ".app-aware-link",
                            ],
                        )

                        content = await self._first_visible_text(
                            card,
                            [
                                ".feed-shared-update-v2__description",
                                ".update-components-text",
                                ".feed-shared-text",
                            ],
                        )

                        link = ""
                        try:
                            href = await card.locator("a[href*='/feed/update/']").first.get_attribute("href")
                            if href:
                                link = href if href.startswith("http") else f"{self.base_url}{href}"
                        except Exception:
                            link = ""

                        if not link and "update" in post_id:
                            cleaned = post_id.split(":")[-1]
                            link = f"{self.base_url}/feed/update/{cleaned}/"

                        results.append(
                            {
                                "source_type": "feed",
                                "source_id": post_id,
                                "title": "",
                                "company": "",
                                "author": author or "Autor desconocido",
                                "summary": (content[:260] + "...") if len(content) > 260 else content,
                                "content": content,
                                "seniority": self._estimate_seniority("", content),
                                "apply_type": "N/A",
                                "url": link or page.url,
                                "scraped_at": datetime.now(UTC).isoformat(),
                            }
                        )
                    except Exception:
                        continue

                if len(results) == before:
                    no_new_rounds += 1
                else:
                    no_new_rounds = 0

                await page.evaluate("window.scrollBy(0, 1100)")
                await asyncio.sleep(2)

            return results
        finally:
            await browser.close()
            await playwright.stop()

    async def scrape_mixed(self, keywords: str, limit: int, antiquity_days: int | None = None) -> list[dict[str, Any]]:
        jobs_limit = max(1, limit // 2)
        feed_limit = max(1, limit - jobs_limit)

        jobs = await self.scrape_jobs(keywords=keywords, limit=jobs_limit, antiquity_days=antiquity_days)
        feed = await self.scrape_posts(keywords=keywords, limit=feed_limit, antiquity_days=antiquity_days)
        return jobs + feed
