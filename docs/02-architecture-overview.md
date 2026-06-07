# Architecture Overview

## Purpose

This file defines the actual architecture shape of Operator Core.

It explains how the system is structured after the workflow-state model, Airtable model, Telegram interaction contract, module responsibilities, and operator autonomy rules have been aligned.

This file is architectural truth for:
- system layers
- workflow-state placement
- shared core vs lane boundaries
- confirmation handling
- project-context ownership
- operator-facing consequences

## Core architecture model

Operator Core is a human-in-the-loop operator platform.

It is not a freeform assistant.
It is not a project-blind bot.
It is not a chat-only layer on top of hidden actions.

The architecture is built around one controlled workflow path:

1. Telegram receives the operator request
2. the shared core interprets and validates the request
3. project context is resolved authoritatively
4. a Job is created or resumed
5. a Run is created when execution starts
6. the correct lane module prepares business intent
7. Airtable stores or reads business state through the persistence boundary
8. Events are written for audit-relevant changes
9. a response is formatted from real workflow state
10. Telegram delivers the result back to the operator

This flow is the center of the architecture.

## Architectural outcome

The architecture must produce a system that is:

- practical for Julia in daily use
- inspectable and controllable for Emanuel
- project-safe
- state-safe
- confirmation-safe
- auditable
- extensible to more projects later

## Main architecture layers

Operator Core is best understood as six connected layers.

### Layer 1: Operator interface

Primary interface:
- Telegram

Telegram is responsible for:
- receiving operator messages
- carrying reply context
- carrying role-aware operator interaction
- delivering formatted results
- exposing confirmation and inspection flows

Telegram is not responsible for:
- business state ownership
- workflow-state ownership
- project-context authority
- semantic business-field ownership

Telegram is the working surface, not the workflow authority.

### Layer 2: Shared core control layer

The shared core is the workflow authority layer.

This layer contains the modules that own:
- request normalization
- request classification
- project-context resolution
- workflow-state transitions
- confirmation control
- rule validation
- persistence orchestration
- audit logging
- response shaping

This layer is shared across projects.

Its job is not to own project semantics.
Its job is to keep execution safe, consistent, and traceable.

### Layer 3: Lane execution layer

Lane modules own domain-specific business logic inside their lane.

The active lane structure is:

- `content_ops`
- `affiliate_ops`
- `knowledge_state_ops`
- `review_analytics_ops`
- `funnel_website_ops`

Lane modules interpret project semantics only inside their domain.

They may:
- prepare business-object reads and writes
- own field-level semantic intent in their lane
- request structured LLM support
- propose state changes on business records they own

They must not:
- own Jobs
- own Runs
- own Events
- own project-context resolution
- bypass confirmation control
- invent parallel workflow-state systems

### Layer 4: Workflow-state layer

Workflow state is a first-class architecture layer.

It is not just a logging pattern.
It is not just response wording.

The workflow-state layer consists of:

- `Jobs`
- `Runs`
- `Events`

These records are the authoritative control trail of system execution.

#### Jobs
Jobs are the business-level workflow units.

They answer:
- what work was requested
- which project it belongs to
- which lane it belongs to
- whether it is queued, running, waiting, completed, failed, or cancelled
- whether confirmation is pending, approved, rejected, or not required

#### Runs
Runs are execution attempts linked to Jobs.

They answer:
- when execution actually started
- whether that attempt succeeded, failed, aborted, or was skipped
- which execution attempt belongs to which Job

#### Events
Events are append-only workflow and audit markers.

They answer:
- what meaningful change happened
- which entity it affected
- which Job or Run it relates to
- when confirmation was requested or resolved
- when important errors or updates happened

These three objects together make the workflow visible and auditable.

### Layer 5: Business-state layer

Business state is stored in Airtable.

Airtable is the operational business state layer, not the workflow engine.

It stores the project-facing records such as:

- `Project State`
- `Content Ideas`
- `Content Drafts`
- `Affiliate Offers`
- `Offer Mappings`
- `Funnel Pages`
- `Reviews`
- `Analysis Snapshots`
- `Evidence Packs`

These records hold the real business objects that the operators work with daily.

Airtable must not be treated as:
- a chat transcript
- a hidden dump of model text
- a replacement for workflow-state ownership

Business state and workflow state are distinct and must stay distinct.

### Layer 6: Structured model-assistance layer

The model layer supports structured generation and reasoning through the shared core.

Its role is to help with:
- analysis synthesis from existing state and analytics
- writer-brief preparation from explicit analysis objects
- structured content outputs
- structured review outputs
- structured mapping suggestions
- lane-specific generation tasks

The model layer is never the authority for:
- workflow-state transitions
- project-context resolution
- confirmation decisions
- business-field ownership
- final system truth

The model assists.
The architecture controls.

## Analysis foundation objects

The current next architecture layer on top of the live Telegram/content slice is an explicit analysis foundation.

The core objects are:

- `AnalysisSnapshot`
- `WriterBrief`
- `EvidencePack`
- `ModelExecutionMeta`

These objects do not replace Jobs, Runs, Events, or business records.

They exist to make the path from raw operational data to later model output inspectable and traceable:

1. raw Airtable / analytics / rule sources are read
2. explicit analysis snapshots are built
3. a writer brief is prepared from those snapshots
4. later model output can point back to a concrete evidence pack

The analysis foundation must live in shared-core-controlled services and persistence contracts.
It must not be hidden inside Telegram wording, formatter shortcuts, or ad hoc model prompts.

## State authority model

The architecture uses two different classes of authoritative state.

### Authoritative workflow fields

These fields are authoritative in the control layer:

- `job_status`
- `run_status`
- `approval_state`

These fields answer workflow questions such as:
- is the work running
- is it waiting for input
- is it waiting for approval
- did the execution succeed
- is the action completed or cancelled

These fields belong to the workflow-state layer.

### Authoritative business semantic fields

These fields are authoritative in the business layer:

- `content_stage`
- `monetization_stage`
- `review_outcome`

These fields answer domain questions such as:
- where the content object is in its content lifecycle
- where a monetization-related object is in its monetization lifecycle
- what the review result actually is

These fields belong to the business-state layer and are owned by the correct lane modules.

### Non-duplication rule

Architecture must not allow:
- pseudo-status fields that repeat the meaning of authoritative fields
- response text that pretends to replace stored state
- modules that write semantic meaning outside their ownership boundary

This rule is necessary to prevent state drift.

## Project-context model

Project context is a first-class architectural concern.

It is not a convenience helper.
It is not a best-effort guess done by multiple modules.

### Authoritative context owner

`project_resolver` is the single authoritative owner of project-context resolution.

It decides:
- which project the request belongs to
- whether reply metadata is valid
- whether explicit project input is valid
- whether stored chat-level project context may be used
- whether there is a context conflict that must block execution

No other module may silently override resolved project context.

### Context resolution result

Every actionable request must have one authoritative project result:

- `resolved_project_key`

This is then consumed by:
- `job_service`
- `run_service`
- lane modules
- `airtable_service`
- `response_formatter`

### Cross-project safety

The architecture must stop unsafe execution when:
- explicit project context conflicts with reply context
- stored chat context conflicts with the current request
- the target object belongs to a different project than the resolved request

Project safety is more important than convenience.

## Confirmation model

Confirmation is an architectural workflow stop.

It is not only a Telegram UX pattern.
It is not only a phrasing convention.

### What confirmation means architecturally

When confirmation is required:

- the request is valid
- the target action is understood
- the action is not safe to execute directly
- the Job enters a real paused control state
- no protected write may execute before human approval

This means confirmation changes real workflow state.

### Required stored state for confirmation

At minimum:

- `job_status=waiting_for_approval`
- `approval_state=pending`

And the system should write:
- a confirmation Event

### Architectural result

Confirmation creates a real stop in the workflow path.

The architecture must not allow:
- risky writes before confirmation
- fake confirmation only in chat wording
- parallel hidden execution while the Job still appears pending

### Confirmation resume

When confirmation is approved:

- the existing pending Job is resumed
- a new Run may be created for resumed execution
- the protected write is then allowed to execute
- the Job reaches its next valid terminal or active state
- confirmation resolution is logged as an Event

### Confirmation rejection

When confirmation is rejected:

- the existing pending Job is resolved
- no protected write executes
- the Job moves to its correct non-executed terminal state
- confirmation resolution is logged as an Event

## Shared core vs lane architecture

The architecture depends on a strict split between workflow control and business semantics.

### Shared core owns

Shared core modules own:
- Telegram intake and output handling
- command classification
- project-context resolution
- Job creation and Job state changes
- Run creation and Run state changes
- Event creation
- confirmation control
- rules enforcement
- persistence orchestration
- response-state formatting

Shared core is responsible for making work safe and traceable.

### Lane modules own

Lane modules own:
- business-object meaning in their lane
- allowed field-level write intent in their lane
- domain interpretation of project semantics
- domain-specific object updates

Lane modules are responsible for making business updates semantically correct.

### Why this split matters

If shared core starts owning business semantics:
- project meaning drifts
- workflow logic becomes domain-contaminated
- reusable core quality declines

If lane modules start owning workflow state:
- Jobs and Runs lose authority
- confirmation behavior drifts
- auditability breaks

The architecture only works if both layers stay inside their boundaries.

## Kernel execution flow

The canonical execution flow is:

1. `telegram_gateway`
   - receives message
   - normalizes Telegram metadata

2. `command_router`
   - classifies request
   - determines action type
   - determines lane target
   - determines whether this is new work, continuation, inspection, or confirmation resolution

3. `project_resolver`
   - resolves authoritative `resolved_project_key`
   - blocks mixed-project conflicts

4. `rules_engine`
   - validates whether the action is allowed
   - validates whether confirmation is required
   - validates write boundaries

5. `job_service`
   - creates the Job
   - or resumes / updates the existing pending Job in confirmation flows
   - owns `job_status`
   - owns `approval_state`

6. `run_service`
   - creates a Run when real execution starts
   - owns `run_status`

7. lane module
   - interprets business intent
   - prepares proposed reads and writes for owned business objects

8. `rules_engine`
   - validates proposed business writes if needed

9. `airtable_service`
   - reads or writes business records
   - does not decide semantic meaning itself

10. `event_log_service`
    - writes audit-relevant Events

11. `response_formatter`
    - formats output from real state and actual results

12. `telegram_gateway`
    - sends the response

This is the canonical architecture path for normal execution.

## Canonical continuation flow

Reply-based continuation is part of the architecture, but only in a controlled way.

The continuation path is:

1. Telegram reply arrives
2. reply metadata is normalized
3. `command_router` classifies the request as continuation
4. `project_resolver` validates the project context from reply metadata and current input
5. `job_service` creates a new continuation Job
6. `continuation_of_job_id` links to the earlier Job
7. execution continues through the normal kernel flow

A continuation is a new workflow unit linked to prior work.
It is not a hidden reuse of fragile chat history.

## Canonical confirmation flow

When a risky action requires approval, the path is:

1. request is received and routed normally
2. `project_resolver` resolves project context
3. `rules_engine` marks confirmation as required
4. `job_service` creates or updates the Job with:
   - `job_status=waiting_for_approval`
   - `approval_state=pending`
5. `event_log_service` writes `confirmation_requested`
6. `response_formatter` returns a waiting-for-confirmation result
7. no protected write executes yet

When `/confirm` arrives:

1. the pending Job is targeted safely
2. `job_service` resolves and updates the existing pending Job
3. `run_service` creates the resumed Run
4. the owning lane executes the protected action
5. `airtable_service` persists the write
6. `event_log_service` writes `confirmation_resolved`
7. the response shows the confirmed result

When `/reject` arrives:

1. the pending Job is targeted safely
2. `job_service` marks rejection on the existing pending Job
3. no protected write executes
4. `event_log_service` writes `confirmation_resolved`
5. the response shows the rejected result

This is a real workflow path, not just a UI conversation branch.

## Role consequences in architecture

Julia- and Emanuel-differences are architectural consequences of the control model, not separate systems.

### Julia consequence

Because Julia is the daily operator, the architecture must support:

- low-friction content and workflow actions
- short mobile-usable responses
- direct execution for low-risk useful work
- reply-based continuation that is simple to use
- no forced backend-style interaction for normal tasks

This is not only a product choice.
It is an interface consequence of a controlled workflow architecture.

### Emanuel consequence

Because Emanuel is the controller and maintainer, the architecture must also support:

- deeper inspection of Jobs and state
- explicit visibility into workflow status
- confirmation resolution control
- clearer exposure of record references and workflow identifiers
- operational oversight without bypassing architecture boundaries

This is also not just a product preference.
It is a consequence of control ownership and auditability requirements.

### Shared rule

Julia and Emanuel may see different response depth.
They must not operate on different workflow laws.

The architecture is one system with one control model.

## Architectural priorities

The architecture is optimized for:

- practical daily usability
- explicit workflow state
- project-safe execution
- confirmation-safe control
- auditable actions
- clean shared-core reuse
- later addition of more projects without redesigning workflow authority

It is explicitly not optimized for:

- generic chatbot behavior
- freeform project mixing
- hidden autonomous operation
- architecture theater
- vague layer descriptions without ownership boundaries

## Anti-drift architecture rules

The architecture is drifting if:

- Telegram wording becomes the real state instead of Jobs, Runs, and records
- `project_resolver` is bypassed or duplicated
- confirmation exists only in responses and not in workflow state
- lane modules write workflow records directly
- shared core writes business semantic fields directly
- Airtable becomes a mixed dump of workflow and business semantics without ownership boundaries
- response formatting claims completion before real state is saved

If any of these happen, the architecture must be corrected.

## First active architecture target

The first real active target remains:

- one shared core
- one active project instance: `everydayengel`
- Julia can use the system practically through Telegram
- Emanuel can inspect and control the system through the same architecture
- the workflow-state model is already strong enough that later projects can be added without changing core authority rules

This is the standard the architecture must satisfy now.

## Outcome

The intended architecture is:

- Telegram as working interface
- shared core as workflow authority
- lane modules as business-semantic owners
- Jobs, Runs, and Events as explicit workflow-state layer
- Airtable as visible business-state layer
- project context resolved once and owned centrally
- confirmation implemented as real workflow pause
- responses derived from real stored state
- useful for Julia
- controllable for Emanuel
- stable enough for later multi-project expansion
