from __future__ import annotations

import re
import unicodedata

WORK_MODES = (
    ("hibrido", "hibrido"),
    ("híbrido", "hibrido"),
    ("hybrid", "hibrido"),
    ("en remoto", "remoto"),
    ("remoto", "remoto"),
    ("100% remote", "remoto"),
    ("remote", "remoto"),
    ("teletrabajo", "remoto"),
    ("home office", "remoto"),
    ("presencial", "presencial"),
    ("on-site", "presencial"),
    ("onsite", "presencial"),
)

# Variantes de nombre → canonical
KNOWN_COUNTRIES: dict[str, str] = {
    "ecuador": "Ecuador",
    "méxico": "México", "mexico": "México",
    "colombia": "Colombia",
    "perú": "Perú", "peru": "Perú",
    "argentina": "Argentina",
    "chile": "Chile",
    "venezuela": "Venezuela",
    "uruguay": "Uruguay",
    "paraguay": "Paraguay",
    "bolivia": "Bolivia",
    "costa rica": "Costa Rica",
    "panamá": "Panamá", "panama": "Panamá",
    "guatemala": "Guatemala",
    "el salvador": "El Salvador",
    "honduras": "Honduras",
    "nicaragua": "Nicaragua",
    "república dominicana": "República Dominicana",
    "republica dominicana": "República Dominicana",
    "cuba": "Cuba",
    "puerto rico": "Puerto Rico",
    "españa": "España", "espana": "España", "spain": "España",
    "estados unidos": "Estados Unidos",
    "united states": "Estados Unidos",
    "usa": "Estados Unidos",
    "u.s.": "Estados Unidos",
    "ee.uu.": "Estados Unidos",
    "ee uu": "Estados Unidos",
    "brasil": "Brasil", "brazil": "Brasil",
    "canadá": "Canadá", "canada": "Canadá",
    # buckets regionales
    "américa latina": "LATAM",
    "america latina": "LATAM",
    "latinoamérica": "LATAM",
    "latinoamerica": "LATAM",
    "sudamérica": "LATAM",
    "sudamerica": "LATAM",
    "latam": "LATAM",
}

# Banderas de país (emoji regional indicators) → país canónico.
# Útil para feed: muchas vacantes ponen "🇨🇴 Colombia" o solo la bandera.
FLAG_TO_COUNTRY: dict[str, str] = {
    "🇪🇨": "Ecuador", "🇨🇴": "Colombia", "🇲🇽": "México", "🇵🇪": "Perú",
    "🇦🇷": "Argentina", "🇨🇱": "Chile", "🇻🇪": "Venezuela", "🇺🇾": "Uruguay",
    "🇵🇾": "Paraguay", "🇧🇴": "Bolivia", "🇨🇷": "Costa Rica", "🇵🇦": "Panamá",
    "🇬🇹": "Guatemala", "🇸🇻": "El Salvador", "🇭🇳": "Honduras",
    "🇳🇮": "Nicaragua", "🇩🇴": "República Dominicana", "🇨🇺": "Cuba",
    "🇵🇷": "Puerto Rico", "🇪🇸": "España", "🇺🇸": "Estados Unidos",
    "🇧🇷": "Brasil", "🇨🇦": "Canadá",
}

# Mapa ciudad → país (LATAM + España + USA principales).
# Para fallback cuando no hay location_text estructurado.
CITY_TO_COUNTRY: dict[str, str] = {
    # Ecuador
    "Quito": "Ecuador", "Guayaquil": "Ecuador", "Cuenca": "Ecuador",
    "Manta": "Ecuador", "Ambato": "Ecuador", "Loja": "Ecuador",
    "Riobamba": "Ecuador", "Machala": "Ecuador", "Portoviejo": "Ecuador",
    "Latacunga": "Ecuador", "Esmeraldas": "Ecuador", "Babahoyo": "Ecuador",
    "Ibarra": "Ecuador", "Milagro": "Ecuador", "Durán": "Ecuador",
    "Salinas": "Ecuador", "Tena": "Ecuador", "Puyo": "Ecuador",
    "Macas": "Ecuador", "Tulcán": "Ecuador",
    # Colombia
    "Bogotá": "Colombia", "Bogota": "Colombia", "Medellín": "Colombia",
    "Medellin": "Colombia", "Cali": "Colombia", "Cartagena": "Colombia",
    "Barranquilla": "Colombia", "Bucaramanga": "Colombia",
    "Pereira": "Colombia", "Santa Marta": "Colombia", "Yopal": "Colombia",
    "Manizales": "Colombia", "Ibagué": "Colombia", "Villavicencio": "Colombia",
    # México
    "Ciudad de México": "México", "CDMX": "México", "Monterrey": "México",
    "Guadalajara": "México", "Puebla": "México", "Querétaro": "México",
    "Queretaro": "México", "Tijuana": "México", "León": "México",
    "Mérida": "México", "Merida": "México",
    # Perú
    "Lima": "Perú", "Arequipa": "Perú", "Cusco": "Perú",
    "Trujillo": "Perú", "Chiclayo": "Perú", "Piura": "Perú",
    # Argentina
    "Buenos Aires": "Argentina", "Córdoba": "Argentina",
    "Rosario": "Argentina", "Mendoza": "Argentina", "La Plata": "Argentina",
    # Chile
    "Santiago": "Chile", "Valparaíso": "Chile", "Concepción": "Chile",
    # España
    "Madrid": "España", "Barcelona": "España", "Valencia": "España",
    "Sevilla": "España", "Málaga": "España", "Bilbao": "España",
    "Zaragoza": "España",
    # USA
    "New York": "Estados Unidos", "Miami": "Estados Unidos",
    "Los Angeles": "Estados Unidos", "Chicago": "Estados Unidos",
    "Houston": "Estados Unidos",
}

# Patrones explícitos del estilo "📍Ubicación: X" o "Lugar: X"
LOCATION_LABEL_PATTERNS = (
    re.compile(r"(?:📍|🌎|📌|🇽)\s*[Uu]bicaci[oó]n\s*:?\s*([^\n|·]+?)(?=[\n|·💻🚨✅📍🌎📌]|$)"),
    re.compile(r"[Uu]bicaci[oó]n\s*:\s*([^\n|·]+?)(?=[\n|·]|$)"),
    re.compile(r"[Ll]ugar\s*:\s*([^\n|·]+?)(?=[\n|·]|$)"),
    re.compile(r"[Cc]iudad\s*:\s*([^\n|·]+?)(?=[\n|·]|$)"),
    re.compile(r"📍\s*([^\n|·💻🚨✅🌎📌]+?)(?=[\n|·]|$)"),
)


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value or "")
    return "".join(c for c in normalized if not unicodedata.combining(c))


def _norm(value: str) -> str:
    return _strip_accents(value).lower().strip()


def extract_work_mode(text: str) -> str | None:
    if not text:
        return None
    text_norm = _norm(text)
    for needle, canonical in WORK_MODES:
        if _norm(needle) in text_norm:
            return canonical
    return None


def detect_country(text_norm: str) -> str | None:
    if not text_norm:
        return None
    # Buscar la variante más larga primero (evita "ecuador" matchee dentro de "rep. ecuatoriana" raro)
    for variant in sorted(KNOWN_COUNTRIES.keys(), key=len, reverse=True):
        # Match con bordes de palabra cuando sea posible
        if " " in variant:
            if variant in text_norm:
                return KNOWN_COUNTRIES[variant]
        else:
            if re.search(rf"\b{re.escape(variant)}\b", text_norm):
                return KNOWN_COUNTRIES[variant]
    return None


def _extract_tpe(location_text: str) -> dict[str, str | None]:
    if not location_text:
        return {"city": None, "state": None, "country": "Ecuador", "work_mode": None}
    parts = [p.strip() for p in location_text.split("/") if p.strip()]
    return {
        "city": parts[0] if parts else None,
        "state": parts[1] if len(parts) > 1 else None,
        "country": "Ecuador",
        "work_mode": None,
    }


# Provincias/territorios de Canadá (códigos de 2 letras que usa Job Bank).
CA_PROVINCES: dict[str, str] = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba",
    "NB": "New Brunswick", "NL": "Newfoundland and Labrador",
    "NS": "Nova Scotia", "NT": "Northwest Territories", "NU": "Nunavut",
    "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec",
    "SK": "Saskatchewan", "YT": "Yukon",
}

_JOBBANK_LOC_RE = re.compile(r"^(.*?)\s*\(([A-Za-z]{2})\)\s*$")


def _extract_jobbank(location_text: str) -> dict[str, str | None]:
    """Job Bank: location_text formato 'Ciudad (PROV)' o 'Various locations'.
    País siempre Canadá; modalidad se infiere luego del content."""
    geo: dict[str, str | None] = {
        "city": None, "state": None, "country": "Canadá", "work_mode": None,
    }
    text = (location_text or "").strip()
    if not text or text.lower().startswith("various"):
        return geo
    m = _JOBBANK_LOC_RE.match(text)
    if m:
        geo["city"] = m.group(1).strip() or None
        geo["state"] = CA_PROVINCES.get(m.group(2).upper(), m.group(2).upper())
    else:
        geo["city"] = text
    return geo


def _extract_linkedin(location_text: str) -> dict[str, str | None]:
    if not location_text:
        return {"city": None, "state": None, "country": None, "work_mode": None}

    work_mode = extract_work_mode(location_text)

    # Quitar la modalidad entre paréntesis al final
    clean = re.sub(r"\s*\([^)]+\)\s*$", "", location_text).strip()
    parts = [p.strip() for p in clean.split(",") if p.strip()]

    city: str | None = None
    state: str | None = None
    country: str | None = None

    # Localizar el país en alguno de los segmentos
    country_idx = -1
    for i, segment in enumerate(parts):
        c = detect_country(_norm(segment))
        if c:
            country = c
            country_idx = i
            break

    if country_idx >= 0:
        if country_idx >= 2:
            # ej: "Quito, Pichincha, Ecuador"
            city = parts[country_idx - 2]
            state = parts[country_idx - 1]
        elif country_idx == 1:
            # ej: "Pichincha, Ecuador" → asumimos provincia
            state = parts[0]
        # Si country_idx == 0: solo país

    if country is None:
        # Buscar país en el texto completo (caso "América Latina")
        country = detect_country(_norm(clean))

    return {"city": city, "state": state, "country": country, "work_mode": work_mode}


def _detect_city_in_text(text: str, text_norm: str) -> tuple[str | None, str | None]:
    """Returns (city, inferred_country) si encuentra match en CITY_TO_COUNTRY."""
    # Probar cadenas largas primero ("Buenos Aires" antes que "Cali")
    for city in sorted(CITY_TO_COUNTRY.keys(), key=len, reverse=True):
        city_norm = _norm(city)
        if " " in city_norm:
            if city_norm in text_norm:
                return city, CITY_TO_COUNTRY[city]
        else:
            if re.search(rf"\b{re.escape(city_norm)}\b", text_norm):
                return city, CITY_TO_COUNTRY[city]
    return None, None


def _extract_from_content_fallback(content: str) -> dict[str, str | None]:
    """Posts del feed (sin location_text) → buscar en el cuerpo.

    Estrategia capa por capa:
    1. Bandera emoji conocida → país.
    2. Patrón explícito "📍Ubicación: X" → location label, parsear ciudad/país.
    3. Match de país por nombre.
    4. Match de ciudad conocida (LATAM/España/USA) → infiere país.
    5. Modalidad de trabajo en cualquier parte del texto.
    """
    geo: dict[str, str | None] = {
        "city": None, "state": None, "country": None, "work_mode": None,
    }
    if not content:
        return geo

    text_norm = _norm(content)

    # 1. Bandera emoji
    for flag, country in FLAG_TO_COUNTRY.items():
        if flag in content:
            geo["country"] = country
            break

    # 2. Patrón explícito "📍Ubicación: ..."
    for pattern in LOCATION_LABEL_PATTERNS:
        m = pattern.search(content)
        if m:
            label_text = m.group(1).strip()
            label_norm = _norm(label_text)
            # Quitar emojis residuales y trim
            label_clean = re.sub(r"[^\w\sáéíóúüñÁÉÍÓÚÜÑ,/-]", " ", label_text).strip()
            if label_clean:
                # Probar split por coma → "Ciudad, Estado"
                parts = [p.strip() for p in label_clean.split(",") if p.strip()]
                # Detectar país en el label completo
                lbl_country = detect_country(label_norm)
                if lbl_country and not geo["country"]:
                    geo["country"] = lbl_country
                # Detectar ciudad conocida
                city_match, inferred = _detect_city_in_text(label_clean, label_norm)
                if city_match:
                    geo["city"] = city_match
                    if not geo["country"]:
                        geo["country"] = inferred
                elif parts:
                    # Si la primera parte no es país conocido, asumirla como ciudad libre.
                    first_norm = _norm(parts[0])
                    if not detect_country(first_norm):
                        geo["city"] = parts[0]
                if len(parts) > 1 and not geo["state"]:
                    second_norm = _norm(parts[1])
                    if not detect_country(second_norm):
                        geo["state"] = parts[1]
                break

    # 3. País por nombre directo
    if not geo["country"]:
        geo["country"] = detect_country(text_norm)

    # 4. Ciudad conocida → infiere país si falta
    if not geo["city"]:
        city_match, inferred = _detect_city_in_text(content, text_norm)
        if city_match:
            geo["city"] = city_match
            if not geo["country"]:
                geo["country"] = inferred

    # 5. Modalidad
    geo["work_mode"] = extract_work_mode(content)

    return geo


def extract_geo(
    location_text: str | None,
    content: str | None = "",
    source_type: str | None = "",
) -> dict[str, str | None]:
    """Estrategia por fuente:
    - tpe: parsear location_text con formato 'Ciudad / Provincia', país=Ecuador.
    - linkedin jobs: parsear location_text 'Ciudad, Estado, País (Modalidad)'.
    - linkedin feed: location_text vacío → buscar en content.
    """
    location_text = (location_text or "").strip()
    content = content or ""
    source_type = (source_type or "").lower()

    if source_type == "tpe":
        return _extract_tpe(location_text)

    if source_type == "jobbank":
        geo = _extract_jobbank(location_text)
        if not geo["work_mode"]:
            geo["work_mode"] = extract_work_mode(content)
        return geo

    if location_text:
        return _extract_linkedin(location_text)

    return _extract_from_content_fallback(content)
