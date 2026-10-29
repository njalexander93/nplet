# Pull-request pre-merge checks

**All six checks must pass.** Open a failed check for its failing step, command
log and evidence artifact. The [PR Pre-Merge Check workflow](../../.github/workflows/pr-checks.yml)
runs on pull requests targeting `main`, including documentation-only changes.
Bugbot is an independent check, outside these six jobs. AI review starts only after
all six pass; see [PR size check and AI review](#pr-size-check-and-ai-review).

The workflow uses the interpreter in [`.python-version`](../../.python-version),
Poetry 2.4.2 and locked dependencies. A newer run for the same pull request cancels
an in-progress run. Normal cancellation stops further validation work; artifact
retention steps still attempt to preserve available diagnostic evidence.

## What runs

| Check | Runner | Passing result |
| --- | --- | --- |
| `PR Size Check` | Ubuntu 24.04 | The PR changes at most 2,000 lines (additions + deletions). |
| `Code Quality Check` | Ubuntu 24.04 | Configuration, formatting, lint and types all pass. |
| `Linux Tests (Unit/Integration)` | Ubuntu 24.04 | Unit and integration suites both pass. |
| `Windows Tests (Unit/Integration)` | Windows 2025 | Unit and integration suites both pass. |
| `MacOS Tests (Unit/Integration)` | macOS 15 | Unit and integration suites both pass. |
| `Test Quality Check` | Ubuntu 24.04 | Required native unit, integration and tooling evidence from **Linux, Windows and macOS**, per-OS function checks, the historical **80% unrounded** aggregate, focused test lint and three assertion probes pass. |

Size, quality and the three OS jobs can run concurrently. Quality steps run in order:
`validate-config`, `format-check`, `lint`, `type-check`, then `ticket-references`
(no Linear issue identifiers or issue URLs in tracked files). Later steps still run
after an earlier failure unless cancelled; the failure still fails the job.
Each OS similarly runs `test-unit`, then `test-integration`, then
`test-tooling-unit`, including later suites after an earlier failure. The matrix
does not cancel peer jobs on failure.

Test Quality waits for all three OS jobs. Unless cancelled, it runs even when an
OS job fails. A missing or failed native record produces a named failure while
independent lint and assertion probes still run. It validates both suites from
Linux, Windows and macOS, plus separate tooling unit evidence on each OS.
All three operating systems are equal supported development environments,
regardless of the OS used by the author or reviewer. The historical 80% production
aggregate combines Linux and Windows evidence. That calculation is an additional
coverage gate; it does not define platform support. Each OS must independently
pass its applicable native checks, and results from another OS cannot replace them.
The gate retains a
`quality.json` result for each control; a blocked dependent check is not a pass.
It also retains native suite totals, a pinned-base source diff and the order
replay status as **advisory** entries. These do not create a hidden score or a
second percentage floor.

The function check compares AST-listed source functions with executable lines
and branch arcs in each applicable native **unit** report separately. A path
missed on one OS fails even when another OS covers it. Scripts use the
separate tooling unit report, so they do not change the production denominator.
The checked-in [exception ledger](../../scripts/quality_exceptions.json) starts
empty. An exception requires the exact source digest, paths, platform, case IDs,
independent reviewer and review date. Its named platforms must match the native
reports where those exact paths remain missing. A named reviewer entry needs
real review; the checker also requires each cited exact test node to have
executed successfully on the claimed native platforms. It rejects stale or
unused line/arc exceptions.
It cannot decide whether an assertion is meaningful. Gaps without such evidence
fail. Focused Ruff rules are `F631,PT010,PT011,PT012,PT026,PT030`.
The [probe specification](../../scripts/quality_probes.json) selects one wrong
result, missing exception and omitted effect. Each runs from a clean isolated
copy. Its baseline must pass, and its changed target must fail in the selected
test assertion. Syntax errors, import errors, unrelated failures and timeouts
do not count as killed faults. The three probes sample assertion effectiveness;
they do not provide a mutation score.

The matrix tests the pinned development interpreter. It does not establish every
interpreter in the declared runtime range, every Linux distribution, or installed
host-hook delivery. See [development limits](development.md#source-layout) and
[host integration evidence](host-hooks.md#validation-and-remaining-evidence).

## Reproduce locally

1. Follow [worktree setup](development.md#set-up-each-worktree). Run commands from
   the repository root with the locked Poetry environment.
2. Run the existing local checks:

   ```sh
   poetry run python scripts/dev.py check-local
   ```

   This still runs configuration, formatting, lint, types and the full test suite
   on the current OS. It stops at the first failure and saves evidence in ignored
   `.coverage.local/`. **The other native OS runs and combined quality validation
   remain pending.**
3. Reproduce quality steps individually. Run all five to inspect independent
   failures, as the workflow does:

   ```sh
   poetry run python scripts/dev.py validate-config
   poetry run python scripts/dev.py format-check
   poetry run python scripts/dev.py lint
   poetry run python scripts/dev.py type-check
   poetry run python scripts/dev.py ticket-references
   ```

4. Reproduce split native collection on Linux, Windows and macOS from the same
   clean, unchanged commit. Use separate external output directories per suite:

   ```sh
   poetry run python scripts/dev.py test-unit --platform-coverage --evidence-dir <platform-directory>/unit
   poetry run python scripts/dev.py test-integration --platform-coverage --evidence-dir <platform-directory>/integration
   poetry run python scripts/dev.py test-tooling-unit --evidence-dir <tooling-directory>/<platform>/tooling-unit
   ```

   Run later suites even if an earlier one fails, then preserve all exit results.
   A later successful suite does not erase an earlier failure.
5. Put the six production suite directories under one external artifact directory.
   Put the three tooling records under a separate external directory. Preserve
   hidden `.coverage` files and manifests. From a clean checkout of that same
   commit, run the full quality check into another external output directory:

   ```sh
   poetry run python scripts/test_quality.py <artifact-directory> --tooling-dir <tooling-directory> --output-dir <output-directory>
   ```

   Replace angle-bracket paths with actual paths; quote paths containing spaces.
   The quality check rejects missing, failed, duplicate or incomplete native
   suites, dirty checkouts, changed source bytes and mismatched revisions. Its
   `combined/` subdirectory retains the historical Linux/Windows aggregate.
   Native Linux, Windows and macOS evidence remains separately required; this
   subdirectory is not a summary of supported operating systems.
   The standalone combiner still accepts split or full suite evidence for its
   narrower historical check, including a successful native `check-local`
   record. Copy each default `.coverage.local/` output beneath its OS directory
   before combining. The quality gate requires six split records.

The [command runner](../../scripts/dev.py), [quality gate](../../scripts/test_quality.py),
[combiner](../../scripts/combine_coverage.py)
and [coverage policy](../../pyproject.toml) are canonical. Coverage includes
branches and uses one 80% floor across the production package. Platform collection
suspends the per-run floor so Windows-only and POSIX-only code can be combined.
The strict `test`, `check` and `ci` commands retain the single-run 80% floor.
Native receipt validation regenerates the JSON report from its raw branch
database and the exact checkout source with the pinned Coverage.py version.
It compares every source row and total after normalizing native path separators,
so translated multiline lines and branches validate without accepting a
rehash of fabricated coverage rows. JUnit totals include recorded subtest calls.
The coverage policy selects `ctrace`. On the pinned Python 3.14 interpreter,
`sysmon` missed both entry and exit arcs of the startup-binding context manager
in `register`, even though its real unit contract passed. The
[tracer regression](../../tests/integration/tooling/test_register_coverage_core.py)
runs that contract under the configured tracer and a `sysmon` comparison. It
requires complete configured arcs and records both measurements separately;
an upstream `sysmon` repair will not fail the check.

## Evidence and retries

- The Test Quality job summary records the **PR head**, **PR base**, **tested merge
  commit**, run attempt and native-test/quality results. Keep the workflow-run
  link and all six check results with those identities. A passing older run does
  not validate a newer candidate.
- `python-quality` contains `config/`, `format/`, `lint/` and `types/` evidence.
  Each command manifest records the OS, interpreter, commit, clean/dirty status,
  tracked-source digest, commands and exit codes. Start/end fields detect changes
  during validation; logs retain command output.
- `python-tests-linux`, `python-tests-windows` and `python-tests-macos` each contain
  `unit/` and `integration/`. Each suite has its own manifest, log, `tests.xml`,
  `coverage.xml` and hidden `.coverage` database when collection completes.
- `python-tooling-linux`, `python-tooling-windows` and `python-tooling-macos` each
  contain a separate `tooling-unit` receipt and branch coverage report.
- `python-test-quality` contains `quality.json`, `function-gaps.json`, focused
  lint output, probe baseline/mutant logs and `combined/combined.json`. Read
  each named result. Incomplete runs may retain only diagnostic output; missing
  artifacts are not passing evidence.
- Artifacts have **14-day retention**. Stable native artifact names are scoped
  to the workflow run. A partial rerun replaces that platform's suite evidence
  and can reuse successful same-revision peer artifacts from an earlier attempt
  of the same run. Earlier attempt logs remain available in Actions.
- Rerun a failed job when peer artifacts remain available. If inputs have expired
  or are missing, rerun the native jobs and Test Quality, or all jobs. Never combine
  artifacts from a different candidate or substitute local success for the
  current pull-request result.

## PR size check and AI review

`PR Size Check` runs first in parallel with the other jobs, so an oversized PR
fails without waiting for the native tests. It runs the
[size gate](../../scripts/pr_size_gate.py) from the PR's base commit, never PR
code, with a read-only token and no secrets, so fork PRs run it too. The gate
counts GitHub's whole-PR additions plus deletions against the base; the job
result is the size result. A failing size check does not cancel the other jobs.

| Changed lines | Result |
| --- | --- |
| ≤ 2,000 | Job passes. |
| > 2,000 | Job fails and AI review is skipped. Split the PR. |
| Unknown or invalid totals | Job fails with "Could not determine PR size". |
| Head moved since the run started | Job fails; the newer push runs its own check. |

The size result used to be a `PR Size Check` commit status posted by the AI review
workflow. It is now a job with the same name, and nothing posts that status.

The [AI review workflow](../../.github/workflows/ai-review.yml) starts when a
PR Pre-Merge Check run succeeds. Its `admit` job reads GitHub only and runs
from the default branch. It first reruns the size gate there: a PR controls its
own `pr-checks.yml`, so it could make its CI size job pass regardless of size.
Then the [admission check](../../scripts/pr_review_admit.py) admits the review
only when the run's head is still the PR's head and the latest CI run for that
head passed with all six jobs, including `PR Size Check`.
Otherwise the review is skipped. Fork PRs are skipped, because GitHub does not
link their CI runs to the PR.

Marking a draft ready does not rerun CI, so its review waits for the next push.
To review it sooner, to review an authorized fork PR, or to retry a review, run
the "AI review" workflow from the default branch with the PR number and the PR's
current full head SHA. A manual run passes the same admission check, so it needs
passing CI for that head too.

```sh
gh workflow run ai-review.yml -f pr-number=<number> -f head-sha=<full-head-sha>
```

A newly admitted review for a PR cancels that PR's older review still in progress.
Failed, cancelled or stale CI completions are rejected before that point, so they
never cancel a review.

## Readiness notification

The "Ready for human review" message means the PR passed all automation: the
AI reviewer account approved it after PR Pre-Merge Check passed. A review that
requests changes, or any other outcome, posts nothing.

The reviewer exposes no verdict output, so after every completed review run the
AI review workflow calls the
[Discord announcer](../../.github/workflows/pr-announce.yml). Its `ready` job runs
the [readiness check](../../scripts/pr_review_ready.py), which reads GitHub only
and never checks out PR code. The announcer posts "Ready for human review" with
the PR, CI run and AI review links only when:

- the PR is open, not a draft and still at the reviewed head and base;
- the AI reviewer account's latest review of that head is an approval carrying
  the head/base marker; the account is resolved from the reviewer token, so
  another user's approval never counts;
- the latest PR Pre-Merge Check run for the head, when GitHub links it to PRs,
  includes this PR, and that run and its six required jobs succeeded.

Otherwise the run notes the reason and posts nothing. GitHub API or
authentication failures fail the run. The post uses the existing
`DISCORD_PR_WEBHOOK_URL` secret and sends no mentions. Repeated review runs may
announce again; delivery is not exactly-once. A review run before CI passes,
such as an early manual run, stays silent: rerun the "AI review" workflow after
CI is green. The check runs in its own job just before the post, not at the
same instant, so a push in between can still be announced; the next CI run
reviews the new head.

## Tests for the checking tools

These tests protect the validation machinery itself:

- [test_pr_workflow.py](../../tests/integration/tooling/test_pr_workflow.py)
  checks the six-job configuration, the read-only size job, six downloads and continuation conditions. It exercises
  collector arguments with injected subprocess failures and checks that later
  collectors run, failures remain recorded and suite evidence stays separate.
  It does not execute GitHub's scheduler; hosted runs provide that evidence.
- [test_pre_commit.py](../../tests/integration/tooling/test_pre_commit.py)
  installs real Git hooks in disposable clones with the locked tools. It checks
  successful commits, rejection of formatting/lint/type errors, partial staging,
  restoration of unstaged edits and missing-environment failures. It validates
  local commit hooks, not GitHub branch enforcement.
- [test_platform_coverage.py](../../tests/integration/tooling/test_platform_coverage.py)
  exercises coverage transport, combination and evidence rejection, including
  incomplete suites and changed checkout bytes. Synthetic coverage and platform
  labels test those rules; they do not prove tests ran on native Windows/Linux.
  Native job artifacts supply that separate evidence.
- [test_test_quality.py](../../tests/unit/tooling/test_test_quality.py) checks
  exact function gaps, stale exception reviews and changed tooling receipts.
  Its integration counterpart checks that independent failures remain visible.
- [test_pr_size_gate.py](../../tests/unit/tooling/test_pr_size_gate.py) and
  [test_ai_review_workflow.py](../../tests/integration/tooling/test_ai_review_workflow.py)
  check the size limit, summary text, head binding, and the AI review workflow's
  CI trigger, size recount, admission job and skip conditions. They do not execute GitHub's scheduler.
- [test_pr_review_ready.py](../../tests/unit/tooling/test_pr_review_ready.py)
  checks each readiness condition with injected GitHub responses: PR state and
  commits, reviewer identity, the latest approval and its marker, CI run and job
  results, and failure handling. It also checks review admission: current head,
  CI run state and every required job, including `PR Size Check`. `test_pr_announce_workflow.py` and
  `test_ai_review_workflow.py` check the ready route wiring and its Discord
  payload. They do not execute GitHub's scheduler.

## Merge enforcement and human review

Creating these checks does not make them required branch checks. **Changing the
required status name from `Test Coverage Check` to `Test Quality Check` is a
separate, human-owned setup step after merge.** The initiating live-rule read
found only Cursor Bugbot required; do not claim the old name is currently
required. At rollout, read the live rule again, add the new exact check source
and name, remove an old requirement only if one actually exists, and preserve
other requirements. Do not report enforcement until a read-back confirms it.

`PR Size Check` is required only once a human adds it to the `main` rules as a
GitHub Actions check; the AI review itself is not a required check.

At delivery, inspect the current rules for `main` and the current candidate's
results. Record pending enforcement separately from test results. Preserve other
required checks, including independently configured Bugbot requirements. Follow
[submission readiness and PR verification](contributor-workflow.md#8-submit-and-verify-delivery):
finish work and available checks before opening a review-ready PR; PR-triggered
checks can honestly remain pending after submission. Passing checks do not
authorize merge: [human PR review and human-controlled merge](../framework/baseline.md#authoring-and-publication-rules)
still apply.
