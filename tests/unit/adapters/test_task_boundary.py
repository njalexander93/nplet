"""Verify finite effect contracts independently of lifecycle storage."""

import shlex

import pytest

from nplet.adapters import session_repository as carrier
from nplet.adapters import task_boundary as boundary
from nplet.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit

# Freeze the approved provider contract independently of production allowlists.
REQUIRED_LINEAR_OPERATIONS = frozenset(
    {
        "get_issue",
        "list_issues",
        "search",
        "get_project",
        "list_projects",
        "get_user",
        "list_users",
        "get_team",
        "list_teams",
        "get_issue_status",
        "list_issue_statuses",
        "list_issue_labels",
        "list_project_labels",
        "get_milestone",
        "list_milestones",
        "list_cycles",
        "list_comments",
        "save_issue",
        "save_project",
        "save_comment",
        "save_issue_label",
        "save_project_label",
        "save_milestone",
    }
)
REQUIRED_GITHUB_READS = frozenset(
    {
        "fetch_pr",
        "get_pr_info",
        "get_pr_diff",
        "fetch_pr_patch",
        "list_pr_changed_filenames",
        "fetch_pr_file_patch",
        "fetch_pr_comments",
        "list_pull_request_reviews",
        "list_pull_request_review_threads",
    }
)


def test_provider_inventories_match_the_approved_contract() -> None:
    """Detect removed required operations and unapproved additions independently."""
    assert boundary.LINEAR_OPERATIONS == REQUIRED_LINEAR_OPERATIONS
    assert boundary.GITHUB_READS == REQUIRED_GITHUB_READS


@pytest.mark.parametrize("operation", sorted(REQUIRED_LINEAR_OPERATIONS))
def test_named_linear_operations_are_free(operation: str) -> None:
    """Admit each named parent operation while preserving the child boundary.

    Args:
        operation: Independently named supported provider operation.
    """
    # Native identity and provenance validation precede this shared classification.
    event = {"tool_name": "mcp__codex_apps__linear__" + operation, "tool_input": {}}
    assert boundary.task_free(event, "codex")
    assert not boundary.task_free({**event, "child": True}, "codex")


@pytest.mark.parametrize(
    "operation",
    [
        "delete_comment",
        "delete_customer",
        "delete_customer_need",
        "delete_diff_comment",
        "delete_status_update",
        "get_unknown",
    ],
)
def test_unnamed_provider_operations_stay_gated(operation: str) -> None:
    """Retain readiness for excluded deletes and unknown reads.

    Args:
        operation: Excluded or unknown native operation.
    """
    assert not boundary.task_free(
        {"tool_name": "mcp__codex_apps__linear_" + operation, "tool_input": {}}, "codex"
    )


@pytest.mark.parametrize("operation", sorted(REQUIRED_GITHUB_READS))
@pytest.mark.parametrize("namespace", ["mcp__codex_apps__github_", "mcp__codex_apps__github__"])
def test_declared_review_reads_have_exact_fields(operation: str, namespace: str) -> None:
    """Cover all nine review reads through both observed native spellings.

    Args:
        operation: Declared review-read operation.
        namespace: Captured native connector spelling.
    """
    # Construct the declared repository field, including file-patch's explicit target.
    args = {
        "link_id": "github",
        "repository_full_name" if operation == "get_pr_info" else "repo_full_name": "owner/repo",
        "pr_number": 7,
    }
    if operation == "fetch_pr_file_patch":
        args["path"] = "src/example.py"
    assert boundary.github_read(namespace + operation, args)
    assert not boundary.github_read(namespace + operation, {**args, "arbitrary": "value"})


@pytest.mark.parametrize(
    "command",
    [
        "pwd",
        "cat -- src/example.py",
        "rg --no-config -n needle src",
        "rg --no-config --files",
        "git --no-pager -c core.fsmonitor=false status --short",
        "git --no-pager -c core.fsmonitor=false diff --no-ext-diff --no-textconv",
        "gh pr view 7 --repo owner/repo --json number,reviews,comments",
        "gh pr diff 7 --repo owner/repo --patch --color never",
        "gh api --method GET repos/owner/repo/pulls/7/reviews --paginate",
    ],
)
def test_bounded_read_commands(command: str) -> None:
    """Admit complete known argument vectors.

    Args:
        command: Finite foreground read command.
    """
    assert boundary.read_command(command, "claude-code")


@pytest.mark.parametrize(
    "command",
    [
        "cat file > output.md",
        "cat file && touch src/file.py",
        "python -c 'print(1)'",
        "rg needle",
        "rg --no-config --pre shell needle",
        "git diff",
        "git --no-pager status --short",
        "git --no-pager diff --output=notes.md",
        "gh pr view 7 --repo owner/repo --web",
        "gh pr diff 7 --repo owner/repo --output file",
        "gh api --method POST repos/owner/repo/pulls/7/reviews",
        "pip install package",
        "npm update",
        "cat $(touch file)",
        "cat $TARGET",
        "cat `touch file`",
        "env cat file",
        "cat file; pwd",
        "rg --no-config *",
        "git --no-pager diff --no-ext-diff --no-textconv *",
        "cat ~/file",
        "cat {one,two}",
    ],
)
def test_opaque_or_mutating_commands_stay_gated(command: str) -> None:
    """Reject wrappers, shell effects, package commands and unsafe read options.

    Args:
        command: Mutation-capable or unproven command.
    """
    assert not boundary.read_command(command, "claude-code")


@pytest.mark.parametrize(
    "query,fields",
    [
        (boundary.THREAD_QUERY, ["-f", "owner=owner", "-f", "repo=repo", "-F", "number=7"]),
        (boundary.COMMENTS_QUERY, ["-f", "id=PRRT_example", "-f", "cursor=opaque=="]),
    ],
)
def test_graphql_is_limited_to_literal_review_templates(query: str, fields: list[str]) -> None:
    """Permit scalar pagination without allowing arbitrary GraphQL programs.

    Args:
        query: Fixed read-only template.
        fields: Its validated variable arguments.
    """
    # Canonical quoting keeps GraphQL dollars literal instead of shell expansion.
    words = ["gh", "api", "graphql", "-f", "query=" + query, *fields]
    assert boundary.read_command(shlex.join(words), "claude-code")
    assert not boundary.gh_read([*words, "-f", "extra=untrusted"])
    assert not boundary.gh_read(["gh", "api", "graphql", "-f", "query=mutation{delete}", *fields])


def test_malformed_inputs_do_not_claim_a_free_effect() -> None:
    """Return false rather than infer a file or provider effect from malformed data."""
    assert not boundary.task_free({"tool_name": "Read", "tool_input": "opaque"}, "codex")
    assert boundary.edit_targets("Write", {"file_path": "note.md"}) is None
    assert boundary.edit_targets("apply_patch", {"command": "opaque"}) is None
    # A header without its ": " separator is malformed, not an empty target.
    for header in ("*** Add File", "*** Update File", "*** Delete File", "*** Move to"):
        bare = f"*** Begin Patch\n*** Update File: a.md\n{header}\n*** End Patch"
        assert boundary.edit_targets("apply_patch", {"command": bare}) is None
    assert not boundary.read_command(None, "codex")
    assert not boundary.read_command("cat '", "codex")


def test_target_roles_follow_the_project_repository_directory(tmp_path) -> None:
    """Gate every in-repository target and free every target outside the repositories.

    Args:
        tmp_path: Disposable parent holding a stand-in root and an external directory.
    """
    # Extension and purpose no longer matter inside a project code repository.
    root = tmp_path / "repo"
    root.mkdir()
    roots = boundary.root_test([root])
    for target in ("notes/x.md", "README.md", "docs/draft.md", "src/x.py"):
        assert boundary.target_role(str(root), target, roots) == "protected"
    # Control-store paths keep their own role inside the repository.
    for target in (".task/x", ".git/x", ".claude/x"):
        assert boundary.target_role(str(root), target, roots) == "control"
    # Absolute and traversing targets outside the repository are external.
    assert boundary.target_role(str(root), str(tmp_path / "vault" / "x.md"), roots) == "external"
    assert boundary.target_role(str(root), "../outside.md", roots) == "external"
    # Malformed targets cannot be located and remain gated.
    assert boundary.target_role(str(root), None, roots) == "protected"
    assert boundary.target_role(str(root), "", roots) == "protected"


def test_edit_roles_free_sessions_outside_repositories_and_keep_batch_roles(tmp_path) -> None:
    """Classify every patch target and treat a non-repository session as all external.

    Args:
        tmp_path: Disposable non-Git directory standing in for the session root.
    """
    # A mixed patch keeps one role per target.
    root = tmp_path / "repo"
    root.mkdir()
    observed = {
        "cwd": str(root),
        "tool_name": "apply_patch",
        "tool_input": {
            "command": (
                f"*** Begin Patch\n*** Add File: {tmp_path / 'external.md'}\n+prose\n"
                "*** Add File: src/app.py\n+source\n*** End Patch"
            )
        },
    }
    repository = core.Repository(root, root / ".git", [root])
    assert boundary.edit_roles(observed, repository) == ["external", "protected"]
    assert not boundary.task_free(observed, "codex", roles=["external", "protected"])
    assert boundary.edit_roles({"tool_name": "unknown", "tool_input": {}}, repository) == []
    # Without a project repository every target, even one beside the session, is external.
    assert boundary.edit_roles(observed, None) == ["external", "external"]
    assert boundary.task_free(observed, "codex", roles=["external", "external"])
    # Uncomputed roles keep the edit gated.
    assert not boundary.task_free(observed, "codex")


def test_legacy_shell_reads_and_unverified_file_aliases_remain_gated() -> None:
    """Do not admit a free transport whose eventual process handle would be untracked."""
    # Legacy execution can yield a handle, so its existing readiness/async route owns it.
    observed = {
        "tool_name": "exec_command",
        "tool_input": {"cmd": "pwd", "login": False, "shell": "/bin/sh"},
    }
    assert not boundary.task_free(observed, "codex")
    assert boundary.task_free({"tool_name": "Bash", "tool_input": {"command": "pwd"}}, "codex")
    assert boundary.edit_targets("write_file", {"path": "note.md", "content": "prose"}) is None


def test_control_alias_inside_the_repository_keeps_the_control_role(tmp_path) -> None:
    """Detect a control store reached through an ordinary in-repository link.

    Args:
        tmp_path: Disposable parent holding the stand-in repository.
    """
    from tests.platform_support import link_directory

    # The lexical spelling hides the control name; the resolved spelling exposes it.
    root = tmp_path / "repo"
    (root / ".task").mkdir(parents=True)
    link_directory(root / "notes", root / ".task")
    is_root = boundary.root_test([root])
    assert boundary.target_role(str(root), "notes/state.json", is_root) == "control"
    # A control name aliasing the root itself still exposes the control name above it.
    link_directory(root / ".codex", root)
    assert boundary.target_role(str(root), ".codex/hooks.json", is_root) == "control"
    assert boundary.target_role(str(root), ".codex/notes/x.md", is_root) == "control"


class ReadDirectory:
    """Model already-open read-only directories; creating opens are deliberately unsupported."""

    def __init__(self, entries: dict) -> None:
        """Retain one directory's immutable test entries.

        Args:
            entries: Directory names mapped to child entries or JSON values.
        """
        self.entries = entries
        self.identity = [1, 2]

    def __enter__(self):
        """Expose this existing directory without creating storage.

        Returns:
            The same read-only directory handle.
        """
        return self

    def __exit__(self, *_args) -> None:
        """Release the modeled handle without writing state.

        Args:
            *_args: Ignored context-manager exception details.
        """

    def exists(self, name: str) -> bool:
        """Inspect only an existing entry name.

        Args:
            name: Direct directory entry.

        Returns:
            Whether the entry exists.
        """
        return name in self.entries

    def child(self, name: str, create: bool = False):
        """Open one existing child and reject any attempted collection creation.

        Args:
            name: Existing child directory name.
            create: Must remain false for an existing-state lookup.

        Returns:
            Another read-only directory handle.

        Raises:
            KeyError: If the required existing child is absent.
        """
        assert create is False
        return ReadDirectory(self.entries[name])

    def close(self) -> None:
        """Release the modeled existing descriptor without state changes."""

    def lock(self, name: str = "lock", *, create: bool = True):
        """Model only a persistent existing lock without creation.

        Args:
            name: Existing lock entry.
            create: Must be false for the lookup.

        Returns:
            This existing directory as a bounded lock context.
        """
        assert create is False
        assert name in self.entries
        return self

    def json(self, name: str):
        """Read stored JSON without manufacturing a default.

        Args:
            name: Existing JSON file name.

        Returns:
            The independent test's stored JSON value.

        Raises:
            KeyError: If the requested JSON entry is absent.
        """
        return self.entries[name]


@pytest.fixture
def admission_store(tmp_path, monkeypatch):
    """Provide a read-only store double matched by the real integration controls.

    Args:
        tmp_path: Canonical checkout path replacing external Git discovery.
        monkeypatch: Guard every creating or executing lifecycle boundary.

    Returns:
        Tree, native call, participant key and committed state for focused controls.
    """
    # Construct independent identity and committed admission facts.
    from nplet.adapters import common

    root = tmp_path.resolve()
    observed = {
        "cwd": str(root),
        "session_id": "parent",
        "tool_name": "Write",
        "tool_use_id": "owned",
        "tool_input": {},
    }
    key = common.core.participant_key({"host": "codex", "session_id": "parent"})
    registration = {
        "repo_id": "repo",
        "common": str(root / ".git"),
        "common_identity": [1, 2],
        "main": str(root),
        "main_identity": [1, 2],
    }
    state = {
        "participants": {
            key: {"generation": 1, "status": "ready", "pending": {"owned": {"status": "pending"}}}
        },
        "requests": {},
    }
    tree = {
        ".task": {
            ".repository.json": registration,
            ".bindings": {
                key + ".json": {"issue_id": "TEST-1", "binding_generation": 1},
                key + ".lock": None,
            },
            ".control": {
                "repository.json": registration,
                "issues": {"TEST-1": {"state.json": state, "lock": None}},
            },
        }
    }
    # Creating opens or lifecycle transactions fail instead of passing unnoticed.
    monkeypatch.setattr(
        common.core, "repository", lambda _path: common.core.Repository(root, root / ".git", [root])
    )
    monkeypatch.setattr(common.core, "fs_identity", lambda _path: [1, 2])
    monkeypatch.setattr(common.core.Directory, "absolute", lambda _path: ReadDirectory(tree))
    monkeypatch.setattr(common.core, "execute", lambda *_args: pytest.fail("lifecycle mutation"))
    return tree, carrier.carry_repository(observed), key, state


@pytest.mark.parametrize("missing", ["bindings", "binding"])
@pytest.mark.parametrize("field", ["common", "common_identity", "main"])
def test_unbound_registered_completion_rejects_foreign_identity(
    admission_store, missing: str, field: str
) -> None:
    """Validate present repository registration before treating missing admission as absence.

    Args:
        admission_store: Existing read-only directory and registration fixture.
        missing: Absent binding collection or participant binding.
        field: Foreign repository identity field.
    """
    from nplet.adapters import common

    # Absence of a binding cannot hide a foreign or replaced repository registration.
    tree, observed, key, _state = admission_store
    task = tree[".task"]
    if missing == "bindings":
        task.pop(".bindings")
    else:
        task[".bindings"].pop(key + ".json")
    task[".repository.json"][field] = [9, 9] if field == "common_identity" else "/foreign"
    with pytest.raises(common.core.WorkspaceError) as failure:
        common.existing_admission(observed, "codex")
    assert failure.value.code == "REPOSITORY_MISMATCH"


def test_existing_admission_distinguishes_absence_and_corruption(admission_store) -> None:
    """Probe registration-only and assignment-only state without manufacturing directories.

    Args:
        admission_store: Independent existing directory and JSON fixture.
    """
    from nplet.adapters import common

    # Absent task, registration-only and unbound stores all have no admission.
    tree, observed, key, _state = admission_store
    task = tree.pop(".task")
    assert common.existing_admission(observed, "codex") is None
    tree[".task"] = task
    bindings = task.pop(".bindings")
    assert common.existing_admission(observed, "codex") is None
    task[".bindings"] = bindings
    binding = bindings.pop(key + ".json")
    assert common.existing_admission(observed, "codex") is None
    # A legitimate unregistered Task marker is not a registered participant.
    registration = task.pop(".repository.json")
    control = task.pop(".control")
    assert common.existing_admission(observed, "codex") is None
    task.pop(".bindings")
    assert common.existing_admission(observed, "codex") is None
    # Durable binding or control state without registration must fail closed.
    task[".bindings"] = {key + ".json": binding, key + ".lock": None}
    with pytest.raises(common.core.WorkspaceError) as captured:
        common.existing_admission(observed, "codex")
    assert captured.value.code == "RECOVERY_REQUIRED"
    task[".control"] = control
    with pytest.raises(common.core.WorkspaceError) as captured:
        common.existing_admission(observed, "codex")
    assert captured.value.code == "RECOVERY_REQUIRED"
    # Existing transactional state is never silently classified as absent.
    task[".repository.json"] = registration
    control["issues"]["TEST-1"]["transaction.json"] = {}
    with pytest.raises(common.core.WorkspaceError) as captured:
        common.existing_admission(observed, "codex")
    assert captured.value.code == "RECOVERY_REQUIRED"
    assert control["issues"]["TEST-1"]["transaction.json"] == {}


def test_completion_binding_disappearing_before_lock_is_not_recreated(
    admission_store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Treat a binding gone after preflight as no admission without changing pending work.

    Args:
        admission_store: Existing registered participant with one pending operation.
        monkeypatch: Model the binding read after acquisition of its persistent lock.
    """
    from nplet.adapters import common

    # The core must re-read the binding under lock rather than reuse its preflight contents.
    tree, observed, key, state = admission_store
    monkeypatch.setattr(common.core.Store, "binding", lambda _store: None)
    assert common.existing_admission(observed, "codex") is None
    assert state["participants"][key]["pending"] == {"owned": {"status": "pending"}}
    assert set(tree[".task"][".bindings"]) == {key + ".json", key + ".lock"}


def test_existing_admission_correlates_pending_replay_and_local_handles(admission_store) -> None:
    """Select only this participant's exact call, replay receipt or unique process handle.

    Args:
        admission_store: Read-only directory fixture with one admitted call.
    """
    from nplet.adapters import common

    # Native call identity selects existing work regardless of mutable tool target arguments.
    _tree, observed, key, state = admission_store
    admitted = common.existing_admission(observed, "codex")
    assert admitted["issue_id"] == "TEST-1"
    assert common.existing_admission({**observed, "tool_use_id": "foreign"}, "codex") is None
    # A settled call replays only against its exact stored start-request digest.
    start = {
        **admitted,
        "operation": "tool-start",
        "request_id": "pre:owned",
        "tool_id": "owned",
        "poll_handle": None,
    }
    state["requests"][common.core.sha(b"pre:owned")] = {
        "digest": common.core.sha(common.core.canonical(start))
    }
    pending = state["participants"][key]["pending"]
    pending.clear()
    assert common.existing_admission(observed, "codex") is not None
    assert common.existing_admission(observed, "claude-code") is None
    # Polls with no prehook still require a single participant-local process handle.
    pending["original"] = {"status": "unknown", "handle": "process"}
    poll = {
        **observed,
        "tool_name": "write_stdin",
        "tool_use_id": "poll",
        "tool_input": {"session_id": "process"},
    }
    assert common.existing_admission(poll, "codex") is not None
    pending["other"] = {"status": "unknown", "handle": "process"}
    with pytest.raises(common.core.WorkspaceError) as captured:
        common.existing_admission(poll, "codex")
    assert captured.value.code == "UNKNOWN_OPERATION"
    with pytest.raises(common.core.WorkspaceError) as captured:
        common.existing_admission({**poll, "tool_input": {"session_id": True}}, "codex")
    assert captured.value.code == "ASYNC_HANDLE_CONFLICT"


def test_selected_lookup_requires_exact_assignment_and_native_reservation(
    admission_store, monkeypatch
) -> None:
    """Independent calls cannot consume startup selection or its recorded native call ID.

    Args:
        admission_store: Read-only native session fixture.
        monkeypatch: Isolate only the already-tested lookup-marker presence check.
    """
    from nplet.adapters import common

    # Store an exact assignment and one reserved provider call independently of task state.
    tree, observed, key, _state = admission_store
    bindings = tree[".task"][".bindings"]
    bindings[key + ".assignment.json"] = {"issue_id": "TEST-1"}
    bindings[key + ".lookup-required.json"] = {"issue_id": "TEST-1"}
    bindings[key + ".lookup.json"] = {"id": "TEST-1", "tool_id": "owned"}
    monkeypatch.setattr(common, "lookup_required", lambda *_args: True)
    selected = {**observed, "tool_input": {"id": "TEST-1"}}
    assert common.selected_lookup(selected, "codex")
    assert common.selected_lookup(selected, "codex", complete=True)
    assert not common.selected_lookup(observed, "codex")
    assert not common.selected_lookup(
        {**selected, "tool_use_id": "foreign"}, "codex", complete=True
    )
    bindings.pop(key + ".lookup.json")
    assert not common.selected_lookup(selected, "codex", complete=True)
    # Cursor's specific callback validates serialized arguments without inventing a call ID.
    assert (
        common.selected_lookup(
            {**selected, "hook_event_name": "beforeMCPExecution", "tool_input": '{"id":"TEST-1"}'},
            "codex",
        )
        is False
    )
    cursor_key = common.core.participant_key({"host": "cursor", "session_id": "parent"})
    bindings[cursor_key + ".assignment.json"] = {"issue_id": "TEST-1"}
    bindings[cursor_key + ".lookup-required.json"] = {"issue_id": "TEST-1"}
    assert common.selected_lookup(
        {**selected, "hook_event_name": "beforeMCPExecution", "tool_input": '{"id":"TEST-1"}'},
        "cursor",
    )
    assert not common.selected_lookup(
        {**selected, "hook_event_name": "beforeMCPExecution", "tool_input": {}}, "cursor"
    )
    # Corrupt marker/assignment agreement is a distinct failure, never free startup.
    bindings[key + ".lookup-required.json"] = {"issue_id": "TEST-2"}
    with pytest.raises(common.core.WorkspaceError) as captured:
        common.selected_lookup(selected, "codex")
    assert captured.value.code == "BINDING_CONFLICT"


def test_existing_admission_does_not_reuse_another_hosts_start(admission_store) -> None:
    """A similarly named native call under another host cannot reuse a Codex receipt.

    Args:
        admission_store: Read-only store with independently keyed participants.
    """
    from nplet.adapters import common

    # A bound Claude participant has no pending work despite the Codex participant's call.
    tree, observed, _key, state = admission_store
    claude_key = common.core.participant_key({"host": "claude-code", "session_id": "parent"})
    tree[".task"][".bindings"][claude_key + ".json"] = {
        "issue_id": "TEST-1",
        "binding_generation": 1,
    }
    tree[".task"][".bindings"][claude_key + ".lock"] = None
    state["participants"][claude_key] = {"generation": 1, "status": "ready", "pending": {}}
    assert common.existing_admission(observed, "claude-code") is None


def test_file_edit_shapes_enumerate_all_targets() -> None:
    """Accept deterministic edit fields and reject unrecognized patch headers."""
    assert boundary.edit_targets("Write", {"file_path": "note.md", "content": "prose"}) == [
        "note.md"
    ]
    assert boundary.edit_targets(
        "Edit", {"file_path": "note.md", "old_string": "old", "new_string": "new"}
    ) == ["note.md"]
    assert (
        boundary.edit_targets(
            "apply_patch", {"command": "*** Begin Patch\n*** Surprise: note.md\n*** End Patch"}
        )
        is None
    )


def test_update_patch_end_of_file_marker_preserves_exact_targets() -> None:
    """Recognize the valid update marker without ignoring unknown patch headers."""
    # The native patch parser accepts this exact end-of-file update syntax.
    patch = (
        "*** Begin Patch\n*** Update File: notes/note.md\n@@\n-old\n+new\n"
        "*** End of File\n*** End Patch"
    )
    assert boundary.edit_targets("apply_patch", {"command": patch}) == ["notes/note.md"]
    assert (
        boundary.edit_targets(
            "apply_patch", {"command": patch.replace("*** End of File", "*** Unknown Header")}
        )
        is None
    )


@pytest.mark.parametrize(
    "words",
    [
        ["gh", "pr", "view", "0", "--repo", "owner/repo"],
        ["gh", "pr", "view", "7", "--web", "owner/repo"],
        ["gh", "api", "graphql", "-f"],
        ["gh", "api", "graphql", "-f", "missing-equals"],
        ["gh", "api", "graphql", "-f", "query=one", "-f", "query=two"],
        ["gh", "api", "graphql", "-f", "number=7"],
    ],
)
def test_review_argument_failures_remain_protected(words: list[str]) -> None:
    """Reject incomplete, duplicate, redirected or ill-typed review arguments.

    Args:
        words: A mutation-capable or malformed review argument vector.
    """
    assert not boundary.gh_read(words)


def test_literal_shell_data_cannot_become_expansion() -> None:
    """Treat a literal dollar argument as unproven unless it is a fixed GraphQL template."""
    assert not boundary.read_command("", "codex")
    assert not boundary.read_command("cat '$TARGET'", "claude-code")
    assert not boundary.read_command("cat '$TARGET'", "codex")
    assert not boundary.read_command("cat '|'", "codex")


def test_validated_provider_planning_and_native_reads_have_no_task_effect() -> None:
    """Cover each parent effect family after its separate native validation."""
    assert boundary.task_free(
        {"tool_name": "MCP:save_issue", "tool_input": {}}, "cursor", "save_issue"
    )
    assert boundary.task_free(
        {
            "tool_name": "mcp__codex_apps__github_list_pull_request_reviews",
            "tool_input": {"link_id": "github", "repo_full_name": "owner/repo", "pr_number": 7},
        },
        "codex",
    )
    assert boundary.task_free({"tool_name": "update_plan", "tool_input": {}}, "codex")
    assert boundary.task_free({"tool_name": "Read", "tool_input": {}}, "claude-code")
    assert boundary.task_free(
        {"tool_name": "Bash", "tool_input": {"command": "pwd"}}, "claude-code"
    )
    # Cursor's native Shell is normalized to the shared command-only effect after validation.
    assert boundary.task_free({"tool_name": "Bash", "tool_input": {"command": "pwd"}}, "cursor")
    assert not boundary.task_free({"tool_name": "Shell", "tool_input": {"command": "pwd"}}, "codex")
    assert not boundary.task_free(
        {"tool_name": "Shell", "tool_input": {"command": "pwd", "working_directory": "other"}},
        "cursor",
    )
    # Normalization does not grant another host the Claude-only review transport.
    review = {"tool_name": "Bash", "tool_input": {"command": "gh pr view 7 --repo owner/repo"}}
    assert boundary.task_free(review, "claude-code")
    assert not boundary.task_free(review, "cursor")
    assert not boundary.task_free(review, "codex")


@pytest.mark.parametrize(
    "tool,args",
    [("mcp__ccd_turn__update_plan", {"items": []}), ("mcp__ccd_turn__wrap_up", {"recap": "done"})],
)
def test_claude_planning_provenance_and_no_admission(tool: str, args: dict, monkeypatch) -> None:
    """Admit SDK parent planning only; no helper result creates implementation authority.

    Args:
        tool: Exact observed planning helper.
        args: Its finite UI argument shape.
        monkeypatch: Reject any attempted lifecycle admission or mutation.
    """
    from nplet.adapters import common

    # Real native validation checks name, provenance, arguments and parent identity.
    observed = {
        "tool_name": tool,
        "tool_use_id": "plan",
        "tool_input": args,
        "mcp_server": {"name": "ccd_turn", "source": "sdk"},
        "repository": None,
    }
    assert common.native_tool(observed, "claude-code") == tool
    for changed in [
        {**observed, "child": True},
        {**observed, "mcp_server": {"name": "ccd_turn", "source": "user"}},
    ]:
        with pytest.raises(common.core.WorkspaceError) as captured:
            common.native_tool(changed, "claude-code")
        assert captured.value.code == "HOST_UNSUPPORTED_PROVIDER"
    # Neither pre nor a free post result calls the lifecycle or grants readiness.
    monkeypatch.setattr(common, "native_bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(common, "existing_admission", lambda *_args: None)
    monkeypatch.setattr(common.core, "execute", lambda *_args: pytest.fail("planning admission"))
    assert common.native_pre(observed, "claude-code") is None
    assert (
        common.native_post({**observed, "tool_response": {}}, "claude-code", failed=False) is None
    )


def test_child_handback_keeps_existing_readiness_requirement(monkeypatch) -> None:
    """A child handback retains readiness but needs no pending-operation settlement.

    Args:
        monkeypatch: Isolate existing readiness without granting a parent helper route.
    """
    from nplet.adapters import common

    observed = {
        "tool_name": "SubagentHandback",
        "tool_use_id": "report",
        "tool_input": {},
        "child": True,
        "repository": None,
    }
    calls = []
    monkeypatch.setattr(common, "native_bootstrap", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(common, "lookup_required", lambda *_args: False)
    monkeypatch.setattr(common, "request_for", lambda *_args: {"operation": "ready"})
    monkeypatch.setattr(
        common.core,
        "execute",
        lambda request: calls.append(request["operation"]) or {"ok": True, "code": "OK"},
    )
    assert common.native_pre(observed, "claude-code") is None
    assert calls == ["ready"]


def test_unreadable_roots_stay_lexically_protected_and_unresolvable_targets_stay_protected(
    tmp_path, monkeypatch
) -> None:
    """Protect a root that cannot be read by its path and gate an unresolvable target.

    Args:
        tmp_path: Disposable parent holding one readable stand-in root.
        monkeypatch: Replace only path resolution at its filesystem boundary.
    """
    root = tmp_path / "repo"
    root.mkdir()
    missing = tmp_path / "missing"
    # A missing root stays a root by its lexical path; a readable root stays a root.
    is_root = boundary.root_test([missing, root])
    assert is_root(missing)
    assert is_root(root)
    # An unreadable candidate that matches no root path is not a root.
    assert not boundary.root_test([missing])(tmp_path / "other")
    # A target under the missing root path is still protected.
    assert boundary.target_role(str(root), str(missing / "src/app.py"), is_root) == "protected"
    # A target that cannot be resolved cannot be proven external.
    is_root = boundary.root_test([root])
    monkeypatch.setattr(
        boundary.Path, "resolve", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("gone"))
    )
    assert boundary.target_role(str(root), "notes/x.md", is_root) == "protected"
