from __future__ import annotations

import re
import unicodedata

_TYPE_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "internship",
        ("intern", "internship", "pasante", "pasantia", "becario", "becaria", "trainee"),
    ),
    (
        "part-time",
        ("part time", "part-time", "medio tiempo", "media jornada", "tiempo parcial"),
    ),
    (
        "contract",
        (
            "contract",
            "contractor",
            "contrato por proyecto",
            "por contrato",
            "freelance",
            "honorarios",
            "obra cierta",
            "obra y tiempo",
        ),
    ),
    (
        "temporary",
        ("temporary", "temporal", "eventual", "por temporada"),
    ),
    (
        "full-time",
        (
            "full time",
            "full-time",
            "tiempo completo",
            "jornada completa",
            "permanente",
            "indefinido",
            "indeterminado",
        ),
    ),
)

REMOTE_KEYWORDS = (
    "remoto",
    "remote",
    "100% remoto",
    "trabajo en casa",
    "home office",
    "homeoffice",
    "teletrabajo",
)


def _strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _normalize(value: str) -> str:
    text = _strip_accents(value or "").lower()
    return re.sub(r"\s+", " ", text)


def classify_job_type(*chunks: str) -> str | None:
    text = _normalize(" ".join(c for c in chunks if c))
    if not text:
        return None
    for label, keywords in _TYPE_PATTERNS:
        for kw in keywords:
            if kw in text:
                return label
    return None


def detect_remote(*chunks: str) -> bool:
    text = _normalize(" ".join(c for c in chunks if c))
    if not text:
        return False
    return any(kw in text for kw in REMOTE_KEYWORDS)
