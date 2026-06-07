# Operator Core — working conventions

Guidance for future sessions in this repository. Keep changes consistent with the rules below.

## What this repo is
A **public portfolio snapshot** of `operator-core`: a human-in-the-loop operator runtime
(Telegram interface → Python shared core → Airtable business state) with `Jobs`/`Runs`/`Events`
as an explicit workflow-state layer and per-domain lane modules for business semantics. The
baseline architecture doc is [`docs/02-architecture-overview.md`](docs/02-architecture-overview.md).

## Stack
- **Python ≥ 3.11**, single package under `src/operator_core/` (`package-dir = src`).
- **Zero runtime dependencies** — `pyproject.toml` declares `dependencies = []` on purpose; the
  zero-dependency runtime is a selling point. HTTP to Telegram/OpenAI/Anthropic/Airtable uses the
  standard library (`urllib`). Do **not** add runtime deps. `pytest` is the only dev/test
  dependency, and CI tooling is a dev concern — that is fine.
- Tests run with `python -m pytest -q`. `pyproject.toml` sets `pythonpath = ["src"]` and
  `--import-mode=importlib`, so `src/` is importable without an editable install and same-named
  test files coexist. Current state: **green** (1004 passed, 122 skipped, 38 xfailed).

## Architecture rules (single-writer ownership)
Exactly one module owns each piece of state — never write it from elsewhere:
- `Job.status` → `core/backbone/job_service.py`
- `Run.status` + run timing → `core/backbone/run_service.py`
- the `Events` table → `core/backbone/event_log_service.py`
- the project key → `core/project_resolver.py`
- all Airtable writes → `integrations/airtable_service.py` (lanes go **through** it, never the
  raw Airtable API)
- business semantics → the owning lane (`content_ops`, `affiliate_ops`, `funnel_ops`,
  `review_ops`, `knowledge_ops`); lanes never touch Jobs/Runs/Events/workflow state.

`core/backbone/execution_service.py` orchestrates the lifecycle by delegating to those services;
it never writes state directly.

## Docs must match code
This is the prime directive for this repo. **Never claim behaviour the code does not have.**
- If a doc asserts an invariant the code violates, fix the code or correct the doc — never paper
  over it.
- Some doc names are role names, not file names (`telegram_gateway`, `llm_service`); the real
  mapping and the verified ownership invariants are in the Code–Doc Alignment report in
  [`docs/PUBLIC-READINESS-CHECKLIST.md`](docs/PUBLIC-READINESS-CHECKLIST.md).
- The **confirmation/approval subsystem** (`rules_engine`, `approval_state`,
  `waiting_for_approval`, `/confirm`, `/reject`) is **implemented** behind the single-writer
  rules — only `job_service` writes `approval_state`/status, only `event_log_service` writes the
  confirmation events. **Continuation/parent Job links** (`continuation_of_job_id` /
  `parent_job_id`) remain roadmap — keep those labelled as not-yet-implemented.
- Every Mermaid block must render and every internal link must resolve.

## Privacy guardrails
- Keep private content out: no business/strategy/monetization material.
- The active project is referenced only by its codename `everydayengel`.
- The per-project `projects/<key>/` business fixtures are intentionally excluded; tests that need
  them skip automatically (see `tests/conftest.py`).
- Roles are framed as **Operator (Julia)** and **Maintainer (Emanuel)** — first names only, no
  further personal data.

## Workflow
Work on a branch, commit in logical chunks with clear messages, verify the suite is green before
claiming done, and keep the docs consistent with any code change.
