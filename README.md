# JobsOn - Fase 1 (LinkedIn + UI + Persistencia)

Proyecto Python para buscar empleos en LinkedIn con estrategia mixta:
- `jobs`: sección oficial de vacantes.
- `feed`: búsqueda abierta en muro/post.
- `mixed`: combina ambos.

Incluye:
- Inicio de sesión manual cuando no hay sesión guardada.
- Reuso de sesión guardada para correr en modo headless en siguientes ejecuciones.
- Interfaz visual web para lanzar búsquedas y revisar resultados.
- Persistencia incremental con deduplicación en Supabase (o fallback SQLite local).

## 1) Instalación

```bash
cd /Users/erick/github/JobsOn
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## 2) Configurar Supabase (recomendado)

1. Entra a tu proyecto de Supabase.
2. Ve a **SQL Editor**.
3. Abre el archivo `/Users/erick/github/JobsOn/supabase/schema.sql`.
4. Copia su contenido y ejecútalo en Supabase.
5. En Supabase, ve a **Project Settings > API** y copia:
- `Project URL`
- `anon` key (o service role key en entorno privado)

## 3) Configurar variables de entorno

```bash
cd /Users/erick/github/JobsOn
cp .env.example .env
```

Edita `.env` y completa:
- `SUPABASE_URL`
- `SUPABASE_KEY`
- `SUPABASE_TABLE` (deja `linkedin_results` si usas la tabla del schema)
- `APP_ROLE`:
  - `full` para tu máquina local (puede scrapear + visualizar).
  - `viewer` para servidor en producción (solo visualiza desde DB).

Ejemplo correcto de `.env` (importante: cada valor en una sola línea):
```bash
SUPABASE_URL=https://TU-PROYECTO.supabase.co
SUPABASE_KEY=TU_KEY_COMPLETA
SUPABASE_TABLE=linkedin_results
APP_ROLE=full
SQLITE_PATH=/Users/erick/github/JobsOn/data/jobson.db
WEB_HOST=127.0.0.1
WEB_PORT=5050
```

## 4) Ejecutar interfaz visual (recomendado)

```bash
cd /Users/erick/github/JobsOn
source .venv/bin/activate
python3 main.py --open
```

Luego abre en navegador:
- `http://127.0.0.1:5050`

## 5) Ejecutar por CLI (similar a tu flujo)

### Menú interactivo
```bash
cd /Users/erick/github/JobsOn
source .venv/bin/activate
python3 main.py --cli
```

### Comando directo
```bash
cd /Users/erick/github/JobsOn
source .venv/bin/activate
python3 main.py --feature mixed --keywords "python remoto" --limit 20 --days 7
```

## Notas importantes

- Primera ejecución sin sesión: se abrirá navegador visible para login manual.
- Al detectar login, se guarda sesión en `sessions/storage_state.json`.
- Siguientes ejecuciones usarán sesión guardada (headless) si sigue válida.
- Cada ejecución también guarda respaldo CSV local en `data/`.
- Si no configuras Supabase, se usa SQLite local en `data/jobson.db`.
- Si ves error `401 Unauthorized`, revisa:
  - URL y key correctas en `.env`.
  - que ejecutaste el SQL de `supabase/schema.sql`.
  - que RLS/policies permiten lectura e inserción.

## Arquitectura para producción (scraper local + visor en nube)

1. En tu Mac (local), usa `APP_ROLE=full` y ejecuta scraping.
2. En producción, despliega la misma app con `APP_ROLE=viewer`.
3. Ambos apuntan al mismo Supabase.
4. Resultado: lo que scrapeas localmente aparece en producción sin repetir datos.
