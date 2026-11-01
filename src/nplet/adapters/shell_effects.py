"""Recognize the literal file mutations of one shell command as edit effects.

The grammar is the subset that bash and zsh share, plus zsh's ``>!`` redirections. The
command is tokenized here, quote-aware and character by character; it is never run
or expanded. ``cp``, ``mv``, ``rm`` and ``echo`` are assumed to be the standard system
programs: aliases, functions and ``PATH`` shadowing are not analyzed. Recognized
mutations are output redirections in any segment and the literal operands of ``cp``,
``mv`` and ``rm`` with individually spelled flags. Anything else is unchecked: it yields
no effect and marks the result opaque. Syntax that makes segmentation untrustworthy
yields no effects at all. The recognizer never decides admission.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import NamedTuple

from nplet.adapters import repository_location
from nplet.adapters.file_content import ABSENT, BINARY, TEXT, UNKNOWN, Budget, classify
from nplet.adapters.task_boundary import Effect, root_test
from nplet.lifecycle import task_workspace as core

# Word provenance: written out, changed by expansion, or changed by globbing.
LITERAL, EXPANDED, GLOBBED = "literal", "expanded", "globbed"
SEPARATORS = frozenset({";", "&&", "||", "|", "&"})
# Individually spelled flags that change neither the targets nor the written content.
FLAGS = {"cp": {"-f", "-i", "-p", "-n"}, "mv": {"-f", "-i", "-n"}, "rm": {"-f", "-i"}}
PROGRAMS = {"cp": "cp", "mv": "mv", "rm": "rm", "/bin/cp": "cp", "/bin/mv": "mv", "/bin/rm": "rm"}
# Programs whose arguments have no file effect; their redirections still count.
NO_EFFECT = frozenset({"echo", "cd", "pushd", "pwd", "cat"})


class Word(NamedTuple):
    """One shell word with its quote-removed text.

    Attributes:
        text: The word after quote removal, without any expansion.
        kind: ``literal``, ``expanded`` or ``globbed``.
    """

    text: str
    kind: str


class Shell(NamedTuple):
    """The recognized file effects of one shell command.

    Attributes:
        effects: One effect per identified target, in command order.
        targets: The absolute lexical spelling of every effect's target.
        opaque: Whether some part of the command was not analyzed.
    """

    effects: list[Effect]
    targets: list[str]
    opaque: bool


def _tokens(command: str) -> list[Word | str] | None:
    """Split a command into words, separators and normalized redirection operators.

    Args:
        command: Native shell command text.

    Returns:
        Words, separators from ``SEPARATORS`` and the operators ``>`` (file output),
        ``>&`` (output duplication or file) and ``<`` (input); None when the syntax makes
        segmentation untrustworthy.
    """
    # A line break can start a command that segmentation would miss.
    if "\n" in command or "\r" in command:
        return None
    tokens: list[Word | str] = []
    text: str | None = None
    kind, quoted = LITERAL, False

    def end() -> bool:
        """Close the word in progress.

        Returns:
            False when the word is a bare brace that opens or closes a group.
        """
        nonlocal text, kind, quoted
        # A bare brace word groups commands; otherwise record any word in progress.
        if text in ("{", "}") and not quoted:
            return False
        if text is not None:
            tokens.append(Word(text, kind))
        text, kind, quoted = None, LITERAL, False
        return True

    i = 0
    while i < len(command):
        c, following = command[i], command[i + 1 : i + 2]
        # Single quotes keep everything literal up to the closing quote.
        if c == "'":
            close = command.find("'", i + 1)
            if close < 0:
                return None
            text, quoted, i = (text or "") + command[i + 1 : close], True, close + 1
            continue
        # Double quotes are literal except for expansion; a substitution runs a command.
        if c == '"':
            j = i + 1
            while j < len(command) and command[j] != '"':
                if command[j] == "`" or command.startswith("$(", j):
                    return None
                if command[j] in "$\\":
                    kind = EXPANDED
                j += 2 if command[j] == "\\" else 1
            if j >= len(command):
                return None
            text, quoted, i = (text or "") + command[i + 1 : j], True, j + 1
            continue
        # Unquoted blanks end a word; escapes, substitutions, grouping and comments are opaque.
        if c in " \t":
            if not end():
                return None
        elif c in "\\`()" or (c == "$" and following == "(") or (c == "#" and text is None):
            return None
        # Separators, including the ``&>`` output form, end the word in progress.
        elif c in ";|&":
            if not end():
                return None
            if c == "&" and following == ">":
                raw, token = ("&>>" if command.startswith("&>>", i) else "&>"), ">"
                # zsh's forced-overwrite form ends with ``!``.
                raw += "!" if command.startswith("!", i + len(raw)) else ""
            else:
                raw = c * 2 if c in "&|" and following == c else c
                token = raw
            tokens.append(token)
            i += len(raw)
            continue
        # Digits right before a redirection name its descriptor rather than an operand.
        elif c in "<>":
            # A ``<`` within a word or before ``N-M>`` can be a zsh numeric glob.
            if c == "<" and (
                (text is not None and not text.isdigit())
                or re.match(r"<[0-9]*-[0-9]*>", command[i:])
            ):
                return None
            if text is not None and text.isdigit() and not quoted:
                text = None
            elif not end():
                return None
            raw = command[i : i + 2]
            if raw in ("<<", "<>"):
                return None
            operator = {">>": ">", ">|": ">", ">&": ">&", "<&": "<"}.get(raw)
            if operator is None:
                raw, operator = c, c
            # zsh's forced-overwrite forms ``>!``, ``>>!`` and ``>&!`` always name a file.
            if c == ">" and command.startswith("!", i + len(raw)):
                raw, operator = raw + "!", ">"
            tokens.append(operator)
            i += len(raw)
            continue
        # Any other character extends the word, recording expansion or globbing.
        else:
            # zsh expands a leading ``=`` to a command's path.
            if c == "$" or (c == "=" and text is None):
                kind = EXPANDED
            elif c in "*?[~{" and kind != EXPANDED:
                kind = GLOBBED
            text = (text or "") + c
        i += 1
    # Close the final word; a group brace there is still opaque.
    return tokens if end() else None


def _segments(tokens: list[Word | str]) -> list[tuple[list[Word], list[tuple[str, Word]]]] | None:
    """Group tokens into segments of argument words and redirections.

    Args:
        tokens: Tokens from ``_tokens``.

    Returns:
        Per segment, its words and its (operator, target) redirections; None when a
        redirection has no target word.
    """
    segments: list[tuple[list[Word], list[tuple[str, Word]]]] = [([], [])]
    i = 0
    while i < len(tokens):
        token = tokens[i]
        # Words are arguments and separators start the next segment.
        if isinstance(token, Word):
            segments[-1][0].append(token)
        elif token in SEPARATORS:
            segments.append(([], []))
        # A redirection consumes its following target word.
        else:
            target = tokens[i + 1] if i + 1 < len(tokens) else None
            if not isinstance(target, Word):
                return None
            segments[-1][1].append((token, target))
            i += 1
        i += 1
    return segments


def segment_words(command: str) -> list[list[str]] | None:
    """Return each segment's argument words after quote removal.

    Args:
        command: Native shell command text.

    Returns:
        Per segment, its word texts in order; None when segmentation is untrustworthy.
    """
    tokens = _tokens(command)
    segments = None if tokens is None else _segments(tokens)
    return None if segments is None else [[w.text for w in words] for words, _ in segments]


def _operands(program: str, words: list[Word]) -> list[str | None] | None:
    """Collect a program's literal operands after its individually spelled flags.

    Args:
        program: ``cp``, ``mv`` or ``rm``.
        words: The words after the program name.

    Returns:
        The operands in order, with None for a non-literal or empty operand after
        ``--``; None overall for such a word before ``--``, an unsupported or combined
        flag, or a word starting with ``-`` after an operand and before ``--``.
    """
    operands: list[str | None] = []
    options = True
    for word in words:
        # Only written-out words have a known meaning; after ``--`` a word is an operand.
        if word.kind != LITERAL or not word.text:
            if options:
                return None
            operands.append(None)
            continue
        # Before ``--``, a leading dash must be one supported flag.
        if options and word.text == "--":
            options = False
        elif options and word.text.startswith("-"):
            # After an operand, BSD treats a flag as an operand and GNU as an option.
            if word.text not in FLAGS[program] or operands:
                return None
        else:
            operands.append(word.text)
    return operands


def shell_effects(
    command: str, cwd: str | None, repository: core.Repository | None, budget: Budget
) -> Shell:
    """Describe every literal file mutation of one shell command as an effect.

    Workspace targets are never content-inspected, so their ``before`` is None. Other
    targets are read at their resolved point; a failed resolution makes a side unknown.
    Redirected output is text only for ``echo`` with literal, flag-free, escape-free
    operands; a copy or move carries its source's actual state.

    Args:
        command: Native shell command text.
        cwd: Validated execution directory; None leaves relative operands unchecked.
        repository: The session's carried project repository; None when the session
            lies outside every repository.
        budget: The call's inspection budget, shared by every side.

    Returns:
        The effects, their target spellings and whether anything went unanalyzed.
    """
    # Untrustworthy segmentation hides every effect.
    tokens = _tokens(command)
    segments = None if tokens is None else _segments(tokens)
    if segments is None:
        return Shell([], [], True)
    # One root predicate and one discovery memo cover every target of the call.
    is_root = root_test([] if repository is None else repository.trees)
    memo: repository_location.Memo = {}
    effects: list[Effect] = []
    targets: list[str] = []
    opaque = False
    # Every content a path may hold after earlier recognized operations, any of which
    # may be skipped, declined or fail: states, or paths whose original content arrived.
    contents: dict[Path | None, frozenset[str | Path]] = {}
    # Each original file is classified at most once per command.
    originals: dict[Path, str] = {}

    def locate(base: str | None, target: str) -> tuple[repository_location.Location, Path | None]:
        """Resolve one operand, joining a relative one to the execution directory.

        Args:
            base: Execution directory, or None for an absolute operand.
            target: Literal operand.

        Returns:
            The location and its resolved path, or None when resolution failed.
        """
        # The resolved point is last; a failed point has no path to read.
        location = repository_location.resolve(base or "", target, repository, is_root, memo)
        point = location.points[-1]
        return location, None if point.error else point.path

    def possible(path: Path | None) -> frozenset[str | Path]:
        """List what one resolved side may hold.

        Args:
            path: Resolved path, or None when resolution failed.

        Returns:
            States and original paths; unknown without a path.
        """
        return frozenset({UNKNOWN}) if path is None else contents.get(path, frozenset({path}))

    def state(path: Path | None) -> str:
        """Classify one resolved side by the most protective content it may hold.

        Args:
            path: Resolved path, or None when resolution failed.

        Returns:
            Text if any possibility is text, then unknown, binary and absent.
        """
        states = set()
        for known in possible(path):
            if isinstance(known, Path) and known not in originals:
                originals[known] = classify(known, budget)
            states.add(originals[known] if isinstance(known, Path) else known)
        return next((s for s in (TEXT, UNKNOWN, BINARY) if s in states), ABSENT)

    def keep(path: Path | None, more: frozenset[str | Path]) -> None:
        """Add contents a path may now hold to what it may already hold.

        Args:
            path: Resolved path, or None when resolution failed.
            more: States or original paths the operation may leave there.
        """
        # A union stays bounded by the command's distinct paths and states.
        contents[path] = possible(path) | more

    def add(
        op: str,
        location: repository_location.Location,
        path: Path | None,
        after: str | None,
        before: str | None = None,
    ) -> None:
        """Record one effect, reading its existing side outside the workspace only.

        Args:
            op: Effect operation.
            location: Target location.
            path: Resolved target path, or None when resolution failed.
            after: Output content state.
            before: Already classified existing state, read once per path.
        """
        targets.append(str(location.points[0].path))
        if not location.workspace and before is None:
            before = state(path)
        effects.append(Effect(op, location, None if location.workspace else before, after))

    def written(path: Path | None) -> str:
        """Choose between creating and overwriting one output target.

        Args:
            path: Resolved target path, or None when resolution failed.

        Returns:
            ``create`` when the path does not lexically exist, otherwise ``write``.
        """
        return "create" if path is not None and not os.path.lexists(path) else "write"

    base = cwd
    for words, redirections in segments:
        program = words[0].text if words and words[0].kind == LITERAL else None
        # Redirected output is text only for a literal echo without options or escapes.
        output = UNKNOWN
        if program == "echo" and all(
            w.kind == LITERAL and not w.text.startswith("-") and "\\" not in w.text
            for w in words[1:]
        ):
            output = TEXT
        for op, word in redirections:
            # Input and descriptor duplication, moving or closing open no file for output.
            if op == "<" or (
                op == ">&" and word.kind == LITERAL and re.fullmatch(r"[0-9]+-?|-", word.text)
            ):
                continue
            # A non-literal or unanchored target is unchecked; /dev/null holds nothing.
            if (
                word.kind != LITERAL
                or not word.text
                or (base is None and not os.path.isabs(word.text))
            ):
                opaque = True
            elif word.text != "/dev/null":
                location, path = locate(base, word.text)
                add(written(path), location, path, output)
                keep(path, frozenset({output}))
        name = PROGRAMS.get(program or "")
        # A program that may change directory (cd, a wrapper, a keyword or an unknown
        # program) makes later relative operands unchecked.
        if words and name is None and program not in ("echo", "pwd", "cat"):
            base = None
        # Other programs' arguments are unchecked unless known to have no file effect.
        if name is None:
            opaque = opaque or (bool(words) and program not in NO_EFFECT)
            continue
        arguments = _operands(name, words[1:])
        if not arguments:
            opaque = True
            continue
        # Each identifiable removed operand is deleted; a directory needs workspace membership.
        if name == "rm":
            for operand in arguments:
                if operand is None or (base is None and not os.path.isabs(operand)):
                    opaque = True
                    continue
                location, path = locate(base, operand)
                if path is not None and path.is_dir() and not location.workspace:
                    opaque = True
                else:
                    add("delete", location, path, None)
                    keep(path, frozenset({ABSENT}))
            continue
        # A copy or move needs every operand identified; a move still removes each
        # identified source, so only its unresolved operands stay unchecked.
        known = [a is not None and (base is not None or os.path.isabs(a)) for a in arguments]
        if not all(known):
            opaque = True
            for source, ok in zip(arguments[:-1], known, strict=False):
                if name == "mv" and source is not None and ok:
                    source_location, source_path = locate(base, source)
                    # A directory is content-independent only inside the workspace.
                    if source_location.workspace or source_path is None or not source_path.is_dir():
                        # The move may fail, so later reads keep the source's content.
                        add("move-from", source_location, source_path, None)
            continue
        operands = [a for a in arguments if a is not None]
        # A usable destination is required: an existing directory takes many sources.
        sources, destination = operands[:-1], operands[-1]
        full = os.path.join(base or "", destination)
        into = os.path.isdir(full)
        names = [Path(source).name for source in sources]
        if (
            not sources
            or (len(sources) > 1 and not into)
            or (destination.endswith("/") and not os.path.exists(full))
            or (into and any(n in ("", ".", "..") for n in names))
        ):
            opaque = True
            continue
        for source, leaf in zip(sources, names, strict=True):
            source_location, source_path = locate(base, source)
            location, path = locate(base, os.path.join(destination, leaf) if into else destination)
            # A directory is content-independent only inside the workspace, so each moved
            # side there is kept and anything else is unchecked.
            if source_path is not None and source_path.is_dir():
                inside = location.workspace and (name == "cp" or source_location.workspace)
                if name == "mv" and not inside:
                    if source_location.workspace:
                        add("move-from", source_location, source_path, None)
                    if location.workspace:
                        add("move-to", location, path, UNKNOWN)
                if not inside:
                    opaque = True
                    continue
            # The output carries the source's actual state, read only when some side's
            # protection depends on content; a move also removes the source.
            inside = location.workspace and (name == "cp" or source_location.workspace)
            after = UNKNOWN if inside else state(source_path)
            if name == "mv":
                add("move-from", source_location, source_path, None, after)
                add("move-to", location, path, after)
            else:
                add(written(path), location, path, after)
            keep(path, possible(source_path))
            if name == "mv":
                keep(source_path, frozenset({ABSENT}))
    return Shell(effects, targets, opaque)
