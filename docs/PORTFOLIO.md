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
- **Human-in-the-loop design** — approval and oversight are first-class, not bolted on.
- **Quality and testing discipline** — a large `tests/` tree (~98 test modules) covering
  routing, formatting, integrations, and the proactive layer.
- **Documentation and architecture discipline** — written-down architecture, module
  responsibilities, and an honest readiness assessment.

## Where to look first

In order, for a fast but real impression:

1. `README.md` — what the system is and how to run/test it.
2. `docs/ARCHITECTURE.md` — concise architecture entry point (this doc set's map).
3. `docs/02-architecture-overview.md` — the deeper architecture document.
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

- **CI / test health:** continuous integration is not yet wired up in this snapshot, and
  the test suite is not guaranteed green on every branch. See
  `docs/PUBLIC-READINESS-CHECKLIST.md` for the exact state and the steps to make tests
  reliably collectable and green before adding CI.
- **License:** no open-source license is granted. The repository is shared as a portfolio
  snapshot — all rights reserved, portfolio use only.

This file is documentation only. It makes no claims about live production or deployment status.
