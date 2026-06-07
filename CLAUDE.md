# JobsOn

Feeder de empleos LinkedIn + TuPortalEmpleo → revisor humano → publicación automática en WordPress (WP Job Manager + theme Cariera) en erecruit.ca.

## Stack

- **Python 3.14** + venv en `.venv/`. Activar: `source .venv/bin/activate` o llamar binarios con `.venv/bin/python`.
- **Flask** (UI revisor, port 5050) — `main.py --port 5050`.
- **Playwright** (LinkedIn scraper, headless Chromium).
- **requests** + regex (TPE y Job Bank scrapers, HTTP simple, sin navegador).
- **Supabase** (Postgres) — tabla `linkedin_results`. Conexión en `.env`.
- **OpenRouter** (Gemini 2.5 Flash) — normalizador AI opcional para títulos/descripciones.
- **WordPress** destino: `erecruit.ca` (prod) y `staging2.erecruit.ca`. Plugin custom en `deploy/wordpress/jobson-rest.php` (mu-plugin v1.3.2).

## Pipeline end-to-end

```
SCRAPER (local, APP_ROLE=full)
  ├── LinkedInScraper (Playwright, sesión persistente en sessions/storage_state.json)
  │     scrape_jobs(keywords, limit, days, location)  ← location ej. "Ecuador"
  ├── TuPortalEmpleoScraper (requests HTTP, sin auth)
  └── JobBankScraper (requests HTTP, sin auth — jobbank.gc.ca, Canadá)
        scrape_jobs(keywords, limit, days, location)  ← solo avisos directos (postedonJB)
        ↓
ENRICHMENT (jobson.service.SearchService)
  ├── geo_extractor → city, state, country, work_mode
  ├── job_type_classifier → job_type_guess, is_remote
  └── contact_filter → apply_email, apply_url_external, company_website, contact_status
        ↓
SUPABASE linkedin_results (dedupe por source_type|source_id)
  - review_status: pending → approved/rejected → published
  - wp_status:    pending → synced/skipped/failed/discarded
        ↓
REVISOR Flask UI (/review)
  - Filtros por source/wp_status/contact/review
  - Aprueba/rechaza/sincroniza
        ↓
SYNC a WP (publish_batch en wp_publisher.py)
  - clean_company() purga portal-leaks → derive de email o "Reclutador Independiente"
  - AI normaliza title/content/categories (cache en BD)
  - Featured image por categoría (10 fotos Unsplash) sideloaded vía mu-plugin
  - Mu-plugin crea/encuentra: employer user + company CPT, linkea via _company_manager_id
        ↓
WORDPRESS erecruit.ca (job_listing CPT + Companies extension)
  - Featured image + _job_cover_image (banner grande Cariera)
  - _application = apply_email | apply_url_external | company_website
```

## Comandos clave

```bash
# UI revisor (sirve también el lanzador de búsquedas)
.venv/bin/python main.py --port 5050

# Search por CLI
.venv/bin/python main.py --source linkedin,tpe --keywords "marketing" --limit 30

# Crear admin del revisor
.venv/bin/python main.py --create-admin

# Sync a WP (filtros + flags)
.venv/bin/python main.py --publish-wp \
  --wp-filter pending|synced|failed|all   # filtro por wp_status
  --review approved|published             # filtro por review_status
  --wp-filter-status me_interesa|all      # filtro por user_status
  --wp-status publish|draft|pending       # estado destino del post WP
  --limit 100
  --dry-run                               # imprime payload, no toca WP
  --wp-test                               # prefijo "[JobsOn TEST]" + manifest para cleanup
  --ai-force                              # invalida cache IA y re-llama OpenRouter
  --ai-disable                            # off IA, cae a normalización regex

# Cleanup de un test batch
.venv/bin/python main.py --wp-cleanup-test 20260518-102634
```

## Entornos (.env)

- **`.env`** → staging (`staging2.erecruit.ca`, user `jobson_v1`).
- **`.env.prod`** → producción (`erecruit.ca`, user `directorec`). Gitignoreado.

Para sync contra prod en una sola shell:
```bash
set -a && . ./.env.prod && set +a && .venv/bin/python main.py --publish-wp ...
```

## Reglas estrictas (decisiones del usuario)

1. **Nunca inventar `_company_email` en WP.** Solo se guarda si llega un email corporativo validado. El user de WP (employer) sí usa pattern `no-reply+slug@erecruit.ca` porque WP exige email — pero esa cuenta no se expone públicamente.
2. **Portal-leak purge** (`jobson/wp_normalize.clean_company`):
   - Si `company` matchea `PORTAL_COMPANY_BLACKLIST` (tuportalempleo, computrabajo, bumeran, indeed, linkedin, workable, lever, greenhouse, multitrabajos, elempleo):
     - Email con dominio corporativo → derivar (`csanchez@besttalents.com.ec` → `Besttalents` + website `besttalents.com.ec`).
     - Email genérico (gmail/hotmail/outlook/yahoo/icloud/etc, ver `GENERIC_EMAIL_DOMAINS`) → `"Reclutador Independiente"` (1 entidad compartida).
     - Sin email → skip + reportar en `summary['discarded']`. **No mutamos `wp_status` en Supabase** (preserva historial de sync previo y los deja elegibles para reproceso).
3. **AI override de company validado:** la sugerencia de OpenRouter pasa por `clean_company` antes de sobreescribir. Rechaza strings garbage (`null`, `none`, `n/a`, `unknown`, `desconocida`). Si la IA propone algo no aceptable, mantenemos el valor ya purgado.
4. **Featured image** por categoría desde Unsplash (10 URLs estables). El mu-plugin sideloadea cada URL una sola vez (cache `jobson_image_cache` por `sha1(url)` en una WP option) y reusa el attachment_id.

## Gotchas LinkedIn

- **`wait_until="load"` no funciona con `&location=`** — LinkedIn streams analytics y nunca dispara `load`. Siempre usar `wait_until="domcontentloaded"` (el scraper hace `sleep(4)` después para que pinten las cards).
- **Sesión persistente** en `sessions/storage_state.json`. Si caduca (cookies invalidadas), botón "Renovar sesión" en la UI abre Chromium no-headless para login manual.
- **Filtrar por ciudad/país**: solo via `location=` param (no `geoId=`). El campo `keywords=` busca en título Y descripción del aviso, no como ubicación.
- **TPE scraper actualmente solo guarda el teaser**, no entra al detalle del aviso. Por eso `apply_email`, `content` completo y nombres de empresa quedan limitados. Arreglar entrando a cada URL individual es trabajo pendiente.

## Gotchas Job Bank (jobbank.gc.ca)

- **Solo capturamos avisos DIRECTOS** (`<span class="postedonJB">` en la card). Los avisos agregados (CareerBeacon, Talent.com, etc.) solo enlazan a terceros y no exponen contacto → se descartan en `_parse_cards`.
- **El email/web NO está en el HTML estático.** Se revela con el botón "Show how to apply", que es un **POST JSF parcial** (`jsf.ajax.request`) con `ViewState=stateless`. `JobBankScraper._reveal_apply` lo replica: POST a la URL del posting con los params `jakarta.faces.partial.*` + `action=applynowbutton`. La respuesta es un `<partial-response>` XML con el bloque `applynow` (email, URL de empresa o teléfono).
- **Métodos de aplicación variados:** "By email" (email corporativo o genérico), "By Direct Apply"/"Online" (URL de empresa), "In person"/"By phone" (sin contacto digital → `sin_contacto`/skipped).
- **Búsqueda:** `searchstring=` (matching por ocupación NOC, no full-text literal — frases largas como "Employment Law" devuelven 0; usa términos cortos: `human resources`, `recruiter`, `lawyer`, `compliance`). `locationstring=` ej. `Toronto, ON`. `sort=M` = más recientes primero. Paginación con `&page=N`.
- **`location_text` formato `Ciudad (PROV)`** (ej. `North Vancouver (BC)`); `geo_extractor._extract_jobbank` lo parsea a city + provincia + país Canadá. `Various locations` → city None.
- **Agregadores canadienses** (careerbeacon, talent.com, allstarjobs.ca, civicjobs.ca, eluta, workopolis, neuvoo, jobbank.gc.ca) están en `DEFAULT_PORTAL_BLACKLIST` para que sus URLs no se tomen como web de empresa.

## Mu-plugin WP (deploy/wordpress/jobson-rest.php)

- **v1.3.2** activo en prod y staging. Versión en frontmatter del archivo.
- **Deploy manual** vía SiteGround File Manager o SFTP: copiar a `wp-content/mu-plugins/jobson-rest.php` (sobreescribir). Los mu-plugins se autocargan, no requieren activación.
- Endpoints expuestos:
  - `POST /wp-json/jobson/v1/upsert` — crea/actualiza `job_listing` idempotente por `_jobson_dedupe_key`.
  - `GET /wp-json/jobson/v1/find?dedupe_key=...` — lookup.
- Cada upsert: resuelve employer user → resuelve company CPT (por title) → asigna `_company_manager_id` → sideloadea featured image → setea `_thumbnail_id` + `_job_cover_image` (banner Cariera).

## Estructura de archivos clave

```
jobson/
  scraper/
    linkedin.py        # Playwright + scrape_jobs(location)
    tuportalempleo.py  # HTTP, sin auth
    jobbank.py         # HTTP, sin auth — Job Bank Canadá, reveal JSF de contacto
  storage/
    supabase_repository.py
    sqlite_repository.py
    base.py
  service.py           # SearchService — orquesta scraper + enrichment + upsert
  wp_publisher.py      # WPPublisher — sync a WP, batch + purge + AI cache
  wp_normalize.py      # normalize_record, clean_company, pick_featured_image_url
  ai_normalizer.py     # cliente OpenRouter
  contact_filter.py    # extracción email/URL + clasificación portal vs empresa
  geo_extractor.py
  job_type_classifier.py
  cloudflare_email.py  # decoder de __cf_email__ TPE
  web/
    app.py             # Flask routes
    search_jobs.py     # JobRunner (background scrape con polling de status)
    auth.py            # Flask-Login email/password
    templates/         # index.html, review.html, login.html

deploy/
  wordpress/jobson-rest.php   # mu-plugin (subir manualmente)

supabase/schema.sql           # tabla linkedin_results + índices
sessions/storage_state.json   # cookies LinkedIn (no commitear)
.env / .env.prod              # config staging/prod (.env.prod gitignoreado)
scripts/run_daily.sh          # cron wrapper
```

## Convenciones

- Repos paralelos en `BaseRepository`: sqlite (local dev) y supabase (prod). Misma interfaz.
- `VALID_SOURCES = ("linkedin", "tpe", "jobbank")`. `source_type` en el record: linkedin→`jobs`/`feed`, tpe→`tpe`, jobbank→`jobbank`.
- `VALID_WP_STATUSES = {"pending","synced","skipped","failed","discarded"}`. `synced`/`failed`/`discarded` son **terminales** (upsert preserva el valor previo).
- Featured-image cache en WP es persistente y compartido — borrar un job no borra el attachment.

## Memoria persistente (Claude)

Detalles adicionales en `/Users/erick/.claude/projects/-Users-erick-github-JobsOn/memory/` (índice en `MEMORY.md`).
