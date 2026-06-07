# Operator Core — Public Portfolio Snapshot

[![CI](https://github.com/emanuelschille/operator-core-public/actions/workflows/ci.yml/badge.svg)](https://github.com/emanuelschille/operator-core-public/actions/workflows/ci.yml)

Operator Core is a human-in-the-loop operator platform for structured project
workflows: a Python core engine, a messaging interface for the operator, an
operational state layer, and modular workflow lanes for content, affiliate,
funnel, and review operations.

> **About this repository.** This is a **cleaned, public portfolio snapshot** of a
> larger private project. It contains the engine source, the test suite, and a set of
> architecture/portfolio documents. Internal business and strategy material, local
> tooling configuration, and private history are intentionally **not** included.
> See "What is and isn't in this snapshot" below.

## What this repo shows

- **Structured backend development in Python** — a clear `src/operator_core/` package
  with separated concerns (command routing, integrations, a proactive layer, project
  resolution).
- **Operator / automation thinking** — work is modeled as Jobs, Runs, and Events with
  explicit status transitions, not ad-hoc scripts.
- **Workflow and state modeling** — modular "lanes" (content, affiliate, funnel,
  review, knowledge) on one shared core instead of one monolithic assistant.
- **External-system integration design** — a messaging transport, an operational state
  layer, and a model provider, each behind a clear service boundary.
- **Human-in-the-loop design** — the operator initiates every action; nothing runs
  autonomously, and each action is an auditable Job/Run/Event. High-impact commands pass through
  a real confirmation gate (`/confirm` · `/reject`) before any write.
- **Testing and documentation discipline** — a large `tests/` tree and a numbered,
  written-down architecture.

## Tech and working style

- Python, single package under `src/operator_core/`, no third-party runtime
  dependencies (`pyproject.toml` declares `dependencies = []`).
- Test-first habits: a broad `tests/` suite pins routing, formatting, integrations,
  and the proactive layer.
- Documentation-driven: design, scope, and decisions are written down in `docs/`.

## Documentation

- [`docs/PORTFOLIO.md`](docs/PORTFOLIO.md) — what this project demonstrates and where to look.
- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — concise architecture entry point.
- [`docs/02-architecture-overview.md`](docs/02-architecture-overview.md) — the deeper architecture document.
- [`docs/03-modules-and-responsibilities.md`](docs/03-modules-and-responsibilities.md) — module-by-module responsibilities.
- [`docs/EMPLOYER-DEMO.md`](docs/EMPLOYER-DEMO.md) — a guided 5-minute, read-only walkthrough.
- [`docs/PUBLIC-READINESS-CHECKLIST.md`](docs/PUBLIC-READINESS-CHECKLIST.md) — honest status, including test/CI and license state.

## Explore locally

```bash
# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install the package (editable) plus pytest for the test suite
pip install -e .
pip install pytest

# Run the test suite (src/ is wired via pyproject, so this works as-is)
python -m pytest -q
```

## Honest status

This is a **public portfolio snapshot**. The test suite is **reliably collectable and green**:
`python -m pytest -q` reports **1026 passed, 122 skipped, 38 xfailed (exit 0)**, and CI runs it
on every push and PR across Python 3.11 and 3.12 (see the badge above).

The skips and xfails are deliberate and documented, not hidden failures:

- **122 skipped** — tests that need the private `projects/<key>/` business fixtures, which are
  intentionally excluded from this snapshot. They run normally if that data is present.
- **38 xfailed** — tests asserting behaviour the code has since refactored (tracked test/code
  drift), registered with reasons rather than rewritten to rubber-stamp current output. (None of
  these concern the confirmation subsystem.)

The **confirmation/approval subsystem is now implemented** (a `rules_engine` policy, a
`waiting_for_approval` gate with `approval_state`, and `/confirm` · `/reject` resolution, all
tested). The remaining honest roadmap item is **continuation/parent Job links**. The full
picture — the Code–Doc Alignment report and the exact remaining items — is in
[`docs/PUBLIC-READINESS-CHECKLIST.md`](docs/PUBLIC-READINESS-CHECKLIST.md).

## What is and isn't in this snapshot

**Included:** `src/operator_core/`, `tests/`, `pyproject.toml`, `.gitignore`, and the
`docs/` files listed above.

**Deliberately excluded** (kept private): any business/strategy/monetization material,
project-specific operational content, local agent/tooling configuration, environment
files, and the original Git history. The first active project instance is referred to
by its short codename only; none of its business or strategy content is reproduced here.

## License / usage

No open-source license is granted. This repository is shared as a **portfolio /
viewing snapshot**: **all rights reserved, portfolio use only.** Please do not reuse or
redistribute the code without permission. See [`LICENSE`](LICENSE) for the exact terms.
