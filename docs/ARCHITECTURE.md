# Operator Core — Architecture Entry Point

This is a concise, public-facing map into the deeper documents. For the full detail — diagrams,
a worked example, trade-offs, and an honest status — read
[`02-architecture-overview.md`](02-architecture-overview.md) and
[`03-modules-and-responsibilities.md`](03-modules-and-responsibilities.md).

## What it is

A human-in-the-loop **operator runtime**: a person drives project operations through Telegram; a
Python shared core turns each message into an auditable unit of work and persists business state
in Airtable. It is **not** a freeform chatbot, and nothing runs autonomously — the operator
initiates every action.

## Three core ideas

1. **Workflow state is first-class.** Every action becomes a `Job`, each execution attempt a
   `Run`, and every audit-relevant change an `Event` — with explicit, enforced status
   transitions, not ad-hoc scripts or chat text.
2. **Single-writer ownership.** Exactly one module owns each piece of state: `job_service` owns
   Job state, `run_service` owns Run state, `event_log_service` owns Events, `project_resolver`
   owns project context, and `airtable_service` is the only path to Airtable.
3. **Shared core vs lanes.** A reusable shared core owns workflow control; per-domain **lane**
   modules (`content_ops`, `affiliate_ops`, `funnel_ops`, `review_ops`, `knowledge_ops`) own
   business semantics. Neither crosses into the other.

## Design boundaries (current phase)

- No multi-provider model routing.
- No SaaS multi-tenancy, billing, or public sign-up.
- No autonomous, operator-absent execution — the operator triggers every action.
- High-impact commands pass through a **confirmation gate** (`/confirm`, `/reject`); see the
  [confirmation flow](02-architecture-overview.md#confirmation-flow).

## Deeper reading (included in this snapshot)

- [`02-architecture-overview.md`](02-architecture-overview.md) — full architecture, with a
  [worked end-to-end example](02-architecture-overview.md#worked-end-to-end-example) and a
  [code map](02-architecture-overview.md#where-this-lives-in-the-code).
- [`03-modules-and-responsibilities.md`](03-modules-and-responsibilities.md) — module-by-module
  responsibilities and ownership rules.

This file is documentation only and asserts nothing about live deployment status.
