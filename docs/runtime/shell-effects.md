# Recognized shell mutations

Claude `Bash` and Cursor parent `Shell` commands other than the
[finite shell reads](host-hooks.md#finite-shell-reads) are
gated by the file effects the adapter can identify in the command text. The command
is tokenized, never run or expanded, using the grammar bash and zsh share plus
zsh's `>!` forms. The adapter assumes `cp`, `mv`, `rm` and `echo`
are the standard system programs; aliases, functions and `PATH` shadowing are not
analyzed. Each identified target becomes an [effect](task-workspace.md#task-free-and-protected-work)
and receives the same decision, approval and scope checks as a structured edit.

## Recognized effects

| Recognized | Effect |
| --- | --- |
| `>`, `>>`, `>\|`, `>!`, `>>!`, their `N` and `&` forms, and `>&!`, with a literal target, in any segment | `create` or `write`. Output is text only for `echo` with literal operands, no leading `-` and no backslash; otherwise unknown |
| `rm` with `-f`, `-i`, `--` | `delete` |
| `cp` with `-f`, `-i`, `-p`, `-n`, `--` | `create` or `write` at the destination, carrying the source's actual content state |
| `mv` with `-f`, `-i`, `-n`, `--` | `move-from` the source and `move-to` the destination, carrying the source's state |
| `cp`, `mv` and `rm` as `/bin/` paths | As above |

Segments split on unquoted `;`, `&&`, `||`, `|` and `&`; each is analyzed even when
it may not run. An existing directory destination receives `destination/basename`;
several sources need one. Descriptor duplication, moving and closing (`>&N`, `>&N-`,
`>&-`) and `/dev/null` are not mutations. Because any step may be skipped,
declined or fail, a later source counts every content an earlier recognized write,
copy, move or removal in the same command may leave there, and the most protective
one decides. A workspace target is never content-inspected, so a workspace
redirection or copy is protected. Outside the workspace a binary copy is not text,
while a text copy or `echo` redirection into another repository is protected.

## Unchecked commands

Everything else is **unchecked**: it yields no effect and never gates on its own.
This includes other programs (Git, package managers, `sed -i`, `touch`, `ln`,
`printf` output), combined or other flags, recursive operations, globbed or
expanded words (including zsh `=cmd`), directory operands outside the workspace,
and a missing trailing-slash destination. A flag or `--` after an operand is unchecked, because BSD
programs read it as an operand and GNU programs as an option. Each `rm` operand
stands alone: after
`--` an expanded operand is unchecked while literal ones stay identified, but
before `--` it could be an option, so the whole argument list is unchecked. A
directory moved across the workspace boundary keeps only its workspace side. A
move whose destination is unresolved still removes each identified source, and an
unresolved source still writes unknown content to an identified destination. Both
moved sides use one classification of the source, which is skipped when every
affected side is in the workspace, and `>&!` always names a file. Known effects beside an unchecked command still apply:
`python build.py && rm /other/README.md` still decides the delete. Line breaks,
unquoted backslashes, backticks, `$(`, parentheses, brace groups, `[[`
conditional expressions, comments,
here-documents, `<>`, a `<` inside a word or before `N-M>` (a zsh numeric glob)
and malformed quoting make segmentation untrustworthy, so the
whole command is unchecked.

## Execution directory

Relative operands resolve from the command's **execution directory**, not the chat
or session directory: Cursor's validated nonempty `working_directory` or `cwd`,
otherwise the hook's `cwd`. It must be an absolute existing directory; otherwise
relative operands are unchecked. After any segment other than `cp`, `mv`, `rm`,
`echo`, `pwd`, `cat` or a program spelled as a path, which may change directory,
later relative operands are unchecked while absolute ones stay identified. The session repository
still comes from the hook's working directory.

## Denials and lifecycle commands

An effect on the exemption policy file is denied with `POLICY_ADMINISTRATION`, and
one on a `.task` or `.git` control store with `NOT_OWNER`. A command that runs a
lifecycle entry point, directly or as a Python interpreter's script, `-m` module or
`-c` code, keeps its lifecycle-control route instead of running free; a name given
only as data does not count. With untrustworthy syntax, any mention of an entry
point counts.
A child's shell keeps its scoped admission unless it has a protected effect. One
inspection budget covers every content read of a command. Task-free shell work
leaves stale readiness, a pending ticket read and unrelated pending records
untouched. These routes are covered by protocol tests against recorded hook
envelopes; no native observation of the execution-directory mapping is recorded.
