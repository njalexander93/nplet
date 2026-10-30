"""Parse the roadmap approval record and repository scope grant.

These are pure text checks over recorded roadmap fields; they do not authenticate the
approver or enforce prose intent.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TypedDict

from nplet.lifecycle._errors import WorkspaceError, require

REVISION = re.compile(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", re.ASCII)
APPROVED_REVISION = re.compile(r"(?<![\w-])approved revision (" + REVISION.pattern + ")", re.ASCII)
SCOPE_ENTRY = re.compile(r"- `([^`\x00]+)`: (`[^`]*`(?:, `[^`]*`)*)")


class ScopeEntry(TypedDict):
    """Name repository-relative paths granted under one absolute worktree root."""

    root: str
    paths: list[str]


def header_value(text: str, name: str) -> str | None:
    """Return the value of one unique ``**Name:**`` roadmap header line.

    Args:
        text: Roadmap markdown.
        name: Header field name without the bold markers or colon.

    Returns:
        The stripped value, or ``None`` when the field is absent or repeated.
    """
    # Accept only one header line so a duplicate cannot shadow the recorded value.
    prefix = "**" + name + ":**"
    values = [line[len(prefix) :].strip() for line in text.splitlines() if line.startswith(prefix)]
    return values[0] if len(values) == 1 else None


def revision_token(text: str) -> str | None:
    """Return the roadmap's proposal revision token, if its first word is one.

    Args:
        text: Roadmap markdown.

    Returns:
        The first ``**Proposal revision:**`` word when it matches the token syntax.
    """
    # A trailing period or annotation is not part of the revision token.
    words = (header_value(text, "Proposal revision") or "").split()
    return words[0] if words and REVISION.fullmatch(words[0]) else None


def roadmap_approved(text: str) -> bool:
    """Check that the roadmap records approval of its current proposal revision.

    This reads recorded state only. It does not authenticate the approving human or
    enforce the prose intent of the approval record.

    Args:
        text: Roadmap markdown.

    Returns:
        Whether status is approved and the record names the current revision token.
    """
    # Require all three recorded fields with no template placeholder or pending record.
    status, revision, record = (
        header_value(text, name)
        for name in ("Proposal status", "Proposal revision", "Approval record")
    )
    if status is None or revision is None or record is None:
        return False
    pending = record.casefold().startswith("pending")
    if pending or any("<" in value for value in (status, revision, record)):
        return False
    # Match the approved revision named by the record against the current revision.
    approved = APPROVED_REVISION.search(record)
    current = revision_token(text)
    return status.startswith("Approved") and approved is not None and approved[1] == current


def repository_scope(text: str) -> list[ScopeEntry]:
    """Parse the roadmap's repository scope grant syntax.

    Repository identity is resolved later by the adapter; this checks syntax only.

    Args:
        text: Roadmap markdown.

    Returns:
        Granted entries; empty when the field is absent, ``none`` or a template placeholder.

    Raises:
        WorkspaceError: ``INVALID_REQUEST`` if the field is repeated or malformed.
    """
    # Treat an absent field, explicit none, or template placeholder as no grant.
    prefix = "**Repository scope:**"
    lines = text.splitlines()
    found = [i for i, line in enumerate(lines) if line.startswith(prefix)]
    if not found:
        return []
    require(len(found) == 1)
    value = lines[found[0]][len(prefix) :].strip()
    if value == "none" or re.fullmatch(r"<[^<>]*>", value):
        return []
    require(value == "")
    # Read the consecutive entry lines directly below the header.
    entries: list[ScopeEntry] = []
    for line in lines[found[0] + 1 :]:
        if not line.startswith("- "):
            break
        match = SCOPE_ENTRY.fullmatch(line)
        if match is None:
            raise WorkspaceError("INVALID_REQUEST")
        root, paths = match[1], re.findall(r"`([^`]*)`", match[2])
        require(Path(root).is_absolute() and all(e["root"] != root for e in entries))
        # Accept one file, one subtree, or exactly ./ for the whole repository.
        for path in paths:
            parts = path.removesuffix("/").split("/")
            require(
                path == "./"
                or (
                    not path.startswith("/")
                    and not set(path) & set("*?[\\\x00")
                    and all(part not in {"", ".", ".."} for part in parts)
                )
            )
        entries.append({"root": root, "paths": paths})
    # Bound the grant and require at least one entry under an empty header value.
    require(0 < len(entries) <= 32 and sum(len(e["paths"]) for e in entries) <= 256)
    return entries


def legacy_scope(text: str) -> list[ScopeEntry] | None:
    """Parse repository scope from committed bytes that predate validation.

    Args:
        text: Roadmap markdown.

    Returns:
        Parsed entries, or ``None`` when the committed field is malformed.
    """
    # Committed roadmaps may predate the validator, so report rather than fail.
    try:
        return repository_scope(text)
    except WorkspaceError:
        return None


class RoadmapRecord(TypedDict):
    """Retain the revision token and scope of the last committed roadmap."""

    revision: str | None
    scope: list[ScopeEntry] | None


def roadmap_record(text: str) -> RoadmapRecord:
    """Summarize roadmap markdown for later scope-change checks.

    Args:
        text: Roadmap markdown.

    Returns:
        Its revision token and scope; the scope is None when malformed.
    """
    return {"revision": revision_token(text), "scope": legacy_scope(text)}


def check_scope_change(previous: RoadmapRecord | None, new: str) -> RoadmapRecord:
    """Require a new pending proposal revision for any repository scope change.

    Args:
        previous: Record of the committed roadmap; None when it was not retained.
        new: Replacement roadmap markdown.

    Returns:
        The replacement's record.

    Raises:
        WorkspaceError: ``INVALID_REQUEST`` if the new scope is malformed, if it changes
            without a new revision token and a ``Pending`` approval record, or if it
            grants anything while the previous record is unknown.
    """
    # Validate the replacement grant and accept unchanged scope as-is.
    record = roadmap_record(new)
    scope = repository_scope(new)
    # Without the previous revision a reused one cannot be detected, so grant nothing.
    if previous is None:
        require(scope == [])
        return record
    if scope == previous["scope"]:
        return record
    # A changed grant must be re-proposed under a new revision awaiting approval.
    require(
        record["revision"] is not None
        and record["revision"] != previous["revision"]
        and (header_value(new, "Approval record") or "").startswith("Pending")
    )
    return record
