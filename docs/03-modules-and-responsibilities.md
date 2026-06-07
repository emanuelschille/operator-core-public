# Modules and Responsibilities

## Purpose

This file defines the responsibility boundaries inside Operator Core.

The goal is to make the shared core and the lane modules work together in a way that is:
- state-safe
- auditable
- project-aware
- confirmation-safe
- aligned with the Airtable model
- aligned with the Telegram interaction model

This file is authoritative for module ownership of workflow actions and state transitions.

> ### How to read this file (snapshot reconciliation)
>
> Start from [`02-architecture-overview.md`](02-architecture-overview.md) — it is the baseline,
> with diagrams, a worked example, and an honest **Status and roadmap**. This file is the deep
> per-module companion. Two reconciliations apply throughout, verified against the code in the
> **Code–Doc Alignment report** in [`PUBLIC-READINESS-CHECKLIST.md`](PUBLIC-READINESS-CHECKLIST.md):
>
> **1 · Module names → real code.** Some role names below do not match the file tree:
>
> | Name used here | Real location |
> |---|---|
> | `telegram_gateway` | `interfaces/telegram/` + `integrations/telegram_service.py` |
> | `llm_service` | `integrations/anthropic_service.py`, `integrations/openai_service.py` |
> | `rules_engine` | `core/rules_engine.py` (confirmation policy) |
> | `knowledge_ops`, `review_ops`, `funnel_ops` | `core/knowledge_ops`, `core/review_ops`, `core/funnel_ops` |
> | *(orchestrators, not listed historically)* | `core/request_flow/service.py`, `core/backbone/execution_service.py` |
>
> **2 · Implemented vs roadmap.** The **confirmation / approval** subsystem below —
> `rules_engine`, `approval_state`, `job_status=waiting_for_approval`, `/confirm`, `/reject` — is
> **implemented** (see [`02-architecture-overview.md`](02-architecture-overview.md#confirmation-flow)).
> The only part still on the **roadmap** is **continuation / parent links**
> (`continuation_of_job_id` / `parent_job_id`), flagged where it appears below.

## Responsibility model

Operator Core uses one main service with separated internal modules.

Responsibility is split into two layers:

### Shared core modules
Shared core modules own:
- Telegram intake and output
- project context resolution
- command interpretation
- Jobs
- Runs
- Events
- analysis snapshot orchestration
- evidence packaging contracts
- rules validation
- persistence access
- LLM access
- response formatting

Shared core modules must remain reusable across projects.

### Lane modules
Lane modules own:
- domain-specific business behavior
- object-specific write intent
- interpretation of project semantics inside their lane
- field-level mutation proposals for business objects in their lane

Lane modules are project-aware.
Lane modules must not replace shared workflow control.

## Hard authority rules

### Single-writer rule for workflow records

Only one module class may own each workflow record type:

- `Jobs` are owned by `job_service`
- `Runs` are owned by `run_service`
- `Events` are owned by `event_log_service`

No other module may write these tables directly.

### Single-writer rule for workflow fields

Only `job_service` may write:
- `job_id`
- `job_status` *(implemented as `Job.status`)*
- `approval_state` *(implemented — `ApprovalState` on the `Job` model)*
- `parent_job_id` *(roadmap — not implemented)*
- `continuation_of_job_id` *(roadmap — not implemented)*
- `current_run_id` *(implemented as `latest_run_id`)*

Only `run_service` may write:
- `run_id`
- `run_status`
- `started_at`
- `finished_at`
- run execution snapshots

Only `event_log_service` may write:
- `event_id`
- `event_type`
- `entity_type`
- `entity_id`
- event payload records

### Project context ownership

Only `project_resolver` may determine:
- `resolved_project_key` *(implemented as `ResolvedProjectContext.project_key`)*
- project-context conflicts *(planned)*
- whether explicit, reply-based, or chat-level context is valid *(planned — the snapshot
  resolves from runtime configuration only)*

Other modules may consume resolved project context.
Other modules must not silently override it. *(Verified: no module assigns the project key
outside `project_resolver`.)*

### Confirmation ownership

> **Implemented.** `rules_engine` (`core/rules_engine.py`) decides; `job_service` owns
> `approval_state` and the `waiting_for_approval` status; `execution_service` gates the run and
> `event_log_service` records `confirmation_requested` / `confirmation_resolved`.

`rules_engine` decides whether confirmation is required.

Only `job_service` may write:
- `approval_state`
- `job_status=waiting_for_approval`
- resumed or rejected confirmation outcomes on Jobs

### Airtable write path rule

All Airtable persistence must go through `airtable_service`.

This means:
- lane modules may define business write intent
- shared core may orchestrate that write
- `airtable_service` performs the actual Airtable read/write operation

No module should bypass this path.

## Authoritative semantic field ownership

The following semantic fields are authoritative and must not be duplicated by module-local pseudo-status logic:

- `content_stage`
- `monetization_stage`
- `review_outcome`

### `content_stage`
Owned by:
- `content_ops`

May be written on:
- `Content Ideas`
- `Content Drafts`

Must not be written directly by:
- `telegram_gateway`
- `command_router`
- `project_resolver`
- `job_service`
- `run_service`
- `event_log_service`
- `airtable_service`
- `llm_service`
- `response_formatter`

### `monetization_stage`
Owned by:
- `affiliate_ops` for content-related monetization progression
- `funnel_ops` for funnel/page-related monetization progression

May be written on:
- `Content Ideas`
- `Content Drafts`
- `Funnel Pages`

Special rule for content creation:
- `content_ops` may set the initial project-default value for `monetization_stage` when creating a new content record if that field is required and no earlier value exists
- `content_ops` must not later reinterpret or advance `monetization_stage` as if it owned monetization semantics

Must not be written directly by shared core modules.

### `review_outcome`
Owned by:
- `review_ops`

May be written on:
- `Reviews`

Must not be written directly by shared core modules.

### Project truth records
`Project State` records are owned by:
- `knowledge_ops`

Other lane modules may propose learnings or rule candidates.
They must not directly mutate project truth without going through the explicit knowledge-state path.

## Shared core modules

## `analysis_foundation_service`

### Responsibility
Owns the shared-core analysis foundation between raw sources and later writer execution.

### Must do
- read analytics context and relevant project rule/state sources
- build explicit `AnalysisSnapshot` objects
- prepare `WriterBrief` contracts
- assemble `EvidencePack` payloads
- keep all of the above serializable for Run snapshots and later persistence

### Must not do
- become a Telegram UX surface
- write Jobs, Runs, or Events directly
- bypass `airtable_service`
- replace lane-owned business semantics
- silently trigger provider comparisons or boost logic

### Notes
This module is a shared-core foundation layer.
It exists to improve decision quality and traceability before wider writer-slot rollout.

## `telegram_gateway`

### Responsibility
Owns Telegram transport intake and output delivery.

### Must do
- receive Telegram updates
- normalize incoming message data
- capture chat, user, message, and reply metadata
- pass normalized requests into the shared core
- send formatted responses back to Telegram

### Must not do
- create Jobs
- create Runs
- write Events
- resolve final project context
- mutate business records
- decide confirmation policy
- write authoritative semantic fields

### Notes
`telegram_gateway` is transport-facing only.
It must stay thin.

## `command_router`

### Responsibility
Owns interaction classification and route selection.

### Must do
- detect command name or structured intent
- classify request type
- determine whether the request is:
  - new work
  - continuation work
  - inspection
  - confirmation resolution
  - helper interaction
- determine the target lane
- determine which shared services must be called next
- enforce command-to-job and command-to-object mapping at routing level

### Must not do
- create Jobs directly
- create Runs directly
- write Events directly
- resolve final project context on its own
- mutate business records
- set `job_status`
- set `approval_state`
- write authoritative semantic fields

### Notes
`command_router` decides the route.
It does not own workflow state.

## `project_resolver`

### Responsibility
Owns project-context resolution.

### Must do
- resolve `resolved_project_key`
- validate explicit project arguments
- validate reply-derived project metadata
- validate chat-level project context if used
- detect cross-project conflicts
- block unsafe mixed-project execution
- return one authoritative project resolution result to the workflow

### Must not do
- create Jobs
- create Runs
- write Events
- mutate business records
- invent new project defaults beyond configured safe behavior
- silently switch project context during execution

### Notes
Project context must be resolved once and then treated as authoritative for that request.

## `job_service`

### Responsibility
Owns Job lifecycle and Job workflow state.

### Must do
- create Jobs
- create continuation Job links
- attach related entity references to Jobs
- update `job_status`
- update `approval_state`
- mark Jobs as waiting for input
- mark Jobs as waiting for approval
- mark Jobs as completed, failed, blocked, or cancelled
- own confirmation resolution state on Jobs
- resume or reject pending confirmation Jobs
- keep Job state transitions consistent

### Must not do
- create Runs directly without using `run_service`
- write Events directly without using `event_log_service`
- resolve project context on its own
- write business records directly without lane ownership and `airtable_service`
- write authoritative semantic fields on business objects

### Exclusive ownership
Only `job_service` may write:
- `job_status`
- `approval_state`
- continuation links
- parent/sub-job relationships
- primary Job result summary fields

### Notes
If a lane module or another shared module needs a Job state change, it must request that through `job_service`.

## `run_service`

### Responsibility
Owns execution-attempt tracking.

### Must do
- create Runs for executable workflow attempts
- link Runs to Jobs
- set `run_status`
- store start and finish timestamps
- store execution snapshots where needed
- track retry attempts
- support resumed execution after confirmation approval

### Must not do
- create Jobs
- update `job_status` directly
- update `approval_state`
- write business records
- write authoritative semantic fields
- resolve project context

### Exclusive ownership
Only `run_service` may write:
- `run_id`
- `run_status`
- execution timing fields
- run snapshots

### Notes
`run_service` reports outcomes back to `job_service`.
It does not own Job meaning.

## `event_log_service`

### Responsibility
Owns Event creation.

### Must do
- write audit-relevant Events
- log Job creation and Job state changes
- log Run start and Run finish states
- log confirmation requested and confirmation resolved
- log business record creation and update events when relevant
- log important failures and rule rejections where auditability matters

### Must not do
- replace business records
- replace Jobs
- replace Runs
- decide workflow meaning on its own
- mutate business records
- write authoritative semantic fields

### Exclusive ownership
Only `event_log_service` may write the `Events` table.

### Notes
Other modules emit event intents.
`event_log_service` writes them.

## `airtable_service`

### Responsibility
Owns the Airtable integration boundary.

### Must do
- read Airtable records
- create Airtable records
- update Airtable records
- isolate Airtable-specific API details from the rest of the system
- keep project-aware base access consistent

### Must not do
- decide which business fields should change
- decide confirmation rules
- decide project semantics
- set `job_status`
- set `approval_state`
- resolve project context
- invent field values outside explicit caller intent

### Notes
`airtable_service` is a persistence adapter.
It must remain semantically thin.

## `llm_service`

### Responsibility
Owns structured model interaction.

### Must do
- build model calls from validated context
- call the model
- normalize structured outputs
- return outputs to the calling module

### Must not do
- route commands
- resolve project context
- create Jobs
- create Runs
- write Events
- write Airtable directly
- decide final workflow state
- directly write authoritative semantic fields

### Notes
`llm_service` produces structured assistance.
It does not own workflow control.

## `rules_engine`

> **Implemented as `core/rules_engine.py`.** The snapshot ships the confirmation-decision part
> (`requires_confirmation` over a declared high-impact command set); broader write-boundary
> validation described below remains a design target.

### Responsibility
Owns rule validation and execution boundaries.

### Must do
- validate whether the requested action is allowed
- apply project-specific rules
- apply role-aware restrictions
- validate write permissions by module and field class
- decide whether confirmation is required
- validate proposed writes before persistence
- reject unsafe or conflicting actions

### Must not do
- create Jobs directly
- create Runs directly
- write Events directly
- mutate business records directly
- format final operator responses
- silently change project context

### Notes
`rules_engine` is the policy layer.
It validates.
It does not persist.

## `response_formatter`

### Responsibility
Owns operator-facing response shaping.

### Must do
- format responses according to response-state contract
- keep Operator (Julia)-facing responses concise and practical
- keep Maintainer (Emanuel)-facing responses more inspectable where needed
- preserve clarity about:
  - what was saved
  - what is pending
  - what failed
  - what requires confirmation

### Must not do
- create Jobs
- create Runs
- write Events
- mutate business records
- set `job_status`
- set `approval_state`
- alter authoritative semantic fields
- replace workflow decisions with formatting shortcuts

### Notes
Formatting must reflect real state, not simulate it.

## Lane modules

Lane modules own business-object logic.
They do not own workflow records.

All lane modules must follow these common rules:
- may request business-object reads and writes through `airtable_service`
- may call `llm_service` when needed
- must submit proposed writes through `rules_engine` validation where required
- must not write `Jobs`, `Runs`, or `Events` directly
- must not set `job_status` directly
- must not set `approval_state` directly
- must not resolve project context directly

## `content_ops`

### Responsibility
Owns content-lane business logic.

### Owns business objects
- `Content Ideas`
- `Content Drafts`

### May write
- content idea fields
- content draft fields
- `draft_type`
- `platform`
- `pillar`
- `cta_softness`
- `julia_presence_level`
- `content_stage`

### May conditionally write
- initial project-default `monetization_stage` on newly created content records if required by the model and no earlier value exists

### Must not do
- own affiliate mapping logic
- own offer records
- own `review_outcome`
- later reinterpret `monetization_stage` as monetization authority
- write Jobs, Runs, or Events directly

### Notes
`content_ops` owns the content lifecycle dimension.
It does not own affiliate semantics beyond safe initialization on create.

## `affiliate_ops`

### Responsibility
Owns monetization-support logic for content and offers.

### Owns business objects
- `Affiliate Offers`
- `Offer Mappings`

### May write
- offer fields
- mapping fields
- `fit_level`
- mapping-level `cta_softness`
- content-related `monetization_stage` when monetization progression is the actual purpose of the action

### May target
- `Content Ideas`
- `Content Drafts`
- `Affiliate Offers`
- `Offer Mappings`

### Must not do
- own `content_stage`
- own `review_outcome`
- write funnel page records as primary owner
- write Jobs, Runs, or Events directly

### Notes
`affiliate_ops` owns monetization progression on content-related records once monetization work is actually being performed.

## `knowledge_ops`

### Responsibility
Owns durable project-truth writing.

### Owns business objects
- `Project State`

### May write
- project rules
- project assumptions
- project constraints
- operating truth records
- supersession relationships between Project State records

### Must not do
- generate Review outcomes
- write content lifecycle fields
- write monetization lifecycle fields unless explicitly represented as project-truth records rather than object state
- write Jobs, Runs, or Events directly

### Notes
This lane is the only business lane that may mutate `Project State` directly.

## `review_ops`

### Responsibility
Owns review and learning-output logic.

### Owns business objects
- `Reviews`

### May write
- review summaries
- hypotheses
- next actions
- `review_outcome`

### May propose
- learning candidates for later `Project State` storage

### Must not do
- directly mutate `Project State`
- own `content_stage`
- own `monetization_stage`
- write Jobs, Runs, or Events directly

### Notes
If review learning should become durable project truth, that transition must happen through the knowledge-state path.

## `funnel_ops`

### Responsibility
Owns funnel and page-planning logic.

### Owns business objects
- `Funnel Pages`

### May write
- page brief fields
- funnel planning fields
- routing fields
- `page_status`
- `approval_state` only if that field exists on the business record and the write has already been approved through workflow control
- `monetization_stage` on Funnel Pages

### Must not do
- own `content_stage`
- own `review_outcome`
- own offer mapping records
- write Jobs, Runs, or Events directly

### Notes
`funnel_ops` owns page/funnel business semantics, not shared workflow state.

## Workflow ownership by action type

## New executable request

For a normal new executable request, ownership is:

1. `telegram_gateway`
   - receives and normalizes message

2. `command_router`
   - classifies command and request type

3. `project_resolver`
   - returns authoritative project context

4. `rules_engine`
   - validates requested action and confirmation need

5. `job_service`
   - creates the Job

6. `run_service`
   - creates the Run when execution starts

7. selected lane module
   - prepares business logic and proposed business writes

8. `rules_engine`
   - validates proposed writes if needed

9. `airtable_service`
   - persists business records

10. `job_service`
    - updates `job_status`

11. `run_service`
    - closes `run_status`

12. `event_log_service`
    - writes audit events

13. `response_formatter`
    - formats the result

14. `telegram_gateway`
    - sends the response

## Continuation request

For a continuation request:

1. `telegram_gateway`
   - provides reply metadata

2. `command_router`
   - classifies the request as continuation

3. `project_resolver`
   - validates project context from reply metadata and current input

4. `job_service`
   - creates a new continuation Job
   - links `continuation_of_job_id`

5. remaining execution follows the normal path

The original Job is not overwritten by the continuation request.

## Confirmation-required request

> **Implemented.** The three confirmation flows below (confirmation-required, approval,
> rejection) match the code: see the
> [confirmation flow diagram](02-architecture-overview.md#confirmation-flow) and the tests under
> `tests/core/backbone/test_confirmation_gate.py` and
> `tests/core/request_flow/test_confirmation_flow.py`.

For a request that requires confirmation:

1. `telegram_gateway`
   - receives request

2. `command_router`
   - classifies intended action

3. `project_resolver`
   - resolves project

4. `rules_engine`
   - decides confirmation is required

5. `job_service`
   - creates or updates the Job
   - sets `job_status=waiting_for_approval`
   - sets `approval_state=pending`

6. `event_log_service`
   - writes `confirmation_requested`

7. `response_formatter`
   - returns `waiting_for_confirmation`

No destructive or high-impact write should execute before confirmation is resolved.

## Confirmation approval

For `/confirm`:

1. `telegram_gateway`
   - receives message

2. `command_router`
   - classifies confirmation action

3. `project_resolver`
   - validates project context

4. `job_service`
   - resolves target pending Job
   - sets `approval_state=approved`

5. `run_service`
   - creates a resumed Run

6. original owning lane module
   - executes the pending business action

7. `airtable_service`
   - persists the business write

8. `job_service`
   - updates terminal `job_status`

9. `event_log_service`
   - writes `confirmation_resolved`

10. `response_formatter`
    - returns the confirmed result

`job_service` owns the resume logic.
No separate confirmation module should bypass this.

## Confirmation rejection

For `/reject`:

1. `telegram_gateway`
   - receives message

2. `command_router`
   - classifies rejection action

3. `project_resolver`
   - validates project context

4. `job_service`
   - resolves target pending Job
   - sets `approval_state=rejected`
   - sets terminal non-execution `job_status`

5. `event_log_service`
   - writes `confirmation_resolved`

6. `response_formatter`
   - returns rejection result

No business write should execute after rejection.

## Inspection request

For `/status` or `/job` style inspection:

1. `telegram_gateway`
   - receives request

2. `command_router`
   - classifies as inspection

3. `project_resolver`
   - resolves context

4. `job_service`
   - creates inspection Job if the interaction model requires it

5. selected service or lane module
   - performs read-only inspection

6. `response_formatter`
   - returns inspect result

Inspection must not silently mutate business records.

## Write permission summary

## Shared core may write

### `job_service`
- Jobs only

### `run_service`
- Runs only

### `event_log_service`
- Events only

### `airtable_service`
- executes persistence operations requested by authorized callers
- does not own semantic meaning

## Lane modules may write through `airtable_service`

### `content_ops`
- `Content Ideas`
- `Content Drafts`
- `content_stage`
- initial default `monetization_stage` on create only if allowed

### `affiliate_ops`
- `Affiliate Offers`
- `Offer Mappings`
- content-related `monetization_stage`

### `knowledge_ops`
- `Project State`

### `review_ops`
- `Reviews`
- `review_outcome`

### `funnel_ops`
- `Funnel Pages`
- page planning fields
- page-level `monetization_stage`

## Modules that must never directly write business semantic fields

These modules must never directly write:
- `content_stage`
- `monetization_stage`
- `review_outcome`

Modules:
- `telegram_gateway`
- `command_router`
- `project_resolver`
- `job_service`
- `run_service`
- `event_log_service`
- `airtable_service`
- `llm_service`
- `response_formatter`

## Anti-drift rules

The module design is drifting if:
- routing modules start mutating business records
- lane modules start owning Job or Run state
- confirmation logic gets split across multiple writers
- Airtable adapter starts deciding semantics
- formatter output no longer matches real workflow state
- project context can be silently overridden outside `project_resolver`

If any of these happen, the architecture must be corrected.

## Outcome

The module layer should make Operator Core:
- workflow-safe
- confirmation-safe
- auditable
- project-aware
- lane-structured
- reusable across projects
- aligned with the current Airtable and Telegram contracts
