"""Check roadmap approval and repository scope syntax without a workspace."""

from __future__ import annotations

import os

import pytest

from nplet.lifecycle import task_workspace as core

pytestmark = pytest.mark.unit

# A drive-less root is relative on Windows.
BASE = "C:/abs" if os.name == "nt" else "/abs"
ROOT = f"{BASE}/worktree"
APPROVED = (
    "**Proposal status:** Approved by the user.\n"
    "**Proposal revision:** r5 (2026-10-30)\n"
    "**Approval record:** Human approved revision r5. Authorizes steps 1-3.\n"
)


def scope(*entries: str) -> str:
    """Build a roadmap whose repository scope lists the given entry lines.

    Args:
        *entries: Entry lines placed directly below the header.

    Returns:
        Roadmap markdown with a trailing unrelated line.
    """
    return "**Repository scope:**\n" + "".join(e + "\n" for e in entries) + "\nBody\n"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("# No field\n", []),
        ("**Repository scope:** none\n", []),
        ("**Repository scope:** <none, or approved entries; see docs>\n", []),
        (scope(f"- `{ROOT}`: `src/a.py`"), [{"root": ROOT, "paths": ["src/a.py"]}]),
        (
            scope(f"- `{ROOT}`: `src/`, `docs/x.md`", f"- `{BASE}/other`: `./`"),
            [
                {"root": ROOT, "paths": ["src/", "docs/x.md"]},
                {"root": f"{BASE}/other", "paths": ["./"]},
            ],
        ),
        (
            scope(*(f"- `{BASE}/r{i}`: `a`" for i in range(32))),
            [{"root": f"{BASE}/r{i}", "paths": ["a"]} for i in range(32)],
        ),
    ],
)
def test_repository_scope_accepts_grants(text: str, expected: list[core.ScopeEntry]) -> None:
    """Accept absent, none, placeholder, file, subtree and whole-repository grants.

    Args:
        text: Roadmap markdown.
        expected: Parsed scope entries.
    """
    assert core.repository_scope(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "**Repository scope:** everything\n",
        scope(),
        scope("- `relative`: `a`"),
        scope(f"- `{ROOT}`: `a`", f"- `{ROOT}`: `b`"),
        scope(f"- `{ROOT}` `a`"),
        scope(f"- `{ROOT}`: a"),
        *(
            scope(f"- `{ROOT}`: `{path}`")
            for path in (
                "",
                "/abs",
                "../a",
                "a/../b",
                ".",
                "./a",
                "a/./b",
                "a//b",
                "*.py",
                "a?",
                "[ab]",
                "a\\b",
                "a\x00b",
            )
        ),
        scope(*(f"- `{BASE}/r{i}`: `a`" for i in range(33))),
        scope(*(f"- `{BASE}/r{i}`: " + ", ".join(f"`p{j}`" for j in range(129)) for i in range(2))),
        "**Repository scope:** none\n**Repository scope:** none\n",
    ],
)
def test_repository_scope_rejects_malformed_grants(text: str) -> None:
    """Reject unsafe roots or paths, duplicates, empty grants and exceeded limits.

    Args:
        text: Roadmap markdown.
    """
    with pytest.raises(core.WorkspaceError) as captured:
        core.repository_scope(text)
    assert captured.value.code == "INVALID_REQUEST"


@pytest.mark.parametrize(
    ("text", "approved"),
    [
        (APPROVED, True),
        (APPROVED.replace("Approved by", "Proposed by"), False),
        (APPROVED.replace("Human approved", "Pending; approved"), False),
        (APPROVED.replace("Human approved", "pending; approved"), False),
        (APPROVED.replace("revision r5. Authorizes", "revision r4. Authorizes"), False),
        (APPROVED.replace("r5 (2026", "<r5> (2026"), False),
        (APPROVED.replace("r5 (2026", "r5. (2026"), False),
        (APPROVED.replace("**Proposal revision:** r5 (2026-10-30)\n", ""), False),
        (core.initial_payload("TEST-1")["roadmap.md"].decode(), False),
    ],
)
def test_roadmap_approved_requires_matching_recorded_revision(text: str, approved: bool) -> None:
    """Pass only an Approved status whose record names the current revision token.

    Args:
        text: Roadmap markdown.
        approved: Expected approval result.
    """
    assert core.roadmap_approved(text) is approved
