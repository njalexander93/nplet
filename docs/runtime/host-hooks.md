# Local host hooks

The lifecycle, task boundary and ticket-first startup contract are runtime-neutral.
Authorized task-free work requires no Task assignment or local registration.
Protected implementation retains verified startup, readiness and explicit roadmap
approval. See the [contributor procedure](contributor-workflow.md). This checkout
implements three native adapters over that shared core:

| Host | Project configuration | Python entry | Participant host |
| --- | --- | --- | --- |
| Codex | `.codex/hooks.json` | `nplet.adapters.codex` | `codex` |
| Claude Code | `.claude/settings.json` | `nplet.adapters.claude` | `claude-code` |
| Cursor Agent | `.cursor/hooks.json` | `nplet.adapters.cursor` | `cursor` |

The integration targets local macOS, Linux and native Windows checkouts with an
editable Poetry installation. Native Windows requires Git for Windows and local
NTFS storage. Generic Claude chat, Cowork, cloud/remote agents and Cursor Tab are
not covered. Platform tests and installed-host acceptance are separate requirements. Repository hooks are cooperative guardrails, not a sandbox or execution authority.
A successful local protocol test does not prove that a host loaded or trusted its configuration.

## Native protocols and failure semantics

Ticket-read protocol sources checked on 2026-10-09; launcher and shell references checked on 2026-10-07:

- [Claude Code hook reference](https://code.claude.com/docs/en/hooks): stdin JSON carries
  `session_id`, `cwd`, and `hook_event_name`; tool events add `tool_use_id`, `tool_name`, and
  `tool_input`. `PostToolUse` reports successful execution with `tool_response`;
  `PostToolUseFailure` carries `error` and interruption information. Pre-tool denial uses
  `hookSpecificOutput.permissionDecision`; an empty decision preserves normal permission
  checks. `UserPromptSubmit` can return `decision: "block"`. Exit 2 blocks supported blocking
  events. Other errors, malformed responses, or host timeouts can be non-blocking.
  See [input](https://code.claude.com/docs/en/hooks#common-input-fields),
  [pre-tool decisions](https://code.claude.com/docs/en/hooks#pretooluse-decision-control), and
  [exit semantics](https://code.claude.com/docs/en/hooks#exit-code-output).
- [Cursor hook reference](https://prod.cursor.com/docs/hooks): `conversation_id` is stable
  across turns; `generation_id` is not. Generic `preToolUse`/`postToolUse` carry the actual
  `tool_use_id`. `postToolUse.tool_output` is a JSON string; the documented Shell example
  contains `exitCode`. Admission uses `permission`; prompt submission uses `continue`.
  Session start and compaction cannot enforce blocking. `failClosed: true` blocks hook
  failures; exit 2 also blocks. Other failure exits otherwise fail open. Project hooks run
  from the project root. See [configuration](https://prod.cursor.com/docs/hooks#configuration),
  [events](https://prod.cursor.com/docs/hooks#hook-events), and
  [failure options](https://prod.cursor.com/docs/hooks#per-script-configuration-options).

The implementation's choices below are intentionally narrower than those host protocols.

### Observed Codex completion contract

On 2026-10-14, native callbacks in Codex Desktop 0.160.1 on macOS showed
`Bash` and `apply_patch` final responses as text, and local `webrun` responses
as content lists. The adapter settles these exact callback shapes using the
native `tool_use_id`. Output text is not parsed as transport metadata and cannot
invent an asynchronous process handle. This records completion, not command
success. Unrecognized response shapes retain pending work.

For a delayed shell command, the final poll delivered `PostToolUse` under the
original Bash call ID. Successful, nonzero-exit and delayed shell calls were
observed settling after the repair. Patch and local web calls were also observed
settling. See the official [PostToolUse contract](https://learn.chatgpt.com/docs/hooks#posttooluse).
Legacy `exec_command` and `write_stdin` transport envelopes retain their separate
typed-status and handle-correlation checks.

Native MCP content results can omit `isError`. Their correlated final content
object also settles the parent call without interpreting content as a process
handle or claiming provider success. This shape was observed for the documentation
connector, including a live completion after the repair.

Completion evidence does not establish fresh ticket-first startup, another host
version, or another operating system. Child evidence and its limits follow below.

### Codex native child route (restricted)

On 2026-10-14, Codex Desktop 0.160.1 on macOS delivered a host-issued UUID in
`agent_id` on child start, first and later tool callbacks, and stop. Follow-up turns
retained that UUID. The adapter normalizes it as
`<parent session_id>/agent/<agent_id>`; each child gets a separate participant,
packet, acknowledgment and ownership. The version-pinned
[hook runtime](https://github.com/openai/codex/blob/rust-v0.160.1/codex-rs/core/src/hook_runtime.rs)
uses the child thread ID for the shared hook context. Tool or message text never
supplies this identity.

The supported launch comes from a ready coordinator. The two native contracts
are separate:

- Desktop 0.162.0-alpha.17.2 exposed `spawn_agent` with an explicit
  `fork_context: false` and a plain-text `message`. Its response contains a
  host-issued UUID `agent_id`, used as the control target. Unknown fields,
  inherited history and structured-input launches are outside this route.
- The earlier `collaborationspawn_agent` route requires `fork_turns: "none"`
  and `task_name`. Its response supplies a canonical `/root/...` control target.

Native launch receipts admit a bounded cohort of roadmap-only readers. The host
does not expose a direct spawn-call key on child start, so launch order, task name
and arrival time never select write ownership. Only an explicit coordinator
`scope` grants a particular observed participant its owned `context/` paths.
Initial readers have no write permission.

Startup returns exact lifecycle and source-read commands. A child reads its packet,
reads each available source with the exact `cat -- <absolute path>` command,
acknowledges its digest, and verifies ready. Managed source paths use the registered
canonical store, including from linked checkouts. Acknowledgment requires the
child's own correlated final source bytes to match its current packet. Truncated,
foreign and obsolete deliveries do not count. Children can use only their own
`read`, `acknowledge`, `ready`, `diagnose` and ready, owned `update` transactions.
Generic shell/file writes, provider tools and nested delegation remain denied.
This route supports managed output notes, not arbitrary repository implementation
commands. Normal host permissions remain in force.

The adapter records admitted source reads in the shared lifecycle pending map
through `source-read-start`, as well as its delivery receipts. A missing completion
therefore blocks participant detachment. Historical receipts created before this
tracking existed are not silently migrated or cleared; retain their uncertainty
and reconcile only against exact terminal evidence.

Parent controls require targets returned by that parent's observed launches.
The first terminal spawn completion freezes the observed target or its absence.
A replay cannot add, replace or remove that target. An exact retry can still
settle the parent call if an earlier attempt saved the receipt but failed before
core settlement; it does not rewrite the receipt.
The current native route uses UUID targets for `send_input`, `wait_agent` and
`close_agent`; unsupported controls such as resume remain denied. The older
follow-up, message and interruption route uses the exact canonical `/root/...`
target. A control address does not itself grant lifecycle ownership. A control's
completion settles only its parent call. Child stop/cancellation is an advisory
record; it does not fabricate a source completion, detach the child, or settle
sibling work. Final answers provide the handback; child message tools are not
admitted.

Join retries reuse the shared bounded revision/lock retry budget. A failed start
leaves tool admission closed. If the host replays that same start, the reserved
UUID can retry without consuming another slot; otherwise use a fresh child.
Follow-up does not emit another start. Revision conflicts on an owned update
require a fresh read and bounded retry with a new request ID. Source receipts
retain up to 256 unresolved reads; settled evidence is compacted by packet and
source. A real late completion frees unresolved capacity. Unknown work is never
cleared just to free a slot. Admission permits at most 32 launch receipts per
parent binding generation, including failed attempts without affirmative native
evidence that no child started. Exhaustion reports `HOST_CHILD_CAPACITY_EXHAUSTED`;
do not reset the registry or keep retrying in that binding. A fresh parent joins
under its own identity and needs an explicit coordinator handoff after pending
work is resolved. Uncertain old activity stays retained. This bounds lifetime
attempts, not the host's simultaneous-child ceiling.

Development controls observed three sibling readers and disjoint owned updates
under one parent, with revision-conflict recovery and intentional identity and
ownership denials. Current Desktop controls also exercised UUID-based follow-up,
wait and close callbacks. Keep these development observations separate from the
final-candidate three-burst acceptance. The delivery record must identify the
exact tested runtime source, fresh-session startup, overlapping child intervals,
owned outputs, failed attempts and remaining pending work. Installed Codex
acceptance on other operating systems is not established by these macOS trials.

## Setup and bootstrap

For issue sequencing, approval and assigned step threads, use the
[canonical contributor procedure](contributor-workflow.md). Hook setup supplies
local lifecycle integration, not proposal approval.

1. Install this checkout's editable package and locked tools with Poetry. Hooks
   use a fixed invocation-local Git alias to run `adapters/launch.sh` from the
   repository root, including when the host starts in a nested directory.
   The launcher selects `.venv/bin/python` on POSIX or
   `.venv/Scripts/python.exe` under Git for Windows, then the selected host adapter.
   Missing setup exits 2. There is no global interpreter fallback or persistent
   Git alias. See [Git shell-alias behavior](https://git-scm.com/docs/git-config#Documentation/git-config.txt-alias).
2. Review and activate project hooks using the host's own controls. This change does not alter
   global settings or grant trust. Keep existing permission prompts and restrictions enabled.
   Record loading/activation evidence separately from observed callbacks. An enabled
   definition alone is insufficient evidence of trusted execution. If trust or callback
   delivery is unverified, retain that limit; a successful manual lifecycle call does
   not demonstrate a native callback or identify a runtime defect.
3. Submit exactly one `Task: ISSUE-ID` line. The selected adapter records the
   identifier without requiring local registration. Read that exact Linear ticket
   through the runtime's supported provider integration. The adapter admits this
   narrow read before readiness and verifies its native completion. See
   [ticket-read protocols](#ticket-read-protocols). A new Task line gates protected
   tools until its exact ticket read completes, even if the session was ready.
   Independent named task-free work remains available. The startup preparation
   route also permits loading the issue-read tool's
   schema and reading the governing procedure. The rule is runtime-neutral; this
   checkout implements schema loading in the Claude Code adapter as `ToolSearch` (a
   Claude Code mechanism) loading only configured `get_issue` schemas, and `Read`
   or a plain `cat` of exactly `<checkout>/docs/runtime/contributor-workflow.md`
   or `<checkout>/AGENTS.md`. These calls get no lifecycle decision and write
   nothing, so the selected ticket read remains the first issue-provider
   operation in startup. Within the reserved preparation route, Codex admits
   only exact native command-only `Bash` reads of those two
   absolute paths, spelled `cat <path>` or `cat -- <path>` with canonical quoting.
   It does not admit an opaque schema-loading wrapper. Cursor has no preparation
   phase. Other protected tools stay denied until the ticket read completes. A session already bound
   to another issue may submit a Task line too: the adapter records the new issue as
   a pending assignment and the binding stays on the old issue until the verified
   read moves it (step 5). If that read or the move fails, the old binding remains;
   a Task line for the bound issue returns to it and drops the unfinished switch's
   lookup correlation.
4. Recover with `nplet.adapters.common.bootstrap_command(request, host)`
   from the intended worktree's Poetry environment. On POSIX and Claude's Bash
   tool it emits the exact `shlex.join` lifecycle invocation. Native Windows
   Codex/Cursor use a quoted PowerShell call to `adapters/bootstrap.py` with one
   canonical encoded JSON argument. This avoids native argument differences
   between Windows PowerShell 5.1 and PowerShell 7. The decoded request uses the
   same lifecycle implementation and checks. Include the adapter's host value and
   actual session identity. Claude uses `session_id`; Cursor uses
   `conversation_id` as lifecycle `session_id`. An unready session may run
   `diagnose` in its pre-registration shape, or in its issue-level shape with
   `repo_id`, `issue_id` and optional `binding_generation` for the issue in its
   recorded assignment. Both shapes are read-only and accept no other fields.
   For a recorded participant with a packet, the issue-level result also returns
   `packet`, its committed reference list exactly as stored, and `sources`, the
   aligned list of each reference's `recorded_sha256`, `current_sha256` (`null`
   when unreadable) and `available`. These values support bounded lifecycle
   recovery without requiring a separate hashing command in the stranded session.
   The issue coordinator may also run one `scope` before readiness: a self-refresh
   whose `target_participant` is its own key and whose `packet` repeats its current
   packet (the issue-level `diagnose` `packet`) with the same references in the same order, changing only each `sha256`
   to a digest, with no `owned_paths` and no other fields. It recovers a coordinator
   whose own governing source changed (`SOURCE_STALE`): copy `packet` and replace
   each `sha256` with the aligned `sources[*].current_sha256`. The core then clears the
   acknowledgment, so readiness still needs `read`, `acknowledge` with the returned
   `packet_digest`, then `ready`. A scope that repeats unchanged digests is
   accepted and bumps the revision but does not restore readiness; the failed
   `read` lists the changed references in `stale`. The rule is shared by every native host.
   An explicit `rebind` (`new_issue_id`, optional `new_binding_generation`,
   `evidence`, and `issue_uuid` naming the target) is admitted when its
   `new_issue_id` is the recorded Task issue, or when it leaves the recorded issue
   for a target that already has committed state. When the session has a live
   binding, the rebind's `issue_id` must name that bound issue. A subagent never runs it.
5. A successful matching issue-read completion invokes the shared startup orchestrator. It derives the
   main worktree from Git, registers or resumes, creates a new issue with the observed
   master as coordinator, installs an initial reader packet, reads its source bytes,
   acknowledges the exact digest and verifies `ready`. Existing roadmap bytes,
   coordinator and packet survive repeated starts. The core `update` that commits a
   coordinator's `roadmap.md` moves only that coordinator's own `roadmap.md` packet
   reference to the committed digest and clears its acknowledgment. The coordinator
   then runs the admitted `read` and `acknowledge` bootstrap commands; the next
   protected tool verifies readiness. Reader packets are unchanged. Out-of-band edits
   remain conflicts.
   When the session's binding names another issue, the orchestrator moves it with
   core `rebind` instead of `create`, `resume` or `join`, citing the verified ticket
   identity as evidence. An absent issue is created with the session as coordinator.
   An existing issue still needs an assignment for the session or its own earlier
   participation, otherwise `SCOPE_MISSING`. Pending work on the old participant, or
   participants still attached to an old issue it coordinates, refuse the move with
   `PENDING_OPERATION`. The old issue keeps its payload and coordinator; only this
   participant is detached.
   Governing files come from the selected checkout; task bytes come from the main
   worktree's canonical issue directory. A new session without an assignment
   joins as a roadmap-only reader; it cannot replace another coordinator or edit
   coordinator-owned notes. Coordinator handoff still requires the recorded owner
   and explicit transfer. Assigned steps attach with their verified actual
   host/session identity and own scope. Follow [explicit lifecycle setup](task-workspace-usage.md#explicit-setup-and-bootstrap)
   for recovery. The recovery operation/field allowlist and assignment checks live in
   `adapters/common.py`. Wrappers, chaining, redirection, alternate interpreters,
   conflicting worktrees, and cross-host/session bootstrap requests receive no exemption.

### Repair and acceptance isolation

When repairing the guard itself, use a human-authorized development checkout and
a separate disposable acceptance checkout. If the human disables development
hooks, record that fact: development operations and manual lifecycle recovery
are not installed-hook acceptance. Keep acceptance hooks enabled and trusted.

Freeze the candidate before each acceptance run. Record file digests, copy only
the intended source changes, and preserve the acceptance project's hook settings
and independent lifecycle store. Do not copy development `.task` state into it.
Start with a fresh unbound session and verify task-free reads, edits outside the
project code repositories and authorized administration without task state.
Verify protected denials before native ticket-first startup. Run child
concurrency only after parent readiness and completion tracking pass.

A new or forked chat has its own observed session identity. Inherited messages
do not transfer the coordinator role. Join under the new identity and use an
explicit supported handoff from the recorded coordinator, retaining unresolved
pending work. A failed acceptance chat should return its exact denial and native
call identity to the development chat. Repeat only after a specific source,
configuration or input change; do not turn inaccessible-file retries into a
human relay loop.

On 2026-10-15, local Codex `0.162.0-alpha.17.2` emitted `SessionStart` and
tool callbacks for a chat created through the desktop task tool, but no captured
`UserPromptSubmit` for either its initial prompt or a tool-sent follow-up. Both
ticket calls were denied without registration. A human-submitted `Task:` message
in that same chat emitted `UserPromptSubmit`; the correlated native ticket read
then registered the independent checkout and reached readiness. Shell and second
provider-call completions settled. This identifies an observed delivery-route
limit, not permission to synthesize the missing prompt event or bypass startup.
For that route, use a directly submitted Task message before acceptance; recheck
the callback evidence on other versions.

### Private Codex diagnostic capture

The optional `NPLET_CODEX_CAPTURE_DIR` environment variable enables metadata-only
capture in the Codex supervisor. It does not grant trust, readiness, child identity,
or permission. Other adapters and the default unset path remain unchanged.

1. Use the reviewed candidate checkout and its own Poetry environment. Create an
   existing absolute capture directory with mode `0700` on POSIX.
2. Add `NPLET_CODEX_CAPTURE_DIR=/absolute/private/capture` to the reviewed Codex
   hook command environment while retaining the original adapter launcher. Review
   and trust the exact changed definitions through the host's supported controls,
   then restart affected sessions. Do not infer hot reload or activation from a
   source edit, project trust, manual invocation, or another checkout.
3. Verify originating `cwd` and native callback delivery in the records. Capture
   parent spawn, child start/stop, first and later tool callbacks, and follow-up;
   use only admitted operations. An actual denial remains a denial.

Capture runs after the lifecycle worker returns and is reaped. A separate writer
has a 0.25-second communication budget after process creation, an 8 KiB per-record
cap, and 1024 exclusive slots (at most 8 MiB). Process creation and kill/reap are
outside that communication timeout; it is not an end-to-end latency guarantee. Files use mode `0600`. A full, unavailable, unsafe, or failed
diagnostic destination does not replace the adapter's response. Keep this private
opt-in scoped to the diagnostic hook command; do not add a global trust bypass.

Records retain only fixed top-level native metadata, timestamps, known adapter
decision values, the adapter exit status, and response shape summaries. Summaries
use fixed field names and types; only integer exit codes and boolean status flags
retain values. Task/provider contents, nested tool
inputs and responses, arbitrary keys, and unknown fields are omitted. Absent
`agent_id` stays absent; a parent `session_id` is not child identity. The filtered
record cannot prove omitted native fields are unavailable. Investigate a specific
correlation gap against the native schema and narrowly extend capture only when
evidence supports it. Synthetic protocol tests establish no live host acceptance.

### After pulling the rename

A checkout installed before the 2026-10 rename of the package to `nplet` needs
these steps once:

1. Pull `main`.
2. Delete `.venv/` and run `poetry sync`, so the environment holds only the `nplet` distribution.
3. In the ignored `.claude/settings.local.json`, rename the
   [connector mapping](#claude-desktop-connector-mapping) key to
   `NPLET_CLAUDE_LINEAR_CONNECTOR_ID`, keeping the same UUID value.
4. Restart every Codex, Claude Code and Cursor session on the checkout. Hosts
   capture hook commands at session start, so open sessions still call the old launcher path.
5. Leave `.task/` data as it is. It stores checkout paths and lifecycle state, not package names.

### Ticket-read protocols

Every supported adapter follows the same sequence: record the requested ticket,
validate its provider read and completion, then automate local registration,
attachment, source reading, acknowledgment and readiness. Native event names and
provider envelopes are adapter details.

| Runtime | Ticket-read routing | Completion and identity |
| --- | --- | --- |
| Codex | Exact native `mcp__codex_apps__linear__get_issue` or legacy `mcp__codex_apps__linear_get_issue` with only `{id: ISSUE-ID}`. | `PreToolUse` and `PostToolUse` correlate the native `tool_use_id`; read `tool_response`. |
| Claude Code | Configured `linear` or `linear-server` MCP `get_issue`, or an explicitly mapped Desktop Linear connector, with `{id: ISSUE-ID}`. | `PreToolUse` and `PostToolUse` correlate the native `tool_use_id`; read `tool_response`. MCP metadata requires Claude Code 2.1.274 or later; named-server definition scopes are `user`, `project`, `plugin` and `sdk`. Desktop mappings require `claudeai`, `dynamic` or `sdk`. |
| Cursor Agent | Generic `preToolUse` records `MCP:get_issue`, the exact arguments and native call ID; `beforeMCPExecution` checks the configured `linear` or `linear-server` name and official HTTPS MCP URL. | Both pre-hook validations are required. Generic `postToolUse` settles the matching native call using JSON `tool_output`. `afterMCPExecution` does not establish startup readiness. |

The mappings follow the [Claude hook reference](https://code.claude.com/docs/en/hooks#pretooluse-input)
and [Cursor hook reference](https://cursor.com/docs/hooks). Configured server names
select an integration; a name alone does not authenticate a provider. Keep host
permissions and trusted configuration in effect. Cursor's two pre-hooks can arrive
in either order. Completion must identify the admitted native call, so an old
MCP-specific completion cannot settle a fresh retry. The Cursor adapter accepts
`https://mcp.linear.app/mcp` and its `/readonly` endpoint; missing or different
server metadata is an unsupported provider configuration, not a missing ticket.
After a local startup failure, Cursor retains the Task assignment and allows a
fresh lookup with either pre-hook order.

#### Claude Desktop connector mapping

Local Claude Code sessions in Desktop can expose a connected Linear server as
`mcp__<connector-uuid>__get_issue`. Verify the connector is Linear in Desktop's
connector settings before mapping its ID. A UUID-shaped name alone does not
identify Linear. Merge this environment setting into the ignored
`.claude/settings.local.json`, preserving any existing settings:

```json
{
  "env": {
    "NPLET_CLAUDE_LINEAR_CONNECTOR_ID": "<verified-linear-connector-uuid>"
  }
}
```

Use the exact lowercase UUID from that connector's settings URL. Reload the
Claude Code session so the hook process receives the environment setting.
This is local routing configuration, not an OAuth credential or a grant of
Claude permissions. Never populate it from a tool argument or accept arbitrary
UUIDs as Linear. Missing or malformed mappings leave opaque connectors blocked.

The tool name and native `mcp_server.name` must match that configured ID, and
`mcp_server.source` must be `claudeai`, `dynamic` or `sdk`. Desktop 2.1.293
was observed reporting `sdk` for its Linear connector; the other two are
documented remote configuration sources in [Anthropic's provenance contract](https://code.claude.com/docs/en/agent-sdk/typescript#mcpserverprovenance).
Missing or other provenance remains unsupported. The exact-ticket argument,
native call correlation, provider response validation and startup readiness
checks still apply. Before that read, `ToolSearch` has only a narrow
preparation exception: a `select:` query naming nothing but configured
`get_issue` tools (`mcp__linear__get_issue`, `mcp__linear-server__get_issue` or
the mapped connector's), or a keyword query of at most 128 letters, digits,
spaces, `_`, `+` or `-` that contains `get_issue`. Either form allows only
`query` and an integer `max_results` from 1 to 20, and the whole query is at
most 256 characters. A `Read` or `Bash` call
qualifies only when it names exactly `<checkout>/docs/runtime/contributor-workflow.md`
or `<checkout>/AGENTS.md`; the comparison is lexical, no path entry may be a
symbolic link or junction, and `Bash` must split to exactly `cat` plus that
literal path. This preparation route preserves exact ticket-first startup;
independent named task-free work remains available. This does not
add support for cloud/Cowork workspaces.

For a successful Claude `PostToolUse`, Desktop can supply `tool_response` as a
content-block list. The adapter restores its success envelope before invoking
the shared parser; it still requires exactly one text block containing the
requested issue and a canonical immutable UUID. Failed native calls retain the
separate `PostToolUseFailure` path. On 2026-10-09, the local Desktop 2.1.293
ticket-first session exercised both callbacks with `sdk` provenance and this list
shape, ending in `TASK_WORKSPACE_READY`. This establishes that observed local
path, not cloud/Cowork support or another platform's installed-host coverage.

The provider parser accepts two explicit input contracts: the observed Codex
connector `CallToolResult` containing one JSON-text issue with shorthand `id` and
immutable `uuid`, or a normalized issue object with shorthand `identifier` and
UUID `id`. Both must match the requested ticket and contain a valid UUID. The
normalized object is a tested adapter contract; it is not evidence that an
installed native Linear MCP server returned that shape. Unexpected provider
shapes produce a specific invalid-response diagnostic without creating a workspace.

Typed missing-ticket errors and the observed Codex missing-reference envelope
stop without workspace creation. The latter identifies `INVALID_ARGUMENT` with
one exact structured error: `invalid_request`, status 400, the provider's
missing-reference message and a request ID. Generic invalid arguments remain
provider errors. Authentication, permission, network and invalid-response failures
retain their distinct diagnostics. Provider text is data; it grants no execution
authority. The orchestrator reads and hashes actual assigned source bytes before
acknowledgment.

[Codex documents native hook decisions for calls nested in JavaScript code
mode](https://learn.chatgpt.com/docs/hooks#tool-calls-from-code-mode). A visible
execution wrapper does not by itself identify the native callback shape. If a
runtime exposes only an opaque wrapper, that wrapper gets no pre-readiness tool
exemption.

Tests exercise these protocols with synthetic events. Installed configuration,
callback delivery and real provider round trips require separate evidence for
**each** runtime. Another runtime can implement the same shared startup contract
through its own verified adapter; it does not inherit support from these three.

### Codex bootstrap tool input

The Codex adapter accepts only `Bash` or `exec_command` for bootstrap. Supply the canonical
command from step 4 as `command` (Bash) or `cmd` (exec_command). Installed native
`Bash` callbacks may contain only `{"command": "<canonical command>"}`; that exact
shape is supported. For the legacy transport form, explicitly set `login: false`
and `shell: "/bin/sh"` on POSIX. Native Windows accepts `shell: "powershell.exe"`
or `shell: "pwsh.exe"` with the helper's encoded command. Run without a TTY and omit the `tty` field entirely; even `tty: false`
is outside the accepted field allowlist.

For example, the `exec_command` tool input must have this shape, replacing the placeholder
with the exact command from step 4 (use the corresponding Windows shell there):

```json
{
  "cmd": "<canonical lifecycle command from step 4>",
  "login": false,
  "shell": "/bin/sh"
}
```

The only accepted input fields are `command`, `cmd`, `login`, `shell`, `workdir`,
`yield_time_ms`, `max_output_tokens`, `sandbox_permissions`, `justification`, and `prefix_rule`.
Use one command field. These requirements come from the
[Codex bootstrap parser](../../src/nplet/adapters/codex.py); they do not change host
permission requirements. Claude and Cursor use their own native tool-input shapes.

### Bounded hook processes

Git must be available to the host. If Git cannot start or find the repository,
the launcher does not run and Git's failure status is preserved. The host's own
error policy then applies; do not assume exit 2. Once the launcher starts, missing
environment or Python process failure maps to exit 2. Cursor's configured
`failClosed` gates still apply. See the native failure semantics above.

When manually wrapping a hook in PowerShell `-Command`, append
`exit $LASTEXITCODE` to preserve its native exit status. A bare wrapper converts
native exit 2 to outer exit 1; those are different host outcomes. The platform
tests check both cases separately. See Microsoft's
[PowerShell process exit rules](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_powershell_exe#-command).
This test wrapper does not change the project hook commands or prove an installed
host's shell behavior. Claude's documented default uses Git Bash when installed;
this checkout requires Git for Windows. See Claude's
[command hook fields](https://code.claude.com/docs/en/hooks#command-hook-fields).

A supervisor reads bounded input and executes the selected adapter in a child
process. At the deadline it kills and waits for that worker before returning a
failure response. A timed-out worker cannot continue lifecycle writes after the
supervisor reports the timeout. The shared lifecycle transaction handles interrupted
writes on the next operation. This preserves each host's response and exit conventions;
it does not change the host's own timeout or fail-open behavior.

### Claude foreground Bash

Claude's project settings set `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS=1`. The
[official environment reference](https://code.claude.com/docs/en/env-vars#variables) documents
that this disables automatic backgrounding, the background tool parameter, and Ctrl+B.
The adapter requires that inherited value before ordinary Bash admission. It does not set it
inside the hook to fabricate proof of the parent runtime's configuration.

## Implemented coverage

These rules are implemented in `adapters/task_boundary.py`, `adapters/common.py`, `adapters/claude.py`,
`adapters/cursor.py`, `adapters/codex.py` and `adapters/codex_children.py`.
The first table compares Claude Code and Cursor; the second records Codex boundaries:

| Boundary | Claude Code | Cursor Agent |
| --- | --- | --- |
| Identity | Exact native session ID. A hook carrying `agent_id` with a valid `session_id` is a subagent keyed `<session_id>/agent/<agent_id>`; `subagent_id`, `parent_conversation_id`, background markers and `agent_id` without `session_id` are rejected | Exact conversation ID; require any session ID to match; one workspace root |
| Ordinary tools | Read, Write, Edit, Glob, Grep, NotebookEdit; foreground Bash under the setting above. `ToolSearch`, `Skill`, `SendMessage` and `AskUserQuestion` are task-free parent no-effect operations with no pending entry; a tool they surface is evaluated on its own call. Their availability does not authorize sending messages. A subagent's `SubagentHandback` is a readiness-checked no-effect operation admitted only under a child identity. Any other valid parent tool that no route names is an [unchecked call](#unchecked-parent-calls), except the kept-closed set | Read, Write, Edit, Grep, Delete; Shell with supported foreground arguments; finite task-free parent reads also accept validated native metadata as described below. A parent MCP call outside the Linear inventory is an [unchecked call](#unchecked-parent-calls) |
| Linear parent provider | The named inspection and administration operations below are task-free through the configured connector, with the same server/provenance checks as startup. Children retain only the original five read operations and require readiness | The same named parent operations are task-free. Generic `preToolUse` validates native identity and structured operation; `beforeMCPExecution` independently checks the exact operation, serialized object arguments, configured server name and URL. Both blocking hooks must pass; ordinary calls create no cross-callback ledger. Startup alone retains its paired lookup reservation |
| Desktop PR monitor after readiness | The desktop app's built-in `ccd_pr` server only: `get_status` and `set_monitor`, for a parent session (a subagent child gets neither). The tool name must be exactly `mcp__ccd_pr__get_status` or `mcp__ccd_pr__set_monitor`; the hook's `mcp_server` object must be present with `name` `ccd_pr` and `source` `sdk`, the provenance the desktop app reports for its built-in server; a call without that object, or with any other name or source, is denied. `set_auto_merge`, `bind_pr`, `unbind_pr` and every other `ccd_pr` operation stay denied. Each call is pending work under its native `tool_use_id`, settled by `PostToolUse` or `PostToolUseFailure`. Live capture (2026-10-11, macOS Desktop, hooks enabled): a ready parent session called `get_status` and then `set_monitor` (`auto_fix` on the open PR); both were admitted, recorded as pending work and settled by their completion events, and the app returned the PR state and confirmed the monitor. The server identity was captured separately the same day with a throwaway project whose `PreToolUse` hook appended the raw event: `tool_name` `mcp__ccd_pr__get_status`, `mcp_server` `{"name": "ccd_pr", "source": "sdk"}`, `agent_id` null, alongside `session_id`, `tool_use_id`, `cwd`, `permission_mode` and `transcript_path`. The check is pinned to that name and source | No native provider admission |
| Ticket-first startup | Exact configured Linear issue read before readiness; matching native completion runs shared startup | Exact Linear issue read with generic call-ID and MCP server checks before shared startup |
| Admission | Validate native identity, provider provenance and child restrictions first. Named task-free parents and unchecked parent calls need no readiness; other supported effects require readiness. Preserve native permissions | Validate the same boundary in Cursor's native shape; allow only that particular task-free, bootstrap, unchecked or ready supported call |
| File-tool completion | A supported success/failure event with required payload settles only existing admitted work. A task-free or unchecked completion creates no state | Same; native failure type is checked for existing work |
| Shell completion | Successful Bash result with string stdout/stderr, `interrupted: false`, and no async markers | Every admitted parent Shell call remains pending until independently verified termination and explicit lifecycle recovery; native callbacks cannot settle it |
| Ambiguous shell result/failure | Retain pending operation and report recovery | Same; booleans are not exit codes |
| Child/provider/background tools | Deny a child's unsupported tools and providers, background identities and the parent's [kept-closed set](#unchecked-parent-calls). A ready parent's foreground `Agent` call (no `run_in_background: true`, no `isolation`) is pending work under its `tool_use_id`; `Task`, `TaskOutput`, `TaskStop`, `SpawnAgent`, `Workflow` and `Agent` from a child are denied. `SubagentStart` joins the child (restricted, below); `SubagentStop` is an observation | Only the experimental [scoped foreground Task route](#cursor-scoped-foreground-tasks) below; unsupported child tools, child MCP calls and remote sessions denied |
| Session start | Advisory recovery/readiness context; no readiness grant | Advisory context only |
| Compaction | PreCompact/PostCompact observations only; no context or decision output | Advisory context only |
| Stop/session end | Optional observation only | Optional observation only; no automatic follow-up |

| Codex boundary | Implemented admission and limit |
| --- | --- |
| Parent providers | Named Linear and GitHub review operations below are task-free. Other generic `mcp__` tools, including non-Linear operations, pass the readiness gate as ordinary pending work when earlier special-case handling reaches this route. There is no connector or operation allowlist. Existing child/delegation tool-name denials still apply before this route. A matching native MCP completion with the validated content-block shape settles its pending call; other responses require typed completion evidence. |
| Archive provider gate | Exact coordinator-bound document save/read requests have a separate archive gate. This gate is not exclusive: a false return permits fallthrough to generic readiness admission. Earlier archive-specific errors can prevent fallthrough; a legacy document save without an existing export fails before generic readiness. Passing generic admission does not verify an archive or grant cleanup authority. |
| Ticket-first startup | The exact selected-ticket read has reserved correlation and startup precedence. Independent named task-free work remains available. Matching native completion validates the issue before shared startup. |
| Child tools and providers | A verified child has its own binding and scope. Only command-only Bash for its own canonical lifecycle commands and exact packet-source reads is admitted; generic provider tools are denied. |
| Native child control | Exact supported spawn/control tools use parent-bound receipts and correlated completion. Unsupported delegation names remain denied. See the [restricted Codex child route](#codex-native-child-route-restricted) for supported shapes and evidence limits. |

Codex admits generic provider calls as readiness-gated pending work, while Claude Code
and Cursor admit unlisted parent calls as [unchecked](#unchecked-parent-calls) work with
no lifecycle record. Neither route enforces the contributor procedure's permitted provider operations.
Provider success is not lifecycle authority, and this table does not establish
installed-host delivery for every provider or operating system.

### Task-free parent operations

The shared [task boundary](task-workspace.md#task-free-and-protected-work) applies
after native identity, provider provenance and child restrictions. These are
finite implemented contracts, not prefix-based permission grants.

| Parent effect | Implemented operation contract |
| --- | --- |
| Linear inspection | `get_issue`, `list_issues`, `search`, `get_project`, `list_projects`, `get_user`, `list_users`, `get_team`, `list_teams`, `get_issue_status`, `list_issue_statuses`, `list_issue_labels`, `list_project_labels`, `get_milestone`, `list_milestones`, `list_cycles`, `list_comments` |
| Linear administration | `save_issue`, `save_project`, `save_comment`, `save_issue_label`, `save_project_label`, `save_milestone`, including actual create and update. Human authorization and read-back still apply. No local issue startup, assignment, status or approval is inferred |
| Codex GitHub review | `fetch_pr`, `get_pr_info`, `get_pr_diff`, `fetch_pr_patch`, `list_pr_changed_filenames`, `fetch_pr_file_patch`, `fetch_pr_comments`, `list_pull_request_reviews`, `list_pull_request_review_threads`. Native Codex connector names accept the verified single-underscore and double-underscore operation separators. Each requires `link_id`, a repository field and positive numeric `pr_number`. `get_pr_info` uses `repository_full_name`; the others use `repo_full_name`. File patch also requires `path`. Only `get_pr_diff` has an optional `format`, either `diff` or `patch` |
| Planning and research | Codex `webrun`, `update_plan`, `request_user_input`, `request_user_input_async`. Claude parent `ToolSearch`, `Skill`, `SendMessage`, `AskUserQuestion`, plus exact `mcp__ccd_turn__update_plan` and `mcp__ccd_turn__wrap_up` with server `{name: ccd_turn, source: sdk}`. Planning helpers grant no implementation approval |
| Local inspection | Supported native `Read`, `Glob`, `Grep`, and the finite shell reads below. Read access can include protected files |
| Edits without protected effects | Known `Write`, `Edit`, `apply_patch` or Claude `NotebookEdit` shapes with no [protected effect](task-workspace.md#task-free-and-protected-work): no workspace target, no text into another Git repository and no exemption policy target. Declared vault and non-repository targets qualify. Host permissions still apply |

Structured edits use the
[target repository evidence](task-workspace.md#target-repository-identity-and-location-evidence),
[vault exemptions](task-workspace.md#explicit-vault-exemptions) and
[content classification](task-workspace.md#edit-content-classification). A
protected edit also needs [recorded approval and scope](task-workspace.md#recorded-approval-and-repository-scope).
An unknown or malformed shape of an edit tool is denied with `INVALID_REQUEST`;
other tools keep the existing edit roles.

The five operations `delete_comment`, `delete_customer`, `delete_customer_need`,
`delete_diff_comment` and `delete_status_update` remain outside this task-free
inventory. Claude denies them and every other `delete_` operation on a Linear-named
server; Cursor denies them on the configured Linear server. Codex retains its
readiness fallback. In Claude and Cursor, other parent operations that no route
names are [unchecked calls](#unchecked-parent-calls); in Codex they retain their
existing gated or unsupported route.
Task-free access grants no merge, publication or review-submission authority.

#### Unchecked parent calls

Claude Code and Cursor admit a valid parent call that no existing route names as
unchecked external work. Its pre and post hooks allow it with no readiness check,
ticket-read check, pending entry or settlement, and record no state. An existing
pending ticket read, stale packet or pending work is unchanged. Native permission
prompts and provider authentication still apply. Unchecked is not a safety
certification and grants no ticket, lifecycle, approval, merge or publication authority.

In Claude Code, these stay closed or keep their own route:

- Subagent children keep their existing restrictions and get no unchecked calls.
- Delegation: `Agent` outside the ready parent's foreground route, `Task`,
  `TaskOutput`, `TaskStop`, `SpawnAgent` and `Workflow`; a parent's `SubagentHandback`.
- `Monitor` and `PowerShell`, which run shell commands outside the Bash policy.
- `MultiEdit`, which has no supported edit shape.
- Every `delete_` operation on a Linear-named server. Other operations on a
  Linear-named server outside the named inventory are unchecked and grant no
  Linear authority; inventory operations keep their server and provenance checks.
- Desktop app servers: `ccd_pr` beyond `get_status` and `set_monitor`, all of
  `ccd_session_mgmt` and `ccd_settings`. `ccd_turn` keeps its exact planning route.
- `EnterWorktree` and `ExitWorktree` are parent-only, readiness-gated pending work
  settled by their completion events.
- Structured edits keep the edit decision and `INVALID_REQUEST` for malformed
  shapes; Bash keeps the finite reads below and readiness for everything else.

In Cursor, a generic `MCP:<operation>` call whose operation is outside the Linear
inventory is unchecked through `preToolUse`, `beforeMCPExecution`,
`afterMCPExecution`, `postToolUse` and `postToolUseFailure`, after
`beforeMCPExecution` checks that its input serializes an object. A call that names
a Linear inventory operation, a Linear server name or a Linear URL must match the
configured server name, URL and inventory. Limitation: another server's operation
that shares a Linear operation name is therefore denied. Non-MCP Cursor tools
outside the supported set stay denied, and children get no MCP calls.

The routes below record observed coverage; this inventory is not an allowlist.
Cursor rows were observed on Cursor 3.27.22 (macOS 26.6.2); Claude Code rows in a
fresh unbound desktop session started in the worktree.

| Host | Route | Adapter tests | Installed host |
| --- | --- | --- | --- |
| Claude Code | Browser (`mcp__claude-in-chrome__*`), computer use (`mcp__computer-use__*`), PDF/document MCP servers, other desktop app servers such as `ccd_sidebar`, and built-in tools such as `WebFetch`: unchecked by exact native `tool_name` | Covered, including unbound and unready parents | Observed only for `WebFetch` (public font CSS) and `mcp__Claude_Browser__preview_start`: admitted with no task. Browser extension, computer use, PDF/document MCP and other app servers not observed |
| Claude Code | Native `Read` and `Write` of files outside the repository (not unchecked routes; the structured-edit guard applies) | Covered | Observed admitted with no task: `Read` of an external PDF and `Write` of an external SVG. PDF rendering failed because the host lacks `pdftoppm` |
| Claude Code | `EnterWorktree`, `ExitWorktree`: readiness-gated pending work | Covered | Not observed |
| Claude Code | Kept-closed set above: denied for a ready parent | Covered | Not observed |
| Cursor | App/MCP servers other than Linear through the generic and MCP-specific callbacks | Covered | Observed: all four callbacks admitted, no task created; a Linear delete, and GitHub's same-named `list_issues`, are denied at `beforeMCPExecution` |
| Cursor | Browser, computer use and PDF/document routes: unchecked only when delivered as MCP calls; a non-MCP route stays denied | Covered through the generic MCP route | Not observed separately |

#### Finite shell reads

Use one canonical command, spelled exactly as Python `shlex.join` produces it.
Composition, redirection, shell expansion, interpreter wrappers and unknown flags
receive no task-free admission. On Codex, use native `Bash` with only `command`.
Legacy `exec_command` remains readiness-gated because it can return an untracked
process handle. Its existing `write_stdin` correlation checks remain in force.
Claude requires foreground Bash and inherited background-disable configuration.
Cursor parent `Shell` accepts a string `command` plus native `cwd`,
`working_directory` and integer `timeout` metadata from 1 through 2147483647.
Supply at most one directory field. Empty `cwd` means no override. Cursor also
copies the override into the event's top-level `cwd`; the session's repository
still comes from its workspace root. An ordinary
command's directory may lie in another repository or in none. Canonical lifecycle,
bootstrap, reconciliation and child Shell commands still require the same repository
and report `REPOSITORY_MISMATCH`; a redirected lifecycle command is denied rather
than admitted as ordinary Shell. Other fields remain unsupported.

The canonical shell effect carries a top-level advisory `command_cwd`, reserved for
shell-effect recognition (no current adapter decision reads it): the event `cwd` for Claude Bash and for Cursor with no
override or empty `cwd`, or null when Cursor supplies any override. Null means the
directory is unknown; a recognized relative file effect must not become unchecked
solely for that reason. Installed Cursor 3.27.22 (macOS 26.6.2) admitted overrides
naming another repository, no repository and a relative path, and reported each one
unchanged in both callback `cwd` fields, yet `pwd` and Python `os.getcwd()` probes
reported the workspace root each time. A requested override therefore does not show
where a command runs, and the base for a relative override is unobserved.
`command_cwd` never replaces the event `cwd` or the session's recorded repository.
On installed Claude Desktop 4.4212.0, a ready session's `cd <dir> && <command>` ran
that command in `<dir>`, while both callbacks reported the session directory as
`cwd`. The host then reset the shell to the session directory, and the next
independent Bash call ran there.

Metadata validation grants no readiness or synchronous completion guarantee.
Only the finite reads below are task-free and create no pending lifecycle work.
Other commands require current readiness and are recorded as pending before
execution. Timeout can background execution. Every admitted parent Shell call
remains pending after native success or failure callbacks, including callbacks
with integer `exitCode` and no async markers. Settlement requires independent
verification of the original process termination followed by explicit lifecycle
`tool-complete` recovery. Pending work does not serialize all tool calls.
Task-free reads, exact lifecycle transactions and child restrictions retain
their existing separate routes.

Observed Cursor 3.25.9 on macOS reported `postToolUse` with `exitCode: 0`
and no async markers for `sleep 2` with `timeout: 1000`, before the process
terminated. It also reported a nonzero exit through `postToolUseFailure`
without a typed exit code. Neither callback supplies reliable termination
evidence. No timeout or duration heuristic grants settlement. Inspect the
original native terminal result and use verified explicit recovery as
described below.

| Command | Admitted arguments |
| --- | --- |
| `pwd` | No arguments |
| `cat` | One or more literal paths, optionally after `--`; no option operands or `/dev/stdin` |
| `rg` | Must include `--no-config`. Other flags are only `--files`, `--hidden`, `--line-number`, `-n`, `-l`, `--files-with-matches`, `-i`, `--ignore-case`, `--fixed-strings`, `-F`, `--`, `--no-ignore` |
| `git --no-pager -c core.fsmonitor=false status` | Only `--short`, `--porcelain`, `--branch`, `--untracked-files=no` and literal operands |
| `git --no-pager -c core.fsmonitor=false ls-files` | Only `--cached`, `--others`, `--exclude-standard`, `--stage` and literal operands |
| `git --no-pager -c core.fsmonitor=false diff` | Requires `--no-ext-diff --no-textconv`; optional `--stat`, `--name-only`, `--cached`, `--staged` and literal operands |
| `git --no-pager -c core.fsmonitor=false show` or `log` | Requires `--no-ext-diff --no-textconv`; optional `--stat`, `--name-only`, `--oneline` and literal operands |

The Git forms also admit `--` before operands. They disable pager, filesystem
monitor, external diff and text conversion behavior where applicable. General
shell commands and package install/remove/update commands remain protected.

#### Claude GitHub review fallback

Claude has no verified GitHub connector route. Its task-free review route is one
foreground `gh` command with an explicit positive PR number and `OWNER/REPO`.
The exact parser and two literal GraphQL queries are in
[`task_boundary.py`](../../src/nplet/adapters/task_boundary.py).

- `gh pr view NUMBER --repo OWNER/REPO` accepts no extra option, `--comments`,
  or `--json` with only `number,title,body,url,state,isDraft,baseRefName,baseRefOid,headRefName,headRefOid,author,files,comments,reviews,latestReviews,reviewDecision,statusCheckRollup`.
- `gh pr diff NUMBER --repo OWNER/REPO` accepts no extra option, `--name-only`
  or `--patch`, optionally followed by `--color never`. `--color never` alone is
  also admitted.
- `gh api --method GET` accepts only `repos/OWNER/REPO/pulls/NUMBER/files`,
  `comments` or `reviews`, and `repos/OWNER/REPO/issues/NUMBER/comments`,
  optionally followed by `--paginate`.
- `gh api graphql` accepts only the literal `THREAD_QUERY` or `COMMENTS_QUERY`
  from the parser. Use `-f query=...`, `-f owner=...`, `-f repo=...`, `-F number=...`,
  `-f id=...` and optional `-f cursor=...` as required by that query. Follow both
  thread and nested-comment pagination; an initial page does not prove a complete read.

Arbitrary queries, mutations, headers, bodies, `@file`, stdin, aliases, output
files, `--jq`, templates and browser routes receive no task-free admission.
Provider authentication and permissions still apply.

### Claude subagent child route (restricted)

The rule behind this route is runtime-neutral: a child joins only with a verified,
host-issued child identity and its own reader binding. `Agent`, `SubagentStart` and
`agent_id` are the Claude Code mechanism for it. Cursor has a separate experimental [scoped Task route](#cursor-scoped-foreground-tasks);
Codex uses its separate [native child contract](#codex-native-child-route-restricted).
The Claude route is
implemented and covered by synthetic protocol tests, plus one observed local Desktop
validation session (2026-10-09, macOS): `SubagentStart` fired before the
child's first tool call and delivered the fixed `additionalContext`; the child's
`PreToolUse`/`PostToolUse` carried the parent `session_id` with `agent_id`, so the child
was keyed and settled under its own participant while the parent stayed ready;
`SubagentStop` fired at the end of each child turn; the `Agent` `tool_input` carried
`description`, `prompt` and `model`. That session also showed the child's
`SubagentHandback` being denied as an unsupported tool, which is why it is now admitted
for child identities. One local session is not platform coverage: the route stays
**restricted**, and Linux, Windows and cloud/Cowork hosts remain unverified. On
`SubagentStart` the adapter requires the parent binding (host plus raw `session_id`)
to be ready and one admitted parent `Agent` call to be pending. It then runs core
`join` for the child session (a roadmap-only reader packet, `issue_uuid` checked)
followed by `read`, `acknowledge` and `ready`, and records the child's issue
assignment for its bootstrap gate. Its `additionalContext` contains fixed text with the
child's participant key and packet digest only. Otherwise it returns a non-blocking
`systemMessage`, because `SubagentStart` cannot block; the unbound child's first tool
is then denied. Child tool calls are admitted and settled under the child session.
A child gets only the Linear read operations (`get_issue`, `get_user`, `list_users`,
`list_issue_statuses`, `list_comments`); its `save_issue` and `save_comment` are denied
with `HOST_UNSUPPORTED_PROVIDER`, so provider writes stay with the parent.
Before readiness the child's lifecycle bootstrap admits only the recovery shapes
(`read`, `acknowledge`, `resume` and the like), never `scope` or `create`; the core
refuses a child `scope` (`NOT_OWNER`). The coordinator may `scope` the child's key with
owned `context/` paths. `SubagentStop` records an observation and never detaches. No
field links a `SubagentStart` to a specific `Agent` call, so the check is "some admitted
parent `Agent` call is pending", not an exact pairing.

A second round of that validation session (2026-10-10, macOS, hooks enabled)
showed the route failing under concurrency: a ready parent issued three `Agent` calls
within six seconds; all three were admitted as pending work, but only one child was
joined. The other two never received a binding, so every one of their tool calls,
including `SubagentHandback`, was denied with `BINDING_MISSING` and their reports were
lost. A single `Agent` call afterwards worked. The likely cause is concurrent hook
processes (sibling `SubagentStart` joins and the parent's own tool events) advancing the
issue revision between the adapter's state read and the child's `join`, or lock waits
exhausting the 2-second runner deadline. `SubagentStart` fires once and cannot block,
so a child that misses its join cannot recover: the parent must dispatch a fresh
subagent. The adapter therefore re-reads the committed revision immediately before each
`join` attempt and retries `REVISION_CONFLICT` and `BUSY` up to 8 times within a
1-second budget that leaves room inside the runner deadline. If the join still fails,
the advisory `systemMessage` names the last code and the child stays unbound. The core
checks only the packet digest on `acknowledge`, so revision drift between the child's
`read` and `acknowledge` does not refuse it; a refused `REVISION_CONFLICT` is retried
once after a fresh `read`. The retry is covered by synthetic tests only; it has not yet
been observed with concurrent live `Agent` calls.

The same session also showed that this Desktop build's `Agent` tool has no
`run_in_background` parameter: a supplied `run_in_background: "true"` string reached
the hook as an ordinary foreground call and was admitted. The denial of a boolean
`true` stays in the adapter but is unexercised on this host. An `Agent` call with
`isolation: "worktree"` was denied as designed.

Claude discards `systemMessage` and `continue` from both compaction events.
[PreCompact](https://code.claude.com/docs/en/hooks#precompact) supports blocking, but this
adapter adds no compaction blocking policy.
[PostCompact](https://code.claude.com/docs/en/hooks#postcompact) has no decision control.
Handled compaction events return `{}`, including identity/observation failures. Recovery
context uses `SessionStart` or normal pre-tool denials; compaction never grants readiness
or settles pending tools. Existing malformed-input and process-failure exits still apply.

The tool IDs used for pending work are supplied by the host. Generated request UUIDs are
transaction identities, never substitutes for missing session or tool IDs. Exact completion retries
are idempotent. Missing or malformed identity fails admission. No native adapter substitutes
`codex` for another host, invokes provider writes, settles ambiguous work, or runs cleanup on stop.

The Claude Linear inventory checks the server, provenance and operation name only.
Field-level rules, such as an assignee equal to the initiating human or the target state
name, remain in the contributor procedure's read-back rule; provider success is not
lifecycle authority. Denials name the admitted next operation for their code; see
[explicit setup](task-workspace-usage.md#explicit-setup-and-bootstrap). A coordinator
denied with `SOURCE_STALE` recovers through issue-level `diagnose` and the self-refresh `scope` built from its `sources` (setup step 4),
then `read`, `acknowledge` and `ready`; a reader asks the coordinator to re-scope it.

Unsupported MCP/provider routes must use a separately verified provider workflow. No native
archive-provider coverage is claimed here. Interrupted or ambiguous shell work remains pending.
Do not rerun an admitted command to replace missing completion evidence. Inspect
the original native terminal result. Only after independently verifying termination,
use explicit lifecycle `tool-complete` for the exact stored pending `tool_id`
with `completed: true` under the original participant's host, session and binding.
Otherwise retain pending work. A stop event, empty result or timeout is not
termination evidence. Shell commands remain cooperative tool calls; these
hooks do not prove that arbitrary commands did not launch their own independent child processes.

## Cursor scoped foreground Tasks

The candidate adapter supports assigned source reads and owned managed-note writes.
It does not admit generic child Shell, project-file Write/Edit, child providers or
nested Tasks. This is an experimental implementation, not completed native
acceptance. Installed profiles require separate verified deployment; changing this
checkout does not upgrade existing installations or retire their obligations.

A ready parent prepares an exact Task assignment through `cursor-plan`. Native
`preToolUse` consumes that assignment and records the parent call before allowing
execution. The actual child conversation and parent call identify its independent
participant, packet and owned paths. Source content and correlated successful Read
completion establish receipts before acknowledgment. A lifecycle `read` returns
source references; it does not replace source delivery. Use the exposed model Read
argument `path`; captured hook envelopes use `file_path`.

Exact lifecycle and reconciliation commands accept native `cwd` and positive
integer `timeout` metadata. Empty `cwd` means no override; nonempty overrides must
remain in the same repository. The timeout can background execution. These commands
rely on their checked core transactions, not Shell return, to establish effects.
After an uncertain return, inspect the original request and committed state before
retrying. Metadata alone does not authorize a command; the separate
[task-free parent read route](#finite-shell-reads) applies its finite grammar and
grants no protected Shell or child authority.

The adapter does not register `subagentStart` or `subagentStop` hooks. Their opaque
observations supply no authority or completion evidence. Actual Task admission,
child tool callbacks and independently verified results own those transitions.

After a Task callback, use the exact reconciliation command supplied in its context:
`cursor-reconcile --issue <issue-id> --call <call-token>` through the indicated
adapter entry point. The native prehook verifies the issue selector against the
actual ready parent. Reconciliation reads only the selected issue and independently
obtains the exact pinned native result. The core revalidates invocation, generation
and pending effects before atomically detaching authority and settling the parent
call. A report, tool completion, authority retirement and native invocation outcome
are distinct facts. Parent output acceptance remains separate.

A resumed conversation requires a fresh admitted invocation, generation, assignment
and source acknowledgment. Old callbacks cannot revive its earlier authority.
Missing or unsupported results remain unresolved; neither elapsed time nor an idle
UI establishes completion. Existing issue transactions provide crash recovery.

When a retained version-2 child index exists, its known raw-session children are
rejected before parent fallback, regardless of retained status. The adapter reads
that index without rewriting or retiring it. Malformed index data fails closed.
This guard does not identify unclaimed returned children missing from the index,
preserve old child operations, or establish safe installation over a legacy runtime.
A supported transition remains necessary before live cutover.

Native classification hashes the exact raw parent call or child conversation with
its namespace into a full 64-digit SHA-256 key. Compressed prefix paths locate one
retained issue; the route grants no authority. The first identity needs only a leaf
and root reference. A branch is added where keys diverge, with at most 64 selected
descendants in the worst case. Existing one-digit paths remain readable.
Each node has at most sixteen edges or one exact terminal identity and issue selector.
A missing edge or validated compressed-prefix mismatch means an unknown identity.
A missing root or referenced
node fails closed; native hooks never recreate it or interpret it as absence.
The adapter recovers the selected issue transaction under its existing lock and
revalidates the committed invocation and current participant generation. Ordinary
parents without a selected route do not enumerate retained issue histories.
Missing local bindings cannot turn a retained routed child into a parent.

The adapter publishes descendants durably before their parent references, and makes
each exact route reachable from the root before admission, child binding or
terminal-only retirement commits. Before the route becomes reachable, the corresponding
authority cannot commit. Once reachable, an interrupted claim remains restrictive
until the exact operation is retried or deliberately recovered. A root completion marker
binds the routing directory identity, so deleting or replacing that directory does
not establish a fresh empty registry.

An existing modern registry needs explicit complete population before routing
cutover. Run the following maintenance command from the registered repository root
in a standalone terminal outside Cursor native hooks. Supply the complete sorted
list of retained issue identifiers, including cleaned histories. The example list
and positive finite budget are operator inputs, not an inferred authorization.

```sh
poetry run python src/nplet/adapters/cursor_children.py \
  cursor-routing-recover --issues-json '["TEAM-1","TEAM-2"]' --budget-ms 30000
```

Recovery may roll selected issue transactions forward through their existing
recovery mechanism. It bounds the explicit inventory by the lifecycle request-size limit, checks
it against the retained issue collection, recovers every selected issue and writes
exact modern pointers. It checks the full inventory again before publishing the
completion marker. Markerless recovery can finish interrupted root initialization. It preserves existing
root edges and compatible orphan descendants; it never prunes pending route claims.
An incomplete or changed inventory, unreadable selected state,
expired budget or interrupted population leaves cutover unverified; partial
pointers remain restrictive and the explicit command can be retried. A coexisting
version-2 index remains byte-for-byte unchanged and keeps its raw-child denial.
Routing does not aggregate lifetime identities into a fixed-capacity file. Each
node uses the existing file-byte limit, with at most sixteen edges or one bounded
native identity. New branches therefore do not consume space reserved for an
admitted Task's future child identity. Encoded-size checks precede atomic node
replacement. Exact replay leaves node bytes unchanged. Nodes, routes and edges are
never evicted, truncated or deleted by the adapter. The completion marker and
required references detect missing storage; they do not authenticate valid-JSON
edits. Routing files retain the cooperative protected-state trust boundary.

This operation does not migrate legacy authority, retire legacy obligations or
update installed profiles. Native hooks never run population as a fallback and do
not admit this maintenance command as ordinary reconciliation.

These protocol tests do not establish native installation or live cutover. Source
delivery, genuine resume, repeated overlapping bursts and failure recovery still
require actual native acceptance with hooks active.

## Avoiding duplicate imported hooks

Cursor's [third-party hook reference](https://prod.cursor.com/docs/reference/third-party-hooks)
says Claude project hooks are imported when its third-party-import setting is enabled, which is
the default. Both imported and native matching hooks can run.
The [Cursor environment contract](https://prod.cursor.com/docs/hooks#environment-variables)
provides `CURSOR_VERSION` to hook processes.

The Claude shell entry and Python main exit silently when that variable is present, before reading
input or touching lifecycle state. Only the native Cursor adapter owns those events. This is a
no-decision duplicate skip, not an unconditional permission grant. No global import setting is
changed. Cursor uses generic pre/post hooks for ordinary tools and native call identity.
Its MCP-specific pre-hook additionally checks configured Linear server routing; the
MCP-specific post-hook does not duplicate completion or lifecycle mutation.

## Validation and remaining evidence

Disposable tests exercise both translators, core mutations, malformed envelopes, native response
shapes, duplicate callbacks, host separation, strict bootstrap rejection, and imported-hook skipping.
All three adapters have ticket-first startup regression coverage. Native protocol
tests use synthetic events; they are not actual host callbacks.

### Validate the Codex workflow

Keep run-specific observations, versions, source hashes, failures and artifact
locations in the Linear ticket. Use this guide for the validation procedure and
reusable operating constraints.

1. Use a disposable registered repository with trusted project hooks and normal
   host permissions. Record the app and bundled runtime versions, OS, interpreter,
   configuration and evidence that hooks loaded once. Pin the effective source
   bytes; record any difference between the checkout's Git HEAD and installed code.
2. Maintain a coverage matrix in the ticket for every configured hook. Record the
   expected decision, lifecycle mutation, native identity and completion behavior.
   Label evidence as installed-host callbacks, synthetic tests or manual lifecycle
   calls. Mark unobserved events explicitly unverified, including compaction,
   interruption and session end; never infer them from a successful stop.
3. Verify the complete ticket-first startup and preparation contract. Confirm an
   actual `UserPromptSubmit` callback, exact issue identity, source delivery,
   acknowledgment and readiness. If a tool-created Task message produces no prompt
   callback, obtain direct human input; do not fabricate the callback or substitute
   manual registration for native startup evidence.
4. Exercise required provider reads, writes and read-back, file edits, parallel
   shell calls, nonzero exits and delayed completion. Correlate native call IDs
   with pending-work settlement. Test unknown and foreign handles without clearing
   another operation. Record the actual tool envelopes; normalized `Bash` callbacks
   do not establish legacy `write_stdin` coverage. Preserve host permission authority;
   an empty adapter decision alone does not prove a visible permission prompt.
5. Verify roadmap acknowledgment, coordinator source refresh, reader refresh,
   repeated startup, legacy recovery, issue switching and failed-switch return.
   For an invalidated or stale packet, verify a protected-work denial before
   recovery and readiness afterward. Independent task-free work must preserve
   the stale packet and unresolved pending state. Follow
   [source placement and recovery](task-workspace-usage.md#explicit-setup-and-bootstrap).
   Preserve old work and label any operator intervention as manual recovery.
6. Validate the [restricted native child route](#codex-native-child-route-restricted)
   with host-issued identities, separate packets, actual source delivery and owned
   output verification. Prove three-way overlap across three fresh sibling bursts,
   successful handback and zero orphan pending work. Exercise inherited-context,
   child-provider and nested-launch denials without widening the contract.
7. Report Desktop and CLI separately for macOS, Linux and native Windows. Keep
   installed-hook acceptance separate from each platform's CI protocol and launcher
   results. Another surface's success does not establish activation on an untested
   host; record unavailable coverage and its next action in the ticket.
8. Retain redacted callbacks, before/after lifecycle state, source and output hashes,
   commands, timestamps and independent verification with the ticket's delivery
   evidence. Preserve initial failures and corrected results separately. Do not
   settle ambiguous work, edit control files or disable hooks to obtain a pass.

### Other validation limits

Inside the Claude desktop app's command sandbox, the `tests/integration/tooling` cases that
require child-process coverage (the coverage smoke, `check-local`, platform-coverage and
PR-workflow tests) fail with `child-only coverage: failed` at any commit, because the sandbox
blocks the child interpreter's coverage startup; observed 2026-10-11 on macOS at the base
and branch commits alike, with an unsandboxed rerun refused by the hook. Treat native CI as
the arbiter for `make check` from such a session and do not weaken those tests to pass there.

Before claiming actual-host acceptance, use a disposable registered repository and retain:

1. The installed host version and evidence that these exact project hook definitions loaded once.
2. An observed native session/tool ID pair, task-free work without phantom state, and a protected-operation denial before acknowledgment.
3. A supported foreground call and its matching completion, with no remaining pending entry.
4. A malformed/unsupported call denial and retained state after ambiguous completion.
5. For Cursor with third-party imports enabled, one native mutation and no Claude-host binding.
6. For Claude, inherited background-disable configuration and preserved permission prompting.

Protocol tests do not establish trusted host loading or actual provider interaction.
The PR's validation evidence identifies the native platforms and exact revision tested.
Claude's host-level fail-open cases remain a boundary:
the wrapper converts process failures to exit 2 and the Python deadline precedes the configured
host timeout, but an externally killed or timed-out hook is not guaranteed enforcement.

**Earlier observation.** In a local Desktop session before this allowlist existed,
`save_issue`, `list_users` and `list_comments` completed although the adapter of that
revision denied every MCP tool after readiness. The current revision includes those operations in its named configured-connector
allowlist, so the observation no longer points at a defect. A
host-side timeout fail-open was considered as the cause but never established; the
project hooks keep `timeout: 10` and the runner its 2-second deadline, and an explicit
`onFailure` setting in the hook definitions remains unverified on an installed host.

Installed-host acceptance of the task-free boundary requires fresh Codex and Claude
sessions on the frozen candidate. Historical dated observations above remain
historical. Retain actual prompt, pre-tool and post-tool callbacks; authorized
remote ticket creation, read-back and meaningful update; unbound task-free work;
protected denials; ticket startup; approved implementation; stale/pending-state
preservation; and no phantom lifecycle state. Manual lifecycle calls and synthetic
events cannot substitute for these observations. Cursor installed validation is
deferred until its prerequisite host and child integration work is complete;
shared-policy tests do not establish installed Cursor acceptance.
