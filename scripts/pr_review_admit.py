"""Admit a pull request head for AI review only after its PR Pre-Merge Check passed.

The helper runs in a trusted GitHub Actions job from the default-branch checkout and
never executes pull request code. It reads the live pull request and the CI run for
the requested head, then emits the head, base and draft-readiness for the reviewer.
Size policy lives in the CI job ``PR Size Check``; admission only requires it passed.
"""

import os
from collections.abc import Mapping

from scripts.pr_review_ready import Failure, Skip, get, require_ci
from scripts.pr_size_gate import NUMBER, REPOSITORY, SHA, Http, finish, ref_sha, request


def main(env: Mapping[str, str] = os.environ, http: Http = request) -> int:
    """Admit the requested pull request head when its current CI run fully passed.

    Args:
        env: Process environment with the GitHub Actions inputs.
        http: Request function, injectable so tests need no network.

    Returns:
        0 when admitted, otherwise 1 so no review job starts.
    """
    # Validate untrusted inputs before any request is sent.
    number, head = env.get("PR_NUMBER", ""), env.get("HEAD_SHA", "")
    repo = env.get("GITHUB_REPOSITORY", "")
    if not (NUMBER.match(number) and SHA.match(head) and REPOSITORY.match(repo)):
        message = (
            f"Invalid input: PR_NUMBER {number!r}, HEAD_SHA {head!r}, GITHUB_REPOSITORY {repo!r}."
        )
        finish(env, message, error=True, outputs={})
        return 1
    api = f"{env.get('GITHUB_API_URL') or 'https://api.github.com'}/repos/{repo}"
    token = env.get("GITHUB_TOKEN", "")

    try:
        # Bind admission to the live head; an old completion never authorizes a new head.
        pull = get(http, f"{api}/pulls/{number}", token, dict)
        live, base = ref_sha(pull, "head"), ref_sha(pull, "base")
        if live is None or base is None:
            raise Failure("pull request response had no head or base SHA")
        if live != head:
            raise Skip(f"head-sha {head} is not the current PR head {live}")

        # Require every required CI job, including PR Size Check, to have passed for the head.
        require_ci(http, api, token, number, head)
    # Report every refusal as an error so the review jobs never start.
    except Exception as error:  # Any refusal or read failure blocks admission
        reason = str(error) if isinstance(error, Skip | Failure) else type(error).__name__
        finish(env, f"AI review not admitted: {reason}.", error=True, outputs={})
        return 1

    # Only a PR GitHub reports as open and non-draft is eligible for automatic review.
    ready = pull.get("state") == "open" and pull.get("draft") is False
    outputs = {"head": head, "base": base, "ready": "true" if ready else "false"}
    finish(env, f"AI review admitted for PR #{number} at {head}.", error=False, outputs=outputs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
