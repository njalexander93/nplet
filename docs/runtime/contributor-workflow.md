# Contributor issue workflow

Authorized research, review, planning, notes outside the project code repositories and project administration can proceed without a Task assignment. The adapters admit only the [named task-free operations](host-hooks.md#task-free-parent-operations). These operations grant no implementation authority. Before protected implementation, establish verified ticket startup, current readiness and explicit roadmap approval through this procedure.

Use this procedure when asked to start or resume a repository issue, including a short “start AGENT-X” request. The initiating thread is the **master**. An explicitly assigned child follows the [step-thread route](#step-thread-route), not a second master workflow.

This is the canonical contributor procedure required by [root instructions](../../AGENTS.md). It defines agent behavior; prose alone does not enforce it. Preserve the [lifecycle contract](task-workspace.md), scoped ownership, recovery rules, supported platforms and [human-controlled publication](../framework/baseline.md#authoring-and-publication-rules).

## Issue intake and Planned readiness

Creating, updating and planning an issue is task-free project administration through the [task-free parent route](host-hooks.md#task-free-parent-operations). It needs no Task binding and grants no implementation authority. The packaged [sizing and classification policy](../../src/nplet/resources/organization/baseline.md#issue-sizing-and-classification) is authoritative for points, classifications and Planned readiness; apply it rather than restating it.

1. **Capture.** After creating an issue, read it back and report its link and actual provider status, such as Proposed, Backlog or Triage. State plainly that it is a placeholder, not Planned. A rough description is enough at capture.
2. **Scope.** Before moving an issue to Planned, complete the [scoping record](#scoping-record) in its description. Estimate additions plus deletions for every affected area: implementation, tests, fixtures, configuration, documentation and supporting changes. A replacement counts both the removed and the added lines. Derive points from the total, then assign Agent Class, Assurance Level, Type and Execution independently. Consult the recorded [estimate outcomes](#estimate-outcome-record) of comparable completed issues when estimating.
3. **Decompose.** Above 5 points or 1,000 estimated lines, first seek smaller concrete deliverables, and scope and classify each increment the same way. An 8-point issue records why reasonable splitting fails and needs explicit human approval before Planned. Never admit a 13-point issue; decompose it instead. Link sibling increments as related and use blocking relationships only for genuine prerequisites. Keep each increment a separately reviewable PR.
4. **Move to Planned** only when every required record field is complete and, for 8 points, the human approval is recorded. Otherwise leave the issue in its capture status and report the missing fields. Read back the Planned status.

The scoping record and Planned status do not approve an implementation roadmap, admit a Wave or authorize execution. Starting the issue still follows bootstrap and the unchanged [proposal approval gate](#3-present-a-concrete-proposal-and-wait).

**Waves.** Wave selection caps a Wave at 12 points and 3,000 estimated changed lines; both are caps, not targets ([Wave boundaries](../../src/nplet/resources/organization/baseline.md#planning-and-wave-boundaries)). This procedure starts one issue per master; it neither selects nor admits Waves.

### Scoping record

Keep the record compact. Open questions must not leave a required field unresolved.

```text
Deliverable:
Completion criteria:
Estimate basis (additions + deletions per area):
  implementation / tests / fixtures / configuration / documentation / supporting:
Estimated total: <low>–<high> lines; points:
Agent Class:
Assurance Level:
Type:
Execution:
Decomposition: split into <increments> | not split because <reason>
8-point rationale and human approval reference (8 points only):
Related (sibling increments):
Blocked by (genuine prerequisites only):
Open questions:
```

**Exit:** the issue is either a reported placeholder in its capture status or Planned with a complete record. Neither state starts the issue.

## Before protected implementation: bootstrap readiness

1. Submit exactly one `Task: <issue-id>` line. Read that exact Linear ticket as the first issue-provider operation in the startup sequence, using the selected runtime's supported issue-read tool. Independent task-free work remains available while the ticket read is pending. The issue-startup preparation route also admits named reads: loading the issue-read tool's schema and reading this procedure or the root `AGENTS.md`. The rule is runtime-neutral; Claude Code supports `ToolSearch` for the configured `get_issue` schema and exact governing file reads; Codex supports the two exact governing shell reads. See the host guide for each admitted spelling. This read precedes local workspace readiness. A session already bound to another issue may submit the Task line: the verified ticket read moves its binding through `rebind` and preserves the old issue; unresolved tool work, or participants still attached to an issue it coordinates, must be settled or detached first; a failed read keeps the old binding, and a Task line for that issue returns to it. The adapter validates the requested identifier and correlates the native completion; see [runtime-specific ticket-read protocols](host-hooks.md#ticket-read-protocols). A verified provider response that it could not find the requested issue stops with its identifier and creates no issue workspace. Authentication, network, permission and malformed-response failures retain their distinct diagnostics.
2. After the response confirms the identifier and immutable issue UUID, the shared startup orchestrator uses the actual runtime/session and Git checkout to register or resume the workspace. A new issue assigns the initiating session as coordinator and installs its initial reader-specific packet. A returning session retains its existing coordinator, packet, roadmap and approval. Another session can join as a roadmap-only reader after the verified ticket read. Reader readiness does not transfer coordinator write authority; handoff remains explicit. Startup reads the packet's actual source bytes, acknowledges its exact digest and verifies `ready`.
3. Recover reported registration, source, identity or runtime failures through [explicit setup and recovery](task-workspace-usage.md#explicit-setup-and-bootstrap). Automate missing local setup after the ticket read; do not make internal IDs, registration or packet assembly human-supplied prerequisites. An adapter must observe the exact provider operation and its completion. If the runtime exposes only an opaque wrapper, report that protocol limitation rather than granting a general tool exemption. A manual lifecycle invocation is recovery evidence, not proof that an installed native callback ran.

The contract is runtime-neutral. This checkout implements adapters for Codex, Cursor and Claude Code. Another runtime must implement and verify the same identity, provider, readiness and ownership contract before claiming support. A runtime's native callback schema belongs in its adapter, not in the shared contributor procedure.

**Exit:** readiness is established before protected issue operations. A confirmed missing ticket stops issue startup. Missing local registration, assignment or acknowledgment must be established automatically after the verified read. If an actual provider, identity, permission or runtime failure prevents that work, report the specific failure and recovery action; do not present local setup as a human-supplied eligibility requirement. Continue independent authorized work where possible. Bootstrap does not count as a verified issue transition: stage 1 still sets and reads back In Progress at the first supported assignment start, including planning.

## 1. Establish the master and issue state

1. Use the verified Linear issue read from bootstrap. Read its full criteria and applicable governing inputs through the [task catalog](../README.md). Refresh provider state when needed. Named Linear inspection and administration calls use the [task-free parent route](host-hooks.md#task-free-parent-operations), including create and update. Claude Code and Cursor retain their configured provider checks. Other Codex parent `mcp__` calls retain generic readiness admission and pending tracking; existing child/delegation tool-name denials still apply. Its archive gate is not exclusive: when it returns false, the call can still pass the generic readiness route. An earlier archive-specific error can prevent that fallthrough; for example, a legacy document save without an export fails before generic readiness. Cursor admits the named task-free Linear route only. Inspect existing authorized work, branches, PRs and recorded progress before starting duplicate work.
2. Resolve the **initiating human** from explicit session evidence and a verified provider identity. Connector “me”, issue creator and service-account identity are not sufficient. If identity is ambiguous, ask for the human and resolve the provider user before assigning.
3. If unassigned or assigned to someone else, assign the issue to that initiating human. Read the issue back and verify the exact assignee ID. This is the contributor exception in [authoring and publication rules](../framework/baseline.md#authoring-and-publication-rules).
4. At assignment start, **including planning**, set the issue to **In Progress** and read back its state. If already In Progress, verify it. On resume, preserve a verified later delivery state and continue from the recorded gate; do not reset completed work or silently reopen a terminal issue. Keep an issue that is **Blocked** by a prerequisite in Blocked until the [stop route](#stop-route-for-a-necessary-prerequisite) resume conditions hold; that resume step, not startup, moves it to In Progress.
5. Name the master `AGENT-X · Master — <short issue title>` using the actual identifier. Record its actual thread identity, issue identity, observed assignment and status in the workspace once available.

**Exit:** the issue, initiating human, master identity and applicable status are verified. Record unavailable integrations or failed writes/read-backs as explicit blockers; never claim the transition succeeded without evidence.

## 2. Establish or resume shared task state

1. Verify the shared state established by the [bootstrap prerequisite](#before-protected-implementation-bootstrap-readiness). Preserve existing state on resume. Manual data requires supported adoption; cleaned state requires verified recovery before protected task work.
2. Use the canonical `.task/<ISSUE-ID>/roadmap.md` and `context/`. Linked local worktrees share the same issue workspace through [storage and identity](task-workspace.md#storage-and-identity); different issues remain separate. Preserve existing progress, ownership, approval and unresolved work.
3. Preserve the explicitly assigned master coordinator and bounded source packets. For a changed packet, read it, acknowledge its exact digest and verify readiness. Use payload locators such as `roadmap.md` or `context/<name>.md` for managed notes; absolute paths through worktree issue symlinks are rejected by the external source reader. Refresh stale required sources through the coordinator. Use revision-checked lifecycle updates; reread and retry conflicts.
4. Inspect the selected host’s [setup](host-hooks.md#setup-and-bootstrap), [coverage](host-hooks.md#implemented-coverage) and [validation limits](host-hooks.md#validation-and-remaining-evidence). Determine whether installed hooks actually run on the chosen path. Record observed callbacks or the precise unverified/unsupported boundary.

**Exit:** a usable shared roadmap/context workspace and current readiness are verified. A setup failure blocks dependent work: report the exact error and recovery action. Manual core calls establish lifecycle evidence only; separate missing setup, documentation gaps and runtime defects. Preserve platform and child-identity restrictions.

## 3. Present a concrete proposal and wait

1. Populate the roadmap with the issue objective, criteria, governing references, exact candidate context, existing progress and proposed numbered steps. Each step names **scope, dependencies, outputs, verification and exit criteria**. Identify permitted inputs and ownership boundaries. For an issue already in Planned, reassess its [scoping record](#scoping-record) against the sizing policy. A re-estimate that changes the points updates the scoping record in the proposal. One above 5 points or 1,000 lines needs proposed decomposition or the 8-point exception; one at 13 points needs decomposition. Reassess only the started issue; do not clean up the backlog.
2. Present the populated proposal and its revision to the human. Mark it proposed and awaiting approval. A blank template, prior approval of the general workflow or a request to start the issue is not approval of this implementation roadmap.
3. Stop before implementation until the human explicitly approves that proposal. Record the attributable approval and exact approved revision. Silence is not approval.

**Exit:** explicit approval is recorded. On resume, verify existing approval against the current scope; preserve it when still applicable.

## 4. Execute within the approval boundary

Approval authorizes the listed steps **through PR submission**, including routine handoffs and checks. Do not request repeated approval at each handoff. Human PR review and merge remain separate gates.

A material change to scope, acceptance criteria, authority or delivery obligations requires a revised proposal and explicit approval before affected implementation. Record the change and its impact. Continue unaffected approved work where possible.

Before writing Python, read [source documentation and logic comments](development.md#source-documentation-and-logic-comments). Apply those rules to production code, scripts and tests throughout implementation. Before handing off, inspect every changed Python file for meaningful contract docstrings and comments before each logical phase; a lint pass alone does not satisfy this review.

### Scope discovery during execution

Classify a discovery by its scope, not its size. No line count or percentage makes work a blocker; a decision that changes scope does:

- **Estimate variance:** the work stays inside the approved deliverables but is larger; for example, 1,250 actual against 1,000 estimated lines. Continue and record the difference in the [estimate outcome record](#estimate-outcome-record). It is not a blocker and needs no new approval. When refreshed `main` or the variance breaks the decomposition rules for the remaining work, apply [intake step 3](#issue-intake-and-planned-readiness) through the revised-proposal rule above.
- **Optional improvement:** not needed to meet the acceptance criteria. Record it as a separate issue linked as related through task-free project administration, never as blocking, and continue.
- **Necessary prerequisite:** needed to meet the criteria, outside the approved scope and a reviewable deliverable of its own. Follow the [stop route](#stop-route-for-a-necessary-prerequisite).

A small necessary change that only makes the approved deliverable work, such as about 50 lines of supporting code, is not a new deliverable: make it, note it in the PR and the [estimate outcome record](#estimate-outcome-record), and continue without new approval. Adding another feature to the PR to finish the scoped one is never such a change.

#### Stop route for a necessary prerequisite

1. Pause the whole issue at a consistent point. Verify in-flight step handoffs or record them as unfinished. Do not start the prerequisite.
2. Preserve unfinished work: commit it to the issue branch and push that branch. Do not open a PR; [stage 8](#8-submit-and-verify-delivery) forbids early draft PRs. Record a lifecycle `checkpoint` and an `outcome` with disposition `blocked`, citing the pushed commit, through the existing [state transitions](task-workspace.md#state-transitions); add no lifecycle state or storage.
3. Create the prerequisite issue with a complete [scoping record](#scoping-record) through [issue intake](#issue-intake-and-planned-readiness). It stays in its capture status or Planned; creation is not approval.
4. Link it as blocking the original issue, move the original issue to **Blocked** and read both back. Report an unavailable status or link as a blocker.
5. Report to the human with the blocker record below and stop. The master never executes the prerequisite or launches its thread; the prerequisite gets normal planning, approval and its own master thread.

```text
Discovery:
Cause:
Evidence:
Classification: necessary prerequisite (outside approved scope, own deliverable)
Scope impact:
Completed work:
Preserved at: <branch>@<commit>; lifecycle checkpoint revision <n>
Prerequisite: <issue link>, blocks this issue, status <observed>
Original issue status: Blocked (read back)
Resume conditions:
Not done: prerequisite neither executed nor launched
```

**Resume** only after the prerequisite's verified merge into `main`. Merge `main` into the issue branch, reassess the remaining original scope against the approved roadmap and re-estimate when needed. If the remaining scope, criteria or authority changed materially, present a revised proposal and wait under the [proposal gate](#3-present-a-concrete-proposal-and-wait). Otherwise record the reassessment, set the lifecycle `outcome` back to `active`, move the issue to **In Progress** with read-back and continue under the existing approval. Child completion, the prerequisite's merge or the human's earlier approval of other work is never new approval.

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

After each verified handoff, update progress and dispatch the next eligible step. **Child completion does not end the task.** Continue until all approved steps meet their exit criteria or a blocker requires human input. Record each blocker, its affected work and the next action. A necessary prerequisite blocker follows the [stop route](#stop-route-for-a-necessary-prerequisite). Keep other authorized independent work moving.

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
4. Keep merge human-controlled. Verify the actual PR merge into `main`, then move to **Merged** and read back the state. After acceptance and every issue obligation are verified, record the [estimate outcome](#estimate-outcome-record), then move to **Done** and verify the completed state. An open PR, passing checks or child completion is insufficient.
5. Record supported [completion evidence](task-workspace-usage.md#recovery-and-completion-semantics) before terminal lifecycle disposition. Preserve unresolved obligations and recovery state. Report unavailable transitions/integrations as blockers rather than fabricating closure.

**Exit:** actual merge, human acceptance, required checks, obligations and provider completion are evidenced. Workspace archival/cleanup retains its separate lifecycle prerequisites.

### Estimate outcome record

Before moving to **Done**, record the outcome in the roadmap's delivery section and as a comment on the issue:

```text
Original estimate: <low>–<high> lines; <points> points
Actual: <additions + deletions> lines (the merged PR's diff; excludes merged prerequisites)
Difference and reasons:
Discovered splits and prerequisites:
```

### PR preparation and verification

Apply these steps when creating a PR and when updating it after review or scope changes. Before creating a PR, meet the [submission readiness requirements](#8-submit-and-verify-delivery).

**Title:** use `<ISSUE-ID>: <issue title>`, with the actual issue identifier, a colon and one space, followed by the issue title. For example: `TEAM-123: Require roadmap approval and separate step threads when starting issues`. Do not cite issue identifiers or issue URLs anywhere else in code, tests or documentation; describe the event, date and host instead (`scripts/dev.py ticket-references` enforces this). Verify the title against the linked issue before publication and during PR read-back.

1. Load [`.github/pull_request_template.md`](../../.github/pull_request_template.md) from the candidate checkout. It is the canonical body structure. Copy it before filling content, including when using an API or CLI that does not insert it automatically.
2. Preserve every heading and change-type option in its exact wording and order. Mark applicable options with `x` in **Type of Change** and leave other options unchecked. Fill every section. Put the issue link, concrete problem, resulting behavior and material limitations in **Description**. Put reproducible commands, tested revision, environment, results and pending validation in **Testing**. Use **Screenshots or Command Outputs (if applicable)** for useful output, or `N/A` with a reason. Keep relevant evidence inside these sections.
3. Keep the **Type of Change** checkboxes; omit the code review checklist. Use concise prose and concrete evidence for validation. Report checks actually run, their results and any pending validation in **Testing**. Use `N/A` with a reason for sections that do not apply. Keep automation-owned additions separate from the authored template and preserve them when editing.
4. Before submission, compare the prepared body against the loaded template: exact heading sequence and change-type options, completed section content, and accurate results, pending validation and N/A explanations. Confirm the title, issue link, branch, target `main` and candidate revision. Use structured body arguments or a UTF-8 body file to preserve Markdown.
5. After creating or editing the PR, fetch it again. Verify the published title and authored body against the prepared values, template structure, base/head branches and exact head commit. Repair mismatches before reporting successful submission. Attach the PR to the working chat and retain the read-back in the delivery record. When the session runs in the Claude desktop app with hooks enabled, turn on the app's PR monitor after the read-back (`set_monitor` with `auto_fix` on the published PR URL), confirm it with `get_status`, and record both in the delivery record; on another runtime, record that no monitor is available (see the [host guide](host-hooks.md#implemented-coverage)). Repeat affected checks when the candidate or template changes.

6. Before resolving an inline review thread, reply in that thread with the fix or resolution rationale and relevant commit or verification evidence. If rejecting the finding as a false positive, explicitly state that decision, explain why it does not apply, and cite supporting evidence before resolving the thread.

**Submission exit:** the published PR matches the required title and exact template, its evidence applies to the named revision, and current required-check status is recorded. Submission does not establish human acceptance or authorize merge.

## Step-thread route

1. Use the supplied bounded assignment and actual host/session identity. Follow the [bootstrap prerequisite](#before-protected-implementation-bootstrap-readiness) with the master’s assigned packet; report missing identity or setup before ordinary tools. A Claude `Agent`-tool subagent uses session `<master session>/agent/<agent_id>`; after the master re-scopes it, it runs `read`, `acknowledge` and `ready` through the lifecycle command and never `scope`, `create` or a nested `Agent` call.
2. After scoped read, exact-digest acknowledgment and readiness, read the permitted governing references and verify the selected checkout before implementation. Stop affected work and request refresh when required sources become stale.
3. Implement only your approved step within its owned paths. Preserve other work and use the assigned checks. Leave master roadmap ownership and issue delivery transitions with the master unless explicitly assigned.
4. Return the [handoff](#6-verify-handoffs-and-preserve-evidence) and await the master’s verification or correction request. Do not restart issue planning or infer a new approval gate for this already approved assignment.
