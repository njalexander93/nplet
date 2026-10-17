# Task-workspace contract

**Local workflow guardrail.** The utility manages issue workspace creation, attachment, scoped readiness, archival and recovery. Installed-host trust/callback coverage and provider acceptance require separate evidence; protocol tests are not runtime or Security certification.

Host adapters call a shared lifecycle core for supported local task actions. It does not implement the Control Plane, authenticated Role authority, complete confidentiality, the paper reference checker or the full launcher.

## Scope and governing inputs

Use the public [context and independence rules](../../src/nplet/resources/organization/baseline.md#context-and-independent-assurance), [evidence/privacy rules](../../src/nplet/resources/organization/baseline.md#evidence-and-privacy) and [handoff/outcome rules](../../src/nplet/resources/organization/baseline.md#handoffs-and-outcomes). This document defines local mechanics, not new organizational authority.

The scope is one local non-bare Git repository and its linked worktrees on macOS, Linux or native Windows. Windows storage must use local NTFS. Separate clones, remote hosts, network filesystems, synchronization, exposed services and background scheduling are excluded. Each platform needs its own execution evidence; WSL is not native Windows verification.

## Storage and identity

```text
<registered-main-worktree>/.task/
  .control/
    repository.json
    issues/<ISSUE-ID>/       # lock, state, transactions, archive locator
  <ISSUE-ID>/               # actual task bytes
    roadmap.md
    events.jsonl
    context/                # focused Markdown notes, only when useful
<participating-worktree>/.task/
  .repository.json          # local registration reference, no secrets
  .bindings/<session-key>.json
  <ISSUE-ID> -> <registered-main-worktree>/.task/<ISSUE-ID>
```

The main worktree uses its real issue directory; other worktrees expose **only issue-specific links**: symlinks on POSIX and validated directory junctions on native Windows. Never link the entire `.task` root. `.control` is implementation metadata, outside disposable issue data; it is not the runtime authority ledger. Binding files contain identifiers and scope digests, not task prose. Track reusable code/templates and ignore `/.task/` in every worktree.

### Registration and validation

1. `register` takes an explicit main-worktree path. Resolve it and the caller with Git's `rev-parse --show-toplevel`, `--git-common-dir`, and `worktree list --porcelain`. Use subprocess argument arrays, never shell interpolation. Reject bare repositories and nonregistered worktrees.
2. Record schema version, a generated repository UUID, normalized common-Git path, main-worktree path and filesystem identities. Every attachment revalidates both Git membership and this registration. Branch names and remotes are not identity. Moving/removing the main worktree or replacing the common Git directory requires explicit registration recovery; never silently choose another empty root.
3. Accept issue IDs matching ASCII `^[A-Z][A-Z0-9]{0,15}-[1-9][0-9]{0,9}$` exactly. This is the first increment's supported subset. Reject lowercase, Unicode lookalikes, whitespace, separators, dots, zero/leading-zero numbers and overlong IDs. Do not normalize unsafe input into a different issue. Bind the provider's immutable issue UUID when known; conflicting UUIDs stop attachment.
4. Validate canonical paths through the platform filesystem implementation. POSIX uses no-follow directory descriptors, current-user ownership and private modes (`0700`/`0600`); reject group/world-writable control roots. Windows uses retained directory handles, owner/access checks and private access-control lists. Reject canonical reparse points, symlinks, special files and unexpected hard links. Canonical `.task`, `.control`, issue and context directories must be real directories under the registered root. Unsupported filesystems and unsafe Windows aliases fail closed. These checks do not protect against the same privileged user rewriting the store.
5. An existing worktree link is acceptable only if its target resolves exactly to the registered issue directory and remains in this repository. Reject dangling, foreign or substituted links. Never overwrite an existing real directory or link as an attach side effect. Create a missing link without replacing an existing entry; verify its type and exact target after creation. Windows junctions do not require symbolic-link privileges. Link removal removes the link only.
6. Payload paths are either `roadmap.md` or `context/<safe-name>.md`; use ASCII names matching `[a-z0-9][a-z0-9._-]{0,79}` with no `..` component. No absolute paths, traversal, nested redirection or arbitrary metadata edits. The core alone writes `events.jsonl` and control state. Use the platform implementation's no-follow and retained-handle operations; a resolve-then-recursive-delete sequence is insufficient. Reject Windows device names, alternate data streams, trailing dot/space aliases and case collisions rather than changing the requested path.
7. Read local registration from a caller-worktree reference, not a global current-task marker. Registration copies must agree with the canonical repository UUID/path. A copied registration from a separate clone fails validation.

### Permissions and existing manual state

Required access is narrow: the assigned canonical issue directory, its `.control/issues/<ID>` directory, and read access to repository registration. Setup alone needs permission to create registration/parent directories. The local worktree needs its own link/binding paths. Obtain these through supported host permissions; **links confer no permission**. A denial yields `PERMISSION_REQUIRED` with the exact paths and recovery operation. Never change global security settings, bypass hook trust, or request the entire main checkout merely for convenience.

New-issue cleanup examines only explicitly registered candidate IDs for which the caller already has appropriate access. It does not require broad access to other issues. Inaccessible candidates are retained and reported as skipped; creation of an independent authorized issue can proceed.

Existing manually shared workspaces require `adopt`, not `create --force`. Under the issue lock, inventory and hash existing bytes, preserve roadmap format/content, assign existing note owners explicitly, and record adoption provenance. Do not synthesize old events. Unexpected files or unresolved ownership stop adoption for review. No startup path empties or reinitializes an existing directory.

## Core interface and state

The shared core is `src/nplet/lifecycle/task_workspace.py`, with a Python standard-library API and matching command interface. The reusable entry point is `execute(request: dict) -> dict`; CLI reads one bounded UTF-8 JSON object from stdin, or accepts exactly one `--request-json` argument, and emits one JSON object to stdout. The argument route makes bootstrap callable without shell redirection. Host-specific input never enters the core unchanged. Command names below are operations in the request, not shell fragments.

Every request has `schema_version: 1`, `operation`, `request_id` and explicit `worktree`. **Bootstrap exception:** `register` takes `main_worktree` and no `repo_id`; it creates or validates registration and returns the acquired `repo_id`. A pre-registration `diagnose` omits `repo_id` and returns `REGISTRATION_REQUIRED` or the stored identity. An issue-level `diagnose` supplies `repo_id` and `issue_id` and returns issue presence, revision and storage state without committing state or appending an event; when the caller is a recorded participant with a packet it also returns that committed `packet` exactly as stored (no `available` field) and `sources`, a list aligned with `packet` of `{id, locator, recorded_sha256, current_sha256, available}`. `current_sha256` is the committed manifest digest for a payload-relative locator and the digest of the bytes read through the no-follow source reader for an absolute locator, or `null` when the source is missing or unreadable; `available` is true when it equals `recorded_sha256`. A failed `read`, `acknowledge` or `ready` with `SOURCE_STALE` carries `stale`, the entries of the same shape whose digest differs. Every issue operation then supplies `repo_id`, `host`, `session_id`, and applicable `issue_id`, `participant_id`, `binding_generation`, `expected_revision` and operation arguments. In `rebind`, `issue_id` and `binding_generation` name the old binding, while `new_issue_id`, `new_binding_generation` and `issue_uuid` describe the target. IDs are opaque bounded strings except validated issue IDs; filesystem names derived from session IDs use SHA-256, never raw host identifiers. Persist idempotency by request ID plus canonical request digest. Same ID/same request returns the prior result; same ID/different request is rejected. Do not record arbitrary raw request bodies.

Responses contain `ok`, `code`, identifiers, current `revision`, `binding_generation`, allowed reference descriptors and a bounded diagnostic/recovery action. No private note body is returned by status/diagnostic operations. Suggested CLI exits: `0` success, `2` invalid input, `3` conflict/not-ready, `4` I/O/provider/recovery failure. These exits are **not** a native host hook response protocol.

| Operation | Required behavior |
|---|---|
| `register`, `adopt`, `diagnose` | Explicit local setup, safe migration, or read-only bounded status. Registration/adoption never infer completion. |
| `bind` | Bind an explicitly named issue and scope to a host session. Existing identical binding is idempotent; conflicting issue requires `rebind`. Initial coordinator is explicitly designated at create/adopt; joining a session does not confer that role. |
| `create` | Under the persistent issue lock, create only if absent and no tombstone/history exists; atomically publish a complete template workspace. Concurrent creators converge; differing identities/scopes conflict. |
| `attach`, `resume` | Validate membership, scope, binding generation and payload; register a participant and preserve bytes. A missing directory with a tombstone enters recovery, never empty creation. |
| `join` | After the exact foreground ticket read through a supported runtime adapter, attach a new session as a roadmap-only reader. Require the matching immutable issue UUID and reject caller-supplied packets or ownership. The coordinator and existing assignments stay unchanged. This internal adapter route is excluded from unready shell bootstrap commands. |
| `rebind` | Explicit old issue and expected binding generation, `new_issue_id`, evidence, and `issue_uuid` naming the target; detach old participation, then bind the new issue. The old issue must be the caller's live session binding at the stated generation, and a detached old participation is left only by this request's own retry or replay; otherwise `STALE_BINDING`. An existing target must already hold an assignment for the caller (or name it coordinator), otherwise `SCOPE_MISSING`, and must match a supplied `issue_uuid`, otherwise `ISSUE_MISMATCH`. An absent target is created from the packaged template with the caller as coordinator; this needs `issue_uuid` and refuses an unadopted payload directory (`ADOPTION_REQUIRED`). While a session binding names one issue, `create`/`attach`/`resume`/`join` for another issue return `BINDING_CONFLICT`, so this route is the only way a bound session starts or enters a second issue; the ticket-first startup uses it after the verified ticket read. Preserve old issue state and coordinator ownership. Refuse while participant-owned external operations remain unresolved. Never rebind a parent under active child participants. A subagent identity (`<session>/agent/<id>`) cannot rebind (`NOT_OWNER`). |
| `scope`, `read` | Coordinator installs a versioned packet manifest; participant reads only permitted references/notes. `read` checks each required digest and scope. Scope changes invalidate prior acknowledgment. |
| `acknowledge`, `ready` | Acknowledge exact packet digest/required-source availability, then check binding, scope, ownership, unresolved transaction and filesystem access. This establishes delivery facts, not comprehension or execution authority. |
| `source-read-start` | Record pending preparation for one available assigned `source_locator` in the exact current `packet_digest`, under the participant's own `tool_id`. It does not require acknowledgment or grant ordinary tool readiness. Correlated `tool-complete` settles it; unresolved source reads block detachment like other pending work. |
| `update` | Change one owned payload file with expected issue revision and old file digest; validate provenance and commit through the transaction writer. Revision conflicts require reread/reapply; never last-writer-wins. |
| `checkpoint` | Coordinator records current goal, constraints, pinned source references, progress, blockers, next handoff and exact candidate/evidence references. PR submission records `in_review` and retains context. |
| `event` | Append one allowlisted bounded event under the same issue lock. Caller cannot impersonate another participant or invent provider acceptance. |
| `outcome` | Coordinator records `active`, `blocked`, `in_review`, or terminal `completed`, `cancelled`, `failed`, with source evidence and expected revision. Failed only means terminal when explicitly abandoned with disposition; retryable failure stays blocked. |
| `detach`, `reconcile-participant`, `transfer-coordinator` | Retire only the identified generation after verifying no pending activity; explicit evidence is required for uncertain participants and ownership transfers. No timeout-based coordinator election. |
| `event-rollover` | Coordinator, expected revision, no pending operations and current verified archive required; retain an immutable segment and start the next globally chained stream. |
| `archive-prepare`, `archive-verify` | Produce immutable export request; verify provider read-back and establish an archive receipt for that snapshot. No local boolean substitutes for read-back. |
| `cleanup-plan`, `cleanup-commit` | Select/recheck only eligible issues; record recoverable intent; remove only the verified unchanged issue payload. Never delete control metadata. |
| `restore` | Verify archive bytes and manifest into staging, then publish under lock and rebind with a new generation. Invalid/unavailable archives stop for recovery. |

### State transitions

Keep three dimensions separate:

- **Issue disposition:** `active ↔ blocked`, `active/blocked → in_review`, `in_review → active` for corrections; explicit evidence permits a terminal disposition. `Stop`, session archival, inactivity and a submitted PR do not imply terminal state.
- **Storage:** `absent → present → archive_pending → archive_verified → cleanup_pending → cleaned`. Any changed payload invalidates the verified snapshot. `cleaned → recovery_required → present` only after verified restore. Transaction uncertainty makes readiness false until recovery resolves it.
- **Participant:** `unbound → attached → ready → detached`; a scope/binding change returns it to `attached`. Lost host signals yield `unknown`, never automatically `detached`. Reattachment uses a new generation and fences stale writes.

Terminal recording for a completed issue requires references to exact human acceptance, actual required merge and completion of issue obligations. The utility validates supplied identities/evidence and provider status; it cannot manufacture or independently grant human acceptance. Terminal state still forbids ordinary task edits until explicit reopen, which invalidates cleanup eligibility. Cancellation preserves its recorded disposition/history.

## Ownership, context and participant lifetime

One coordinator owns `roadmap.md`, shared summary and packet assignment. Participants own distinct named notes, recorded in control state, for example `context/implementation.md`. Every note/update carries author/participant, source references and digests, applicability, status, and superseded revision when relevant. No private reasoning is required. Ownership transfers are explicit, revision-checked and recorded.

Packet manifests list reference ID, source locator, content digest/version, authority class, required/optional status, inclusion reason and allowed reader/stage. Required governing sources remain independently retrievable; summaries do not replace them. Refresh allowed references on resume/compaction and after material source changes. A coordinator `update` that commits `roadmap.md` refreshes only that coordinator's own `roadmap.md` reference and clears its acknowledgment; readiness then needs a new `read` and exact-digest `acknowledge`. Reader packets are refreshed only by their own `join`. A stale required reference denies readiness for affected work. A packet checksum does not prove semantic completeness.

Fresh-author and independent-review participants receive explicit allowlists. Do not expose the whole roadmap, events stream, archive, upstream author notes or research by default. An authorized factual safety notice can be added with provenance without disclosing upstream conclusions. Hook output contains only fixed control text, safe IDs and packet digests; task text is retrieved as data through scoped reads. Do not interpolate roadmap/note/prompt content into developer-level `additionalContext`.

Host session identifiers are opaque. Root participant identity is `(repo_id, host, session_id, generation)`. A child also needs a verified distinct child identity and its own scope. Parent issue inheritance does not mean parent packet inheritance. A child needs an unambiguous host-to-tool identity mapping before its scope can be trusted. Do not infer it from cwd, timing, agent type or transcript content. On Claude Code the host-issued `agent_id` supplies it: the child session is `<parent session_id>/agent/<agent_id>`, it joins as a roadmap-only reader only while its parent is ready with a pending `Agent` call, and the coordinator may then `scope` it with owned paths. This route is restricted: observed in one local Desktop session and unverified elsewhere. See the [host guide](host-hooks.md) for per-host child and coverage limitations.

`Stop`/Interrupt are observations, not detachment. `SessionEnd` marks a session-end observation but does not prove children or shell processes ended. Track pending supported tool IDs and returned asynchronous execution handles. Explicit detachment requires no outstanding tools/children and a host confirmation or accountable coordinator reconciliation. A missing PID, expired heartbeat or elapsed time is insufficient. Unknown participants retain the issue and prevent cleanup. No recurring liveness service is introduced.

## Coordination and crash recovery

Use a persistent per-issue lock in `.control/issues/<ID>/`, shared by create/attach/update/archive/cleanup. A process-held OS lock (`flock` on POSIX; `LockFileEx` on Windows) releases when its process exits; never delete/recreate the lock file to break a lock. Bound waits and return `BUSY`; hooks must return a denial before their own host timeout. Acquire two issue locks for rebind in lexical ID order. Setup registration has its own short-lived setup lock.

Maintain an integer payload revision, binding generations, per-file digests, event sequence/head, ownership, participant records and pending transactions in issue control state. Sessions do not hold locks across model/provider calls. Archive exports are snapshots; provider I/O occurs outside the lock and must be compared again at verification/cleanup.

For a payload mutation, stage the new file plus required event and expected state in a recoverable transaction before publication. Flush staged bytes and intent, then atomically replace the payload and state under lock. On interruption, reconcile old/new digests and transaction ID, finishing exactly once or returning `RECOVERY_REQUIRED`; never report partial state as ready. Test failures at every persistence boundary. This is a local transaction protocol, not an atomic transaction with Linear. POSIX flushes files and directory metadata. Windows flushes file bytes and uses write-through namespace publication. Recovery addresses interrupted processes; it does not promise identical power-loss durability across filesystems.

Out-of-band edits are unsupported concurrent writes. Detect manifest mismatch before ready/write/archive/cleanup; preserve changed bytes and return `UNTRACKED_CHANGE`. An explicit coordinator reconciliation imports the inspected change at a new revision. Do not silently overwrite it with template or older staged bytes.

## Events and retention

`events.jsonl` is a **local diagnostic stream**, not the full authority ledger. Write UTF-8 JSON, one complete newline-terminated object per event. The coordinated writer assigns sequence and identifiers. Required workspace-transition events commit with their local operation; optional host observations may fail without stopping unrelated work.

Version 1 event fields:

| Field | Meaning / bound |
|---|---|
| `schema_version`, `event_id`, `seq`, `at` | Version 1; UUID; monotonically increasing integer; UTC timestamp. Ordering uses sequence, not wall clock. |
| `repo_id`, `issue_id`, `participant_id`, `generation` | Validated binding; `system` only for core-owned recovery facts. |
| `type`, `outcome`, `required` | Allowlisted type; `ok/denied/failed/unknown`; retention class. |
| `operation_id`, `revision`, `cause_id` | Transaction/request association, payload revision, optional causal ID. |
| `refs` | At most 16 permitted references with kind, safe locator, revision/digest and reader scope. |
| `code`, `summary` | Typed diagnostic and fixed/sanitized summary, at most 512 characters. No free-form tool output. |
| `prev_digest`, `digest` | SHA-256 chain over canonical JSON excluding `digest`; first prior digest is null. |

Canonical JSON uses sorted keys, compact separators, UTF-8 and no NaN/Infinity. Limit an encoded event to 8 KiB. Types cover create/adopt/attach/rebind, scope/acknowledgment, update/checkpoint/outcome, detach/reconciliation, archive preparation, and recovery. Bounded readiness-denial/check/tool-completion observations are optional; never copy tool arguments, prompt bodies, transcripts, provider payloads, secrets or reasoning. Do not hash secrets as a substitute for omitting them.

A reader accepts only complete validated lines and their chain. An interrupted final line is quarantined in control state under the lock. Required transaction recovery reconstructs its exact event once; unprovable required evidence stops dependent mutations. Optional partial diagnostics may be discarded with a recorded loss count. Corruption inside the committed prefix is an integrity error, not permission to truncate history. A chain detects changes relative to a retained head; it does not resist an actor who rewrites the entire store/verifier.

For this increment, retain the complete bounded stream until verified archive and eligible cleanup; no clock-based expiry or background rotation. Cap the stream at 16 MiB: suppress further optional observations with an aggregate loss counter, and require an explicit archived-segment checkpoint before further required writes if space remains insufficient. Never silently drop required or unresolved-failure records. Segments retain sequence ranges/head digests and reconstructable content in the archive. The event reader enforces packet scope; physical same-user file access is not confidentiality proof.

## Archive provider boundary and recovery

**Provider bridge: foreground operations plus deterministic local verification.** The shared lifecycle consumes explicit provider observations; runtime adapters translate the selected host's tool protocol. A command hook does not gain the foreground session's authenticated provider access. Use that session's available tools and pass verified observations through the documented boundary.

The currently documented archive bridge uses these Codex connector declarations. These names describe one integration, not a requirement that every runtime use Codex. Archive support in another runtime requires equivalent write/read-back observations and explicit protocol verification:

- `mcp__codex_apps__linear_save_document({issue, title, content})` to create a document, or `{id, content}` to update it.
- `mcp__codex_apps__linear_get_document({id})` for read-back.
- `mcp__codex_apps__linear_get_issue({id})` and `linear_list_documents` for identity/recovery lookup.

The command-hook process does not inherit the foreground connector session. Provider work is an explicit foreground operation, never an assumed hook-side transaction. Preserve data when a connector or required observation is unavailable.

### Concrete archive sequence

1. `archive-prepare` requires coordinator ownership and expected revision. Freeze a snapshot and request ID under lock. For terminal collection, `seal:true` requires all other participants detached and no outstanding ordinary tools/children; it detaches the coordinator from payload work and records that fact before snapshotting. The session keeps a maintenance-only binding for this issue. Include cleanup eligibility/disposition intent in that snapshot before hashing. Do not mark it archived. Return an export file and exact document title/content plus issue UUID; export paths are local data, never executable commands.
2. The foreground coordinator uses the connected Linear tool to save exactly that request into an issue-parented document. Use a title containing issue ID and snapshot digest. Preserve previous verified snapshots; never replace their sole recovery bytes with a partial new version. On uncertain save outcome, search/read matching documents before retrying creation. Do not retry blind and infer which duplicate is authoritative.
3. Read the returned document ID through `linear_get_document`. Pass the observed ID, URL, parent issue identity, provider `updatedAt`, and read-back content to `archive-verify`. A provider response imported by the foreground bridge is attributable evidence, not authenticated runtime authority. Retain its origin/request identity; a hand-edited success flag is not accepted.
4. The verifier parses the structured payload, decodes every file, checks byte lengths/digests, validates the repository/issue/snapshot manifest, and compares against the frozen snapshot. Reacquire the issue lock and require the current payload revision/digests to match. A provider-normalized Markdown wrapper may differ; decoded archived bytes may not.
5. Record the verified locator, provider version, payload digest, manifest digest, event prefix/head, snapshot revision and read-back time outside the disposable directory. This receipt authorizes only the specified local eligibility check; it is not human acceptance. If the receipt/snapshot is stale, keep the workspace.

The archive document contains a readable outcome/goal/constraints/progress/blockers/source/handoff history plus a versioned structured manifest and reconstructable payload. For exactness, encode UTF-8 roadmap/context/event bytes in base64 inside a fenced JSON object. Each entry carries relative path, byte length, SHA-256 and disclosure scope. Digests use original bytes. Sensitive material must be excluded before export, not merely encoded. Research stays at its access-controlled durable source; large external evidence needs a verified durable reference and scope.

Use a conservative 256 KiB export-document limit as a **local implementation limit**, not a claimed Linear limit. Larger payloads require numbered issue documents plus a root manifest of document IDs/digests and read-back of every part. If the provider rejects size/content, preserve local data and return `ARCHIVE_PENDING`; never truncate or silently omit meaningful history. Validate actual connector behavior and multipart reconstruction separately from local provider fixtures.

### Cleanup and restore

`cleanup-plan` is a preview. `cleanup-commit` under the same persistent issue lock must freshly verify:

- Recorded terminal disposition and exact outcome evidence; no active, blocked, in-review or uncertain state.
- No attached/ready/unknown participant, pending tool/child or unresolved transaction.
- A successful fresh provider read-back of the referenced archive for this cleanup attempt. The foreground bridge supplies it; no connector access means skip cleanup, not assume the old receipt remains valid.
- Identical current payload bytes, file set, snapshot revision, required event prefix and archive manifest. Required post-snapshot events invalidate eligibility. Archive verification/cleanup bookkeeping lives in persistent control state to avoid a self-invalidating payload snapshot.
- Revalidated repository identity, directory descriptors, safe target and granted permissions.

Write a persistent cleanup intent containing snapshot/manifest identity and the exact directory filesystem identity. Rename only that directory to a unique quarantine child in its already-authorized `.control/issues/<ID>/` directory on the same filesystem, then remove its verified regular files without following links. The lock prevents supported attachment during this transition. Quarantine is recoverable state, never another issue. A failed delete retains the intent and remaining bytes; recovery either completes the verified removal or restores the directory. Unexpected files/path swaps stop removal. Do not issue recursive root deletion.

Keep the tombstone, verified archive locator/manifest, terminal disposition, final cleanup result and recovery generation in `.control/issues/<ID>`. Cleanup bookkeeping survives payload removal. Cleaned links may remain dangling until validated reattachment removes/replaces them; they never authorize empty recreation. Startup only performs collection for already eligible candidates with fresh read-back available; otherwise it reports retention and continues the new issue. There is no remote network archive in SessionEnd or recurring job.

`restore` fetches the root and all parts through the same foreground bridge, validates every digest/path, and reconstructs in a staging directory. Reject duplicate paths, traversal, symlinks, unknown schema, incomplete parts and decompression/size abuse (base64 decoding has explicit bounds). Under lock, require the matching tombstone and no newer payload, publish restored data, append a new lifecycle generation and explicitly reopen. Preserve historical terminal evidence; never overwrite it as though the issue had always been active. Provider loss, tampering, missing tombstone or unknown history yields `RECOVERY_REQUIRED`.

## Host adapters

Native host configuration, event translation, trust and host-specific limitations are documented in [Host hooks](host-hooks.md). Adapters reuse the shared lifecycle core; they must not maintain a competing lifecycle or treat another host's JSON schema as native support.

A hook can deny a covered action only when the host actually invokes and honors it. Disabled/untrusted hooks, missing interpreters, timeout, malformed responses, uncovered paths and same-user filesystem access limit enforcement. Installed-host evidence is required separately for each host and version. Protocol fixtures do not establish that coverage or support for another operating system.

### Task-free work and implementation effects

The shared policy in `src/nplet/adapters/effects.py` classifies observed operations before task readiness. Native adapters supply tool identities, arguments, working directories and every edit target. Caller labels such as `read_only` do not establish a contract.

- **Task-free:** recognized reads/searches, known administrative operations and structured ordinary prose-note edits. These operations need no issue binding, packet acknowledgment or roadmap approval. They create no task workspace or pending-tool record. They remain available with a stale binding or pending ticket lookup.
- **Implementation:** source, tests, scripts, dependencies, build inputs, executable workflows, agent instructions and enforcement changes require current task readiness and the contributor procedure's human approval. Managed `.task` notes retain lifecycle ownership and update rules.
- **Package dependencies:** every dependency change requires a task, including manifests, lockfiles, requirements and constraints files, plus package install/remove/update commands. A prose suffix does not exempt a dependency input such as `requirements.txt`. Supported hooks classify known dependency targets as protected and package-manager execution through the task-gated shell route; this is bounded observed-call admission, not universal filesystem confinement.
- **Ordinary notes:** explicit `.md`, `.txt` or `.rst` destinations outside protected roles. For example, `docs/planning.md` can be a task-free note. A Markdown suffix alone does not make `AGENTS.md`, `SKILL.md` or a governing runtime guide task-free.
- **Unknown effects:** opaque execution, unknown providers, malformed targets and unsupported shell forms require the task gate. The shared `effects.guidance()` diagnostic names supported read/search, exact provider contracts and structured-edit alternatives, and points to this section for the full grammar. Readiness does not remove host-specific unsupported-tool restrictions.

The protected directory roles and exact governing-document registry live in `effects.py`. For targets inside the checkout, ordinary roles are checkout-relative, so an ancestor directory named `src` does not protect every checkout file. Both lexical and resolved root/target pairs are checked. External targets retain conservative absolute-path role checks; source/build/dependency roles outside the checkout are not blanket exemptions. Dot-control ancestors and loader filenames remain protected globally. Exact registered paths are compared conservatively without case distinctions, including future aliases. Loader filenames (`AGENTS.md`, `CLAUDE.md`, `SKILL.md`, `CONTEXT.md`, `GEMINI.md`) are protected anywhere, case-insensitively. The checkout's catalog, framework baseline, authoring definition format and named runtime guides are explicitly registered; the entire `docs/` tree is not protected. Add new loader-consumed instructions or enforcement documents to the registry or trusted `Policy.protected_paths`, including external vault loaders. A renamed file does not automatically carry a semantic role that was never registered.

Every structured edit target is checked before any part is admitted. Rename checks include both endpoints. Lexical and resolved paths, executable files and existing hard links retain protection. Mixed note and implementation edits take the implementation gate. Path checks observe current filesystem state; they do not prevent races or provide adversarial confinement.

Shell admission uses a bounded grammar, not a proof about arbitrary commands. Through Codex `exec_command`, explicitly pass `login: false` and `shell: "/bin/sh"`; omitted defaults can load user startup code and therefore require the task gate. A supported invocation is `exec_command({"cmd": "rg --no-config -n pattern docs", "login": false, "shell": "/bin/sh"})`. Native command-only `Bash` is classified by its observed command contract; that envelope does not establish hidden shell arguments or filesystem confinement. Supported command examples include `cat README.md`, `rg --no-config -n pattern docs`, and `git --no-pager --no-optional-locks -c core.fsmonitor=false status --short`. Git `diff`, `log` and `show` additionally require `--no-ext-diff --no-textconv` before any `--` separator. Every component of a supported pipeline or command list must qualify. Shell writes, substitutions, interpreters, unsupported flags and opaque wrappers require the task route; use a structured edit for an ordinary note.

The complete simple-command grammar is below. Options are separate tokens; unsupported options and combined short-option spellings remain task-gated. A `--` separator ends option parsing for `cat`, `head`, `tail`, `wc`, `ls`, `rg` and supported Git inspections. Later tokens are operands, so option-looking filenames do not count as required flags.

| Command | Supported forms/options |
| --- | --- |
| `pwd` | No argument, `-L` or `-P` |
| `cat` | `-n -b -s -E -T -v -A` |
| `head`, `tail` | `-n VALUE`, `-c VALUE`, `-q`, `-v` |
| `wc` | `-l -w -c -m -L` |
| `ls` | `-l -a -h -t -r -d -1 -A -F` |
| `rg` | Require `--no-config` before any `--`. Flags: `-n -i -l -L -s -S -F -w -x -v -c --files --hidden --no-ignore`. Value options: `--glob -g -e --regexp --type -t --max-count -m --context -C -A -B` followed by a separate value. |
| `sed` | Exactly `-n Np FILE...` or `-n N,Mp FILE...`, with numeric addresses and filenames that do not start with `-`. No substitutions, scripts or execution. |
| `git` | Require the exact prefix `git --no-pager --no-optional-locks -c core.fsmonitor=false`. Subcommands: `status`, `diff`, `log`, `show`, `rev-parse`, `ls-files`; also the exact `branch --show-current` form. Allowed inspection flags: `--short --porcelain --branch --stat --name-only --name-status --oneline --all --cached --staged --no-ext-diff --no-textconv --show-current --verify --abbrev-ref --show-toplevel`. `diff`, `log`, `show` require both `--no-ext-diff --no-textconv` before `--`. |

The grammar accepts compositions using `;`, `&&`, `||` or `|` only when every component qualifies. It excludes `cd`, `gh`, path-based executable names, environment assignments, redirects (including `2>&1`), substitutions, interpreters, unquoted globs/brace/tilde expansion and unsupported options. Dollar signs, backticks, backslashes and multiline commands remain outside this grammar even inside quotes. Quoted literal patterns can avoid shell glob expansion, but a shell-looking token is not a safety claim. Examples outside the task-free grammar include `git branch --list`, `git log -1`, `git log --format=...` and `git rev-list`; the supported Git prefix is still required for admitted forms. These exclusions describe task-free admission, not universal command prohibition.

Providers use exact operation contracts and observed native provenance. Known reads/admin operations can be task-free; an `mcp__` prefix or free-form safety claim grants no exemption. Consult [implemented coverage](host-hooks.md#implemented-coverage) for each host's narrower supported route. Task-free admission preserves native permissions and required user authorization. It does not authorize sending messages, publication or merge.

Readiness verifies scope and source acknowledgment. Roadmap approval remains a human procedure; the runtime does not independently authenticate that decision. Installed-host trust, callback coverage, identity and completion evidence remain separate obligations.

### Bootstrap exceptions

Task-free operations above remain available without readiness. Task-scoped startup and recovery additionally admit:

1. The exact Linear issue read for the recorded `Task: <issue-id>` assignment through a supported runtime adapter. The prompt records the identifier without creating an issue workspace. The adapter validates the native request and matching completion, verifies the returned identifier and immutable UUID, then runs shared registration, attachment, packet reading and readiness. Confirmed missing-ticket responses stop without workspace creation; generic invalid requests and other provider failures retain their diagnostics. This selected-ticket route does not admit general mutations or opaque execution wrappers. Unrelated task-free provider calls use their own verified contracts, without completing the ticket transition. See [ticket-read protocols](host-hooks.md#ticket-read-protocols) for host mappings and evidence limits.
2. Host user-input/permission UI needed to grant the exact requested paths.
3. The reviewed lifecycle command's exact `diagnose/register/bind/adopt/resume/restore` invocation for its assigned issue and allowlisted arguments. `diagnose` is admitted in its pre-registration shape or its read-only issue-level shape (`repo_id`, `issue_id`, optional `binding_generation`) for the assigned issue. The issue coordinator may also run a self-refresh `scope`: its own participant key as `target_participant` and its current packet (the issue-level `diagnose` result's `packet`) with only each `sha256` replaced by a digest (same references, order and other fields), no `owned_paths` and no other fields. The coordinator takes each refreshed digest from the same result's `sources[*].current_sha256`, so recovery needs no file hashing inside the stranded session. The core clears its acknowledgment, so readiness still needs `read`, `acknowledge` and `ready`. A scope that repeats the unchanged digests is accepted but does not restore readiness; the next `read` fails again with `SOURCE_STALE` and its `stale` list. Allow no shell operators, redirection, command substitution, wrapper interpreters, arbitrary files or additional command. Parse a strict argument vector; reject anything the adapter cannot prove is this command.
4. The exact Linear read/save operations bound to a pending immutable archive/recovery request, with checked issue/document ID and expected payload digest. This exception does not allow general provider edits. If nested tool calls conceal those arguments, use an observable direct call or retain the data.

While the recorded Task's ticket read (exception 1) is pending, the session may also make read-only preparation calls. The rule is runtime-neutral. Codex's legacy preparation exception recognizes exact command-only native Bash reads of those two governing files; the Claude Code adapter supports: `ToolSearch` loading only configured `get_issue` schemas, and `Read` or a plain `cat` of exactly `<checkout>/docs/runtime/contributor-workflow.md` or `<checkout>/AGENTS.md`. They receive no lifecycle decision, write nothing and preserve the selected-ticket transition. Other task-free reads use the effect policy. The [Claude connector mapping](host-hooks.md#claude-desktop-connector-mapping) gives the exact bounds.

A sealed maintenance binding permits only the exact archive export/read-back/verify/cleanup operations for its pending request, plus explicit rebind. Its lifecycle API cannot deliver out-of-scope task notes or resume task-scoped implementation; reopen first. Generic task-free file inspection is not a confidentiality boundary. A new-issue session may collect an older issue only with an explicit maintenance assignment and the same checks. This avoids keeping a payload participant live merely to finish its archival.

Task-free reads, search and notes use the explicit effect contracts above; arbitrary shell commands and MCP wildcards receive no exemption. A supported diagnostic path must explain the next action without recursively calling itself through the gate; adapter denials name the admitted next operation for their diagnostic code. PermissionRequest must not automatically grant permissions. Static hook control instructions may explain these routes but cannot promote arbitrary task data to instructions.

### Native child diagnostics

- `SOURCE_DELIVERY_REQUIRED`: before acknowledging, run the exact `cat -- <absolute source path>` through command-only Bash for each undelivered or mismatched current-packet source. Shell-quote the absolute path as required. Wait for matching source-byte completion, then acknowledge the current packet digest and run `ready`.
- `HOST_UNSUPPORTED_TOOL`: for task-scoped execution, a Codex child uses command-only Bash for its own canonical lifecycle commands or exact packet-source reads. Known task-free inspection remains available in its verified joined cohort; generic note mutations remain denied, while managed notes use scoped lifecycle updates. Ordinary inspection of a stale packet source does not establish source delivery or permit acknowledgment. Switching tool families does not expand its implementation authority.
- `HOST_CHILD_CAPACITY_EXHAUSTED`: the parent binding has consumed its 32-launch lifetime budget. Unknown or failed attempts retain their slots. Preserve unresolved activity; do not reset the registry. A fresh parent needs its own identity and an explicit coordinator handoff after pending work is resolved.


### Verification boundaries

Local lifecycle checks must cover worktree sharing/isolation, identity/path validation, scoped context, concurrent writes, crash recovery, pending-operation retention, exact archive reconstruction and cleanup/restore races. Host checks must separately establish actual callback delivery, legitimate trust, allowed bootstrap, denied covered actions, async completion and child identity. Provider checks need real save/read-back and reconstruction in addition to local fixtures.

Retain exact candidate, inputs, failures, repairs, reviewer scope and limitations in the delivery record outside product documentation. Human-reviewed publication does not activate policy or replace exact governance authorization. See the [baseline publication rule](../framework/baseline.md#authoring-and-publication-rules).

For commands, recovery and host limits, see [Task-workspace usage](task-workspace-usage.md#usage).
