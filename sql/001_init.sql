create extension if not exists pgcrypto;
create extension if not exists vector;

create table if not exists strategies (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,
  created_at timestamptz not null default now()
);

create table if not exists strategy_versions (
  id uuid primary key default gen_random_uuid(),
  strategy_id uuid not null references strategies(id),
  version text not null,
  weights jsonb not null,
  risk_constraints jsonb not null,
  weights_hash text not null,
  is_published boolean not null default false,
  created_at timestamptz not null default now(),
  unique(strategy_id, version)
);

create table if not exists tickers (
  id uuid primary key default gen_random_uuid(),
  ticker text not null unique,
  market text,
  name text,
  created_at timestamptz not null default now()
);

create table if not exists daily_snapshots (
  id uuid primary key default gen_random_uuid(),
  ticker text not null,
  asof timestamptz not null,
  strategy_version_id text not null,
  snapshot_type text not null default 'FACTS',
  snapshot_json jsonb not null,
  data_quality_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now()
);
create index if not exists idx_snapshots_ticker_asof on daily_snapshots(ticker, asof desc);

create table if not exists reports (
  id uuid primary key default gen_random_uuid(),
  ticker text not null,
  asof timestamptz not null,
  strategy_version_id text not null,
  tier text not null check (tier in ('TIER0','TIER1','TIER2')),
  run_mode text not null default 'LIVE' check (run_mode in ('LIVE','SHADOW','BACKTEST')),
  status text not null check (status in ('QUEUED','RUNNING','DONE','FAILED')),
  schema_version text not null,
  report_json jsonb,
  created_at timestamptz not null default now()
);
create index if not exists idx_reports_ticker_asof on reports(ticker, asof desc);

create table if not exists decision_logs (
  id uuid primary key default gen_random_uuid(),
  report_id uuid not null references reports(id),
  ticker text not null,
  action text not null check (action in ('BUY','WATCH','AVOID')),
  score int not null check (score between 0 and 100),
  confidence numeric(4,3) not null check (confidence between 0 and 1),
  snapshot_ids text[] not null default '{}',
  model_primary text not null default 'mock-primary',
  model_reviewer text not null default 'NONE',
  cost_usd numeric(10,6) not null default 0,
  latency_ms int not null default 0,
  created_at timestamptz not null default now()
);
create index if not exists idx_decision_logs_ticker_created on decision_logs(ticker, created_at desc);

create table if not exists watchlist (
  ticker text primary key,
  tier text not null check (tier in ('TIER0','TIER1','TIER2')),
  status text not null default 'ACTIVE' check (status in ('ACTIVE','PAUSED','ARCHIVED')),
  updated_at timestamptz not null default now()
);
create index if not exists idx_watchlist_tier_updated on watchlist(tier, updated_at desc);

create table if not exists memory_notes (
  id uuid primary key default gen_random_uuid(),
  ticker text not null,
  summary text not null default '',
  content text not null,
  tags text[] not null default '{}',
  importance int not null default 50 check (importance between 0 and 100),
  links text[] not null default '{}',
  created_at timestamptz not null default now()
);

create table if not exists memory_embeddings (
  note_id uuid primary key references memory_notes(id) on delete cascade,
  embedding vector(1536) not null
);

create table if not exists tool_traces (
  id uuid primary key default gen_random_uuid(),
  report_id uuid,
  tool_name text not null,
  input_digest text not null default '',
  latency_ms int not null,
  cost_usd numeric(10,6) not null default 0,
  error_code text not null default '',
  ok boolean not null,
  created_at timestamptz not null default now()
);
