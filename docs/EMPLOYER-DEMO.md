# Operator Core — 5-Minute Employer Demo

A short, safe, read-only walkthrough for an employer or reviewer. Everything here is
read-only and exposes no secrets. You do not need credentials to follow it.

## Three sentences for a reviewer

1. Operator Core is a Python-first, human-in-the-loop operator platform that turns
   recurring operational work into a structured, auditable workflow engine.
2. It uses a messaging transport as the operator interface, an operational state layer,
   and a model provider for structured generation — each behind a clear service boundary.
3. The design favors traceability and human oversight (Jobs → Runs → Events) over
   "magic" autonomy, and it is built to host multiple real projects on one shared core.

## Suggested 5-minute path

1. **Read the pitch (1 min).** Open `README.md` — purpose, principles, and scope
   boundaries are in the first two screens.
2. **See the architecture (1 min).** Open `docs/ARCHITECTURE.md` for the shape and the three
   core ideas, then read the
   [worked end-to-end example](02-architecture-overview.md#worked-end-to-end-example) in
   `docs/02-architecture-overview.md` — it walks one Telegram message through the real modules.
3. **Look at the engine (1.5 min).** Browse `src/operator_core/`:
   - `core/command_router.py` and `core/project_resolver.py` — how input is routed.
   - `integrations/` — the external-system boundaries.
   - `proactive/checker.py` — the proactive layer.
4. **Check the tests (1 min).** Browse `tests/` to see how behavior is pinned down
   (routing, formatting, integrations, proactive layer).
5. **Read the modules map (0.5 min).** Open `docs/03-modules-and-responsibilities.md`
   to see how responsibilities are split across the package.

## Safe read-only commands

These only read the repository and print structure or run local tests. They do not
touch any external service.

```bash
# Project structure
git ls-files | sed -n '1,80p'

# The engine package
find src/operator_core -name '*.py' | sort

# Test surface
find tests -name 'test_*.py' | sort | sed -n '1,40p'

# Run the test suite locally (green: 1004 passed, 122 skipped, 38 xfailed)
python -m pytest -q

# Compile-check the source and tests
python -m compileall src tests
```

## What this snapshot deliberately leaves out

- `.env` / environment files (do not ship; configuration is via environment variables).
- Business, strategy, and monetization material for the active project — kept private and
  not needed for a technical review.
- Local agent/tooling configuration and the original Git history.

This file is documentation only and makes no production or deployment claims.
