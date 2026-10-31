"""Classify finite parent tool effects without creating task or admission state."""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import NamedTuple

from nplet.adapters import repository_exemptions, repository_location
from nplet.adapters.file_content import ABSENT as ABSENT
from nplet.adapters.file_content import BINARY as BINARY
from nplet.adapters.file_content import TEXT as TEXT
from nplet.adapters.file_content import UNKNOWN as UNKNOWN
from nplet.adapters.file_content import Budget as Budget
from nplet.adapters.file_content import classify as classify
from nplet.lifecycle import task_workspace as core

LINEAR_OPERATIONS = frozenset(
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
GITHUB_READS = frozenset(
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
CONTROL_PARTS = frozenset({".task", ".git", ".codex", ".claude", ".cursor", ".agents"})
GH_FIELDS = frozenset(
    {
        "number",
        "title",
        "body",
        "url",
        "state",
        "isDraft",
        "baseRefName",
        "baseRefOid",
        "headRefName",
        "headRefOid",
        "author",
        "files",
        "comments",
        "reviews",
        "latestReviews",
        "reviewDecision",
        "statusCheckRollup",
    }
)
THREAD_QUERY = (
    "query($owner:String!,$repo:String!,$number:Int!,$cursor:String){repository(owner:$own"
    "er,name:$repo){pullRequest(number:$number){reviewThreads(first:100,after:$cursor){nod"
    "es{id isResolved isOutdated path line comments(first:100){nodes{id body url author{lo"
    "gin}} pageInfo{hasNextPage endCursor}}} pageInfo{hasNextPage endCursor}}}}}"
)
COMMENTS_QUERY = (
    "query($id:ID!,$cursor:String){node(id:$id){... on PullRequestReviewThread{id comments"
    "(first:100,after:$cursor){nodes{id body url author{login}} pageInfo{hasNextPage endCu"
    "rsor}}}}}"
)
# Patch headers mapped to the operation they apply to their target.
PATCH_OPERATIONS = {
    "*** Add File": "create",
    "*** Update File": "write",
    "*** Delete File": "delete",
}


def _stat(path: Path) -> os.stat_result | None:
    """Read one path's status without failing on an unreadable path.

    Args:
        path: Path to read, following links.

    Returns:
        The status, or None when the path cannot be read.
    """
    # An unreadable path only loses its identity, never its lexical protection.
    try:
        return path.stat()
    except OSError:
        return None


def root_test(roots: list[Path]) -> Callable[[Path], bool]:
    """Build a predicate that recognizes a worktree root by path or filesystem identity.

    Args:
        roots: The session repository's worktree roots.

    Returns:
        A predicate that is true for a root's normalized lexical path, including a listed
        root missing on disk, and for any alias spelling of a readable root.
    """
    # Collect lexical paths and readable identities once per edit.
    paths = {Path(os.path.normpath(root)) for root in roots}
    identities = {(info.st_dev, info.st_ino) for root in roots if (info := _stat(root))}

    def is_root(path: Path) -> bool:
        """Report whether one path is a worktree root.

        Args:
            path: Absolute candidate path.

        Returns:
            Whether the path equals a root path or shares a readable root identity.
        """
        # A lexical match needs no filesystem read; otherwise compare identities.
        if path in paths:
            return True
        info = _stat(path)
        return info is not None and (info.st_dev, info.st_ino) in identities

    return is_root


def target_role(cwd: str, value: object, is_root: Callable[[Path], bool]) -> str:
    """Classify one edit target by whether it lies inside a project code repository.

    Args:
        cwd: Native session working directory that relative targets are joined to.
        value: Native target path.
        is_root: Worktree root predicate from ``root_test``.

    Returns:
        ``external`` outside every root, ``control`` for a control-store path inside a
        root, and ``protected`` for any other in-root path or an unresolvable target.
    """
    # Malformed targets cannot be located and stay readiness-gated.
    if not isinstance(value, str) or not value or "\x00" in value:
        return "protected"
    try:
        # Compare the lexical and link-resolved spellings so neither alias hides a root.
        absolute = Path(cwd, value)
        spellings = (Path(os.path.normpath(absolute)), absolute.resolve(strict=False))
        inside = False
        for spelling in spellings:
            # The outermost matching root sees every part below any inner root.
            anchor = next(filter(is_root, reversed((spelling, *spelling.parents))), None)
            if anchor is None:
                continue
            inside = True
            # A control-store name below the root denies in either spelling.
            below = spelling.relative_to(anchor).parts
            if CONTROL_PARTS.intersection(part.lower() for part in below):
                return "control"
    # An unresolvable target cannot be proven external.
    except (OSError, ValueError, RuntimeError):
        return "protected"
    return "protected" if inside else "external"


# Every call to these tools must have a known edit shape; an unknown one is denied.
EDIT_TOOLS = frozenset({"Write", "Edit", "NotebookEdit", "apply_patch"})


def edit_records(tool: str, args: core.JSONObject) -> list[tuple[str, str | None]] | None:
    """Parse supported deterministic edit shapes into ordered target operations.

    Args:
        tool: Native file tool name.
        args: Structured native input.

    Returns:
        One ``(target, operation)`` pair per target, or None for an unknown or
        malformed shape. Write, Edit and NotebookEdit ``write`` their target; a patch adds
        ``create``, ``write`` or ``delete``, and a move splits its update into
        ``move-from`` and ``move-to``. A ``Move to`` not directly after ``Update
        File`` has no operation.
    """
    # Native file edits expose exactly one target, required text fields and optional fields.
    shapes = {
        "Write": ("file_path", {"content"}, set()),
        "Edit": ("file_path", {"old_string", "new_string"}, {"replace_all"}),
        "NotebookEdit": ("notebook_path", {"new_source"}, {"cell_id", "cell_type", "edit_mode"}),
    }
    if tool in shapes:
        key, required, optional = shapes[tool]
        strings = {key, *required}
        if strings <= set(args) <= strings | optional and all(
            isinstance(args[field], str) for field in strings
        ):
            return [(args[key], "write")]
        return None
    # A patch header enumerates all affected files, including both sides of moves.
    if tool == "apply_patch" and set(args) == {"command"} and isinstance(args["command"], str):
        lines = args["command"].splitlines()
        if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
            return None
        records: list[tuple[str, str | None]] = []
        for line in lines[1:-1]:
            # A header needs its ": " separator; a bare name is malformed.
            header, colon, target = line.partition(": ")
            if header == "*** Move to" and colon:
                moved = records[-1:] and records[-1][1] == "write"
                if moved:
                    records[-1] = (records[-1][0], "move-from")
                records.append((target, "move-to" if moved else None))
            elif header in PATCH_OPERATIONS and colon:
                records.append((target, PATCH_OPERATIONS[header]))
            elif line.startswith("*** ") and line != "*** End of File":
                return None
        return records or None
    return None


def edit_targets(tool: str, args: core.JSONObject) -> list[str] | None:
    """Extract every target from supported deterministic edit shapes.

    Args:
        tool: Native file tool name.
        args: Structured native input.

    Returns:
        All targets, or None for an unknown or malformed shape.
    """
    records = edit_records(tool, args)
    return None if records is None else [target for target, _ in records]


def github_read(tool: str, args: core.JSONObject) -> bool:
    """Recognize the exact Codex review connector names and finite field contracts.

    Args:
        tool: Native tool identity.
        args: Structured tool input.

    Returns:
        Whether this is a declared read operation with its exact field shape.
    """
    # Normalize only the two observed namespace spellings.
    operation = next(
        (
            tool[len(p) :]
            for p in ("mcp__codex_apps__github__", "mcp__codex_apps__github_")
            if tool.startswith(p)
        ),
        None,
    )
    if operation not in GITHUB_READS:
        return False
    repo = "repository_full_name" if operation == "get_pr_info" else "repo_full_name"
    required = {"link_id", repo, "pr_number"} | (
        {"path"} if operation == "fetch_pr_file_patch" else set()
    )
    allowed = required | ({"format"} if operation == "get_pr_diff" else set())
    return (
        required <= set(args) <= allowed
        and all(isinstance(args[k], str) and bool(args[k]) for k in required - {"pr_number"})
        and type(args.get("pr_number")) in {int, float}
        and args["pr_number"] > 0
        and args.get("format", "diff") in {"diff", "patch"}
    )


def gh_read(words: list[str]) -> bool:
    """Validate the finite GitHub CLI review argument vectors.

    Args:
        words: One parsed foreground command.

    Returns:
        Whether metadata, diff, comments, reviews or a fixed thread query is read.
    """
    # PR reads require an explicit positive number and repository.
    component = r"[A-Za-z0-9_.-]+"
    repo_pattern = component + "/" + component
    if len(words) >= 6 and words[:2] == ["gh", "pr"] and words[2] in {"view", "diff"}:
        if (
            not re.fullmatch(r"[1-9][0-9]*", words[3])
            or words[4] != "--repo"
            or not re.fullmatch(repo_pattern, words[5])
        ):
            return False
        rest = words[6:]
        if words[2] == "view":
            return rest in ([], ["--comments"]) or (
                len(rest) == 2
                and rest[0] == "--json"
                and bool(rest[1])
                and set(rest[1].split(",")) <= GH_FIELDS
            )
        return rest in (
            [],
            ["--name-only"],
            ["--patch"],
            ["--color", "never"],
            ["--name-only", "--color", "never"],
            ["--patch", "--color", "never"],
        )
    # REST reads have a fixed endpoint grammar and no body/header/output options.
    if words[:4] == ["gh", "api", "--method", "GET"] and len(words) in {5, 6}:
        return bool(
            re.fullmatch(
                r"repos/"
                + repo_pattern
                + r"/(?:pulls/[1-9][0-9]*/(?:files|comments|reviews)|issues/[1-9][0-9]*/comments)",
                words[4],
            )
        ) and (len(words) == 5 or words[5] == "--paginate")
    # GraphQL admits only two literal read templates and scalar variables.
    if words[:3] != ["gh", "api", "graphql"]:
        return False
    fields: dict[str, str] = {}
    rest = words[3:]
    if len(rest) % 2:
        return False
    for flag, field in zip(rest[::2], rest[1::2], strict=True):
        key, sep, value = field.partition("=")
        if not sep or key in fields or flag != ("-F" if key == "number" else "-f"):
            return False
        fields[key] = value
    query = fields.pop("query", None)
    expected = (
        {"owner", "repo", "number"}
        if query == THREAD_QUERY
        else {"id"}
        if query == COMMENTS_QUERY
        else set()
    )
    if not expected or not expected <= fields.keys() <= expected | {"cursor"}:
        return False
    return all(
        re.fullmatch(r"[1-9][0-9]*" if key == "number" else r"[A-Za-z0-9_+=/.-]+", value)
        is not None
        and not value.startswith("@")
        for key, value in fields.items()
    )


def read_command(command: object, host: str) -> bool:
    """Admit one finite read command without shell composition or opaque wrappers.

    Args:
        command: Native shell command text.
        host: Validated host identity; gh review is the Claude fallback.

    Returns:
        Whether the complete command has a known read effect.
    """
    # Reject shell expansion/composition before splitting; quoted GraphQL dollars are data.
    if not isinstance(command, str) or any(c in command for c in ("`", "\n", "\r", "$(", "${")):
        return False
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        words = list(lexer)
    except ValueError:
        return False
    if shlex.join(words) != command:
        return False
    if not words or any(w in {";", "&&", "&", "|", "||", "<", ">", ">>"} for w in words):
        return False
    # Dollar expansion is allowed only inside a literal, canonically quoted GraphQL query.
    if "$" in command and (host != "claude-code" or not gh_read(words)):
        return False
    if words == ["pwd"]:
        return True
    if words[0] == "cat":
        paths = words[2:] if words[1:2] == ["--"] else words[1:]
        return bool(paths) and all(p and not p.startswith("-") and p != "/dev/stdin" for p in paths)
    # Disable ripgrep configuration; all admitted options have finite read-only meanings.
    if words[0] == "rg":
        return "--no-config" in words and all(
            not w.startswith("-")
            or w
            in {
                "--no-config",
                "--files",
                "--hidden",
                "--line-number",
                "-n",
                "-l",
                "--files-with-matches",
                "-i",
                "--ignore-case",
                "--fixed-strings",
                "-F",
                "--",
                "--no-ignore",
            }
            for w in words[1:]
        )
    # Git commands disable pagers and external diff/text conversion where applicable.
    if words[:4] == ["git", "--no-pager", "-c", "core.fsmonitor=false"] and len(words) >= 5:
        operation, args = words[4], words[5:]
        flags = {
            "status": {"--short", "--porcelain", "--branch", "--untracked-files=no"},
            "ls-files": {"--cached", "--others", "--exclude-standard", "--stage"},
            "diff": {
                "--no-ext-diff",
                "--no-textconv",
                "--stat",
                "--name-only",
                "--cached",
                "--staged",
            },
            "show": {"--no-ext-diff", "--no-textconv", "--stat", "--name-only", "--oneline"},
            "log": {"--no-ext-diff", "--no-textconv", "--oneline", "--stat", "--name-only"},
        }
        return (
            operation in flags
            and (
                operation not in {"diff", "show", "log"}
                or {"--no-ext-diff", "--no-textconv"} <= set(args)
            )
            and all(not a.startswith("-") or a == "--" or a in flags[operation] for a in args)
        )
    return host == "claude-code" and gh_read(words)


def task_free(
    event: core.JSONObject,
    host: str,
    provider_operation: str | None = None,
    roles: list[str] | None = None,
) -> bool:
    """Classify a validated parent call; unknown mutation remains readiness-gated.

    Args:
        event: Native envelope whose identity, provenance and child policy passed.
        host: Actual adapter identity.
        provider_operation: Provenance-validated Linear operation, when applicable.
        roles: Edit roles the caller computed for this invocation; None keeps an edit gated.

    Returns:
        Whether the call requires no task lifecycle admission.
    """
    # Child authority remains entirely in existing scoped admission.
    if event.get("child") is True:
        return False
    args, tool = event.get("tool_input"), event.get("tool_name")
    if not isinstance(args, dict) or not isinstance(tool, str):
        return False
    if provider_operation in LINEAR_OPERATIONS:
        return True
    # Codex connector names are actual native provider identities.
    if host == "codex":
        for prefix in ("mcp__codex_apps__linear__", "mcp__codex_apps__linear_"):
            if tool.startswith(prefix) and tool[len(prefix) :] in LINEAR_OPERATIONS:
                return True
        if github_read(tool, args):
            return True
        if tool in {"webrun", "update_plan", "request_user_input", "request_user_input_async"}:
            return True
    # Native read operations cannot modify the inspected paths.
    if tool in {"Read", "Glob", "Grep"}:
        return True
    if tool == "Bash":
        # Native adapters supply a canonical command after validating their transport metadata.
        return set(args) == {"command"} and read_command(args.get("command"), host)
    # Edits are free only when every caller-classified target lies outside the project
    # code repositories; unclassified edits stay gated.
    selected = roles or []
    return bool(selected) and all(role == "external" for role in selected)


def edit_roles(event: core.JSONObject, repository: core.Repository | None) -> list[str]:
    """Classify every deterministic native edit target against the project repositories.

    The gate covers the session's Git repository and its linked worktrees only. A
    separate clone or sibling repository of the same project is gated only by a
    session rooted inside it; multi-repository project identity is deferred.

    Args:
        event: Validated native tool and argument envelope.
        repository: The session's carried project repository; None when the session
            lies outside every repository, so every well-formed target is external.

    Returns:
        Every target role, or an empty list for an unknown edit shape.
    """
    # Unknown edits have no roles; the edit gate denies them.
    targets = edit_targets(event["tool_name"], event["tool_input"])
    if not targets:
        return []
    # One worktree list covers the whole patch or single-file mutation.
    is_root = root_test([] if repository is None else repository.trees)
    return [target_role(event["cwd"], target, is_root) for target in targets]


def edit_locations(
    event: core.JSONObject, repository: core.Repository | None
) -> list[repository_location.Location]:
    """Resolve every deterministic native edit target to repository location evidence.

    Args:
        event: Validated native tool and argument envelope.
        repository: The session's carried project repository; None when the session
            lies outside every repository.

    Returns:
        One location per target in target order, or an empty list for an unknown
        edit shape.
    """
    # Unknown edits have no targets to locate.
    return _locate(event, edit_targets(event["tool_name"], event["tool_input"]) or [], repository)


def _locate(
    event: core.JSONObject, targets: list[str], repository: core.Repository | None
) -> list[repository_location.Location]:
    """Resolve the given targets of one edit, sharing a root predicate and discovery memo.

    Args:
        event: Validated native tool and argument envelope.
        targets: Targets parsed from the event, in order.
        repository: The session's carried project repository, if any.

    Returns:
        One location per target in order.
    """
    # One root predicate and one discovery memo cover every target of the call.
    is_root = root_test([] if repository is None else repository.trees)
    memo: repository_location.Memo = {}
    return [
        repository_location.resolve(event["cwd"], target, repository, is_root, memo)
        for target in targets
    ]


class Effect(NamedTuple):
    """One file effect of a deterministic native edit.

    Attributes:
        op: ``create``, ``write`` (overwrite or append), ``delete``, ``move-from`` or
            ``move-to``.
        location: The target's location evidence from ``edit_locations``.
        before: Content state of the existing file; None for a workspace target, whose
            content is never inspected.
        after: Content state of the output; None for ``delete`` and ``move-from``.
    """

    op: str
    location: repository_location.Location
    before: str | None
    after: str | None


def edit_effects(
    event: core.JSONObject, repository: core.Repository | None, budget: Budget
) -> list[Effect]:
    """Describe every deterministic native edit target as an effect with content states.

    Workspace targets are never content-inspected, so their ``before`` is None. Other
    targets are read at their resolved point; a failed resolution makes ``before``
    unknown. Every output is text, since each supported shape writes text.

    Args:
        event: Validated native tool and argument envelope.
        repository: The session's carried project repository; None when the session
            lies outside every repository.
        budget: The call's inspection budget, shared by every side.

    Returns:
        One effect per target in target order, or an empty list for an unknown or
        malformed edit shape.
    """
    # Unknown or malformed edits, including a misplaced move, have no effects.
    records = edit_records(event["tool_name"], event["tool_input"]) or []
    operations = [op for _, op in records if op]
    if not records or len(operations) != len(records):
        return []
    targets = [target for target, _ in records]
    effects = []
    for op, location in zip(operations, _locate(event, targets, repository), strict=True):
        # The resolved point is last; an invalid target's only point carries its error.
        point = location.points[-1]
        path = None if point.error else point.path
        # Write and NotebookEdit create a target that does not exist yet.
        if (
            event["tool_name"] in {"Write", "NotebookEdit"}
            and path is not None
            and not os.path.lexists(path)
        ):
            op = "create"
        # Read the existing side outside the workspace only.
        before = None if location.workspace else UNKNOWN if path is None else classify(path, budget)
        after = None if op in {"delete", "move-from"} else TEXT
        effects.append(Effect(op, location, before, after))
    return effects


class Decision(NamedTuple):
    """Effect classes of one call, for a workflow guard rather than containment.

    Attributes:
        protected: Effects that need task readiness and scope authority.
        unchecked: Effects whose evidence is incomplete, neither protected nor free.
        free: Effects that need no task.
        policy: Whether some point targets the reserved exemption policy file.
        diagnostic: The exemption policy's bounded error code, if it could not be used.
    """

    protected: list[Effect]
    unchecked: list[Effect]
    free: list[Effect]
    policy: bool
    diagnostic: str | None


def _file_id(path: Path) -> tuple[int, int] | None:
    """Read one path's device and inode, following links.

    Args:
        path: Path to identify.

    Returns:
        The device and inode pair, or None when the path cannot be read.
    """
    info = _stat(path)
    return None if info is None else (info.st_dev, info.st_ino)


def policy_target(effect: Effect, policy: Path) -> bool:
    """Report whether any point of an effect spells the reserved policy file.

    Args:
        effect: Effect to inspect.
        policy: Policy file path.

    Returns:
        Whether a point spells the policy's lexical or real path, is the same file,
        names it case-insensitively in the same directory whether or not that name
        exists, or folds to its spelling while that directory is absent.
    """
    spellings = {os.path.normpath(policy), os.path.realpath(policy)}
    # File identity catches spellings a case-insensitive filesystem treats as equal.
    own, folder, name = _file_id(policy), _file_id(policy.parent), policy.name.casefold()
    folded = {spelling.casefold() for spelling in spellings}
    for point in effect.location.points:
        # An exact spelling or the same existing file is the policy.
        found = _file_id(point.path)
        if os.path.normpath(point.path) in spellings or (found is not None and found == own):
            return True
        # A case-variant name of the policy is reserved whether or not that variant exists.
        if (
            folder is not None
            and point.path.name.casefold() == name
            and _file_id(point.path.parent) == folder
        ) or (
            # Without the policy directory there is no identity, so compare folded spellings.
            folder is None and os.path.normpath(point.path).casefold() in folded
        ):
            return True
    return False


def decide(
    effects: list[Effect],
    exemptions: repository_exemptions.Exemptions,
    policy: Path = repository_exemptions.POLICY,
) -> Decision:
    """Split effects into protected, unchecked and free by their strongest point.

    This is a workflow guard over the evidence the caller gathered; content and
    location may change between inspection and the edit.

    Args:
        effects: Effects from ``edit_effects``.
        exemptions: Loaded vault exemptions; with an error none are exempt.
        policy: Reserved policy file path, which no effect may edit task-free.

    Returns:
        Each effect in exactly one class list, in input order.
    """
    decision = Decision([], [], [], False, exemptions.error)
    for effect in effects:
        # The policy file is reserved whatever its location evidence says.
        reserved = policy_target(effect, policy)
        if reserved:
            decision = decision._replace(policy=True)
        # Workspace effects are protected for every file type.
        if reserved or effect.location.workspace:
            decision.protected.append(effect)
            continue
        # Repository content is protected when any applicable side is text.
        sides = [side for side in (effect.before, effect.after) if side is not None]
        content = 2 if TEXT in sides else 0 if set(sides) <= {BINARY, ABSENT} else 1
        # Rank every point; vault and external points are free, failed ones unchecked.
        ranks = [
            0 if vault else content if point.repository else 1 if point.error else 0
            for point, vault in zip(
                effect.location.points,
                repository_exemptions.exempt(effect.location, exemptions),
                strict=True,
            )
        ]
        # The strongest point decides the effect's class.
        (decision.free, decision.unchecked, decision.protected)[max(ranks, default=1)].append(
            effect
        )
    return decision


def covered(relative: str, granted: list[str]) -> bool:
    """Match one repository-relative POSIX path against granted scope paths.

    Args:
        relative: Path relative to its worktree root.
        granted: Granted entries; ``d/`` covers a subtree and ``./`` everything.

    Returns:
        Whether some entry covers the path, comparing bytes exactly.
    """
    return any(
        entry == "./" or (relative.startswith(entry) if entry.endswith("/") else relative == entry)
        for entry in granted
    )


def _below(path: Path, root: Path | str) -> str | None:
    """Spell a path relative to a root in POSIX form.

    Args:
        path: Real-directory spelling of a point.
        root: Worktree root.

    Returns:
        The relative spelling, ``.`` for the root itself, or None outside it.
    """
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def unauthorized(
    decision: Decision,
    scope: list[core.ScopeEntry],
    session: core.Repository | None,
    memo: repository_location.Memo,
    exemptions: repository_exemptions.Exemptions,
    policy: Path = repository_exemptions.POLICY,
) -> list[Effect]:
    """List protected effects the recorded repository scope does not cover.

    Exempt points outside the workspace, points outside every repository and failed
    points need no authority; every other repository point must be granted under its
    own repository by an entry rooted at one of its worktree roots.

    Args:
        decision: Result of ``decide``.
        scope: Scope entries from the lifecycle ``ready`` result.
        session: The session's carried repository; None outside every repository.
        memo: Invocation-local discovery results.
        exemptions: The exemptions ``decide`` used.
        policy: The reserved policy file path ``decide`` used.

    Returns:
        Uncovered protected effects in order, always including policy targets.
    """
    # Group granted paths by repository identity; an unresolved root or one that is
    # not a worktree root grants nothing, since paths match from the worktree root.
    grants: dict[tuple[str, tuple[int, int]], list[str]] = {}
    for entry in scope:
        identity, _ = repository_location.discover(Path(entry["root"]), memo)
        if identity is not None and os.path.realpath(entry["root"]) in {
            os.path.realpath(tree) for tree in identity.repository.trees
        }:
            grants.setdefault(identity.key(), []).extend(entry["paths"])
    common = None if session is None else os.path.normpath(session.common)
    roots = [] if session is None else [os.path.realpath(tree) for tree in session.trees]
    own = [path for key, paths in grants.items() if key[0] == common for path in paths]
    missing = []
    for effect in decision.protected:
        # Reserved policy edits are never authorized.
        points = effect.location.points
        if policy_target(effect, policy):
            missing.append(effect)
            continue
        flags = repository_exemptions.exempt(effect.location, exemptions)
        inside, granted = False, True
        for point, vault in zip(points, flags, strict=True):
            # Discovery saw the real directory, so relate its real spelling to each root.
            real = Path(os.path.realpath(point.path.parent), point.path.name)
            family = (
                point.repository is not None
                and os.path.normpath(point.repository.repository.common) == common
            )
            # A spelling below a session worktree root is in the workspace even when a
            # nested repository holds it, so the session grant must also cover it.
            nested = None
            if not family:
                nested = next((r for t in roots if (r := _below(real, t)) is not None), None)
            inside = inside or family or nested is not None
            if nested is not None and not covered(nested, own):
                granted = False
            # Only points outside the workspace keep a vault exemption.
            if (vault and not (family or nested is not None)) or point.repository is None:
                continue
            relative = _below(real, point.repository.repository.root)
            if relative is None or not covered(relative, grants.get(point.repository.key(), [])):
                granted = False
        # A workspace effect must also reach the workspace through some point.
        if not granted or (effect.location.workspace and not inside):
            missing.append(effect)
    return missing
