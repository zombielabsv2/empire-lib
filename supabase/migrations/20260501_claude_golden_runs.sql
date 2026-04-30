-- Golden-set eval results, populated by empire.eval.store.store_run.
-- Each row is one nightly replay of a frozen prompt input through the
-- live empire prompt function, scored on schema match + substring
-- assertions. See src/empire/eval/README.md for the consumption pattern.
--
-- Status as of 2026-05-01: applied to project ejvavmpieilvigjktugh via
-- Supabase MCP. This file is the source-of-truth copy for replay /
-- audit.

create table if not exists public.claude_golden_runs (
    id          uuid primary key default gen_random_uuid(),
    app         text not null,
    action      text not null,
    passed      boolean not null,
    duration_s  numeric not null,
    ran_at      timestamptz not null,
    failures    jsonb,
    output      jsonb,
    exception   text
);

create index if not exists idx_golden_runs_app_action_ran_at
    on public.claude_golden_runs (app, action, ran_at desc);

comment on table public.claude_golden_runs is
    'Golden-set eval runs: replays of frozen prompt inputs through live empire prompt functions, scored on schema match + substring assertions. Populated by empire.eval.store.store_run from project-specific golden cron scripts.';

alter table public.claude_golden_runs enable row level security;

-- Service role full access; anon and authenticated have no policy → no access.
-- Goldens contain prompt outputs that may include user data; don't expose to clients.
