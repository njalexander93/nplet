"""Decide whether a pull request is ready to announce for human review.

The helper runs in a trusted GitHub Actions job from the default-branch checkout and
never executes pull request code. It only reads GitHub: the live pull request, the
AI reviewer's latest review for the current head and base, and the CI run for that
head. The AI reviewer token is used solely to resolve the reviewer's login.
"""

import os
from collections.abc import Mapping
from typing import Any

from scripts.pr_size_gate import NUMBER, REPOSITORY, SHA, Http, finish, ref_sha, request

PAGE = 100
CI_WORKFLOW = "pr-checks.yml"
REQUIRED_JOBS = (
    "Code Quality Check",
    "Test Quality Check",
    "Linux Tests (Unit/Integration)",
    "Windows Tests (Unit/Integration)",
    "MacOS Tests (Unit/Integration)",
)


class Skip(Exception):
    """The pull request is not ready; the reason is announced as a notice."""


class Failure(Exception):
    """GitHub could not be read reliably; the reason is reported as an error."""


def get(http: Http, url: str, token: str, kind: type) -> Any:
    """Read one GitHub resource and require a successful response of the expected shape.

    Args:
        http: Request function.
        url: Absolute GitHub REST API URL.
        token: Bearer token for this read.
        kind: Expected JSON type, ``dict`` or ``list``.

    Returns:
        The decoded response body.

    Raises:
        Failure: The response was non-2xx or had the wrong shape.
    """
    # Send the read and reject failed responses.
    code, body = http("GET", url, token, None)
    if not 200 <= code < 300:
        raise Failure(f"GitHub API returned HTTP {code}")
    # Reject untrusted bodies of the wrong shape.
    if not isinstance(body, kind):
        raise Failure("GitHub API returned an unexpected response shape")
    return body


def collection(http: Http, url: str, token: str, key: str) -> list[Any]:
    """Read one GitHub response object and require its named list field.

    Args:
        http: Request function.
        url: Absolute GitHub REST API URL.
        token: Bearer token for this read.
        key: Field that must hold a list, such as ``"jobs"``.

    Returns:
        The list held by the field, possibly empty.

    Raises:
        Failure: The response failed or the field is missing or not a list.
    """
    items = get(http, url, token, dict).get(key)
    if not isinstance(items, list):
        raise Failure(f"GitHub API returned no {key} list")
    return items


def check_pull(pull: dict[str, Any], head: str, base: str) -> None:
    """Require the pull request to be open, ready and still at the admitted commits.

    Args:
        pull: Decoded pull request response.
        head: Admitted head SHA.
        base: Admitted base SHA.

    Raises:
        Skip: The pull request is closed, a draft, or has moved.
    """
    # Compare live state and commits with the admitted run.
    if pull.get("state") != "open":
        raise Skip("the pull request is not open")
    if pull.get("draft") is not False:
        raise Skip("the pull request is a draft")
    if ref_sha(pull, "head") != head or ref_sha(pull, "base") != base:
        raise Skip(f"the pull request is no longer at head {head} and base {base}")


def approved_review(reviews: list[Any], publisher: str, head: str, base: str) -> dict[str, Any]:
    """Return the publisher's latest review for the head when it is a marked approval.

    Args:
        reviews: Every review on the pull request.
        publisher: Login of the AI reviewer.
        head: Admitted head SHA.
        base: Admitted base SHA.

    Returns:
        The approving review.

    Raises:
        Skip: No review, or the latest one is not an approval with the exact marker.
    """
    # Keep only the publisher's reviews of this head; other approvals never count.
    mine = [
        r
        for r in reviews
        if isinstance(r, dict)
        and isinstance(r.get("user"), dict)
        and r["user"].get("login") == publisher
        and r.get("commit_id") == head
        and isinstance(r.get("id"), int)
    ]
    if not mine:
        raise Skip(f"no AI review exists for head {head}")
    # Judge only the latest one, so a later change request overrides an approval.
    latest = max(mine, key=lambda r: r["id"])
    if latest.get("state") != "APPROVED":
        raise Skip(f"the latest AI review is {latest.get('state')}, not APPROVED")
    body = latest.get("body")
    if not isinstance(body, str) or not body.startswith(f"<!-- ai-reviewer:{head}:{base} -->"):
        raise Skip("the latest AI approval lacks the marker for this head and base")
    return latest


def text(value: object) -> str:
    """Return an untrusted value as a single-line output string.

    Args:
        value: Decoded JSON value.

    Returns:
        The string with CR and LF replaced by spaces, or ``""`` for non-strings.
    """
    return value.replace("\r", " ").replace("\n", " ") if isinstance(value, str) else ""


def main(env: Mapping[str, str] = os.environ, http: Http = request) -> int:
    """Decide readiness for the admitted pull request head and base.

    Args:
        env: Process environment with the GitHub Actions inputs.
        http: Request function, injectable so tests need no network.

    Returns:
        0 when eligible or skipped, 1 on invalid input or a GitHub failure.
    """
    # Validate untrusted inputs before any request is sent.
    number, head, base = env.get("PR_NUMBER", ""), env.get("HEAD_SHA", ""), env.get("BASE_SHA", "")
    repo = env.get("GITHUB_REPOSITORY", "")
    if not (
        NUMBER.match(number) and SHA.match(head) and SHA.match(base) and REPOSITORY.match(repo)
    ):
        message = (
            f"Invalid input: PR_NUMBER {number!r}, HEAD_SHA {head!r}, "
            f"BASE_SHA {base!r}, GITHUB_REPOSITORY {repo!r}."
        )
        finish(env, message, error=True, outputs={"eligible": "false"})
        return 1
    root = env.get("GITHUB_API_URL") or "https://api.github.com"
    api = f"{root}/repos/{repo}"
    token, reviewer_token = env.get("GITHUB_TOKEN", ""), env.get("AI_REVIEWER_TOKEN", "")

    try:
        # Require the pull request to be open, ready and at the admitted commits.
        pull = get(http, f"{api}/pulls/{number}", token, dict)
        check_pull(pull, head, base)

        # Resolve the AI reviewer's login with its own token, used nowhere else.
        if not reviewer_token:
            raise Failure("AI_REVIEWER_TOKEN is not set")
        publisher = get(http, f"{root}/user", reviewer_token, dict).get("login")
        if not isinstance(publisher, str) or not publisher:
            raise Failure("GitHub API returned no AI reviewer login")

        # Collect every review page until a short page ends the list.
        reviews: list[Any] = []
        page = 1
        while True:
            url = f"{api}/pulls/{number}/reviews?per_page={PAGE}&page={page}"
            batch = get(http, url, token, list)
            reviews += batch
            if len(batch) < PAGE:
                break
            page += 1
        review = approved_review(reviews, publisher, head, base)

        # Require the latest CI run for the head to have completed successfully.
        url = (
            f"{api}/actions/workflows/{CI_WORKFLOW}/runs"
            f"?head_sha={head}&event=pull_request&per_page={PAGE}"
        )
        runs = [
            r
            for r in collection(http, url, token, "workflow_runs")
            if isinstance(r, dict) and r.get("head_sha") == head and isinstance(r.get("id"), int)
        ]
        if not runs:
            raise Skip(f"no CI run exists for head {head}")
        run = max(runs, key=lambda r: r["id"])
        # A run linked to pull requests must include this one; fork runs list none.
        linked = run.get("pull_requests")
        numbers = [p.get("number") for p in linked or [] if isinstance(p, dict)]
        if linked and int(number) not in numbers:
            raise Skip(f"the latest CI run for head {head} belongs to another pull request")
        if run.get("status") != "completed" or run.get("conclusion") != "success":
            raise Skip(f"the CI run is {run.get('status')}/{run.get('conclusion')}")

        # Require every required CI job to have succeeded in that run.
        url = f"{api}/actions/runs/{run['id']}/jobs?filter=latest&per_page={PAGE}"
        results = {
            j.get("name"): j.get("conclusion")
            for j in collection(http, url, token, "jobs")
            if isinstance(j, dict)
        }
        failed = [name for name in REQUIRED_JOBS if results.get(name) != "success"]
        if failed:
            raise Skip(f"required CI jobs did not succeed: {', '.join(failed)}")

        # Recheck the pull request in case it moved while the evidence was read.
        check_pull(get(http, f"{api}/pulls/{number}", token, dict), head, base)
    # Report an unmet condition as a notice; nothing is announced.
    except Skip as skip:
        finish(
            env, f"Not announcing readiness: {skip}.", error=False, outputs={"eligible": "false"}
        )
        return 0
    # Report any read failure as an error so it is never mistaken for a skip.
    except Exception as error:  # Every read failure blocks the announcement
        reason = str(error) if isinstance(error, Failure) else type(error).__name__
        message = f"Could not determine review readiness: {reason}."
        finish(env, message, error=True, outputs={"eligible": "false"})
        return 1

    # Announce eligibility with single-line outputs for the notifier.
    user = pull.get("user")
    outputs = {
        "eligible": "true",
        "number": number,
        "title": text(pull.get("title")),
        "author": text(user.get("login") if isinstance(user, dict) else None),
        "url": text(pull.get("html_url")),
        "ci_url": text(run.get("html_url")),
        "review_url": text(review.get("html_url")),
    }
    finish(env, f"PR #{number} is ready for human review at {head}.", error=False, outputs=outputs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
