# Build Hostinger (Shared Hosting)

Este paquete está pensado para Hostinger compartido (PHP + Apache), sin servidor Python.

Arquitectura:
- Scraper corre en tu máquina local con Python (`APP_ROLE=full`) y guarda resultados en Supabase.
- En Hostinger publicas el visor cloud (`index.html`) y el módulo de perfil IA (`profile.html`).
- Ambos leen/escriben en Supabase usando `api.php`.

## 1) Preparar Supabase (obligatorio)

1. Entra a tu proyecto de Supabase.
2. Ve a **SQL Editor**.
3. Abre el archivo:
   - `/Users/erick/github/JobsOn/supabase/schema.sql`
   - o `supabase_schema.sql` (si estás usando el .zip de build)
4. Copia todo el contenido y ejecútalo.

Esto crea:
- `linkedin_results` (oportunidades scraping)
- `career_profiles` (perfil profesional)
- `career_profile_reports` (historial de análisis IA)

## 2) (Opcional recomendado) Bucket para CV

Si quieres guardar los archivos CV en la nube (además del texto extraído):

1. En Supabase ve a **Storage**.
2. Crea bucket llamado: `jobson-cv`.
3. Puede ser privado (recomendado).

Nota: aunque no crees bucket, el sistema igual guarda el perfil y texto del CV en tablas.

## 3) Configurar `config.php` para producción

1. En tu máquina:
```bash
cd /Users/erick/github/JobsOn/deploy/hostinger/private
cp config.example.php config.php
```

2. Edita `/Users/erick/github/JobsOn/deploy/hostinger/private/config.php` con valores reales:
- `SUPABASE_URL`
- `SUPABASE_SERVICE_KEY`
- `SUPABASE_TABLE` (normalmente `linkedin_results`)
- `PROFILE_TABLE` (`career_profiles`)
- `PROFILE_REPORTS_TABLE` (`career_profile_reports`)
- `PROFILE_SINGLETON` (`true` recomendado para mantener un perfil principal)
- `SUPABASE_STORAGE_BUCKET` (`jobson-cv` si creaste bucket)
- `OPENROUTER_API_KEY`
- `OPENROUTER_MODEL` (ej: `openai/gpt-4o-mini`)

## 4) Subir a Hostinger

En File Manager de Hostinger:

1. Sube el contenido de `deploy/hostinger/public_html/` dentro de `public_html/`.
   - Deben quedar al menos:
     - `public_html/index.html`
     - `public_html/profile.html`
     - `public_html/api.php`
     - `public_html/.htaccess`

2. Crea carpeta `private` al mismo nivel que `public_html`.
   - Ejemplo: `/home/TU_USUARIO/private/config.php`

3. Sube ahí tu `config.php` (no dentro de `public_html`, salvo fallback).

### Fallback si Hostinger bloquea rutas fuera de `public_html`

Si ves error de config aunque exista en `private/`:

1. Copia `config.php` dentro de `public_html/`.
2. Mantén `.htaccess` para bloquear acceso directo.
3. Prueba de nuevo.

## 5) Uso del módulo cloud

### Oportunidades scraping
- URL: `https://TU-DOMINIO/index.html`
- Puedes filtrar, marcar estado y eliminar (soft delete persistente).
- En el home verás una tarjeta de resumen del perfil para entrar directo a editarlo.

### Perfil IA
- URL: `https://TU-DOMINIO/profile.html`
- Completa perfil profesional.
- Sube CV en español e inglés.
- Activa “Generar análisis IA al guardar”.
- Se guarda:
  - perfil en `career_profiles`
  - reportes IA en `career_profile_reports`
- Con `PROFILE_SINGLETON=true`, cada guardado actualiza el perfil principal (persistencia estable sin duplicar).

## 6) Si algo falla

- Error `Config incompleta`: revisa `SUPABASE_URL` y `SUPABASE_SERVICE_KEY`.
- Error al guardar perfil: verifica que ejecutaste `schema.sql` completo.
- Error IA: revisa `OPENROUTER_API_KEY` y `OPENROUTER_MODEL`.
- Error al subir CV a storage: revisa bucket `jobson-cv` y nombre configurado.
- Si usas RLS, con service key normalmente no aplica restricción.
