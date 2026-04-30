# empire.eval — golden-set evaluation scaffolding

Catches **quality drift** on prompts that ship to users. Cost drift is
caught by `empire-dashboard/api_cost_report.py` (token-per-call deltas).
Schema drift is caught by `empire.lint.json_shape`. This module catches
the third surface: "the model produced valid JSON that's structurally
fine but semantically worse" — by replaying frozen inputs through the
prompt and asserting on properties of the output.

## What's here today

- `runner.py` — `GoldenSpec`, `run_golden(spec) -> GoldenResult`
- `store.py` — `store_run(result)` writes to Supabase `claude_golden_runs`
- Supabase migration: `supabase/migrations/20260501_claude_golden_runs.sql` (already applied)

## Pattern

Each project owns its goldens. A golden is a Python script that calls
the project's actual prompt function (not a re-implementation) with a
frozen fixture and asserts:

- **Schema match** via `expected_shape` (passed to `empire.lint.json_shape`)
- **Required substrings** in the concatenated output (e.g. `"Kairav"`,
  `"Avaz"` for kairav-os emails)
- **Banned substrings** that must never appear (e.g. `"Sanganer"`,
  `"natural dye"` — the same load-bearing rules as `empire.lint.copy_guards`)

Example (would live at `kairav-os/tests/goldens/weekly_email.py`):

```python
import sys
from datetime import date

from empire.eval import GoldenSpec, run_golden, store_run

# Local imports of the project under test.
sys.path.insert(0, "/path/to/kairav-os")
from weekly_email import _generate_digest, _DIGEST_SHAPE

GOLDEN = GoldenSpec(
    app="kairav_os",
    action="weekly_email",
    prompt_fn=_generate_digest,
    prompt_args=(
        [{"date": "2026-04-25", "domain": "AAC", "description": "Used HELP word twice", "score": 4}],
        [],
        [{"date": "2026-04-26", "channel": "Avaz", "topic": "asked for snack"}],
        [{"status": "achieved", "domain": "Communication", "description": "Initiated request"}],
    ),
    expected_shape=_DIGEST_SHAPE,
    must_contain=["Kairav", "Avaz"],
    must_not_contain=["—", "delve", "tapestry"],  # AI tells
)

if __name__ == "__main__":
    result = run_golden(GOLDEN)
    store_run(result)
    if not result.passed:
        for f in result.failures:
            print(f"  FAIL: {f}")
        sys.exit(1)
    print(f"PASS in {result.duration_s:.1f}s")
```

## How to schedule

Each project gets a nightly GH Actions cron that runs its goldens
and writes results to Supabase. Empire dashboard reads from
`claude_golden_runs` to render an "evals across the empire" panel.

```yaml
# .github/workflows/goldens.yml
on:
  schedule:
    - cron: "0 4 * * *"  # 04:00 UTC nightly
  workflow_dispatch:

jobs:
  run-goldens:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - name: Install
        run: pip install httpx git+https://${{ secrets.GH_PAT }}@github.com/zombielabsv2/empire-lib.git@main
      - name: Run all goldens
        env:
          ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
          SUPABASE_URL: ${{ secrets.SUPABASE_URL }}
          SUPABASE_SERVICE_KEY: ${{ secrets.SUPABASE_KEY }}
        run: |
          for f in tests/goldens/*.py; do python "$f" || exit_code=$?; done
          exit ${exit_code:-0}
```

The empire-lib install via git+https with a PAT is the unblock for
private-repo CI; alternative is publishing empire-lib to a private
PyPI / GitHub Packages.

## What's NOT here yet

- **LLM-as-judge scoring.** Future work — let a separate Claude call
  score outputs on rubric dimensions (specificity, tone, accuracy).
- **Cosine-similarity drift.** Compare today's output embedding to last
  week's; flag when similarity drops below a threshold.
- **Cross-empire dashboard view.** Empire-dashboard would query
  `claude_golden_runs` and render the per-app pass-rate chart.

## Cost

Running goldens hits api.anthropic.com — ~$0.01-0.10 per spec depending
on token volume. A typical project with 5 goldens runs nightly costs
~$0.50-2/month. Cheaper than catching a regression after it ships to
production users.
