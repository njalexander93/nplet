"""Classify normalized native operations without reading or changing lifecycle state.

Adapters must derive families, provider identities and targets from native tool contracts,
not from user-supplied safety labels. This is bounded admission policy, not confinement:
path resolution observes current symlinks and cannot prevent filesystem races.
Shell forms assume trusted standard executables/PATH and platform shell parsing; no
classification can establish hostile executable replacement or concurrent path safety. Unknown
execution needs the existing task gate; readiness does not establish human approval.
"""

import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping, cast

Effect = Literal["read", "admin", "note", "protected", "unknown"]


@dataclass(frozen=True)
class Operation:
    """An adapter-verified operation family and its still-untrusted arguments.

    Args:
        family: read, admin, edit, shell, provider or unknown; never a model safety claim.
        arguments: Structured native arguments. Edit uses nonempty targets (all endpoints).
        cwd: Native working directory; relative paths resolve here, never process cwd.
    """

    family: str
    arguments: Mapping[str, object]
    cwd: str


@dataclass(frozen=True)
class ProviderContract:
    """A trusted exact provider operation contract supplied by adapter configuration.

    Args:
        family: read/admin cannot mutate implementation; edit exposes every local target;
            execute can mutate implementation without enumerable local targets.
        target_fields: Exact argument names holding local paths for an edit contract.
    """

    family: Literal["read", "admin", "edit", "execute"]
    target_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class Policy:
    """Trusted registry of implementation roots and provider operation contracts.

    Args:
        root: Absolute checkout root.
        protected_paths: Additional absolute loader-consumed instructions/configuration.
        providers: Exact (provider identity, operation) keys; never inferred by prefix.

    Ordinary notes are .md/.txt/.rst files outside protected trees and conventional
    dependency/build inputs. Registered paths also protect case variants on every OS.
    Unknown file types require a task. Repository governance guides are explicitly registered;
    external vault loaders must register their own protected instruction paths. Managed .task paths
    always retain the lifecycle route and never qualify as task-free notes.
    """

    root: Path
    protected_paths: tuple[Path, ...] = ()
    providers: Mapping[tuple[str, str], ProviderContract] = field(default_factory=dict)


@dataclass(frozen=True)
class Decision:
    """Effect result consumed before an adapter starts native execution.

    Args:
        effect: Strongest observed effect; unknown is mutation-capable opaque execution.
        code: Stable diagnostic suitable for native adapter responses and tests.
        guidance: Supported recovery route; does not grant native host permission.
    """

    effect: Effect
    code: str
    guidance: str

    @property
    def requires_task(self) -> bool:
        """Return whether the current task-readiness gate must run."""
        return self.effect in {"protected", "unknown"}


_PROTECTED_PARTS = {
    "src",
    "tests",
    "scripts",
    "node_modules",
    "requirements",
    "constraints",
    ".git",
    ".task",
    ".github",
    ".codex",
    ".claude",
    ".cursor",
    ".agents",
    "skills",
    "hooks",
    ".aws",
    ".vscode",
    "build",
    "dist",
    ".venv",
}
# These canonical guides are selected by root AGENTS and the documentation task catalog.
_GOVERNING_PATHS = {
    "docs/README.md",
    "docs/framework/baseline.md",
    "docs/authoring/definition-format.md",
    "docs/runtime/contributor-workflow.md",
    "docs/runtime/development.md",
    "docs/runtime/testing.md",
    "docs/runtime/pr-checks.md",
    "docs/runtime/host-hooks.md",
    "docs/runtime/task-workspace.md",
    "docs/runtime/task-workspace-usage.md",
}
_INSTRUCTIONS = {"agents.md", "claude.md", "skill.md", "context.md", "gemini.md"}
_GUIDANCE = (
    "Use a supported structured read/search. Bounded shell inspection supports pwd, cat, "
    "head, tail, wc, ls, sed -n '<N>[,<M>]p', rg --no-config, and "
    "git --no-pager --no-optional-locks -c core.fsmonitor=false; "
    "diff/log/show additionally need --no-ext-diff --no-textconv. Only supported argument "
    "forms qualify; cd, redirects, substitutions, unquoted globs, git -N/--format and "
    "unknown options require the task route. Every pipeline/list component must qualify. "
    "See docs/runtime/task-workspace.md#task-free-work-and-implementation-effects. Use "
    "an exact registered provider read/admin "
    "operation, or a structured edit with every target. For execution or implementation "
    "changes, enter the existing Task ticket/readiness procedure and obtain roadmap approval."
)


def guidance() -> str:
    """Return the shared recovery grammar without constructing a synthetic operation.

    Returns:
        Supported task-free routes, their limits and the canonical effect-boundary guide.
    """
    return _GUIDANCE


def _decision(effect: Effect, code: str) -> Decision:
    """Construct a stable decision and actionable guidance.

    Args:
        effect: Classified effect.
        code: Stable diagnostic identifier.

    Returns:
        Immutable decision with an appropriate supported route.
    """
    return Decision(effect, code, guidance() if effect in {"unknown", "protected"} else "")


def _role_path(candidate: Path, root: Path, resolved_root: Path) -> Path:
    """Retain lexical roles below a known or independently resolved checkout alias.

    Args:
        candidate: Absolute lexical or resolved target whose role is being inspected.
        root: Corresponding trusted checkout root, possibly already canonicalized by Git.
        resolved_root: Canonical checkout identity used to verify an alternate lexical prefix.

    Returns:
        A checkout-relative path when containment or an ancestor alias is established;
        otherwise the full external path. The outermost matching alias wins so an
        internal source symlink back to the checkout cannot erase its source role.

    Raises:
        OSError: An ancestor cannot be resolved for filesystem inspection.
        RuntimeError: A symlink loop prevents ancestor resolution on an older interpreter.
        ValueError: A filesystem path cannot be interpreted during resolution.
    """
    # Prefer known lexical containment so internal symlinks cannot redefine the boundary.
    try:
        return candidate.relative_to(root)
    except ValueError:
        pass
    # Git may canonicalize root while cwd retains an alias. Prove the outermost prefix,
    # rather than removing untrusted external roles or selecting a nearer internal alias.
    for ancestor in reversed(candidate.parents):
        if ancestor.resolve() == resolved_root:
            return candidate.relative_to(ancestor)
    return candidate


def target_effect(target: object, cwd: Path, policy: Policy) -> Effect:
    """Classify both lexical and resolved destinations, retaining symlink aliases.

    Args:
        target: Native path string; invalid paths are unknown.
        cwd: Absolute native working directory.
        policy: Trusted target registry. Ordinary roles are checkout-relative for internal
            targets and absolute for external targets; dot-control roles remain global.

    Returns:
        note for an ordinary prose destination, protected for implementation/control
        inputs, otherwise unknown. No file is opened or mutated.
    """
    # Validate path syntax before consulting the current filesystem.
    if (
        not isinstance(target, str)
        or not target
        or "\x00" in target
        or (os.name != "nt" and "\\" in target)
    ):
        return "unknown"
    if not cwd.is_absolute() or not policy.root.is_absolute():
        return "unknown"
    # Inspect aliases and resolved destinations: neither can erase a protected role.
    path = Path(target)
    path = path if path.is_absolute() else cwd / path
    try:
        candidates = (path.absolute(), path.resolve())
        roots = (policy.root.absolute(), policy.root.resolve())
        protected = tuple(p.resolve() for p in policy.protected_paths) + tuple(
            (policy.root / name).resolve() for name in _GOVERNING_PATHS
        )
        # Existing hardlinks may alias protected files that cannot be enumerated cheaply.
        try:
            metadata = path.stat()
        except FileNotFoundError:
            metadata = None
        if metadata is not None and (metadata.st_nlink > 1 or metadata.st_mode & 0o111):
            return "protected"
    except (OSError, RuntimeError, ValueError):
        return "unknown"
    # Checkout ancestors do not confer ordinary roles on its contents. Resolved escapes
    # retain full external roles; dot-control ancestors and instruction names stay global.
    for candidate, root in zip(candidates, roots, strict=True):
        try:
            role_path = _role_path(candidate, root, roots[1])
        except (OSError, RuntimeError, ValueError):
            return "unknown"
        parts = {part.casefold() for part in role_path.parts}
        control_parts = {part.casefold() for part in candidate.parts if part.startswith(".")}
        name = candidate.name.casefold()
        if (parts | control_parts) & _PROTECTED_PARTS or name in _INSTRUCTIONS:
            return "protected"
        # Conventional dependency/build inputs remain protected despite prose suffixes.
        if name == "cmakelists.txt" or re.fullmatch(r"(?:requirements|constraints).*\.txt", name):
            return "protected"
        # Fold each path component conservatively, including not-yet-created case aliases.
        # This intentionally also gates distinct case variants on case-sensitive hosts.
        folded = tuple(part.casefold() for part in candidate.parts)
        if any(
            folded[: len(p.parts)] == tuple(part.casefold() for part in p.parts) for p in protected
        ):
            return "protected"
    # Only explicit prose suffixes qualify; content alone does not confer execution.
    if all(p.suffix.casefold() in {".md", ".txt", ".rst"} for p in candidates):
        return "note"
    return "protected"


def _edits(arguments: Mapping[str, object], cwd: Path, policy: Policy) -> Decision:
    """Combine every target before admitting any part of a structured mutation.

    Args:
        arguments: targets contains each create/edit/delete/rename endpoint; optional
            executable true forces implementation even for a prose destination.
        cwd: Native absolute working directory.
        policy: Trusted target registry.

    Returns:
        Strongest effect, with malformed/missing targets classified unknown.
    """
    # Require a complete nonempty target list, not a single guessed filename.
    targets = arguments.get("targets")
    if not isinstance(targets, (list, tuple)) or not targets:
        return _decision("unknown", "UNKNOWN_EDIT_TARGETS")
    effects = [target_effect(target, cwd, policy) for target in targets]
    # Protected effects win across a mixed call, including permission changes.
    if "protected" in effects or arguments.get("executable") is True:
        return _decision("protected", "IMPLEMENTATION_TARGET")
    if "unknown" in effects or arguments.get("executable", False) is not False:
        return _decision("unknown", "UNKNOWN_EDIT_TARGETS")
    return _decision("note", "ORDINARY_NOTE")


# These forms admit inspection only; unknown options never inherit a command exemption.
_FLAGS = {
    "cat": {"-n", "-b", "-s", "-E", "-T", "-v", "-A"},
    "head": {"-n", "-c", "-q", "-v"},
    "tail": {"-n", "-c", "-q", "-v"},
    "wc": {"-l", "-w", "-c", "-m", "-L"},
    "ls": {"-l", "-a", "-h", "-t", "-r", "-d", "-1", "-A", "-F"},
    "rg": {
        "-n",
        "-i",
        "-l",
        "-L",
        "-s",
        "-S",
        "-F",
        "-w",
        "-x",
        "-v",
        "-c",
        "--files",
        "--no-config",
        "--hidden",
        "--no-ignore",
        "--glob",
        "-g",
        "-e",
        "--regexp",
        "--type",
        "-t",
        "--max-count",
        "-m",
        "--context",
        "-C",
        "-A",
        "-B",
    },
}
_VALUE_FLAGS = {
    "-n",
    "-c",
    "--glob",
    "-g",
    "-e",
    "--regexp",
    "--type",
    "-t",
    "--max-count",
    "-m",
    "--context",
    "-C",
    "-A",
    "-B",
}
_GIT_FLAGS = {
    "--short",
    "--porcelain",
    "--branch",
    "--stat",
    "--name-only",
    "--name-status",
    "--oneline",
    "--all",
    "--cached",
    "--staged",
    "--no-ext-diff",
    "--no-textconv",
    "--show-current",
    "--verify",
    "--abbrev-ref",
    "--show-toplevel",
}


def _read_command(tokens: list[str]) -> bool:
    """Recognize a complete simple command using a bounded argument grammar.

    Args:
        tokens: A single shlex-tokenized command without composition operators.

    Returns:
        True only for supported inspection forms; never executes a command.
    """
    # Empty commands, path-based executables and environment assignments are opaque.
    if not tokens:
        return False
    command, *args = tokens
    if command == "pwd":
        return args in ([], ["-L"], ["-P"])
    # Git external diff/textconv and config/aliases are excluded by exact command flags.
    if command == "git":
        prefix = ["--no-pager", "--no-optional-locks", "-c", "core.fsmonitor=false"]
        if args[:4] != prefix:
            return False
        args = args[4:]
        # A filename separator ends option parsing; later paths confer no flag evidence.
        options = args[: args.index("--")] if "--" in args else args
        if options and options[0] in {"diff", "log", "show"}:
            if not {"--no-ext-diff", "--no-textconv"}.issubset(options):
                return False
        # Preserve the exact branch inspection form and reject mutation subcommands.
        if not options or options[0] not in {
            "status",
            "diff",
            "log",
            "show",
            "rev-parse",
            "ls-files",
        }:
            return args == ["branch", "--show-current"]
        # Validate only actual options; option-looking filenames are harmless operands.
        return all(not arg.startswith("-") or arg in _GIT_FLAGS for arg in options[1:])
    # Sed admits printing addresses only; substitution, files and execution are excluded.
    if command == "sed":
        return (
            len(args) >= 3
            and args[0] == "-n"
            and re.fullmatch(r"\d+(?:,\d+)?p", args[1]) is not None
            and all(not arg.startswith("-") for arg in args[2:])
        )
    if command not in _FLAGS:
        return False
    # Ripgrep configuration can launch a preprocessor, so opt out explicitly.
    if command == "rg" and "--no-config" not in (
        args[: args.index("--")] if "--" in args else args
    ):
        return False
    # Consume option values explicitly; do not mistake a dangerous option for a filename.
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == "--":
            return True
        if arg.startswith("-") and arg != "-":
            if arg not in _FLAGS[command]:
                return False
            if (command in {"head", "tail"} and arg in {"-n", "-c"}) or (
                command == "rg" and arg in _VALUE_FLAGS - {"-n", "-c"}
            ):
                index += 1
                if index >= len(args) or args[index].startswith("-"):
                    return False
        index += 1
    return True


def shell_effect(command: object) -> Decision:
    """Classify supported shell inspection and compositions without executing shell code.

    Args:
        command: Native command string. Substitutions, expansions, redirection and
            interpreters require the task route, even when presented as read-only.

    Returns:
        read for fully recognized commands/pipelines/and-lists, unknown otherwise.
        This grammar is intentionally smaller than a POSIX shell and makes no proof
        about arbitrary shell effects or hostile executable replacement.
    """
    # Reject expansions before tokenization; quoted expansion examples can use file reads.
    if (
        not isinstance(command, str)
        or not command.strip()
        or any(char in command for char in ("$", "`", "\n", "\r", "\x00", "\\"))
    ):
        return _decision("unknown", "UNKNOWN_SHELL_EFFECT")
    # Unquoted glob/brace/tilde expansion can inject unobserved arguments.
    quote = None
    for character in command:
        if character in {"'", '"'}:
            if quote == character:
                quote = None
            elif quote is None:
                quote = character
        elif quote is None and character in "*?[]{}~!":
            return _decision("unknown", "UNKNOWN_SHELL_EFFECT")
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|<>()")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        return _decision("unknown", "MALFORMED_SHELL")
    # Recognize every component; a single opaque component gates the entire call.
    component: list[str] = []
    for token in tokens:
        if token in {";", "&&", "||", "|"}:
            if not _read_command(component):
                return _decision("unknown", "UNKNOWN_SHELL_EFFECT")
            component = []
        elif token and all(c in ";&|<>()" for c in token):
            return _decision("unknown", "UNKNOWN_SHELL_EFFECT")
        else:
            component.append(token)
    if not _read_command(component):
        return _decision("unknown", "UNKNOWN_SHELL_EFFECT")
    return _decision("read", "SUPPORTED_SHELL_INSPECTION")


def classify(operation: Operation, policy: Policy) -> Decision:
    """Classify adapter-normalized effects without consulting task readiness.

    Args:
        operation: Adapter-derived family, structured arguments and native cwd.
        policy: Trusted paths/provider registry. Caller labels in arguments confer no trust.

    Returns:
        A task-free or readiness-requiring decision. Native permissions, lifecycle
        ownership and human approval remain separate adapter/procedural obligations.
    """
    # Reject malformed normalized envelopes before selecting a contract.
    if (
        not isinstance(operation.arguments, Mapping)
        or not isinstance(operation.cwd, str)
        or "\x00" in operation.cwd
    ):
        return _decision("unknown", "MALFORMED_OPERATION")
    cwd = Path(operation.cwd)
    if not cwd.is_absolute():
        return _decision("unknown", "MALFORMED_OPERATION")
    # Local families are assigned by native normalization, never free-form tool arguments.
    if operation.family in {"read", "admin"}:
        return _decision(cast(Effect, operation.family), "SUPPORTED_" + operation.family.upper())
    if operation.family == "edit":
        return _edits(operation.arguments, cwd, policy)
    if operation.family == "shell":
        return shell_effect(operation.arguments.get("command"))
    # Provider effects come from exact trusted contracts, not names or read_only flags.
    if operation.family == "provider":
        provider, name = operation.arguments.get("provider"), operation.arguments.get("operation")
        if not isinstance(provider, str) or not isinstance(name, str):
            return _decision("unknown", "UNKNOWN_PROVIDER_CONTRACT")
        contract = policy.providers.get((provider, name))
        if contract is None:
            return _decision("unknown", "UNKNOWN_PROVIDER_CONTRACT")
        if contract.family in {"read", "admin"}:
            return _decision(contract.family, "SUPPORTED_PROVIDER_" + contract.family.upper())
        if contract.family == "execute":
            return _decision("protected", "PROVIDER_IMPLEMENTATION")
        values = operation.arguments.get("parameters")
        if not isinstance(values, Mapping) or not contract.target_fields:
            return _decision("unknown", "UNKNOWN_PROVIDER_TARGETS")
        return _edits({"targets": [values.get(key) for key in contract.target_fields]}, cwd, policy)
    return _decision("unknown", "UNKNOWN_OPERATION_EFFECT")
