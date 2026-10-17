# Contributor issue workflow

Use this procedure before implementation changes and when asked to start or resume a repository issue, including a short “start ISSUE-ID” request. The initiating thread is the **master**. An explicitly assigned child follows the [step-thread route](#step-thread-route), not a second master workflow.

This is the canonical contributor procedure required by [root instructions](../../AGENTS.md). It defines agent behavior; prose alone does not enforce it. Preserve the [lifecycle contract](task-workspace.md), scoped ownership, recovery rules, supported platforms and [human-controlled publication](../framework/baseline.md#authoring-and-publication-rules).

## Before implementation: task transition and readiness

Research, inspection, planning, administration and ordinary notes may proceed without a Task line, issue binding or roadmap approval. Use the [effect boundary](task-workspace.md#task-free-work-and-implementation-effects) to distinguish an ordinary note from instructions or enforcement. Native permissions and existing authorization still apply.

Before changing source, tests, scripts, dependencies, build inputs, executable workflows, agent instructions or enforcement, enter the task procedure below. Readiness checks current task scope; human roadmap approval remains the procedural gate in stages 3–4. A checksum or `ready` result does not authenticate approval. Unknown mutation-capable commands require this route or a supported task-free alternative.

1. Submit exactly one `Task: <issue-id>` line. Read that exact Linear ticket through the selected runtime's supported issue-read tool as the first provider operation of this issue transition. Earlier research and unrelated task-free operations remain permitted; they do not establish startup. Bounded preparation routes include loading the ticket-read schema and reading this procedure or root `AGENTS.md`. The rule is runtime-neutral; Claude Code supports `ToolSearch` for the configured `get_issue` schema and exact governing file reads; Codex supports the two exact governing shell reads. See the host guide for each admitted spelling. This read precedes local workspace readiness. A session already bound to another issue may submit the Task line: the verified ticket read moves its binding through `rebind` and preserves the old issue; unresolved tool work, or participants still attached to an issue it coordinates, must be settled or detached first; a failed read keeps the old binding, and a Task line for that issue returns to it. The adapter validates the requested identifier and correlates the native completion; see [runtime-specific ticket-read protocols](host-hooks.md#ticket-read-protocols). A verified provider response that it could not find the requested issue stops with its identifier and creates no issue workspace. Authentication, network, permission and malformed-response failures retain their distinct diagnostics.
2. After the response confirms the identifier and immutable issue UUID, the shared startup orchestrator uses the actual runtime/session and Git checkout to register or resume the workspace. A new issue assigns the initiating session as coordinator and installs its initial reader-specific packet. A returning session retains its existing coordinator, packet, roadmap and approval. Another session can join as a roadmap-only reader after the verified ticket read. Reader readiness does not transfer coordinator write authority; handoff remains explicit. Startup reads the packet's actual source bytes, acknowledges its exact digest and verifies `ready`.
3. Recover reported registration, source, identity or runtime failures through [explicit setup and recovery](task-workspace-usage.md#explicit-setup-and-bootstrap). Automate missing local setup after the ticket read; do not make internal IDs, registration or packet assembly human-supplied prerequisites. An adapter must observe the exact provider operation and its completion. If the runtime exposes only an opaque wrapper, report that protocol limitation rather than granting a general tool exemption. A manual lifecycle invocation is recovery evidence, not proof that an installed native callback ran.

The contract is runtime-neutral. This checkout implements adapters for Codex, Cursor and Claude Code. Another runtime must implement and verify the same identity, provider, readiness and ownership contract before claiming support. A runtime's native callback schema belongs in its adapter, not in the shared contributor procedure.

**Exit:** readiness is established before implementation operations. A confirmed missing ticket stops issue startup. Missing local registration, assignment or acknowledgment must be established automatically after the verified read. If an actual provider, identity, permission or runtime failure prevents that work, report the specific failure and recovery action; do not present local setup as a human-supplied eligibility requirement. Continue independent authorized work where possible. Bootstrap does not count as a verified issue transition: stage 1 still sets and reads back In Progress at the first supported assignment start, including planning.

## 1. Establish the master and issue state

1. Use the verified Linear issue read from bootstrap. Read its full criteria and applicable governing inputs through the [task catalog](../README.md). Refresh provider state when needed. Provider calls use their host's verified identity and admitted operation contract in [implemented coverage](host-hooks.md#implemented-coverage). Known research and administrative calls remain task-free. Unknown provider effects require readiness and any existing host restrictions. Archive operations retain their separate lifecycle ownership and verification checks. Inspect existing authorized work, branches, PRs and recorded progress before starting duplicate work.
2. Resolve the **initiating human** from explicit session evidence and a verified provider identity. Connector “me”, issue creator and service-account identity are not sufficient. If identity is ambiguous, ask for the human and resolve the provider user before assigning.
3. If unassigned or assigned to someone else, assign the issue to that initiating human. Read the issue back and verify the exact assignee ID. This is the contributor exception in [authoring and publication rules](../framework/baseline.md#authoring-and-publication-rules).
4. At assignment start, **including planning**, set the issue to **In Progress** and read back its state. If already In Progress, verify it. On resume, preserve a verified later delivery state and continue from the recorded gate; do not reset completed work or silently reopen a terminal issue.
5. Name the master `AGENT-X · Master — <short issue title>` using the actual identifier. Record its actual thread identity, issue identity, observed assignment and status in the workspace once available.

**Exit:** the issue, initiating human, master identity and applicable status are verified. Record unavailable integrations or failed writes/read-backs as explicit blockers; never claim the transition succeeded without evidence.

## 2. Establish or resume shared task state

1. Verify the shared state established by the [implementation transition](#before-implementation-task-transition-and-readiness). Preserve existing state on resume. Manual data requires supported adoption; cleaned state requires verified recovery before task-scoped implementation.
2. Use the canonical `.task/<ISSUE-ID>/roadmap.md` and `context/`. Linked local worktrees share the same issue workspace through [storage and identity](task-workspace.md#storage-and-identity); different issues remain separate. Preserve existing progress, ownership, approval and unresolved work.
3. Preserve the explicitly assigned master coordinator and bounded source packets. For a changed packet, read it, acknowledge its exact digest and verify readiness. Use payload locators such as `roadmap.md` or `context/<name>.md` for managed notes; absolute paths through worktree issue symlinks are rejected by the external source reader. Refresh stale required sources through the coordinator. Use revision-checked lifecycle updates; reread and retry conflicts.
4. Inspect the selected host’s [setup](host-hooks.md#setup-and-bootstrap), [coverage](host-hooks.md#implemented-coverage) and [validation limits](host-hooks.md#validation-and-remaining-evidence). Determine whether installed hooks actually run on the chosen path. Record observed callbacks or the precise unverified/unsupported boundary.

**Exit:** a usable shared roadmap/context workspace and current readiness are verified. A setup failure blocks dependent work: report the exact error and recovery action. Manual core calls establish lifecycle evidence only; separate missing setup, documentation gaps and runtime defects. Preserve platform and child-identity restrictions.

## 3. Present a concrete proposal and wait

1. Populate the roadmap with the issue objective, criteria, governing references, exact candidate context, existing progress and proposed numbered steps. Each step names **scope, dependencies, outputs, verification and exit criteria**. Identify permitted inputs and ownership boundaries.
2. Present the populated proposal and its revision to the human. Mark it proposed and awaiting approval. A blank template, prior approval of the general workflow or a request to start the issue is not approval of this implementation roadmap.
3. Stop before implementation until the human explicitly approves that proposal. Record the attributable approval and exact approved revision. Silence is not approval.

**Exit:** explicit approval is recorded. On resume, verify existing approval against the current scope; preserve it when still applicable.

## 4. Execute within the approval boundary

Approval authorizes the listed steps **through PR submission**, including routine handoffs and checks. Do not request repeated approval at each handoff. Human PR review and merge remain separate gates.

A material change to scope, acceptance criteria, authority or delivery obligations requires a revised proposal and explicit approval before affected implementation. Record the change and its impact. Continue unaffected approved work where possible.

Before writing Python, read [source documentation and logic comments](development.md#source-documentation-and-logic-comments). Apply those rules to production code, scripts and tests throughout implementation. Before handing off, inspect every changed Python file for meaningful contract docstrings and comments before each logical phase; a lint pass alone does not satisfy this review.

**Exit:** each dispatched assignment maps to a current approved numbered step.

## 5. Dispatch fresh step threads

1. Start **one fresh thread per approved numbered step**. Give it the bounded objective, governing criteria, allowed source references, owned outputs, dependencies, verification, exit criteria and return destination. Keep private reasoning out of public artifacts.
2. Verify the actual child host/session identity. Record its thread identity and ownership in the master roadmap, then install its own lifecycle scope. A created-chat ID or inherited parent context alone does not prove a runtime child identity or readiness. The child-identity rule is runtime-neutral. Codex has a separate [native UUID reader-cohort route](host-hooks.md#codex-native-child-route-restricted), with explicit coordinator scope and bounded managed-note writes. For Claude Code: a foreground `Agent` call (no background, no worktree isolation) from the ready master is the supported child route: `SubagentStart` joins the subagent as `<master session>/agent/<agent_id>` with a roadmap-only packet, and the master then `scope`s that key with the step's owned `context/` paths. This route is restricted: it was observed in one local Desktop session (see the host guide) and is unverified elsewhere, so record manual lifecycle evidence when hooks do not run.
3. Run independent steps in parallel when their ownership and inputs permit it. Start dependent steps only after the master verifies prerequisite outputs. Resolve overlapping writes explicitly.

**Exit:** every active step has a verified identity, bounded packet, recorded owner and satisfied dependencies. An unsupported child path blocks dispatch until a supported path is established; do not infer native-hook coverage from a manual attachment.

## 6. Verify handoffs and preserve evidence

1. Each child returns exact changes and revisions, commands/checks and results, unresolved findings and the next action. Include precise artifact and evidence references.
2. The master inspects the exact output against the step’s criteria. Record accepted results, decisions, evidence and unresolved work in the main roadmap and durable permitted context through lifecycle updates. Child completion alone does not satisfy the exit criteria.
3. If temporary external scratch `step-x.md` handoff files were used, verify that all useful information is durably retained and readable, then delete only those scratch files. Keep owned `context/` notes managed through the lifecycle. The current lifecycle has no individual managed-note delete operation, and `reconcile-files` cannot remove files. Retain managed notes until supported issue cleanup; never bypass ownership, manifest or recovery checks with raw deletion.

**Exit:** the master has verified the handoff and retained its evidence. Temporary cleanup is either verified or explicitly unresolved.

## 7. Continue the approved roadmap

After each verified handoff, update progress and dispatch the next eligible step. **Child completion does not end the task.** Continue until all approved steps meet their exit criteria or a blocker requires human input. Record each blocker, its affected work and the next action. Keep other authorized independent work moving.

**Exit:** every approved step is verified complete, or remaining work has an explicit blocker and owner.

## 8. Submit and verify delivery

**Before opening a PR:** finish the approved implementation and documentation,
verify step handoffs, and pass the checks that can run before submission. Resolve
known failures and incomplete work first. Keep Linear **In Progress** until the
candidate is ready for human review. Open a review-ready PR only at that point;
never use an early draft PR as a staging area or test harness, because opening it
can trigger Linear's review transition.

Checks triggered only by a PR run after submission. Report them as pending in the
PR, then verify their results on the submitted revision and fix failures. This
exception does not defer checks available before submission, waive required
checks, or authorize merge.

1. Prepare, submit and read back a **PR for human review into `main`** using [PR preparation and verification](#pr-preparation-and-verification) and the [publication rules](../framework/baseline.md#authoring-and-publication-rules). Retain the exact candidate revision, diff, relevant check results and acceptance evidence in the delivery record. Validate affected links and templates. Verify changed Python files against the [source documentation rules](development.md#source-documentation-and-logic-comments). Report unproven runtime or platform claims.
2. At review-ready PR submission, move Linear to **In Review** if automation has not already done so, and verify by read-back. Record the PR and lifecycle `in_review` checkpoint; retain context while review and corrections continue.
3. Read the live rules applicable to the target branch to identify required checks. Preserve current-candidate evidence for failures of currently required checks and the applicable **CI failed** label. Reconcile that label against current requirements and results; record a removed requirement as removed, not passed. An absent check that is no longer required is not a merge blocker. Keep repository-local validation and issue-specific acceptance obligations separate from GitHub merge requirements. Follow those obligations without inventing infrastructure work.
4. Keep merge human-controlled. Verify the actual PR merge into `main`, then move to **Merged** and read back the state. After acceptance and every issue obligation are verified, move to **Done** and verify the completed state. An open PR, passing checks or child completion is insufficient.
5. Record supported [completion evidence](task-workspace-usage.md#recovery-and-completion-semantics) before terminal lifecycle disposition. Preserve unresolved obligations and recovery state. Report unavailable transitions/integrations as blockers rather than fabricating closure.

**Exit:** actual merge, human acceptance, required checks, obligations and provider completion are evidenced. Workspace archival/cleanup retains its separate lifecycle prerequisites.

### PR preparation and verification

Apply these steps when creating a PR and when updating it after review or scope changes. Before creating a PR, meet the [submission readiness requirements](#8-submit-and-verify-delivery).

**Title:** use `<ISSUE-ID>: <issue title>`, with the actual issue identifier, a colon and one space, followed by the issue title. For example: `TEAM-123: Require roadmap approval and separate step threads when starting issues`. Do not cite issue identifiers or issue URLs anywhere else in code, tests or documentation; describe the event, date and host instead (`scripts/dev.py ticket-references` enforces this). Verify the title against the linked issue before publication and during PR read-back.

1. Load [`.github/pull_request_template.md`](../../.github/pull_request_template.md) from the candidate checkout. It is the canonical body structure. Copy it before filling content, including when using an API or CLI that does not insert it automatically.
2. Preserve every heading and change-type option in its exact wording and order. Mark applicable options with `x` in **Type of Change** and leave other options unchecked. Fill every section. Put the issue link, concrete problem, resulting behavior and material limitations in **Description**. Put reproducible commands, tested revision, environment, results and pending validation in **Testing**. Use **Screenshots or Command Outputs (if applicable)** for useful output, or `N/A` with a reason. Keep relevant evidence inside these sections.
3. Keep the **Type of Change** checkboxes; omit the code review checklist. Use concise prose and concrete evidence for validation. Report checks actually run, their results and any pending validation in **Testing**. Use `N/A` with a reason for sections that do not apply. Keep automation-owned additions separate from the authored template and preserve them when editing.
4. Before submission, compare the prepared body against the loaded template: exact heading sequence and change-type options, completed section content, and accurate results, pending validation and N/A explanations. Confirm the title, issue link, branch, target `main` and candidate revision. Use structured body arguments or a UTF-8 body file to preserve Markdown.
5. After creating or editing the PR, fetch it again. Verify the published title and authored body against the prepared values, template structure, base/head branches and exact head commit. Repair mismatches before reporting successful submission. Attach the PR to the working chat and retain the read-back in the delivery record. When the session runs in the Claude desktop app with hooks enabled, turn on the app's PR monitor after the read-back (`set_monitor` with `auto_fix` on the published PR URL), confirm it with `get_status`, and record both in the delivery record; on another runtime, record that no monitor is available (see the [host guide](host-hooks.md#implemented-coverage)). Repeat affected checks when the candidate or template changes.

**Submission exit:** the published PR matches the required title and exact template, its evidence applies to the named revision, and current required-check status is recorded. Submission does not establish human acceptance or authorize merge.

## Step-thread route

1. Use the supplied bounded assignment and actual host/session identity. Follow the [implementation transition](#before-implementation-task-transition-and-readiness) with the master’s assigned packet; report missing identity or setup before task-scoped implementation. A Claude `Agent`-tool subagent uses session `<master session>/agent/<agent_id>`; after the master re-scopes it, it runs `read`, `acknowledge` and `ready` through the lifecycle command and never `scope`, `create` or a nested `Agent` call.
2. After scoped read, exact-digest acknowledgment and readiness, read the permitted governing references and verify the selected checkout before implementation. Stop affected work and request refresh when required sources become stale.
3. Implement only your approved step within its owned paths. Preserve other work and use the assigned checks. Leave master roadmap ownership and issue delivery transitions with the master unless explicitly assigned.
4. Return the [handoff](#6-verify-handoffs-and-preserve-evidence) and await the master’s verification or correction request. Do not restart issue planning or infer a new approval gate for this already approved assignment.
