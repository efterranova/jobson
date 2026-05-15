from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

# Dominios que consideramos "portales de empleo" o ATS de terceros.
# Si el contacto solo apunta a estos, no es candidato para WPJM.
DEFAULT_PORTAL_BLACKLIST: tuple[str, ...] = (
    # Globales
    "linkedin.com",
    "indeed.com",
    "glassdoor.com",
    "monster.com",
    "monster.ca",
    "ziprecruiter.com",
    "dice.com",
    "wellfound.com",
    "weworkremotely.com",
    "remote.co",
    "remoterocketship.com",
    "hired.com",
    "angel.co",
    # ATS / SaaS de aplicación
    "workable.com",
    "lever.co",
    "greenhouse.io",
    "smartrecruiters.com",
    "workday.com",
    "myworkdayjobs.com",
    "bamboohr.com",
    "jobvite.com",
    "taleo.net",
    "icims.com",
    "successfactors.com",
    "recruitee.com",
    "ashbyhq.com",
    "breezy.hr",
    "personio.com",
    "jobillico.com",
    "freshteam.com",
    "recruiterbox.com",
    "paylocity.com",
    "dayforcehcm.com",
    # LATAM
    "bumeran.com",
    "bumeran.com.mx",
    "bumeran.com.ar",
    "computrabajo.com",
    "computrabajo.com.mx",
    "computrabajo.com.co",
    "computrabajo.com.ar",
    "occ.com.mx",
    "occ.com",
    "getonbrd.com",
    "getonboard.com",
    "talently.tech",
    "talenthuntermx.com",
    "vivaglobal.com.mx",
    "laborumtuo.com.mx",
    "multitrabajos.com",
)

# Redes sociales y acortadores: NO son portales de empleo (Erick puede querer
# ver una mención a sus redes), pero TAMPOCO cuentan como "web oficial de la
# empresa" para alimentar WPJM. Son una tercera categoría.
DEFAULT_SOCIAL_BLACKLIST: tuple[str, ...] = (
    "youtube.com", "youtu.be",
    "twitter.com", "x.com", "t.co",
    "facebook.com", "fb.com", "fb.me", "fb.watch",
    "instagram.com",
    "tiktok.com",
    "threads.net",
    "pinterest.com",
    "snapchat.com",
    "reddit.com",
    "telegram.me", "t.me",
    "whatsapp.com", "wa.me",
    "lnkd.in",
    "bit.ly", "goo.gl", "tinyurl.com", "ow.ly", "buff.ly", "rebrand.ly",
    "linktr.ee", "linktree.com", "beacons.ai", "carrd.co",
    "spotify.com", "soundcloud.com",
)

EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}"
)
URL_RE = re.compile(
    r"https?://[^\s<>\"')]+",
    flags=re.IGNORECASE,
)
WWW_RE = re.compile(
    r"(?<![@\w.])(www\.[a-zA-Z0-9\-]+(?:\.[a-zA-Z0-9\-]+)+)",
    flags=re.IGNORECASE,
)

EMAIL_NOISE_PATTERNS = (
    "noreply",
    "no-reply",
    "donotreply",
    "do-not-reply",
    "notifications@",
    "@linkedin.com",
    "@example.",
)


def _normalize_domain(host: str) -> str:
    host = host.lower().strip()
    if host.startswith("www."):
        host = host[4:]
    return host.rstrip(".")


def domain_of(url: str) -> str:
    if not url:
        return ""
    raw = url.strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = "http://" + raw.lstrip("/")
    try:
        host = urlparse(raw).netloc
    except Exception:
        return ""
    return _normalize_domain(host)


def is_portal_domain(domain: str, blacklist: tuple[str, ...] | list[str]) -> bool:
    if not domain:
        return True
    domain = _normalize_domain(domain)
    for portal in blacklist:
        portal_norm = _normalize_domain(portal)
        if not portal_norm:
            continue
        if domain == portal_norm or domain.endswith("." + portal_norm):
            return True
    return False


def is_email_noise(email: str) -> bool:
    lowered = email.lower()
    return any(token in lowered for token in EMAIL_NOISE_PATTERNS)


def extract_emails(text: str) -> list[str]:
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for match in EMAIL_RE.findall(text):
        cleaned = match.strip().rstrip(".,;:")
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        if is_email_noise(lowered):
            continue
        seen.add(lowered)
        out.append(cleaned)
    return out


def extract_urls(text: str) -> list[str]:
    if not text:
        return []
    seen: set[str] = set()
    out: list[str] = []

    for match in URL_RE.findall(text):
        cleaned = match.rstrip(".,);:!?\"'")
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(cleaned)

    for match in WWW_RE.findall(text):
        candidate = "https://" + match.strip().rstrip(".,);:!?\"'")
        lowered = candidate.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        out.append(candidate)

    return out


def classify_urls(
    urls: list[str],
    blacklist: tuple[str, ...] | list[str],
    social_blacklist: tuple[str, ...] | list[str] = DEFAULT_SOCIAL_BLACKLIST,
) -> tuple[list[str], list[str], list[str]]:
    """Return (official_urls, portal_urls, social_urls) preserving order."""
    official: list[str] = []
    portal: list[str] = []
    social: list[str] = []
    for url in urls:
        domain = domain_of(url)
        if is_portal_domain(domain, blacklist):
            portal.append(url)
        elif is_portal_domain(domain, social_blacklist):
            social.append(url)
        else:
            official.append(url)
    return official, portal, social


def derive_contact_status(has_email: bool, has_official_url: bool) -> str:
    if has_email and has_official_url:
        return "tiene_ambos"
    if has_email:
        return "tiene_email"
    if has_official_url:
        return "tiene_web"
    return "sin_contacto"


def enrich_record_with_contact(
    record: dict[str, Any],
    blacklist: tuple[str, ...] | list[str] = DEFAULT_PORTAL_BLACKLIST,
) -> dict[str, Any]:
    """Mutate `record` adding contact-related fields and wp_status decision.

    Always called *before* upsert. We never drop records here — Erick wants
    every job in the DB so the blacklist can be audited later.
    """
    blob_parts = [
        str(record.get("title") or ""),
        str(record.get("company") or ""),
        str(record.get("summary") or ""),
        str(record.get("content") or ""),
    ]
    apply_external_url = str(record.get("apply_url_external") or "").strip()
    if apply_external_url:
        blob_parts.append(apply_external_url)
    blob = "\n".join(blob_parts)

    emails = extract_emails(blob)
    urls = extract_urls(blob)
    if apply_external_url and apply_external_url not in urls:
        urls.append(apply_external_url)

    official_urls, _portal_urls, _social_urls = classify_urls(urls, blacklist)

    apply_email = emails[0] if emails else None
    apply_url_external_official: str | None = None

    if apply_external_url:
        ext_domain = domain_of(apply_external_url)
        is_portal = is_portal_domain(ext_domain, blacklist)
        is_social = is_portal_domain(ext_domain, DEFAULT_SOCIAL_BLACKLIST)
        if not is_portal and not is_social:
            apply_url_external_official = apply_external_url

    if not apply_url_external_official and official_urls:
        apply_url_external_official = official_urls[0]

    company_website = official_urls[0] if official_urls else None

    has_email = apply_email is not None
    has_official = apply_url_external_official is not None
    contact_status = derive_contact_status(has_email, has_official)

    if contact_status == "sin_contacto":
        wp_status = "skipped"
        wp_last_error = "sin email ni web oficial detectados"
    else:
        wp_status = "pending"
        wp_last_error = None

    record["extracted_emails"] = emails
    record["extracted_urls"] = official_urls
    record["apply_email"] = apply_email
    record["apply_url_external"] = apply_url_external_official
    record["company_website"] = company_website
    record["contact_status"] = contact_status
    record["wp_status"] = wp_status
    record["wp_last_error"] = wp_last_error

    return record
