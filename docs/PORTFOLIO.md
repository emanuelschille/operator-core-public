# Operator Core — Portfolio Overview

This document explains what Operator Core is meant to demonstrate to an employer,
collaborator, or technical reviewer, and where to look first.

It is a read-first guide. It does not replace the deeper documents in `docs/`.

## What this project is

Operator Core is a Python-first, human-in-the-loop operator platform.
It turns recurring operational work into a structured, auditable workflow engine
with a messaging interface for the operator, an operational state layer, and a model
provider for structured generation and decision support.

It is deliberately **not** a generic chatbot and **not** a multi-user SaaS product.
It is a single-service, shared-core runtime that supports one or more real project
configurations (the first active one is referred to here by its short codename only;
its business and strategy content is not part of this snapshot).

## Why it is a useful portfolio example

This repository shows how someone designs and operates a real system rather than a toy demo:

- **Structured backend development in Python** — a clear `src/operator_core/` package
  with separated concerns (core routing, integrations, proactive layer, project resolution).
- **Operator / automation thinking** — work is modeled as Jobs, Runs, and Events with
  explicit status transitions, not ad-hoc scripts.
- **Workflow and state modeling** — modular "lanes" (content, affiliate, funnel, review,
  knowledge) on one shared core instead of one monolithic assistant.
- **External system integration** — a messaging transport, an operational state-layer
  boundary, and a model provider, each behind a clear service boundary.
- **Human-in-the-loop design** — the operator initiates every action and nothing runs
  autonomously; each action is an auditable Job/Run/Event. (A confirmation/approval gate is a
  documented roadmap item, not yet implemented — stated honestly rather than overclaimed.)
- **Quality and testing discipline** — a broad `tests/` tree (92 test modules; 1004 passing)
  covering routing, formatting, integrations, and the proactive layer, green under CI.
- **Documentation and architecture discipline** — written-down architecture, module
  responsibilities, and an honest readiness assessment.

## Where to look first

In order, for a fast but real impression:

1. `README.md` — what the system is and how to run/test it.
2. `docs/ARCHITECTURE.md` — concise architecture entry point (this doc set's map).
3. [`docs/02-architecture-overview.md`](02-architecture-overview.md) — the deeper architecture
   document. Start with the
   [worked end-to-end example](02-architecture-overview.md#worked-end-to-end-example) and the
   [code map](02-architecture-overview.md#where-this-lives-in-the-code), which link straight into
   the real source files.
4. `src/operator_core/` — the actual engine: `core/`, `integrations/`, `proactive/`.
5. `tests/` — how behavior is pinned down.
6. `docs/03-modules-and-responsibilities.md` — module-by-module responsibilities.

For a guided 5-minute walkthrough, see `docs/EMPLOYER-DEMO.md`.

## What is intentionally **not** in scope here

- No multi-user SaaS layer, billing, or public sign-up.
- No fully autonomous, no-approval agent swarm.
- No multi-provider model routing in the current phase.
- No microservice sprawl — it is a single shared-core service by design.

## What could be improved next (honest)

- **Confirmation/approval subsystem:** the documented confirmation gate (`/confirm`, `/reject`,
  a `rules_engine`, `approval_state`, continuation links) is **designed but not implemented** in
  this snapshot. It is the main code/doc gap, called out explicitly in
  `docs/PUBLIC-READINESS-CHECKLIST.md` rather than hidden.
- **Test/code drift:** 38 tests assert behaviour the code has since refactored and are marked
  `xfail` with reasons; reconciling them is a tracked cleanup item. (CI is wired up and green —
  see the README badge.)
- **License:** no open-source license is granted. The repository is shared as a portfolio
  snapshot — all rights reserved, portfolio use only.

This file is documentation only. It makes no claims about live production or deployment status.
