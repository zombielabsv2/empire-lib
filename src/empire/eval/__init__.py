"""Golden-set evaluation scaffolding for Claude prompt regression detection.

What this catches: quality + behavior drift on prompts that the empire is
already shipping. Cost drift is caught by `empire-dashboard/api_cost_report.py`
(token-per-call deltas). Schema drift is caught by `empire.lint.json_shape`.
This module catches the third surface — "the model produced valid JSON
that's structurally fine but semantically worse" — by replaying frozen
inputs through the prompt and asserting on properties of the output.

The pattern is intentionally low-ceremony so each project can adopt it
without a multi-repo refactor:

    from empire.eval import GoldenSpec, run_golden, store_run

    GOLDEN = GoldenSpec(
        app="kairav_os",
        action="weekly_email",
        prompt_fn=_generate_digest,                    # the live prompt fn
        prompt_args=(SAMPLE_ACTIVITIES, [], SAMPLE_COMM, []),
        expected_shape=_DIGEST_SHAPE,                  # schema check
        must_contain=["Kairav", "Avaz"],               # substring assertions
        must_not_contain=["Sanganer", "—"],            # banned-literal check
    )

    if __name__ == "__main__":
        result = run_golden(GOLDEN)
        store_run(result)                              # persist to Supabase
        sys.exit(0 if result.passed else 1)

Goldens live next to the code they exercise (the project's tests/goldens/
directory) and run on a nightly GH Actions cron per project. Results
flow into the shared `claude_golden_runs` table so the empire dashboard
can show overall pass rate.

What this DOESN'T do:
- LLM-as-judge scoring. Future work.
- Cosine-similarity drift against a previous run. Future work.
- Synthetic-data generation. Each golden's prompt_args are explicit
  fixtures — there is intentionally no magic.
"""
from __future__ import annotations

from empire.eval.runner import GoldenResult, GoldenSpec, run_golden
from empire.eval.store import store_run

__all__ = [
    "GoldenResult",
    "GoldenSpec",
    "run_golden",
    "store_run",
]
