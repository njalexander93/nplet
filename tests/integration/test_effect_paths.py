"""Verify effect classification against real disposable symlinks and path traversal."""

from pathlib import Path

import pytest

from nplet.adapters.effects import Operation, Policy, classify, target_effect

pytestmark = pytest.mark.integration


def test_symlink_and_traversal_targets(tmp_path: Path) -> None:
    """Alias names cannot conceal protected files or protected instruction destinations.

    Args:
        tmp_path: Disposable filesystem root for this integration scenario.
    """
    # Build disposable files and retain an outside sentinel for forbidden effects.
    root = tmp_path / "checkout"
    source = root / "src"
    notes = root / "notes"
    source.mkdir(parents=True)
    notes.mkdir()
    sentinel = source / "main.py"
    sentinel.write_text("original", encoding="utf-8")
    policy = Policy(root)
    # A prose alias to source and a source alias to prose are both protected.
    (notes / "plan.md").symlink_to(sentinel)
    ordinary = notes / "ordinary.md"
    ordinary.write_text("prose with illustrative code", encoding="utf-8")
    (source / "alias.md").symlink_to(ordinary)
    assert target_effect("notes/plan.md", root, policy) == "protected"
    assert target_effect("src/alias.md", root, policy) == "protected"
    assert target_effect("notes/../src/main.py", root, policy) == "protected"
    # A mixed edit classifies all endpoints and performs no filesystem mutation.
    result = classify(
        Operation("edit", {"targets": ["notes/new.md", "notes/plan.md"]}, str(root)), policy
    )
    assert result.requires_task
    assert sentinel.read_text(encoding="utf-8") == "original"
    assert not (notes / "new.md").exists()


def test_dangling_and_loop_symlinks(tmp_path: Path) -> None:
    """Future protected targets and unresolved symlink loops cannot qualify as notes.

    Args:
        tmp_path: Disposable filesystem root for this integration scenario.
    """
    # Resolve dangling aliases through the nonexistent protected destination.
    (tmp_path / "plan.md").symlink_to(tmp_path / "src" / "future.py")
    policy = Policy(tmp_path)
    assert target_effect("plan.md", tmp_path, policy) == "protected"
    # A loop is unknown rather than an exception or a task-free result.
    (tmp_path / "loop.md").symlink_to(tmp_path / "loop.md")
    assert target_effect("loop.md", tmp_path, policy) == "unknown"


def test_hardlink_alias(tmp_path: Path) -> None:
    """Existing hardlink aliases retain the implementation gate.

    Args:
        tmp_path: Disposable filesystem root for this integration scenario.
    """
    # A hardlink bypasses symlink resolution; conservative link-count checks retain the gate.
    source = tmp_path / "original.py"
    source.write_text("source", encoding="utf-8")
    alias = tmp_path / "plan.md"
    alias.hardlink_to(source)
    assert target_effect("plan.md", tmp_path, Policy(tmp_path)) == "protected"
    assert source.read_text(encoding="utf-8") == "source"


def test_existing_dependency_and_governance_aliases(tmp_path: Path) -> None:
    """Existing dependencies, case aliases and rename endpoints cannot become notes.

    Args:
        tmp_path: Disposable checkout for real file identity and alias inspection.
    """
    # Create conventional dependency files and the canonical governing guide.
    runtime = tmp_path / "docs" / "runtime"
    runtime.mkdir(parents=True)
    canonical = runtime / "development.md"
    canonical.write_text("governance", encoding="utf-8")
    dependency = tmp_path / "requirements-dev.txt"
    dependency.write_text("example==1", encoding="utf-8")
    policy = Policy(tmp_path)
    # A case-insensitive host may map this alias to the same file; all hosts retain the gate.
    alias = runtime / "DEVELOPMENT.md"
    if alias.exists():
        assert alias.samefile(canonical)
    else:
        # Case-sensitive hosts still conservatively protect a separately created variant.
        alias.write_text("case variant", encoding="utf-8")
    for target in (dependency, alias):
        decision = classify(
            Operation("edit", {"targets": ["notes/new.md", str(target)]}, str(tmp_path)), policy
        )
        assert decision.effect == "protected"
    # A prose symlink to a dependency and a renamed dependency retain both endpoint roles.
    (tmp_path / "plan.txt").symlink_to(dependency)
    assert target_effect("plan.txt", tmp_path, policy) == "protected"
    assert classify(
        Operation("edit", {"targets": ["requirements-dev.txt", "notes/old.md"]}, str(tmp_path)),
        policy,
    ).requires_task
    assert canonical.read_text(encoding="utf-8") == "governance"
    assert dependency.read_text(encoding="utf-8") == "example==1"
    assert not (tmp_path / "notes" / "new.md").exists()


def test_checkout_symlink_boundary_and_escape(tmp_path: Path) -> None:
    """Lexical and resolved checkout roots ignore ancestors while retaining escaped roles.

    Args:
        tmp_path: Disposable filesystem for a symlinked checkout and outside source tree.
    """
    # Both the alias and real checkout live under implementation-like ancestor names.
    root = tmp_path / "src" / "checkout"
    notes = root / "notes"
    notes.mkdir(parents=True)
    plan = notes / "plan.md"
    plan.write_text("ordinary prose", encoding="utf-8")
    aliases = tmp_path / "build"
    aliases.mkdir()
    alias_root = aliases / "checkout"
    alias_root.symlink_to(root, target_is_directory=True)
    policy = Policy(alias_root)
    assert target_effect("notes/plan.md", alias_root, policy) == "note"
    assert target_effect("notes/new.md", alias_root, policy) == "note"
    # Resolved outside source roles must survive a harmless-looking alias inside notes.
    outside = tmp_path / "external" / "src"
    outside.mkdir(parents=True)
    source = outside / "instructions.md"
    source.write_text("source input", encoding="utf-8")
    (notes / "alias.md").symlink_to(source)
    assert target_effect("notes/alias.md", alias_root, policy) == "protected"
    assert target_effect(str(source), alias_root, policy) == "protected"
    # A lexical source alias stays protected even when resolving to an ordinary note.
    implementation = root / "src"
    implementation.mkdir()
    (implementation / "alias.md").symlink_to(plan)
    assert target_effect("src/alias.md", alias_root, policy) == "protected"
    assert classify(
        Operation("edit", {"targets": ["notes/new.md", "notes/alias.md"]}, str(alias_root)), policy
    ).requires_task
    assert plan.read_text(encoding="utf-8") == "ordinary prose"
    assert source.read_text(encoding="utf-8") == "source input"
    assert not (notes / "new.md").exists()


@pytest.mark.parametrize("ancestor", ["src", "build"])
@pytest.mark.parametrize("absolute_target", [False, True])
def test_git_root_with_lexical_checkout_alias(
    tmp_path: Path, ancestor: str, absolute_target: bool
) -> None:
    """Real Git root discovery must preserve note admission from a lexical checkout alias.

    Args:
        tmp_path: Disposable checkout and alias parent directories.
        ancestor: Source/build-like lexical ancestor of the native working directory.
        absolute_target: Whether the native tool provides an absolute aliased target.
    """
    # Initialize real Git, then give the native adapter an aliased cwd unlike its trusted root.
    import subprocess

    from nplet.adapters import common
    from nplet.lifecycle import task_workspace as core

    real = tmp_path / "real" / "checkout"
    notes = real / "notes"
    notes.mkdir(parents=True)
    (notes / "plan.md").write_text("ordinary note", encoding="utf-8")
    subprocess.run(["git", "init", "--quiet", str(real)], capture_output=True, check=True)
    alias_parent = tmp_path / ancestor
    alias_parent.symlink_to(real.parent, target_is_directory=True)
    cwd = alias_parent / "checkout"
    root, _, _ = core.repository(cwd)
    assert root == real.resolve()
    assert root != cwd
    target = str(cwd / "notes" / "plan.md") if absolute_target else "notes/plan.md"
    event = {
        "cwd": str(cwd),
        "session_id": "alias-note-session",
        "tool_use_id": "alias-note-call",
        "tool_name": "Write",
        "tool_input": {"file_path": target, "content": "planning"},
    }
    # This is the adapter-shaped admission path, not two identical alias roots in Policy.
    decision = common.effect_decision(event, "codex")
    assert decision.effect == "note"
    assert not decision.requires_task
    # A nested source alias back to the checkout must not become a nearer root boundary.
    (real / "src").symlink_to(real, target_is_directory=True)
    (real / "scripts").symlink_to(notes, target_is_directory=True)
    for protected_target in (
        "src/notes/plan.md",
        "scripts/plan.md",
        ".codex/plan.md",
        "notes/AGENTS.md",
        "docs/runtime/development.md",
    ):
        event["tool_input"] = {"file_path": str(cwd / protected_target), "content": "change"}
        assert common.effect_decision(event, "codex").requires_task
    # An innocent lexical note alias cannot erase resolved roles in an unrelated source tree.
    external = tmp_path / "external" / "src"
    external.mkdir(parents=True)
    outside_source = external / "plan.md"
    outside_source.write_text("outside input", encoding="utf-8")
    (notes / "outside.md").symlink_to(outside_source)
    for protected_target in (str(cwd / "notes" / "outside.md"), str(outside_source)):
        event["tool_input"] = {"file_path": protected_target, "content": "change"}
        assert common.effect_decision(event, "codex").requires_task
    # Classification never executes either the note edit or protected writes.
    assert outside_source.read_text(encoding="utf-8") == "outside input"
    assert (notes / "plan.md").read_text(encoding="utf-8") == "ordinary note"
    assert not (real / ".task").exists()
