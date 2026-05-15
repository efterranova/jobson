"""Entrypoint serverless para Vercel.

Vercel busca handlers en /api/*. Este archivo expone la WSGI app de Flask como
variable `app` que la build de Python de Vercel detecta automáticamente.
"""
import os
import sys
from pathlib import Path

# Inserta el repo root en sys.path para que `jobson` importe correctamente.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Fuerza modo viewer: no Playwright, no scraper, no jobs en background.
os.environ.setdefault("APP_ROLE", "viewer")

from jobson.web.app import create_app  # noqa: E402

app = create_app()
