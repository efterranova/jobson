#!/usr/bin/env bash
# Cron diario para alimentar la BD que luego consume WP Job Manager (fase 2).
# Diseñado para correr desde launchd/cron en el mismo equipo donde ya hay sesión
# de LinkedIn guardada (sessions/storage_state.json).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [ ! -d ".venv" ]; then
  echo "Falta .venv. Crea el venv: python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && playwright install chromium"
  exit 1
fi

# shellcheck disable=SC1091
source .venv/bin/activate

# Personaliza: una corrida por keyword. La dedupe garantiza que repetir
# llamadas no genera filas nuevas; solo refresca last_seen_at.
KEYWORDS=(
  "developer remoto"
  "frontend mexico"
  "data engineer"
)

for kw in "${KEYWORDS[@]}"; do
  echo "[run_daily] keyword: $kw"
  python3 main.py --feature mixed --keywords "$kw" --limit 50 --days 1 || {
    echo "[run_daily] falló para: $kw"
  }
done
