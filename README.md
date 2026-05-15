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
- Seguimiento por registro con estados: `me_interesa`, `no_me_interesa`, `ya_aplique`.
- Filtros de búsqueda avanzados: palabras excluyentes e idiomas permitidos.
- Módulo cloud para perfil profesional + CV ES/EN + análisis IA persistente.

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

### Comando directo con filtros nuevos
```bash
cd /Users/erick/github/JobsOn
source .venv/bin/activate
python3 main.py --feature mixed --keywords "python remoto" --exclude-keywords "senior,manager,sales" --languages "es,en" --limit 30 --days 7
```

## Importar a WordPress (WP Job Manager + Cariera, ej. erecruit.ca)

JobsOn puede publicar los registros scrapeados como `job_listing` en un WordPress
con **WP Job Manager** + theme **Cariera** (el stack confirmado de erecruit.ca).

### 1) Subir el mu-plugin al WP destino

WP Job Manager no expone sus meta `_job_*` al REST API por defecto, así que se
incluye un mu-plugin que: (a) los registra para REST, (b) añade un endpoint
custom `/wp-json/jobson/v1/upsert` con idempotencia por `_jobson_dedupe_key`.

1. Copia `deploy/wordpress/jobson-rest.php` al WP en
   `wp-content/mu-plugins/jobson-rest.php` (crea la carpeta `mu-plugins/` si
   no existe). Los mu-plugins **se activan solos**, no requieren UI.
   - Vía SFTP/File Manager del staging, o vía SSH:
     `scp deploy/wordpress/jobson-rest.php user@host:/path/wp-content/mu-plugins/`.
2. Verifica que aparezca el endpoint:
   `curl https://staging.erecruit.ca/wp-json/ | grep jobson`.

### 2) Generar Application Password en WP

1. WP-Admin → **Users → tu usuario admin → Application Passwords**.
2. Nombre: `JobsOn` → **Add New Application Password**.
3. WP te muestra la password con espacios (ej. `abcd 1234 efgh ...`). Cópiala
   tal cual; los espacios son parte del valor.

### 3) Configurar `.env`

Añade:
```bash
WP_BASE_URL=https://staging.erecruit.ca
WP_USER=tu_usuario_admin
WP_APP_PASSWORD=xxxx xxxx xxxx xxxx xxxx xxxx
WP_DEFAULT_STATUS=draft       # publish | draft | pending  (draft es lo conservador)
WP_EXPIRES_DAYS=30
```

### 4) Ejecutar la importación

Dry-run primero (no postea, muestra el payload):
```bash
python3 main.py --publish-wp --wp-filter-status me_interesa --limit 5 --dry-run
```

Importación real, solo los marcados como `me_interesa`, como borradores:
```bash
python3 main.py --publish-wp --wp-filter-status me_interesa --limit 50
```

Publicar TODOS los registros (sin filtrar por user_status), en estado publicado:
```bash
python3 main.py --publish-wp --wp-filter-status all --wp-status publish --limit 200
```

### Cómo se mapea cada campo

| Campo en WP (WPJM/Cariera) | Origen JobsOn |
|---|---|
| `post_title` | `title` |
| `post_content` | `summary` + `content` + link LinkedIn |
| `_company_name` | `company` |
| `_application` | `url` |
| `_job_expires` | `scraped_at` + `WP_EXPIRES_DAYS` |
| `_remote_position` | inferido si título/contenido contiene "remote/remoto/home office/teletrabajo" |
| taxonomía `job-types` | inferido (Full Time / Part Time / Contract / Internship / Temporary) |
| taxonomía `job-categories` | derivado de `keyword` |
| `_jobson_dedupe_key` | `dedupe_key` — usado para idempotencia |

Reejecutar el comando no duplica: si el `dedupe_key` ya existe en WP, se
actualiza el post existente en lugar de crear uno nuevo.

## Notas importantes

- Primera ejecución sin sesión: se abrirá navegador visible para login manual.
- Al detectar login, se guarda sesión en `sessions/storage_state.json`.
- Siguientes ejecuciones usarán sesión guardada (headless) si sigue válida.
- Cada ejecución también guarda respaldo CSV local en `data/`.
- Si no configuras Supabase, se usa SQLite local en `data/jobson.db`.
- Para filtro de idioma se usa `langdetect` local (no requiere IA/OpenRouter).
- Si ves error `401 Unauthorized`, revisa:
  - URL y key correctas en `.env`.
  - que ejecutaste el SQL de `supabase/schema.sql`.
  - que RLS/policies permiten lectura, inserción y actualización.

## Arquitectura para producción (scraper local + visor en nube)

1. En tu Mac (local), usa `APP_ROLE=full` y ejecuta scraping.
2. En producción, despliega la misma app con `APP_ROLE=viewer`.
3. Ambos apuntan al mismo Supabase.
4. Resultado: lo que scrapeas localmente aparece en producción sin repetir datos.

## Módulo Cloud de Perfil IA

En el build de Hostinger (`deploy/hostinger`) ahora existe:
- `profile.html`: formulario de perfil profesional.
- `api.php?action=profile_save`: guarda perfil, CV y análisis IA.
- Persistencia en tablas:
  - `career_profiles`
  - `career_profile_reports`

Para habilitar IA, configura `OPENROUTER_API_KEY` en `deploy/hostinger/private/config.php`.
