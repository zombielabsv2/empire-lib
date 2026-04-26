# empire-lib

Shared infrastructure for the empire — **hard-block, not warn**.

The most-duplicated code across ~20 empire projects (KBK / kari-growth-platform,
AstroMedha v3, mutual-fund-analyzer, empire-dashboard, lotus-lane, kairav-os,
moonpath, astromedha-ads, etc.) lives here in one place.

## Philosophy

This library raises on misuse rather than logging a warning, because advisory
rules don't prevent autopilots from violating them. Memory files and CLAUDE.md
notes are advice; code guards are enforcement. Every empire-wide rule that
matters (no Opus on the API, every email needs an `email_log` row, every
Claude call needs `app`+`action` for cost attribution) is enforced as a raise
here so it can't ship broken.

The single exception: `empire.api.usage_logger.log_usage` is fail-silent on
Supabase outage. Accounting must never block the main API call.

## Install

The repo is **private**. Install via SSH:

```bash
pip install git+ssh://git@github.com/zombielabsv2/empire-lib.git
```

Or pin to a tag:

```bash
pip install git+ssh://git@github.com/zombielabsv2/empire-lib.git@v0.1.0
```

For local dev (in this repo):

```bash
pip install -e ".[test]"
```

## Modules

### `empire.config.supabase_creds`

Multi-layout Supabase credential resolver. Tries env vars, then top-level
`st.secrets`, then nested `[supabase]` / `[connections.supabase]` /
`[database]` sections. Caches **only** positive results — the negative case
is re-checked every call so a late-arriving secrets layer can't poison the
process (the KBK Approvals 2026-04-25 cold-start bug).

```python
from empire.config import get_supabase_creds, SupabaseCredsNotFound

try:
    url, key = get_supabase_creds()
except SupabaseCredsNotFound:
    st.error("Supabase secrets not configured")
```

### `empire.config.env_check`

`require_env(*keys)` returns the values, raises `MissingEnvVars` listing all
missing ones. Uses `k not in os.environ` semantics — empty strings count as
**set** (intentional dry-run signal).

```python
from empire.config import require_env, is_set

env = require_env("ANTHROPIC_API_KEY", "RESEND_API_KEY")
if is_set("DRY_RUN"):  # true even if value is ""
    ...
```

### `empire.api.anthropic_client`

httpx-based wrapper around `https://api.anthropic.com/v1/messages`. Hard-blocks
Opus models, hard-blocks calls without `app`+`action` (so cost reports can
attribute every row), auto-logs to `api_usage_log` on 200 OK, retries 3x on
429/5xx with exponential backoff.

```python
from empire.api import post_messages

result = post_messages(
    app="kbk",
    action="daily_brief",
    model="claude-sonnet-4-6",  # default; "opus" anywhere → raises
    messages=[{"role": "user", "content": "hi"}],
    max_tokens=512,
)
print(result["content"][0]["text"])
```

### `empire.api.usage_logger`

`log_usage(...)` appends a row to Supabase `api_usage_log`. **Fail-silent**
on Supabase outage (this is the only place that warns instead of raising,
because accounting must never break the main API call).

```python
from empire.api import log_usage

log_usage(
    app="astromedha",
    action="morning_briefing",
    model="claude-sonnet-4-6",
    input_tokens=1200,
    output_tokens=400,
)
```

### `empire.email.sender`

`send_email_tracked(...)` posts to Resend, then writes the paired `email_log`
row keyed by `resend_id`. Calling without `user_id`+`profile_person_key`
raises `MissingTrackingContext` (so the engagement webhook can't silently
drop opens/clicks again).

```python
from empire.email import send_email_tracked

send_email_tracked(
    to="user@example.com",
    subject="Daily guidance",
    html="<p>hello</p>",
    user_id="user_abc",
    profile_person_key="kairav",
)
```

### `empire.test.guards`

Autouse pytest fixture blocking live API calls. Add one line to any project's
`conftest.py`:

```python
# conftest.py
from empire.test.guards import block_live_api_hosts  # noqa: F401
```

Now any test that hits `api.anthropic.com`, `api.resend.com`,
`graph.facebook.com`, or `googleads.googleapis.com` raises `LiveAPIBlocked`.
This catches the failure mode where tests patch `caller.httpx.post` but a
sibling helper still imports `httpx.post` directly and rings up real cost
(the 2026-04-24 leak: $2.32 / 642 calls).

## Why each module exists

Every module here closes a real bug from a real session. The full audit
that drove this v0.1.0 lives at `~/EMPIRE_REVIEW_BUGS_AND_COMPONENTS.md`.

| Module | Bug it prevents |
|--------|-----------------|
| `supabase_creds` | KBK Approvals 2026-04-25 — cached negative result kept page broken across reboots |
| `env_check` | Empty-string env vars wrongly treated as missing (`feedback_empty_string_env_check.md`) |
| `anthropic_client` (no Opus) | Empire-wide ban (`feedback_no_opus_on_api.md`); 5x cost if violated |
| `anthropic_client` (telemetry) | 420 unlabeled rows in cost reports because `app`/`action` weren't enforced |
| `usage_logger` | Cost reports invisible without per-call rows in `api_usage_log` |
| `sender` (tracking required) | `feedback_resend_must_pair_email_log.md` — webhook drops opens/clicks |
| `test.guards` | $2.32 / 642 calls leaked when tests patched caller, not API boundary |

## Migration roadmap

Empire repos that will adopt this in v0.2 / v0.3:

| Repo | Modules to migrate | Priority |
|------|-------------------|----------|
| kari-growth-platform | all 6 | P0 — reference impl source |
| astromedha-ads | anthropic_client, usage_logger, supabase_creds | P0 |
| empire-dashboard | usage_logger, supabase_creds | P1 |
| lotus-lane | usage_logger, supabase_creds | P1 |
| mutual-fund-analyzer | supabase_creds | P1 |
| moonpath | supabase_creds, env_check | P1 |
| astromedha-v3 | anthropic_client, sender, usage_logger | P0 |
| kairav-os | usage_logger, supabase_creds | P2 |
| All projects | test.guards | P0 — drop into every conftest |

Migration is a separate session. v0.1.0 ships the package; nothing else changes
until each repo opts in.

## Versioning

SemVer. Breaking changes bump the major. The `0.x` line means anything can change
between minors until the empire is fully migrated.

## License

MIT.
