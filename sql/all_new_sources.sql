-- ============================================================
-- SerpAPI Google Scholar + Scopus 建表 & RPC 一次性执行
-- ============================================================

create extension if not exists vector;

-- ── serp_scholar_papers ──────────────────────────────────────────
create table if not exists public.serp_scholar_papers (
  id text primary key,
  source text not null default 'serp_scholar',
  source_paper_id text,
  doi text,
  version text,
  title text not null,
  abstract text,
  authors jsonb not null default '[]'::jsonb,
  primary_category text,
  categories jsonb not null default '[]'::jsonb,
  published timestamptz,
  link text,
  embedding vector(384),
  embedding_model text,
  embedding_dim int,
  embedding_updated_at timestamptz,
  updated_at timestamptz not null default now()
);
create index if not exists serp_scholar_papers_source_published_idx on public.serp_scholar_papers (source, published desc);
create index if not exists serp_scholar_papers_published_idx on public.serp_scholar_papers (published desc);
create index if not exists serp_scholar_papers_title_abstract_fts_idx on public.serp_scholar_papers using gin (to_tsvector('english', coalesce(title, '') || ' ' || coalesce(abstract, '')));

-- ── scopus_papers ───────────────────────────────────────────────
create table if not exists public.scopus_papers (
  id text primary key,
  source text not null default 'scopus',
  source_paper_id text,
  doi text,
  version text,
  title text not null,
  abstract text,
  authors jsonb not null default '[]'::jsonb,
  primary_category text,
  categories jsonb not null default '[]'::jsonb,
  published timestamptz,
  link text,
  embedding vector(384),
  embedding_model text,
  embedding_dim int,
  embedding_updated_at timestamptz,
  updated_at timestamptz not null default now()
);
create index if not exists scopus_papers_source_published_idx on public.scopus_papers (source, published desc);
create index if not exists scopus_papers_published_idx on public.scopus_papers (published desc);
create index if not exists scopus_papers_title_abstract_fts_idx on public.scopus_papers using gin (to_tsvector('english', coalesce(title, '') || ' ' || coalesce(abstract, '')));

-------------------------------------------------------------------
-- RPC functions
-------------------------------------------------------------------

-- serp_scholar
create or replace function match_serp_scholar_papers_exact(query_embedding vector, match_count int, filter_published_start timestamptz default null, filter_published_end timestamptz default null)
returns table (id text, title text, abstract text, authors jsonb, primary_category text, categories jsonb, published timestamptz, link text, source text, similarity float8)
language sql stable as $$
  select p.id, p.title, p.abstract, p.authors, p.primary_category, p.categories, p.published, p.link, p.source, 1 - (p.embedding <=> query_embedding) as similarity
  from public.serp_scholar_papers p
  where p.embedding is not null
    and (filter_published_start is null or p.published >= filter_published_start)
    and (filter_published_end is null or p.published < filter_published_end)
  order by p.embedding <=> query_embedding limit match_count;
$$;

create or replace function match_serp_scholar_papers_bm25(query_text text, match_count int, filter_published_start timestamptz default null, filter_published_end timestamptz default null)
returns table (id text, title text, abstract text, authors jsonb, primary_category text, categories jsonb, published timestamptz, link text, source text, similarity float8, score float8)
language sql stable as $$
  select p.id, p.title, p.abstract, p.authors, p.primary_category, p.categories, p.published, p.link, p.source, 0::float8 as similarity,
    ts_rank_cd(to_tsvector('english', coalesce(p.title, '') || ' ' || coalesce(p.abstract, '')), plainto_tsquery('english', query_text)) as score
  from public.serp_scholar_papers p
  where to_tsvector('english', coalesce(p.title, '') || ' ' || coalesce(p.abstract, '')) @@ plainto_tsquery('english', query_text)
    and (filter_published_start is null or p.published >= filter_published_start)
    and (filter_published_end is null or p.published < filter_published_end)
  order by score desc limit match_count;
$$;

-- scopus
create or replace function match_scopus_papers_exact(query_embedding vector, match_count int, filter_published_start timestamptz default null, filter_published_end timestamptz default null)
returns table (id text, title text, abstract text, authors jsonb, primary_category text, categories jsonb, published timestamptz, link text, source text, similarity float8)
language sql stable as $$
  select p.id, p.title, p.abstract, p.authors, p.primary_category, p.categories, p.published, p.link, p.source, 1 - (p.embedding <=> query_embedding) as similarity
  from public.scopus_papers p
  where p.embedding is not null
    and (filter_published_start is null or p.published >= filter_published_start)
    and (filter_published_end is null or p.published < filter_published_end)
  order by p.embedding <=> query_embedding limit match_count;
$$;

create or replace function match_scopus_papers_bm25(query_text text, match_count int, filter_published_start timestamptz default null, filter_published_end timestamptz default null)
returns table (id text, title text, abstract text, authors jsonb, primary_category text, categories jsonb, published timestamptz, link text, source text, similarity float8, score float8)
language sql stable as $$
  select p.id, p.title, p.abstract, p.authors, p.primary_category, p.categories, p.published, p.link, p.source, 0::float8 as similarity,
    ts_rank_cd(to_tsvector('english', coalesce(p.title, '') || ' ' || coalesce(p.abstract, '')), plainto_tsquery('english', query_text)) as score
  from public.scopus_papers p
  where to_tsvector('english', coalesce(p.title, '') || ' ' || coalesce(p.abstract, '')) @@ plainto_tsquery('english', query_text)
    and (filter_published_start is null or p.published >= filter_published_start)
    and (filter_published_end is null or p.published < filter_published_end)
  order by score desc limit match_count;
$$;
