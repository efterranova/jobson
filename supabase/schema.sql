create extension if not exists pgcrypto;

create table if not exists public.linkedin_results (
  id uuid primary key default gen_random_uuid(),
  source_type text not null check (source_type in ('jobs', 'feed')),
  source_id text,
  title text,
  company text,
  author text,
  summary text,
  content text,
  seniority text,
  apply_type text,
  url text,
  keyword text not null,
  search_mode text not null,
  scraped_at timestamptz not null,
  dedupe_key text not null unique,
  user_status text,
  status_updated_at timestamptz,
  deleted_at timestamptz,
  created_at timestamptz not null default now()
);

alter table public.linkedin_results
  add column if not exists user_status text;

alter table public.linkedin_results
  add column if not exists status_updated_at timestamptz;

alter table public.linkedin_results
  add column if not exists deleted_at timestamptz;

-- Cache de normalización IA
alter table public.linkedin_results
  add column if not exists ai_normalized_json jsonb;
alter table public.linkedin_results
  add column if not exists ai_prompt_version text;
alter table public.linkedin_results
  add column if not exists ai_normalized_at timestamptz;

-- Workflow de revisión para publicación en WordPress
alter table public.linkedin_results
  add column if not exists review_status text default 'pending';
alter table public.linkedin_results
  add column if not exists reviewed_by uuid;
alter table public.linkedin_results
  add column if not exists reviewed_at timestamptz;
alter table public.linkedin_results
  add column if not exists published_post_id integer;
alter table public.linkedin_results
  add column if not exists published_wp_url text;
alter table public.linkedin_results
  add column if not exists published_at timestamptz;

-- Ubicación estructurada
alter table public.linkedin_results add column if not exists location_text text;
alter table public.linkedin_results add column if not exists city text;
alter table public.linkedin_results add column if not exists state text;
alter table public.linkedin_results add column if not exists country text;
alter table public.linkedin_results add column if not exists work_mode text;
alter table public.linkedin_results add column if not exists is_remote boolean;
alter table public.linkedin_results add column if not exists job_type_guess text;

-- Contacto extraído (núcleo del filtro WPJM)
alter table public.linkedin_results add column if not exists apply_email text;
alter table public.linkedin_results add column if not exists apply_url_external text;
alter table public.linkedin_results add column if not exists company_website text;
alter table public.linkedin_results add column if not exists extracted_emails jsonb default '[]'::jsonb;
alter table public.linkedin_results add column if not exists extracted_urls jsonb default '[]'::jsonb;
alter table public.linkedin_results add column if not exists contact_status text default 'sin_contacto';

-- Elegibilidad técnica para WP (independiente de la decisión humana en review_status)
alter table public.linkedin_results add column if not exists wp_status text default 'skipped';
alter table public.linkedin_results add column if not exists wp_post_id bigint;
alter table public.linkedin_results add column if not exists wp_synced_at timestamptz;
alter table public.linkedin_results add column if not exists wp_last_error text;

-- Tiempos extendidos (first_seen_at = nunca cambia; last_seen_at = se refresca en cada re-scrape)
alter table public.linkedin_results add column if not exists first_seen_at timestamptz default now();
alter table public.linkedin_results add column if not exists last_seen_at timestamptz default now();

-- Constraints
do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'linkedin_results_contact_status_chk') then
    alter table public.linkedin_results
      add constraint linkedin_results_contact_status_chk
      check (contact_status in ('tiene_email','tiene_web','tiene_ambos','sin_contacto') or contact_status is null);
  end if;
  if not exists (select 1 from pg_constraint where conname = 'linkedin_results_wp_status_chk') then
    alter table public.linkedin_results
      add constraint linkedin_results_wp_status_chk
      check (wp_status in ('pending','synced','skipped','failed','discarded') or wp_status is null);
  end if;
end$$;

create index if not exists idx_linkedin_results_first_seen on public.linkedin_results (first_seen_at desc);
create index if not exists idx_linkedin_results_last_seen on public.linkedin_results (last_seen_at desc);
create index if not exists idx_linkedin_results_wp_status on public.linkedin_results (wp_status);
create index if not exists idx_linkedin_results_contact_status on public.linkedin_results (contact_status);

-- source_type ahora también puede valer 'tuportalempleo'. Drop check viejo, recreamos.
do $$
begin
  if exists (select 1 from pg_constraint where conname = 'linkedin_results_source_type_check') then
    alter table public.linkedin_results drop constraint linkedin_results_source_type_check;
  end if;
end$$;

do $$
begin
  -- Drop existing variants to recreate clean
  if exists (select 1 from pg_constraint where conname = 'linkedin_results_source_type_chk') then
    alter table public.linkedin_results drop constraint linkedin_results_source_type_chk;
  end if;
  alter table public.linkedin_results
    add constraint linkedin_results_source_type_chk
    check (source_type in ('jobs','feed','tpe','tuportalempleo'));
end$$;

do $$
begin
  if not exists (
    select 1 from pg_constraint where conname = 'linkedin_results_review_status_chk'
  ) then
    alter table public.linkedin_results
      add constraint linkedin_results_review_status_chk
      check (review_status in ('pending','approved','rejected','published') or review_status is null);
  end if;
end$$;

create index if not exists idx_linkedin_results_review_status
  on public.linkedin_results (review_status);

create index if not exists idx_linkedin_results_published_at
  on public.linkedin_results (published_at desc);

-- Revisores (auth simple)
create table if not exists public.reviewers (
  id uuid primary key default gen_random_uuid(),
  email text not null unique,
  password_hash text not null,
  name text,
  role text not null default 'reviewer' check (role in ('admin','reviewer')),
  active boolean not null default true,
  created_at timestamptz not null default now(),
  last_login_at timestamptz
);

create index if not exists idx_reviewers_email on public.reviewers (email);

create index if not exists idx_linkedin_results_scraped_at
  on public.linkedin_results (scraped_at desc);

create index if not exists idx_linkedin_results_source_type
  on public.linkedin_results (source_type);

create index if not exists idx_linkedin_results_user_status
  on public.linkedin_results (user_status);

create index if not exists idx_linkedin_results_deleted_at
  on public.linkedin_results (deleted_at);

do $$
begin
  if not exists (
    select 1
    from pg_constraint
    where conname = 'linkedin_results_user_status_chk'
  ) then
    alter table public.linkedin_results
      add constraint linkedin_results_user_status_chk
      check (user_status in ('me_interesa', 'no_me_interesa', 'ya_aplique') or user_status is null);
  end if;
end$$;

-- Si tienes RLS activado, crea políticas para permitir insertar/leer con tu key.
-- Ejemplo mínimo (solo para pruebas privadas):
-- alter table public.linkedin_results enable row level security;
-- create policy "allow read" on public.linkedin_results for select using (true);
-- create policy "allow insert" on public.linkedin_results for insert with check (true);
-- create policy "allow update" on public.linkedin_results for update using (true);

-- ======================================================
-- Módulo cloud: Perfil profesional + análisis IA
-- ======================================================

create table if not exists public.career_profiles (
  id uuid primary key default gen_random_uuid(),
  full_name text not null,
  headline text,
  email text,
  phone text,
  location text,
  linkedin_url text,
  portfolio_url text,
  work_authorization text,
  target_roles text not null,
  target_industries text,
  target_countries text,
  work_modes text,
  employment_types text,
  seniority_target text,
  salary_expectation text,
  preferred_languages text,
  skills_core text,
  strengths text,
  constraints text,
  notes text,
  cv_es_filename text,
  cv_es_storage_path text,
  cv_es_text text,
  cv_en_filename text,
  cv_en_storage_path text,
  cv_en_text text,
  ai_brief text,
  ai_analysis text,
  ai_structured jsonb,
  ai_model text,
  ai_last_error text,
  ai_generated_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index if not exists idx_career_profiles_updated_at
  on public.career_profiles (updated_at desc);

create index if not exists idx_career_profiles_full_name
  on public.career_profiles (full_name);

create table if not exists public.career_profile_reports (
  id uuid primary key default gen_random_uuid(),
  profile_id uuid not null references public.career_profiles(id) on delete cascade,
  provider text not null default 'openrouter',
  model text,
  brief text,
  analysis text,
  structured jsonb,
  input_snapshot jsonb,
  prompt_version text,
  created_at timestamptz not null default now()
);

create index if not exists idx_career_profile_reports_profile_id
  on public.career_profile_reports (profile_id);

create index if not exists idx_career_profile_reports_created_at
  on public.career_profile_reports (created_at desc);

-- Opcional: bucket para CV en Supabase Storage (si guardarás archivos allí)
-- insert into storage.buckets (id, name, public)
-- values ('jobson-cv', 'jobson-cv', false)
-- on conflict (id) do nothing;
