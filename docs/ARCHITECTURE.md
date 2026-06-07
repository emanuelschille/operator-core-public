# Operator Core — Architecture Entry Point

This is a concise, public-facing architecture overview and a map into the deeper
documents. For the full detail, read [`02-architecture-overview.md`](02-architecture-overview.md)
and [`03-modules-and-responsibilities.md`](03-modules-and-responsibilities.md).

## High-level shape

- A single Python core engine (`src/operator_core/`) with separated concerns:
  command routing, project resolution, integrations, and a proactive layer.
- Work is modeled as **Jobs, Runs, and Events** with explicit status transitions.
- Modular **workflow lanes** (content, affiliate, funnel, review, knowledge) share one
  core instead of a single monolithic assistant.
- External systems sit behind clear service boundaries: a messaging transport for the
  operator interface, an operational state layer, and a model provider.
- A slow-state notes/context layer provides background context only — never runtime truth.

## Design boundaries (current phase)

- No multi-provider model routing.
- No SaaS multi-tenancy, billing, or public sign-up.
- No fully autonomous, no-approval mode — human approval is first-class.

## Deeper reading (included in this snapshot)

- [`02-architecture-overview.md`](02-architecture-overview.md) — full architecture.
- [`03-modules-and-responsibilities.md`](03-modules-and-responsibilities.md) — module-by-module responsibilities.

This file is documentation only and asserts nothing about live deployment status.
