# Automated AI review

The [caller workflow](../../.github/workflows/ai-review.yml) runs the reusable
[AI Reviewer](https://github.com/njalexander93/ai-reviewer/tree/e64f7571bbe747332cec155e4699d90d944a19b8)
inside Nplet's GitHub Actions run. The workflow and implementation both pin reviewed
commit `e64f7571bbe747332cec155e4699d90d944a19b8`. This is an immutable commit,
not a tagged release. The pinned [upstream guide](https://github.com/njalexander93/ai-reviewer/blob/e64f7571bbe747332cec155e4699d90d944a19b8/README.md)
defines the behavior summarized below.

## Setup and access

1. Give the reviewer GitHub account write access to this repository. The current
   account is `njalexander93-ai`; the implementation resolves identity from its token.
2. Set these repository **Actions secrets**. Never put their values in files or logs.

   | Secret | Required access |
   | --- | --- |
   | `ANTHROPIC_API_KEY` | Claude Opus 5.5 and Claude Sonnet 5.5 |
   | `OPENAI_API_KEY` | GPT-6.1 Sol |
   | `AI_REVIEWER_TOKEN` | Reviewer's GitHub personal access token with repository review access |
   | `LINEAR_API_KEY` | Read access to the Nplet issues referenced by PRs |

3. Merge the caller into `main`. The caller forwards only these four named secrets
   and grants `contents: read`, `issues: read` and `pull-requests: write`.
4. Keep stale approval dismissal enabled on `main`. It was verified enabled during
   installation preparation on 2026-10-23. Preserve required checks and human review;
   merge remains human-controlled. See [GitHub's branch protection guide](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches).
5. After merge, validate a non-draft PR and a same-revision rerun. Verify the reviewer,
   linked issue context, published review, duplicate suppression and reported costs.
   Secret names and collaborator access were checked before installation; credential
   values, live model access and live Linear lookup still require this validation.

The upstream Linear key is optional: an absent or empty key disables Linear lookup.
Keep it configured here for linked requirements. The implementation reads matching
PR attachments and issue identifiers in PR title, body or branch, including issue
URLs. It does not read issue comments or infer related issues. Issue edits alone do
not trigger another review. A configured provider error or inaccessible referenced
issue fails preparation rather than silently removing requirements.

## Runs and reruns

- Same-repository, non-draft PRs run on opening, reopening, publication from draft
  and new commits. Existing PRs need one of these events or a manual run after merge.
- Fork PRs require a maintainer to authorize the review: open **Actions → AI review
  → Run workflow**, select `main` and enter the PR number. This dispatch publishes a
  real review and incurs provider charges. Draft, closed and reviewer-authored PRs
  are rejected by the reusable workflow's implementation.
- To rerun, open the Actions run and use **Re-run all jobs**, or dispatch the PR again.
  Per-PR concurrency serializes requests. A previously published review for the same
  head/base pair suppresses duplicate publication; model calls can still cost money.
- Open the **Report API costs** step after success or failure. It reports estimates
  per model and provider, plus the total. Missing usage is **unknown**, not free.
  Provider billing is authoritative; estimates exclude GitHub Actions charges.
  Set spending limits separately in both provider projects.

See [GitHub's manual workflow instructions](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/manually-run-a-workflow)
and [rerun instructions](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/re-run-workflows-and-jobs).
The existing [PR checks](pr-checks.md) and Discord announcements remain independent.

## Credentials and upgrades

- Rotate keys in repository **Settings → Secrets and variables → Actions**. Preserve
  their names and the explicit caller mapping. Recheck reviewer access, token expiry,
  repository scope, provider model access and Linear issue visibility after rotation.
  A classic `public_repo` token is insufficient for a private repository. See
  [GitHub token scopes](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps)
  and [Linear API keys](https://linear.app/developers/graphql).
- Upgrade through a reviewed PR. Inspect upstream changes, reusable workflow inputs,
  permissions, checkout behavior and passing checks for the exact proposed commit.
  Replace **both** `uses` and `implementation-ref` with that same full commit SHA.
  Update this guide's pinned links and model/access details when they change. Never
  substitute a moving branch or tag for either pin.
- Preserve the trusted checkout: only the pinned reviewer implementation is checked
  out. PR content is read through GitHub's API. Never add checkout or execution of
  PR code to this workflow. See the pinned [reusable workflow](https://github.com/njalexander93/ai-reviewer/blob/e64f7571bbe747332cec155e4699d90d944a19b8/.github/workflows/review.yml)
  and [GitHub's reusable workflow guidance](https://docs.github.com/en/actions/how-tos/reuse-automations/reuse-workflows).

## Diagnose failures

| Symptom | Check |
| --- | --- |
| No automatic run | Caller is on `main`; PR is same-repository and non-draft; an eligible event occurred. Use manual dispatch for an authorized fork. |
| Preparation fails | Reviewer token identity/access, GitHub issue access, Linear key visibility, referenced issue availability and context size. |
| Model or consolidation fails | API access, quota, billing and model availability. Invalid or incomplete reports fail; they do not produce clean approval. |
| Publication fails or skips | Current head/base, valid inline anchors, reviewer permissions and an existing review for the same revision. |
| Approval predates a push | Confirm stale approval dismissal and inspect the newest run. A failed run does not retract an older review itself. |
| Reports are missing | Open the run's `ai-review-results` artifact promptly; retention is **one day**. Preserve needed evidence securely before expiry. |

Read the failing step's log before retrying. The implementation makes no automatic
provider retries. A manual rerun may buy another set of model calls. Capture the run
URL, head/base and safe error summary; never paste credentials or sensitive context
into public logs or issue comments.

## Review scope and disclosure

- Opus 5.5 and GPT-6.1 Sol independently review the same captured diff, before/after
  changed text files, PR description, linked issue requirements and prior inline
  discussion. Sonnet 5.5 consolidates both reports. All use medium reasoning effort.
- A blocking finding requests changes. Two complete reports without blockers approve.
  This is bounded static review: it does not execute tests, browse links or inspect
  unchanged dependencies and the entire repository. Missing context must be reported;
  model judgment and resistance to malicious source instructions are not guaranteed.
- Limits are 80 changed files, 150 KB per file version and 600 KB serialized context.
  Binary, unavailable or oversized content fails without approval. Head and base are
  checked before posting, but a push can race submission; stale approval dismissal
  remains necessary.
- Source, discussion and linked issue content are sent to **Anthropic and OpenAI**.
  Context and model reports are also saved in Nplet Actions artifacts for **one day**.
  Review text may quote requirements. Scope credentials to data appropriate for this
  repository and treat artifact access as part of its trust boundary.
- Both provider keys share a runner step. Step-scoped credentials do not create a
  sandbox between workflow stages. The automation supplements required checks and
  human review; it does not authorize merge.
