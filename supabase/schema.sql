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
  created_at timestamptz not null default now()
);

create index if not exists idx_linkedin_results_scraped_at
  on public.linkedin_results (scraped_at desc);

create index if not exists idx_linkedin_results_source_type
  on public.linkedin_results (source_type);

-- Si tienes RLS activado, crea políticas para permitir insertar/leer con tu key.
-- Ejemplo mínimo (solo para pruebas privadas):
-- alter table public.linkedin_results enable row level security;
-- create policy "allow read" on public.linkedin_results for select using (true);
-- create policy "allow insert" on public.linkedin_results for insert with check (true);
-- create policy "allow update" on public.linkedin_results for update using (true);
