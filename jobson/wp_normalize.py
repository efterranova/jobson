"""Normalización de registros del scraper LinkedIn → estructura WP Job Manager.

Provee:
- clean_title: deduplica títulos repetidos del scraper
- clean_content: quita basura UI de LinkedIn y devuelve HTML limpio
- extract_location: ubicación libre tipo "Remoto, América Latina" o "Quito, Ecuador"
- detect_language: 'es' o 'en' con langdetect (fallback 'es')
- pick_categories: slugs de categorías existentes en el sitio (no crea nuevas)
- pick_job_types: slugs de job-types existentes
- normalize_record: devuelve dict con title, content, location, language, categories, job_types

Las categorías y tipos se mapean a slugs YA EXISTENTES en staging2.erecruit.ca.
Si una keyword no matchea ninguna, cae a 'otros-categoria-general' / 'others-general-category'.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any

try:
    from langdetect import DetectorFactory, detect
    DetectorFactory.seed = 0
    _HAS_LANGDETECT = True
except Exception:  # noqa: BLE001
    _HAS_LANGDETECT = False


# ---------------------------------------------------------------------------
# Slugs ES (canónicos en el sitio). EN se deriva con ES_TO_EN.
# ---------------------------------------------------------------------------

CATEGORY_FALLBACK_ES = "otros-categoria-general"
CATEGORY_FALLBACK_EN = "others-general-category"

KEYWORD_TO_CATEGORY: dict[str, str] = {
    # Marketing / Growth / Publicidad
    "marketing":        "marketing-publicidad-y-medios",
    "publicidad":       "marketing-publicidad-y-medios",
    "growth":           "marketing-publicidad-y-medios",
    "seo":              "marketing-publicidad-y-medios",
    "sem":              "marketing-publicidad-y-medios",
    "ads":              "marketing-publicidad-y-medios",
    "trafficker":       "marketing-publicidad-y-medios",
    "traficker":        "marketing-publicidad-y-medios",
    "media buyer":      "marketing-publicidad-y-medios",
    "social media":     "marketing-publicidad-y-medios",
    "community manager":"marketing-publicidad-y-medios",
    "copywriter":       "marketing-publicidad-y-medios",
    "branding":         "marketing-publicidad-y-medios",
    "content":          "marketing-publicidad-y-medios",
    "lifecycle":        "marketing-publicidad-y-medios",
    "lead generation":  "marketing-publicidad-y-medios",
    "performance":      "marketing-publicidad-y-medios",
    "email marketing":  "marketing-publicidad-y-medios",
    "digital marketing":"marketing-publicidad-y-medios",
    "demand generation":"marketing-publicidad-y-medios",

    # Tecnología / Software
    "developer":     "tecnologia-de-la-informacion-y-software",
    "engineer":      "tecnologia-de-la-informacion-y-software",
    "software":      "tecnologia-de-la-informacion-y-software",
    "frontend":      "tecnologia-de-la-informacion-y-software",
    "backend":       "tecnologia-de-la-informacion-y-software",
    "fullstack":     "tecnologia-de-la-informacion-y-software",
    "full stack":    "tecnologia-de-la-informacion-y-software",
    "python":        "tecnologia-de-la-informacion-y-software",
    "javascript":    "tecnologia-de-la-informacion-y-software",
    "react":         "tecnologia-de-la-informacion-y-software",
    "node":          "tecnologia-de-la-informacion-y-software",
    "devops":        "tecnologia-de-la-informacion-y-software",
    "qa":            "tecnologia-de-la-informacion-y-software",
    "data":          "tecnologia-de-la-informacion-y-software",
    "data scientist":"tecnologia-de-la-informacion-y-software",
    "data engineer": "tecnologia-de-la-informacion-y-software",
    "analyst":       "tecnologia-de-la-informacion-y-software",
    "automatizacion":"tecnologia-de-la-informacion-y-software",
    "automation":    "tecnologia-de-la-informacion-y-software",
    "no-code":       "tecnologia-de-la-informacion-y-software",
    "ai":            "tecnologia-de-la-informacion-y-software",
    "ml":            "tecnologia-de-la-informacion-y-software",

    # Finanzas / Contabilidad
    "finanzas":     "banca-y-servicios-financieros",
    "financiero":   "banca-y-servicios-financieros",
    "contabilidad": "servicios-profesionales-auditoria-contabilidad-legal",
    "contador":     "servicios-profesionales-auditoria-contabilidad-legal",
    "auditor":      "servicios-profesionales-auditoria-contabilidad-legal",
    "tesorero":     "banca-y-servicios-financieros",
    "credit":       "banca-y-servicios-financieros",

    # Admin / Asistente
    "asistente virtual": "servicios-administrativos-y-soporte-de-oficina",
    "asistente":         "servicios-administrativos-y-soporte-de-oficina",
    "administrativo":    "servicios-administrativos-y-soporte-de-oficina",
    "virtual assistant": "servicios-administrativos-y-soporte-de-oficina",
    "secretaria":        "servicios-administrativos-y-soporte-de-oficina",
    "office":            "servicios-administrativos-y-soporte-de-oficina",

    # RRHH
    "reclutador":        "recursos-humanos-y-reclutamiento",
    "reclutamiento":     "recursos-humanos-y-reclutamiento",
    "recursos humanos":  "recursos-humanos-y-reclutamiento",
    "recruiter":         "recursos-humanos-y-reclutamiento",
    "talent":            "recursos-humanos-y-reclutamiento",
    "hr":                "recursos-humanos-y-reclutamiento",

    # Operaciones / PM
    "operaciones":    "operaciones-y-gestion-de-proyectos",
    "operations":     "operaciones-y-gestion-de-proyectos",
    "project manager":"operaciones-y-gestion-de-proyectos",
    "program manager":"operaciones-y-gestion-de-proyectos",
    "scrum":          "operaciones-y-gestion-de-proyectos",
    "agile":          "operaciones-y-gestion-de-proyectos",

    # Ventas / CX / Retail
    "ventas":             "retail-e-commerce-y-atencion-al-cliente",
    "sales":              "retail-e-commerce-y-atencion-al-cliente",
    "atencion al cliente":"retail-e-commerce-y-atencion-al-cliente",
    "customer service":   "retail-e-commerce-y-atencion-al-cliente",
    "customer success":   "retail-e-commerce-y-atencion-al-cliente",
    "account executive":  "retail-e-commerce-y-atencion-al-cliente",
    "bdr":                "retail-e-commerce-y-atencion-al-cliente",
    "sdr":                "retail-e-commerce-y-atencion-al-cliente",
    "ecommerce":          "retail-e-commerce-y-atencion-al-cliente",
    "e-commerce":         "retail-e-commerce-y-atencion-al-cliente",

    # Diseño
    "diseñador":        "diseno-grafico-y-multimedia",
    "diseno":           "diseno-grafico-y-multimedia",
    "designer":         "diseno-grafico-y-multimedia",
    "ui":               "diseno-grafico-y-multimedia",
    "ux":               "diseno-grafico-y-multimedia",
    "graphic designer": "diseno-grafico-y-multimedia",
}


# Featured image por categoría (Unsplash). El mu-plugin sideloadea cada URL
# UNA sola vez a la media library de WP (cache por hash en la opción
# jobson_image_cache) y reusa el attachment_id. Si la categoría no tiene
# imagen mapeada, cae a 'otros-categoria-general'.
_UNSPLASH_PARAMS = "?w=1200&q=80&auto=format&fit=crop"

CATEGORY_FEATURED_IMAGE: dict[str, str] = {
    "marketing-publicidad-y-medios":                        f"https://images.unsplash.com/photo-1533750349088-cd871a92f312{_UNSPLASH_PARAMS}",
    "tecnologia-de-la-informacion-y-software":              f"https://images.unsplash.com/photo-1607799279861-4dd421887fb3{_UNSPLASH_PARAMS}",
    "banca-y-servicios-financieros":                        f"https://images.unsplash.com/photo-1633158829585-23ba8f7c8caf{_UNSPLASH_PARAMS}",
    "servicios-profesionales-auditoria-contabilidad-legal": f"https://images.unsplash.com/photo-1554224154-26032ffc0d07{_UNSPLASH_PARAMS}",
    "servicios-administrativos-y-soporte-de-oficina":       f"https://images.unsplash.com/photo-1487017159836-4e23ece2e4cf{_UNSPLASH_PARAMS}",
    "recursos-humanos-y-reclutamiento":                     f"https://images.unsplash.com/photo-1582213782179-e0d53f98f2ca{_UNSPLASH_PARAMS}",
    "operaciones-y-gestion-de-proyectos":                   f"https://images.unsplash.com/photo-1531403009284-440f080d1e12{_UNSPLASH_PARAMS}",
    "retail-e-commerce-y-atencion-al-cliente":              f"https://images.unsplash.com/photo-1521566652839-697aa473761a{_UNSPLASH_PARAMS}",
    "diseno-grafico-y-multimedia":                          f"https://images.unsplash.com/photo-1626785774573-4b799315345d{_UNSPLASH_PARAMS}",
    "otros-categoria-general":                              f"https://images.unsplash.com/photo-1715059448930-9dff21725605{_UNSPLASH_PARAMS}",
}


# ES → EN equivalente (mismo concepto, otro idioma)
ES_TO_EN: dict[str, str] = {
    "marketing-publicidad-y-medios":                   "marketing-advertising-and-media",
    "tecnologia-de-la-informacion-y-software":         "information-technology-and-software",
    "banca-y-servicios-financieros":                   "banking-and-financial-services",
    "servicios-profesionales-auditoria-contabilidad-legal": "professional-services-auditing-accounting-legal",
    "servicios-administrativos-y-soporte-de-oficina":  "administrative-services-and-office-support",
    "recursos-humanos-y-reclutamiento":                "human-resources-and-recruitment",
    "operaciones-y-gestion-de-proyectos":              "operations-and-project-management",
    "retail-e-commerce-y-atencion-al-cliente":         "retail-e-commerce-and-customer-service",
    "diseno-grafico-y-multimedia":                     "graphic-design-and-multimedia",
    "otros-categoria-general":                         "others-general-category",
}


# Job types existentes en el sitio (slug bilingüe)
JOB_TYPE_BY_LANG: dict[str, dict[str, str]] = {
    "es": {
        "full_time":  "tiempo-completo",
        "part_time":  "medio-tiempo",
        "contract":   "contrato",
        "internship": "pasantia",
        "freelance":  "freelance",
        "per_day":    "por-dia",
    },
    "en": {
        "full_time":  "full-time-en",
        "part_time":  "half-time",
        "contract":   "contract",
        "internship": "internship",
        "freelance":  "freelance-en",
        "per_day":    "per-day",
    },
}

JOB_TYPE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("full_time",  re.compile(r"\b(full[\s-]?time|tiempo\s*completo|jornada\s*completa)\b", re.IGNORECASE)),
    ("part_time",  re.compile(r"\b(part[\s-]?time|medio\s*tiempo|media\s*jornada|half[\s-]?time)\b", re.IGNORECASE)),
    ("contract",   re.compile(r"\b(contract(?:or)?|contrato|temporal|fixed[\s-]?term)\b", re.IGNORECASE)),
    ("internship", re.compile(r"\b(internship|intern|pasant[ií]a|practicante|becario)\b", re.IGNORECASE)),
    ("freelance",  re.compile(r"\b(freelance|por\s*proyecto|self[\s-]?employed)\b", re.IGNORECASE)),
    ("per_day",    re.compile(r"\b(per\s*day|por\s*d[ií]a)\b", re.IGNORECASE)),
]


REMOTE_RX = re.compile(
    r"\b(remote|remoto|en\s*remoto|home\s*office|teletrabajo|work\s*from\s*home|trabajo\s*remoto|100%\s*remoto)\b",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Limpieza de basura LinkedIn — todo lo que aparece antes del contenido real.
# Sustituimos por una sola línea (o vacío) y luego compactamos.
# ---------------------------------------------------------------------------

LINKEDIN_NOISE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"^Compartir\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Mostrar más opciones\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Solicitar\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Guardar\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Guardar «.+?» en .+?$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Reactivar Premium\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Mira una comparación con las otras .+?$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Accede a información exclusiva sobre los candidatos.+?$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Promocionado por .+?$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Coincide con tus preferencias de empleo\..+?$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\s*\d+\s*de\s*\d+\s*coincidencias de aptitudes\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Respuestas gestionadas fuera de LinkedIn\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^\d+\s*personas han hecho clic en «Solicitar»\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^hace\s+\d+\s+(hora|d[ií]a|semana|mes)s?.*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Share\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Save\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Apply\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Show more options\s*$", re.IGNORECASE | re.MULTILINE),
    re.compile(r"^Promoted by hiring team.*$", re.IGNORECASE | re.MULTILINE),
]

# Marcadores que indican "aquí empieza el contenido útil"
CONTENT_START_HINTS: list[re.Pattern[str]] = [
    re.compile(r"(?im)^\s*Acerca del empleo\s*$"),
    re.compile(r"(?im)^\s*About the job\s*$"),
    re.compile(r"(?im)^\s*Job description\s*$"),
    re.compile(r"(?im)^\s*Descripción del puesto\s*$"),
    re.compile(r"(?im)^\s*About Company and Role\s*$"),
]


# ---------------------------------------------------------------------------
# Ubicación: heurísticos sencillos
# ---------------------------------------------------------------------------

# Frases comunes en LinkedIn para la primera línea de ubicación.
# Importante: no usar \s en el caracter set (incluye \n y cruza líneas);
# se permite solo espacio/tab para mantener la captura en una sola línea.
LOCATION_INLINE_RX = re.compile(
    r"^[ \t]*(?P<loc>[A-ZÁÉÍÓÚÑa-záéíóúñ0-9,\.\-/() \t]{2,60}?)[ \t]*·[ \t]*hace\b",
    re.MULTILINE,
)

KNOWN_REGIONS = [
    "América Latina", "Latinoamérica", "Latam", "LATAM",
    "México", "Mexico", "Argentina", "Colombia", "Perú", "Peru",
    "Ecuador", "Chile", "Uruguay", "Paraguay", "Bolivia", "Venezuela",
    "Costa Rica", "Panamá", "Panama", "Guatemala", "Honduras", "Nicaragua",
    "República Dominicana", "Puerto Rico", "El Salvador", "Cuba",
    "España", "Spain", "United States", "USA", "EE.UU.", "Estados Unidos",
    "Canada", "Canadá", "United Kingdom", "UK", "Brasil", "Brazil",
]


# ---------------------------------------------------------------------------
# Funciones públicas
# ---------------------------------------------------------------------------

def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def _norm(s: str) -> str:
    return _strip_accents(s or "").lower()


def clean_title(raw: str | None) -> str:
    """Deduplica títulos que vienen repetidos por el scraper.

    Caso típico del scraper: 'Lead Generation & Marketing Specialist\\nLead Generation & Marketing Specialist'.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    # Si una línea se repite literal en líneas adyacentes, dejamos una sola.
    lines = [l.strip() for l in re.split(r"[\r\n]+", text) if l.strip()]
    if len(lines) >= 2 and lines[0] == lines[1]:
        return lines[0]
    # Algunos vienen como "X X" en una sola línea
    parts = text.split()
    if len(parts) >= 4 and parts[: len(parts) // 2] == parts[len(parts) // 2:]:
        return " ".join(parts[: len(parts) // 2])
    return lines[0] if lines else text


def clean_content(raw: str | None, fallback_summary: str | None = None) -> str:
    """Devuelve HTML limpio. Quita ruido LinkedIn y, si encuentra un marcador
    tipo 'Acerca del empleo', usa solo lo que viene después.
    """
    src = (raw or "").strip() or (fallback_summary or "").strip()
    if not src:
        return "<p>(Sin descripción)</p>"

    # 1) Si hay un marcador de inicio de contenido, recorta desde ahí.
    cut_at = None
    for rx in CONTENT_START_HINTS:
        m = rx.search(src)
        if m:
            cut_at = m.end()
            break
    if cut_at is not None:
        src = src[cut_at:].strip()

    # 2) Aplica patrones de ruido (línea a línea)
    for rx in LINKEDIN_NOISE_PATTERNS:
        src = rx.sub("", src)

    # 3) Compacta líneas vacías
    src = re.sub(r"\n{3,}", "\n\n", src).strip()

    # 4) Pasa a HTML simple: párrafos por bloques separados con doble salto
    blocks = [b.strip() for b in re.split(r"\n{2,}", src) if b.strip()]
    if not blocks:
        return "<p>(Sin descripción)</p>"
    return "\n".join(f"<p>{_escape_html(b).replace(chr(10), '<br>')}</p>" for b in blocks)


def _escape_html(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def extract_location(record: dict[str, Any]) -> str:
    """Devuelve string para `_job_location`. Prioridades:
    1) Inline LinkedIn 'X · hace Y' al inicio de summary/content.
    2) Match contra países/regiones conocidos.
    3) Si detecta señales de remoto → 'Remoto'.
    4) Vacío.
    """
    blob = (record.get("summary") or "") + "\n" + (record.get("content") or "")

    m = LOCATION_INLINE_RX.search(blob)
    if m:
        loc = re.sub(r"\s+", " ", m.group("loc")).strip(" ,.-")
        # Saneamiento: si la captura tiene demasiadas comas o luce como una frase
        # (verbos como "compartir", "guardar"), la descartamos.
        if loc and len(loc) <= 60 and loc.count(",") <= 3 and not re.search(
            r"\b(compartir|share|mostrar|show|guardar|save|solicitar|apply)\b",
            loc, flags=re.IGNORECASE,
        ):
            return loc

    for region in KNOWN_REGIONS:
        if re.search(rf"\b{re.escape(region)}\b", blob, flags=re.IGNORECASE):
            return region

    if REMOTE_RX.search(blob):
        return "Remoto"

    return ""


def detect_language(record: dict[str, Any]) -> str:
    """'es' o 'en'. Fallback 'es' si langdetect no está o falla."""
    if not _HAS_LANGDETECT:
        return "es"
    sample = " ".join(
        (record.get(k) or "")
        for k in ("title", "summary", "content")
    )[:1500].strip()
    if not sample:
        return "es"
    try:
        code = detect(sample)
    except Exception:  # noqa: BLE001
        return "es"
    return "en" if code == "en" else "es"


def pick_categories(record: dict[str, Any], lang: str, max_categories: int = 2) -> list[str]:
    """Devuelve lista de slugs de categorías (en el idioma dado).

    Estrategia priorizada para evitar falsos positivos:
    1. Match en `keyword` del scraper (señal más fuerte: lo que el usuario buscó).
    2. Match en `title` (alta confianza).
    3. Solo si los 2 pasos anteriores no encuentran nada, intenta en summary/content.
    Tope: `max_categories` (default 2). Fallback a 'otros-categoria-general'.
    """
    keyword_blob = _norm(str(record.get("keyword") or ""))
    title_blob   = _norm(str(record.get("title") or ""))
    body_blob    = _norm(" ".join(str(record.get(k) or "") for k in ("summary", "content")))

    picked_es: list[str] = []
    seen: set[str] = set()

    def _scan(blob: str) -> None:
        if not blob:
            return
        for kw in sorted(KEYWORD_TO_CATEGORY.keys(), key=len, reverse=True):
            if len(picked_es) >= max_categories:
                return
            if _norm(kw) in blob:
                slug = KEYWORD_TO_CATEGORY[kw]
                if slug not in seen:
                    seen.add(slug)
                    picked_es.append(slug)

    _scan(keyword_blob)
    if len(picked_es) < max_categories:
        _scan(title_blob)
    if not picked_es:
        # Solo si no encontramos nada en keyword+title, buscamos en cuerpo
        _scan(body_blob)

    if not picked_es:
        picked_es = [CATEGORY_FALLBACK_ES]

    if lang == "en":
        return [ES_TO_EN.get(s, s) for s in picked_es]
    return picked_es


def pick_job_types(record: dict[str, Any], lang: str) -> list[str]:
    """Slug del job-type primario (1 solo). Prioriza señal en título; fallback al primero
    del contenido. Si nada matchea, asume 'tiempo-completo'/'full-time-en' (lo más común).
    """
    title = str(record.get("title") or "")
    body  = " ".join(str(record.get(k) or "") for k in ("summary", "content", "apply_type"))

    # Pase 1: título tiene la palabra
    for key, rx in JOB_TYPE_PATTERNS:
        if rx.search(title):
            return [JOB_TYPE_BY_LANG[lang][key]]

    # Pase 2: primera coincidencia en el body
    for key, rx in JOB_TYPE_PATTERNS:
        if rx.search(body):
            return [JOB_TYPE_BY_LANG[lang][key]]

    return [JOB_TYPE_BY_LANG[lang]["full_time"]]


def pick_featured_image_url(categories: list[str], lang: str = "es") -> str:
    """URL de Unsplash para la primera categoría que matchee.

    Si las categorías vienen en EN, se mapean de vuelta a ES vía ES_TO_EN.
    Si ninguna matchea, devuelve la imagen del fallback ('otros').
    """
    _ = lang  # mapa de imágenes es por concepto, no por idioma
    en_to_es = {v: k for k, v in ES_TO_EN.items()}
    for cat in categories or []:
        es_slug = en_to_es.get(cat, cat)
        url = CATEGORY_FEATURED_IMAGE.get(es_slug)
        if url:
            return url
    return CATEGORY_FEATURED_IMAGE["otros-categoria-general"]


# ---------------------------------------------------------------------------
# Purga de nombres de empresa contaminados (portal-as-company, sufijos basura)
# ---------------------------------------------------------------------------

# Portales que NO son empresas reales. Si llegan como `company`, intentamos
# derivar la empresa del dominio del email; si no se puede, el record se skipea.
PORTAL_COMPANY_BLACKLIST: tuple[str, ...] = (
    "tuportalempleo",
    "computrabajo",
    "bumeran",
    "indeed",
    "linkedin",
    "multitrabajos",
    "elempleo",
    "workable",
    "lever",
    "greenhouse",
)

# Dominios genéricos: no permiten derivar empresa real. Lista corta, ampliable.
GENERIC_EMAIL_DOMAINS: frozenset[str] = frozenset({
    "gmail.com", "googlemail.com",
    "hotmail.com", "hotmail.es", "hotmail.co", "hotmail.com.ec",
    "outlook.com", "outlook.es", "live.com", "msn.com",
    "yahoo.com", "yahoo.es", "yahoo.com.ec", "ymail.com",
    "icloud.com", "me.com", "mac.com",
    "aol.com", "protonmail.com", "proton.me", "pm.me",
    "zoho.com", "gmx.com", "mail.com",
})

# Separadores que indican que el regex del scraper TPE concatenó company+otra cosa
_COMPANY_SUFFIX_BUGS: tuple[str, ...] = (
    " Ubicación:", " Ubicacion:", " ubicación:", " ubicacion:",
    " Instrucción", " Instruccion",
    " Tipo de contrato",
)


def _company_from_email_domain(email: str) -> tuple[str, str]:
    """('Besttalents', 'besttalents.com.ec') desde 'foo@besttalents.com.ec'.

    Devuelve ('','') si el email es vacío, malformado o de dominio genérico.
    """
    if not email or "@" not in email:
        return ("", "")
    domain = email.split("@", 1)[1].strip().lower().rstrip(".")
    if not domain or domain in GENERIC_EMAIL_DOMAINS:
        return ("", "")
    head = domain.split(".", 1)[0]
    if len(head) < 2:
        return ("", "")
    return (head.capitalize(), domain)


INDEPENDENT_RECRUITER_LABEL = "Reclutador Independiente"


def clean_company(company: str | None, apply_email: str | None = "") -> tuple[str, str]:
    """Limpia / deriva el nombre de empresa para publicar.

    Reglas (en orden):
      1. Corta sufijos basura del parser TPE (' Ubicación:', ' Instrucción'…).
      2. Si el resultado matchea un portal conocido (PORTAL_COMPANY_BLACKLIST):
           a. Si el email tiene dominio corporativo → deriva
              ('Besttalents', 'besttalents.com.ec').
           b. Si el email es genérico (gmail, hotmail…) → etiqueta como
              'Reclutador Independiente' (entidad compartida; cada job
              conserva su propio apply_email).
           c. Si no hay email → devuelve ('', '') para SKIP (sin contacto).

    Returns:
      (company_name, company_website_inferred). Si name == '', el caller
      debe NO publicar este record.
    """
    name = (company or "").strip()
    if not name:
        return ("", "")

    # 1) Sufijos basura
    for sep in _COMPANY_SUFFIX_BUGS:
        if sep in name:
            name = name.split(sep, 1)[0].strip()
            break
    name = name.strip(" -·,")
    if not name:
        return ("", "")

    # 2) Portal-as-company → derivar del email o etiquetar
    norm = _strip_accents(name).lower()
    is_portal = any(p in norm for p in PORTAL_COMPANY_BLACKLIST)
    if not is_portal:
        return (name, "")

    email = (apply_email or "").strip()
    if not email or "@" not in email:
        return ("", "")  # sin contacto: skip

    domain = email.split("@", 1)[1].strip().lower().rstrip(".")
    if domain and domain not in GENERIC_EMAIL_DOMAINS:
        head = domain.split(".", 1)[0]
        if len(head) >= 2:
            return (head.capitalize(), domain)

    # Dominio genérico (gmail, hotmail…) → reclutador independiente
    return (INDEPENDENT_RECRUITER_LABEL, "")


def is_remote(record: dict[str, Any]) -> bool:
    blob = " ".join(str(record.get(k) or "") for k in ("title", "summary", "content"))
    return bool(REMOTE_RX.search(blob))


def normalize_record(record: dict[str, Any]) -> dict[str, Any]:
    """Aplica toda la normalización y devuelve un dict listo para mapear a WP."""
    title = clean_title(record.get("title"))
    content_html = clean_content(record.get("content"), fallback_summary=record.get("summary"))
    location = extract_location(record)
    lang = detect_language(record)
    return {
        "title":     title or "Oferta sin título",
        "content":   content_html,
        "location":  location,
        "language":  lang,
        "remote":    is_remote(record) or location.lower().startswith("remoto"),
        "categories": pick_categories(record, lang),
        "job_types":  pick_job_types(record, lang),
    }
