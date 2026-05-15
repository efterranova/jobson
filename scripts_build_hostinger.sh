#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
STAMP="$(date +%Y%m%d_%H%M%S)"
OUT_DIR="$ROOT/dist"
PKG_DIR="$OUT_DIR/jobson_hostinger_$STAMP"

mkdir -p "$PKG_DIR"

cp -R "$ROOT/deploy/hostinger/public_html" "$PKG_DIR/public_html"
cp -R "$ROOT/deploy/hostinger/private" "$PKG_DIR/private"
cp "$ROOT/deploy/hostinger/README.md" "$PKG_DIR/README.md"
cp "$ROOT/supabase/schema.sql" "$PKG_DIR/supabase_schema.sql"

if command -v zip >/dev/null 2>&1; then
  (cd "$OUT_DIR" && zip -r "jobson_hostinger_$STAMP.zip" "jobson_hostinger_$STAMP" >/dev/null)
  echo "Build listo: $OUT_DIR/jobson_hostinger_$STAMP.zip"
else
  echo "Build listo en carpeta: $PKG_DIR"
  echo "No se encontró zip, por eso no se creó archivo .zip"
fi
