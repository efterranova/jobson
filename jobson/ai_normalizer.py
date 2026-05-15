"""Normalizador IA de ofertas LinkedIn → JSON estructurado.

Llama OpenRouter (OpenAI-compatible API) para limpiar el texto crudo del scraper
y extraer campos estructurados sin mencionar LinkedIn ni basura UI.

Envs:
    OPENROUTER_API_KEY=sk-or-v1-...
    OPENROUTER_MODEL=google/gemini-2.5-flash   (default)
    OPENROUTER_TIMEOUT=45                       (segundos, default)

Uso:
    from jobson.ai_normalizer import AINormalizer
    ai = AINormalizer.from_env()
    if ai:
        result = ai.normalize(record)  # dict | None

Resultado esperado (cuando éxito):
    {
      "title": str,
      "company": str | None,
      "location": str | None,
      "is_remote": bool,
      "job_type": "full_time|part_time|contract|freelance|internship|per_day|null",
      "seniority": "junior|mid|senior|lead|manager|director|null",
      "salary": str | None,
      "language": "es" | "en",
      "category_hints": list[str],
      "description_html": str,
    }
"""
from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from typing import Any

import requests

LOG = logging.getLogger(__name__)

PROMPT_VERSION = "1"

SYSTEM_PROMPT = """Eres un normalizador profesional de ofertas de empleo en español e inglés.

Recibes texto crudo extraído de LinkedIn que contiene basura: botones de UI (Compartir, Solicitar, Guardar, Reactivar Premium), métricas (X personas han hecho clic), avisos del portal y duplicaciones del título.

Tu trabajo:
1. ELIMINAR cualquier referencia a LinkedIn, botones de UI, métricas del portal, frases promocionales del portal.
2. Devolver UN ÚNICO objeto JSON VÁLIDO sin texto adicional, en este esquema EXACTO:

{
  "title": "Título limpio y único, sin duplicar",
  "company": "Nombre de empresa o null",
  "location": "Ciudad, País o región. Usa 'Remoto' si es 100% remoto. null si no es claro.",
  "is_remote": true/false,
  "job_type": "full_time|part_time|contract|freelance|internship|per_day|null",
  "seniority": "junior|mid|senior|lead|manager|director|null",
  "salary": "Texto del salario o rango (ej: 'USD 2000-3000/mes') o null",
  "language": "es|en",
  "category_hints": ["1 a 3 keywords descriptivas del rol, en minúscula y sin tildes"],
  "description_html": "HTML SIMPLE con <p>, <ul>, <li>, <strong>. Máximo ~600 palabras. Estructura recomendada: <h3>Acerca del rol</h3><p>...</p><h3>Responsabilidades</h3><ul>...</ul><h3>Requisitos</h3><ul>...</ul><h3>Beneficios</h3><ul>...</ul>. Solo incluye secciones para las que haya contenido real. SIN mencionar LinkedIn ni el portal de origen. Idioma del texto original."
}

Reglas estrictas:
- Si un campo no es claro o no está en el texto, usa null (no inventes).
- description_html NUNCA debe contener: 'LinkedIn', 'Solicitar', 'Compartir', 'Reactivar Premium', 'Mostrar más opciones', 'Coincide con tus preferencias', referencias al portal de origen, emojis decorativos sin sentido.
- Mantén HECHOS verificables del texto original: tecnologías, herramientas, idiomas requeridos, rangos de experiencia.
- No agregues marketing genérico ni frases vacías.
- Responde SOLO con el JSON, sin ```json ni explicaciones."""


USER_TEMPLATE = """Normaliza esta oferta:

Título crudo: {title}
Empresa (si scraper la detectó): {company}
Idioma scrapeado (referencia): {keyword_language}
URL: {url}

--- Texto crudo (summary + content) ---
{body}
--- Fin del texto ---

Devuelve el JSON exacto del esquema. Nada más."""


@dataclass(frozen=True)
class AIConfig:
    api_key: str
    model: str
    timeout: int
    referer: str
    app_title: str


def _looks_like_spanish(text: str) -> bool:
    # heurística cruda solo para hint al modelo en el user prompt
    return bool(re.search(r"[ñáéíóú]|\b(de|que|para|con|por)\b", text.lower()))


class AINormalizer:
    BASE_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, cfg: AIConfig):
        self.cfg = cfg
        self.session = requests.Session()

    @classmethod
    def from_env(cls) -> "AINormalizer | None":
        key = os.getenv("OPENROUTER_API_KEY", "").strip()
        if not key:
            return None
        model = os.getenv("OPENROUTER_MODEL", "").strip() or "google/gemini-2.5-flash"
        try:
            timeout = int(os.getenv("OPENROUTER_TIMEOUT", "45"))
        except ValueError:
            timeout = 45
        cfg = AIConfig(
            api_key=key,
            model=model,
            timeout=timeout,
            referer=os.getenv("OPENROUTER_REFERER", "https://erecruit.ca").strip(),
            app_title=os.getenv("OPENROUTER_APP_TITLE", "JobsOn").strip(),
        )
        return cls(cfg)

    @property
    def prompt_version(self) -> str:
        return f"{PROMPT_VERSION}:{self.cfg.model}"

    def normalize(self, record: dict[str, Any]) -> dict[str, Any] | None:
        title = (record.get("title") or "").strip()
        company = (record.get("company") or "").strip()
        url = (record.get("url") or "").strip()
        body_parts = [(record.get("summary") or ""), (record.get("content") or "")]
        body = "\n\n".join(p for p in body_parts if p).strip()
        if len(body) > 12000:
            body = body[:12000]  # cap para mantener costo predecible

        keyword_language = "es" if _looks_like_spanish(title + " " + body[:500]) else "en"

        user_prompt = USER_TEMPLATE.format(
            title=title or "(sin título)",
            company=company or "(no detectado)",
            keyword_language=keyword_language,
            url=url or "(sin url)",
            body=body or "(sin texto)",
        )

        payload = {
            "model": self.cfg.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2,
        }

        headers = {
            "Authorization": f"Bearer {self.cfg.api_key}",
            "Content-Type":  "application/json",
            "HTTP-Referer":  self.cfg.referer,
            "X-Title":       self.cfg.app_title,
        }

        try:
            resp = self.session.post(self.BASE_URL, json=payload, headers=headers, timeout=self.cfg.timeout)
        except requests.RequestException as exc:
            LOG.warning("OpenRouter request falló: %s", exc)
            return None

        if resp.status_code >= 400:
            LOG.warning("OpenRouter %s: %s", resp.status_code, resp.text[:300])
            return None

        try:
            data = resp.json()
            text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        except Exception as exc:  # noqa: BLE001
            LOG.warning("Respuesta OpenRouter mal formada: %s", exc)
            return None

        return self._parse_json(text)

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any] | None:
        if not text:
            return None
        text = text.strip()
        # Defensa contra ```json fences que algunos modelos meten igual
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        try:
            obj = json.loads(text)
        except json.JSONDecodeError:
            # Intento extraer el primer objeto {...}
            m = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not m:
                LOG.warning("AI: no se pudo parsear JSON")
                return None
            try:
                obj = json.loads(m.group(0))
            except json.JSONDecodeError as exc:
                LOG.warning("AI: JSON inválido tras recorte: %s", exc)
                return None
        if not isinstance(obj, dict):
            return None
        return obj
