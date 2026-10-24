"""Classify finite parent tool effects without creating task or admission state."""

from __future__ import annotations

import os
import re
import shlex
from collections.abc import Callable, Iterator
from pathlib import Path

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


def matching_roots(spelling: Path, is_root: Callable[[Path], bool]) -> Iterator[Path]:
    """Yield every ancestor of one target spelling that is a worktree root.

    Args:
        spelling: Absolute target path, checked first and then through each parent.
        is_root: Worktree root predicate from ``root_test``.

    Returns:
        Each ancestor that is a root, nearest first.
    """
    # Every ancestor that is a root counts, not only the nearest.
    return filter(is_root, (spelling, *spelling.parents))


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
            # Control ownership accumulates across every ancestor that anchors a root.
            for anchor in matching_roots(spelling, is_root):
                inside = True
                # A control-store name below any matching root denies in either spelling.
                below = spelling.relative_to(anchor).parts
                if CONTROL_PARTS.intersection(part.lower() for part in below):
                    return "control"
    # An unresolvable target cannot be proven external.
    except (OSError, ValueError, RuntimeError):
        return "protected"
    return "protected" if inside else "external"


def edit_targets(tool: str, args: core.JSONObject) -> list[str] | None:
    """Extract every target from supported deterministic edit shapes.

    Args:
        tool: Native file tool name.
        args: Structured native input.

    Returns:
        All targets, or None for an unknown or malformed shape.
    """
    # Native file edits expose exactly one target and known editing fields.
    fields = {
        "Write": {"file_path", "content"},
        "Edit": {"file_path", "old_string", "new_string", "replace_all"},
    }
    if tool in fields:
        key = "file_path"
        if (
            set(args) <= fields[tool]
            and isinstance(args.get(key), str)
            and (
                tool == "Edit"
                and isinstance(args.get("old_string"), str)
                and isinstance(args.get("new_string"), str)
                or tool != "Edit"
                and isinstance(args.get("content"), str)
            )
        ):
            return [args[key]]
        return None
    # A patch header enumerates all affected files, including both sides of moves.
    if tool == "apply_patch" and set(args) == {"command"} and isinstance(args["command"], str):
        lines = args["command"].splitlines()
        if not lines or lines[0] != "*** Begin Patch" or lines[-1] != "*** End Patch":
            return None
        targets = []
        for line in lines[1:-1]:
            if line.startswith(
                ("*** Add File: ", "*** Update File: ", "*** Delete File: ", "*** Move to: ")
            ):
                targets.append(line.split(": ", 1)[1])
            elif line.startswith("*** ") and line != "*** End of File":
                return None
        return targets or None
    return None


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
        roles: Already computed edit roles for this invocation, when available.

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
    # Edits are free only when every target lies outside the project code repositories.
    selected = edit_roles(event) if roles is None else roles
    return bool(selected) and all(role == "external" for role in selected)


def edit_roles(event: core.JSONObject) -> list[str]:
    """Classify every deterministic native edit target against the project repositories.

    Args:
        event: Validated native tool and argument envelope.

    Returns:
        Every target role, or an empty list for an unknown edit shape.

    Raises:
        core.WorkspaceError: If the session repository is bare or inconsistent.
        OSError: If Git or a repository root cannot be read.
    """
    # Unknown edits remain readiness-gated without guessing their targets.
    targets = edit_targets(event["tool_name"], event["tool_input"])
    if not targets:
        return []
    # A session outside every repository has no roots, so every well-formed target is external.
    repository = core.project_repository(event["cwd"])
    roots = [] if repository is None else repository[2]
    # One worktree list covers the whole patch or single-file mutation.
    is_root = root_test(roots)
    return [target_role(event["cwd"], target, is_root) for target in targets]
