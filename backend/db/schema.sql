-- DocQuery schema: run this once in the Supabase SQL editor.
create extension if not exists vector;
create extension if not exists pg_trgm;

create table if not exists documents (
  id uuid primary key default gen_random_uuid(),
  filename text not null,
  uploaded_at timestamptz default now(),
  status text not null default 'processing', -- processing | ready | failed
  page_count int,
  chunk_count int,
  error_message text
);

create table if not exists document_chunks (
  id uuid primary key default gen_random_uuid(),
  document_id uuid references documents(id) on delete cascade,
  chunk_text text not null,
  chunk_index int not null,
  page_number int,
  embedding vector(768), -- Gemini text-embedding-004 dimension
  created_at timestamptz default now()
);

create index if not exists document_chunks_embedding_idx
  on document_chunks using ivfflat (embedding vector_cosine_ops) with (lists = 100);

create index if not exists document_chunks_document_id_idx
  on document_chunks (document_id);

create index if not exists document_chunks_fts_idx
  on document_chunks using gin (to_tsvector('english', chunk_text));

create table if not exists conversations (
  id uuid primary key default gen_random_uuid(),
  document_id uuid references documents(id) on delete cascade,
  created_at timestamptz default now()
);

create table if not exists messages (
  id uuid primary key default gen_random_uuid(),
  conversation_id uuid references conversations(id) on delete cascade,
  role text not null, -- user | assistant
  content text not null,
  source_chunk_ids uuid[],
  created_at timestamptz default now()
);

create index if not exists messages_conversation_id_idx
  on messages (conversation_id);

-- One row per chat query, used to power the retrieval-quality stats panel.
create table if not exists query_log (
  id uuid primary key default gen_random_uuid(),
  document_id uuid references documents(id) on delete cascade,
  question text not null,
  avg_similarity float,
  created_at timestamptz default now()
);

create index if not exists query_log_created_at_idx
  on query_log (created_at desc);

-- Self-healing cascade fix: CREATE TABLE IF NOT EXISTS above only applies "on delete cascade"
-- to brand-new databases. Re-running this block against an already-created database (e.g. one
-- set up before this fix existed) drops and re-adds these two FKs with cascade, so deleting a
-- document doesn't fail with a foreign key violation just because it has conversations/queries.
alter table conversations drop constraint if exists conversations_document_id_fkey;
alter table conversations add constraint conversations_document_id_fkey
  foreign key (document_id) references documents(id) on delete cascade;

alter table query_log drop constraint if exists query_log_document_id_fkey;
alter table query_log add constraint query_log_document_id_fkey
  foreign key (document_id) references documents(id) on delete cascade;

-- Semantic search via RPC (cosine similarity, filtered to one document).
create or replace function match_document_chunks(
  p_document_id uuid,
  query_embedding vector(768),
  match_count int default 5
)
returns table (
  id uuid,
  chunk_text text,
  chunk_index int,
  page_number int,
  similarity float
)
language sql stable
as $$
  select
    document_chunks.id,
    document_chunks.chunk_text,
    document_chunks.chunk_index,
    document_chunks.page_number,
    1 - (document_chunks.embedding <=> query_embedding) as similarity
  from document_chunks
  where document_chunks.document_id = p_document_id
  order by document_chunks.embedding <=> query_embedding
  limit match_count;
$$;

-- Keyword search via RPC (Postgres full-text search, filtered to one document).
create or replace function keyword_search_document_chunks(
  p_document_id uuid,
  search_query text,
  match_count int default 5
)
returns table (
  id uuid,
  chunk_text text,
  chunk_index int,
  page_number int,
  rank float
)
language sql stable
as $$
  select
    document_chunks.id,
    document_chunks.chunk_text,
    document_chunks.chunk_index,
    document_chunks.page_number,
    ts_rank(to_tsvector('english', document_chunks.chunk_text), plainto_tsquery('english', search_query)) as rank
  from document_chunks
  where document_chunks.document_id = p_document_id
    and to_tsvector('english', document_chunks.chunk_text) @@ plainto_tsquery('english', search_query)
  order by rank desc
  limit match_count;
$$;
