"""Verify literal shell mutation recognition against real files and Git repositories."""

import zlib
from pathlib import Path

import pytest

from nplet.adapters.file_content import Budget
from nplet.adapters.shell_effects import shell_effects
from nplet.lifecycle import task_workspace as core
from tests.integration.adapters.task_boundary_support import init_repo

pytestmark = pytest.mark.unit

TEXT, BINARY, UNKNOWN, ABSENT = "text", "binary", "unknown", "absent"
IHDR = b"IHDR" + bytes(13)
PNG = b"\x89PNG\r\n\x1a\n" + (13).to_bytes(4, "big") + IHDR + zlib.crc32(IHDR).to_bytes(4, "big")


@pytest.fixture
def repos(tmp_path: Path) -> tuple[Path, Path, core.Repository]:
    """Create a workspace repository and another repository with text and binary files.

    Args:
        tmp_path: Disposable parent directory.

    Returns:
        The workspace root, the other root and the workspace's session repository.
    """
    ws, other = init_repo(tmp_path / "ws"), init_repo(tmp_path / "other")
    # Each repository holds a text file, a binary file and a directory.
    for root in (ws, other):
        (root / "README.md").write_text("readme\n")
        (root / "logo.png").write_bytes(PNG)
        (root / "a b.md").write_text("spaced\n")
        (root / "-dash.md").write_text("dash\n")
        (root / "dir").mkdir()
    session = core.project_repository(ws)
    assert session is not None
    return ws, other, session


def run(
    command: str, cwd: Path | None, session: core.Repository
) -> tuple[list[tuple[str, str, str | None, str | None]], bool]:
    """Recognize one command and flatten its effects into comparable tuples.

    Args:
        command: Shell command text.
        cwd: Execution directory, or None.
        session: Session repository.

    Returns:
        Operation, target spelling, before and after per effect, and the opaque flag.
    """
    shell = shell_effects(command, None if cwd is None else str(cwd), session, Budget())
    flat = [(e.op, str(e.location.points[0].path), e.before, e.after) for e in shell.effects]
    # Every effect's target spelling is reported for the caller's control check.
    assert shell.targets == [target for _, target, _, _ in flat]
    # Compare spellings with forward slashes so Windows paths match the commands' form.
    flat = [(op, Path(target).as_posix(), before, after) for op, target, before, after in flat]
    return flat, shell.opaque


@pytest.mark.parametrize(
    "command",
    [
        "rm README.md; echo $(date)",
        "rm README.md; echo `date`",
        'rm README.md; echo "$(date)"',
        "(rm README.md)",
        "{ rm README.md; }",
        "rm README.md; cat <<EOF",
        "rm README.md\nrm logo.png",
        "rm README.md\rrm logo.png",
        "rm 'README.md",
        'rm "README.md',
        "rm README\\.md",
        "# rm README.md",
        "rm README.md <>f",
        "rm README.md >",
        "rm README.md; {;",
        "rm README.md; }>out",
    ],
)
def test_untrusted_syntax_yields_no_effects(repos: tuple, command: str) -> None:
    """Substitution, grouping, here-documents, line breaks and bad quoting hide everything.

    Args:
        repos: Workspace, other root and session repository.
        command: Command whose segmentation cannot be trusted.
    """
    _, other, session = repos
    assert run(command, other, session) == ([], True)


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf README.md",
        "rm -fi README.md",
        "rm -r README.md",
        "rm --force README.md",
        "rm -dash.md",
        'rm "$HOME"',
        'rm "READ$X.md"',
        "rm *.md",
        "rm ~/README.md",
        "rm REA{D,X}ME.md",
        "rm ''",
        "cp -v README.md copy.md",
        "cp -r dir copy",
        "mv -p README.md moved.md",
        "cp README.md",
        "rm README.md -f",
        "cp README.md copy.md -p",
        "mv README.md moved.md -i",
        'cp -- README.md "$X"',
        "cd dir; mv README.md moved.md",
        "cp README.md logo.png dir/missing",
        "cp README.md nowhere/",
        "rm dir",
        "cp dir copy",
        "mv dir moved",
        "printf '%s' 'rm README.md'",
        "git rm README.md",
    ],
)
def test_unrecognized_arguments_are_unchecked(repos: tuple, command: str) -> None:
    """Unsupported flags, ambiguous operands and unsure destinations yield no effect.

    Args:
        repos: Workspace, other root and session repository.
        command: Command with unchecked arguments.
    """
    _, other, session = repos
    assert run(command, other, session) == ([], True)


@pytest.mark.parametrize(
    ("command", "expected"),
    [
        ("rm README.md", [("delete", "README.md", TEXT, None)]),
        ("rm -f README.md", [("delete", "README.md", TEXT, None)]),
        ("rm -i -f logo.png", [("delete", "logo.png", BINARY, None)]),
        ("/bin/rm -- -dash.md", [("delete", "-dash.md", TEXT, None)]),
        ("rm 'a b.md'", [("delete", "a b.md", TEXT, None)]),
        (
            'rm "a b.md" missing',
            [("delete", "a b.md", TEXT, None), ("delete", "missing", ABSENT, None)],
        ),
        ("rm '$X.md'", [("delete", "$X.md", ABSENT, None)]),
        *[
            (f"cp {flag} README.md copy.md", [("create", "copy.md", ABSENT, TEXT)])
            for flag in ("-f", "-i", "-p", "-n", "--")
        ],
        ("/bin/cp logo.png README.md", [("write", "README.md", TEXT, BINARY)]),
        (
            "cp README.md logo.png dir",
            [
                ("create", "dir/README.md", ABSENT, TEXT),
                ("create", "dir/logo.png", ABSENT, BINARY),
            ],
        ),
        ("cp logo.png dir/", [("create", "dir/logo.png", ABSENT, BINARY)]),
        *[
            (
                f"mv {flag} README.md logo.png",
                [
                    ("move-from", "README.md", TEXT, None),
                    ("move-to", "logo.png", BINARY, TEXT),
                ],
            )
            for flag in ("-f", "-i", "-n", "--")
        ],
        (
            "/bin/mv logo.png dir",
            [
                ("move-from", "logo.png", BINARY, None),
                ("move-to", "dir/logo.png", ABSENT, BINARY),
            ],
        ),
    ],
)
def test_literal_operands_yield_effects(
    repos: tuple, command: str, expected: list[tuple[str, str, str | None, str | None]]
) -> None:
    """Supported flags and literal operands describe each target and its content states.

    Args:
        repos: Workspace, other root and session repository.
        command: Recognized command run in the other repository.
        expected: Operation, relative target, before and after per effect.
    """
    _, other, session = repos
    want = [
        (op, (other / target).as_posix(), before, after) for op, target, before, after in expected
    ]
    assert run(command, other, session) == (want, False)


def test_cross_repository_states_and_workspace_targets(repos: tuple) -> None:
    """Binary stays binary across repositories; workspace targets are never inspected.

    Args:
        repos: Workspace, other root and session repository.
    """
    ws, other, session = repos
    # A binary copy into another repository and a text replacement there.
    assert run(f"cp {ws.as_posix()}/logo.png {other.as_posix()}/new.png", None, session) == (
        [("create", f"{other.as_posix()}/new.png", ABSENT, BINARY)],
        False,
    )
    assert run(f"cp {ws.as_posix()}/README.md {other.as_posix()}/README.md", None, session) == (
        [("write", f"{other.as_posix()}/README.md", TEXT, TEXT)],
        False,
    )
    # Workspace targets have no before state, and a copy into the workspace reads nothing.
    assert run(f"rm {ws.as_posix()}/README.md {ws.as_posix()}/dir", None, session) == (
        [
            ("delete", f"{ws.as_posix()}/README.md", None, None),
            ("delete", f"{ws.as_posix()}/dir", None, None),
        ],
        False,
    )
    assert run(f"cp {other.as_posix()}/logo.png {ws.as_posix()}/x.png", None, session) == (
        [("create", f"{ws.as_posix()}/x.png", None, UNKNOWN)],
        False,
    )
    # A directory moves only within the workspace.
    assert run(f"mv {ws.as_posix()}/dir {ws.as_posix()}/moved", None, session) == (
        [
            ("move-from", f"{ws.as_posix()}/dir", None, None),
            ("move-to", f"{ws.as_posix()}/moved", None, UNKNOWN),
        ],
        False,
    )
    # Across the boundary only the workspace side of a directory move is kept.
    assert run(f"mv {ws.as_posix()}/dir {other.as_posix()}/moved", None, session) == (
        [("move-from", f"{ws.as_posix()}/dir", None, None)],
        True,
    )
    assert run(f"mv {other.as_posix()}/dir {ws.as_posix()}/moved", None, session) == (
        [("move-to", f"{ws.as_posix()}/moved", None, UNKNOWN)],
        True,
    )
    # An unidentifiable rm operand does not hide an identified one.
    for command in (
        f"cd /; rm -f missing {ws.as_posix()}/README.md",
        f'rm -- {ws.as_posix()}/README.md "$OTHER"',
    ):
        assert run(command, other, session) == (
            [("delete", f"{ws.as_posix()}/README.md", None, None)],
            True,
        )
    # A move whose destination is unresolved still removes each identified source.
    for command in (
        f"cd /; mv {ws.as_posix()}/README.md renamed.md",
        f'mv -- {ws.as_posix()}/README.md "$DEST"',
    ):
        assert run(command, other, session) == (
            [("move-from", f"{ws.as_posix()}/README.md", None, None)],
            True,
        )
    assert run(f"cd /; mv {other.as_posix()}/dir renamed", other, session) == ([], True)
    assert run(f"cd /; cp {ws.as_posix()}/README.md copied.md", other, session) == ([], True)
    # Before ``--`` an expanded word could be an option, so nothing is identified.
    assert run(f'rm "$OTHER" {ws.as_posix()}/README.md', other, session) == ([], True)


@pytest.mark.parametrize(
    ("command", "expected", "opaque"),
    [
        ("echo hi > out", [("create", "out", ABSENT, TEXT)], False),
        ("echo hi >out", [("create", "out", ABSENT, TEXT)], False),
        ("echo hi >> README.md", [("write", "README.md", TEXT, TEXT)], False),
        ("echo hi >| README.md", [("write", "README.md", TEXT, TEXT)], False),
        ("echo hi 2> out", [("create", "out", ABSENT, TEXT)], False),
        ("echo hi 2>>out", [("create", "out", ABSENT, TEXT)], False),
        ("echo hi &> out", [("create", "out", ABSENT, TEXT)], False),
        ("echo hi &>> logo.png", [("write", "logo.png", BINARY, TEXT)], False),
        ("echo -n hi > out", [("create", "out", ABSENT, UNKNOWN)], False),
        ("echo 'a\\b' > out", [("create", "out", ABSENT, UNKNOWN)], False),
        ('echo "$X" > out', [("create", "out", ABSENT, UNKNOWN)], False),
        ("printf x > out", [("create", "out", ABSENT, UNKNOWN)], True),
        ("> out", [("create", "out", ABSENT, UNKNOWN)], False),
        ("cat < README.md > out", [("create", "out", ABSENT, UNKNOWN)], False),
        ("echo hi 2>&1", [], False),
        ("echo hi 2>&1-", [], False),
        ("echo hi >&-", [], False),
        ("echo hi 3>&-", [], False),
        ("echo hi > /dev/null", [], False),
        ("echo hi < README.md", [], False),
        ('echo hi > "$OUT"', [], True),
        ("echo hi > *.md", [], True),
        ("echo '>' README.md", [], False),
    ],
)
def test_redirections_write_their_literal_targets(
    repos: tuple, command: str, expected: list, opaque: bool
) -> None:
    """File redirections write; duplication, input and /dev/null do not.

    Args:
        repos: Workspace, other root and session repository.
        command: Command with redirections run in the other repository.
        expected: Operation, relative target, before and after per effect.
        opaque: Whether part of the command is unchecked.
    """
    _, other, session = repos
    want = [
        (op, (other / target).as_posix(), before, after) for op, target, before, after in expected
    ]
    assert run(command, other, session) == (want, opaque)


@pytest.mark.parametrize("separator", [";", "&&", "||", "|", "&"])
def test_every_segment_is_analyzed(repos: tuple, separator: str) -> None:
    """Each segment yields its effects whether or not it runs conditionally.

    Args:
        repos: Workspace, other root and session repository.
        separator: Segment separator.
    """
    _, other, session = repos
    command = f"rm README.md {separator} rm logo.png"
    assert run(command, other, session) == (
        [
            ("delete", f"{other.as_posix()}/README.md", TEXT, None),
            ("delete", f"{other.as_posix()}/logo.png", BINARY, None),
        ],
        False,
    )


def test_unknown_programs_keep_later_effects(repos: tuple) -> None:
    """An unknown program is opaque but does not hide a later literal removal.

    Args:
        repos: Workspace, other root and session repository.
    """
    ws, other, session = repos
    assert run(f"python build.py && rm {other.as_posix()}/README.md", ws, session) == (
        [("delete", f"{other.as_posix()}/README.md", TEXT, None)],
        True,
    )


def test_directory_changes_and_missing_cwd_leave_relative_operands_unchecked(
    repos: tuple,
) -> None:
    """After cd or pushd, or without a directory, only absolute operands are identified.

    Args:
        repos: Workspace, other root and session repository.
    """
    _, other, session = repos
    assert run("cd dir && rm README.md", other, session) == ([], True)
    assert run("pushd dir; echo x > out", other, session) == ([], True)
    assert run(f"cd dir && rm {other.as_posix()}/README.md", other, session) == (
        [("delete", f"{other.as_posix()}/README.md", TEXT, None)],
        False,
    )
    assert run("rm README.md", None, session) == ([], True)
    assert run(f"rm {other.as_posix()}/README.md", None, session) == (
        [("delete", f"{other.as_posix()}/README.md", TEXT, None)],
        False,
    )


@pytest.mark.parametrize(
    "prefix", ["builtin cd dir; ", "command cd dir; ", "X=1 cd dir; ", "if cd dir; then :; fi; "]
)
def test_possible_directory_changes_leave_relative_operands_unchecked(
    repos: tuple, prefix: str
) -> None:
    """Any program that may change directory, not only a bare cd, unanchors relative operands.

    Args:
        repos: Workspace, other root and session repository.
        prefix: Segments that may change the directory.
    """
    _, other, session = repos
    assert run(prefix + "rm README.md", other, session) == ([], True)


@pytest.mark.parametrize("operator", [">!", ">>!", "&>!", "&>>!", ">&!"])
def test_zsh_forced_overwrite_names_its_target(repos: tuple, operator: str) -> None:
    """The zsh ``!`` redirection forms write to the following word.

    Args:
        repos: Workspace, other root and session repository.
        operator: Forced-overwrite redirection.
    """
    ws, other, session = repos
    assert run(f"echo hi {operator} {other.as_posix()}/README.md", ws, session) == (
        [("write", f"{other.as_posix()}/README.md", TEXT, TEXT)],
        False,
    )


@pytest.mark.parametrize("command", ["rm {other}/README<1-1>.md", "rm <1-1>", "echo x > =python3"])
def test_zsh_globs_and_command_paths_are_unchecked(repos: tuple, command: str) -> None:
    """The zsh numeric globs and ``=`` command paths are never read as literal targets.

    Args:
        repos: Workspace, other root and session repository.
        command: Command template.
    """
    ws, other, session = repos
    assert run(command.format(other=other.as_posix()), ws, session) == ([], True)


@pytest.mark.parametrize("command", ["cp", "mv -f", "cp --", "rm -f"])
def test_programs_without_operands_are_unchecked(repos: tuple, command: str) -> None:
    """A program with no operands yields no effect instead of failing.

    Args:
        repos: Workspace, other root and session repository.
        command: Program and flags only.
    """
    ws, _, session = repos
    assert run(command, ws, session) == ([], True)


@pytest.mark.parametrize("target", ["1", "-", "out"])
def test_zsh_forced_duplication_form_writes_a_file(repos: tuple, target: str) -> None:
    """The ``>&!`` form names a file even when its target looks like a descriptor.

    Args:
        repos: Workspace, other root and session repository.
        target: File name after the operator.
    """
    ws, _, session = repos
    assert run(f"echo hi >&! {target}", ws, session) == (
        [("create", f"{ws.as_posix()}/{target}", None, TEXT)],
        False,
    )


def test_move_source_is_classified_once(repos: tuple) -> None:
    """A budget covering one read keeps a moved text source's known state.

    Args:
        repos: Workspace, other root and session repository.
    """
    _, other, session = repos
    outside = other.parent / "outside"
    outside.mkdir()
    size = (other / "README.md").stat().st_size
    shell = shell_effects(
        f"mv {other.as_posix()}/README.md {outside.as_posix()}/moved.md",
        str(other),
        session,
        Budget(remaining=size),
    )
    assert [(e.op, e.before, e.after) for e in shell.effects] == [
        ("move-from", TEXT, None),
        ("move-to", ABSENT, TEXT),
    ]


def test_workspace_only_copies_and_moves_leave_the_budget(repos: tuple) -> None:
    """Content that cannot change protection is not read, so a later deletion stays known.

    Args:
        repos: Workspace, other root and session repository.
    """
    ws, other, session = repos
    budget = Budget(remaining=(other / "README.md").stat().st_size)
    command = (
        f"cp {other.as_posix()}/README.md {ws.as_posix()}/a.md; "
        f"mv {ws.as_posix()}/README.md {ws.as_posix()}/b.md; "
        f"rm {other.as_posix()}/README.md"
    )
    shell = shell_effects(command, str(ws), session, budget)
    assert [(e.op, e.before, e.after) for e in shell.effects] == [
        ("create", None, UNKNOWN),
        ("move-from", None, None),
        ("move-to", None, UNKNOWN),
        ("delete", TEXT, None),
    ]


def test_earlier_writes_set_later_source_states(repos: tuple, tmp_path: Path) -> None:
    """A source overwritten, copied or removed earlier in the command keeps that state.

    Args:
        repos: Workspace, other root and session repository.
        tmp_path: Disposable parent holding a non-repository directory.
    """
    _, other, session = repos
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "logo.png").write_bytes(PNG)
    src, dst = f"{outside.as_posix()}/logo.png", f"{other.as_posix()}/new.txt"
    for program in ("cp", "mv"):
        flat, _ = run(f"echo hello > {src}; {program} {src} {dst}", outside, session)
        assert flat[-1] == ("create" if program == "cp" else "move-to", dst, ABSENT, TEXT)
    # A copy carries the original content of a source changed afterwards.
    flat, _ = run(
        f"cp {src} {outside.as_posix()}/c.png; echo hi > {src}; "
        f"cp {outside.as_posix()}/c.png {dst}",
        outside,
        session,
    )
    assert flat[-1] == ("create", dst, ABSENT, BINARY)
    # A removed or moved-away source is absent for later reads.
    flat, _ = run(f"rm {src}; cp {src} {dst}", outside, session)
    assert flat[-1] == ("create", dst, ABSENT, ABSENT)
    flat, _ = run(f"mv {src} {outside.as_posix()}/m.png; cp {src} {dst}", outside, session)
    assert flat[-1] == ("create", dst, ABSENT, ABSENT)
    # A move to an unresolved destination may fail, so known text is never erased.
    flat, _ = run(f'echo hi > {src}; mv -- {src} "$DEST"; cp {src} {dst}', outside, session)
    assert flat[-1] == ("create", dst, ABSENT, TEXT)


def test_carried_copies_read_the_original_once(repos: tuple, tmp_path: Path) -> None:
    """Copying through a destination reuses the original's classification.

    Args:
        repos: Workspace, other root and session repository.
        tmp_path: Disposable parent holding a non-repository directory.
    """
    _, other, session = repos
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.md").write_text("text\n")
    a, b = f"{outside.as_posix()}/a.md", f"{outside.as_posix()}/b.md"
    command = f"cp {a} {b}; cp {b} {other.as_posix()}/x.md; cp {a} {other.as_posix()}/y.md"
    shell = shell_effects(command, str(outside), session, Budget(remaining=5))
    assert [e.after for e in shell.effects] == [TEXT, TEXT, TEXT]
