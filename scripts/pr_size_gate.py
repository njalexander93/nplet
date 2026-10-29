"""Classify a pull request by changed lines for the PR Size Check job.

The gate runs in the PR Pre-Merge Check workflow from the trusted base checkout with
a read-only workflow token; the job's own result is the size result. It uses the
standard library, reads the live pull request totals reported by GitHub and never
inspects files or patches.
"""

import json
import os
import re
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from typing import Any

LIMIT = 2000
NUMBER = re.compile(r"^[1-9][0-9]*$")
SHA = re.compile(r"^[0-9a-f]{40}$")
REPOSITORY = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

Http = Callable[[str, str, str, dict[str, str] | None], tuple[int, Any]]


def request(method: str, url: str, token: str, body: dict[str, str] | None) -> tuple[int, Any]:
    """Send one authenticated GitHub REST request and decode its JSON response.

    Args:
        method: HTTP method such as ``GET`` or ``POST``.
        url: Absolute GitHub REST API URL.
        token: Workflow token sent as a bearer credential.
        body: JSON payload to send, or ``None`` for no body.

    Returns:
        HTTP status code and the decoded JSON body (``None`` when the body is empty).

    Raises:
        urllib.error.URLError: The request failed, including non-2xx responses.
    """
    # Build the authenticated request with an optional JSON payload.
    data = json.dumps(body).encode() if body is not None else None
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    outgoing = urllib.request.Request(url, data=data, headers=headers, method=method)
    # Send it and decode whatever JSON GitHub returns.
    with urllib.request.urlopen(outgoing, timeout=30) as response:
        raw = response.read()
        return response.status, json.loads(raw) if raw else None


def classify(total: int) -> str:
    """Map a changed-line total to its review tier.

    Args:
        total: Sum of additions and deletions.

    Returns:
        ``auto`` or ``over``.
    """
    # Choose the tier from the fixed line limit.
    return "auto" if total <= LIMIT else "over"


def read_totals(pull: object) -> int | None:
    """Return additions plus deletions when GitHub reported both as valid counts.

    Args:
        pull: Decoded pull request response.

    Returns:
        The changed-line total, or ``None`` when either count is missing or invalid.
    """
    # Reject anything that is not a response object.
    if not isinstance(pull, dict):
        return None
    # Accept only non-negative integers; bool is an int subclass and is rejected.
    counts = [pull.get("additions"), pull.get("deletions")]
    if not all(isinstance(c, int) and not isinstance(c, bool) and c >= 0 for c in counts):
        return None
    return sum(counts)


def summary_for(tier: str, total: int | None, reason: str) -> str:
    """Build the job summary text shown for a tier.

    Args:
        tier: ``auto``, ``over`` or ``unknown``.
        total: Changed-line total, or ``None`` when unknown.
        reason: Short failure reason used by the ``unknown`` tier.

    Returns:
        Summary text, one line per sentence group.
    """
    # Select the approved wording for the tier.
    if tier == "over":
        return (
            f"PR size check failed: this PR changes {total:,} lines (additions + deletions), "
            "over the 2,000-line limit.\n"
            "AI review was skipped. Re-evaluate the scope and split this into smaller PRs."
        )
    if tier == "auto":
        return f"This PR changes {total:,} lines; AI review runs once the other CI checks pass."
    return f"Could not determine PR size: {reason}. AI review was not run."


def ref_sha(pull: object, key: str) -> str | None:
    """Return ``pull[key]["sha"]`` when it is a full lowercase commit SHA.

    Args:
        pull: Decoded pull request response.
        key: ``head`` or ``base``.

    Returns:
        The commit SHA, or ``None`` when absent or malformed.
    """
    # Walk the response defensively; GitHub data is untrusted here.
    ref = pull.get(key) if isinstance(pull, dict) else None
    sha = ref.get("sha") if isinstance(ref, dict) else None
    return sha if isinstance(sha, str) and SHA.match(sha) else None


def append(path: str | None, text: str) -> None:
    """Append text to a GitHub Actions file when the runner provides one.

    Args:
        path: File path from the environment, or ``None`` outside Actions.
        text: Text to append.
    """
    # Skip silently when the runner file is not configured.
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)


def finish(env: Mapping[str, str], message: str, *, error: bool, outputs: dict[str, str]) -> None:
    """Emit the annotation, job summary and step outputs for one outcome.

    Args:
        env: Process environment holding the runner file paths.
        message: Summary text; newlines are joined for the one-line annotation.
        error: Whether to annotate as an error rather than a notice.
        outputs: Step output names and values.
    """
    # Annotate the run and record the human summary.
    print(f"::{'error' if error else 'notice'}::{message.replace(chr(10), ' ')}")
    append(env.get("GITHUB_STEP_SUMMARY"), message + "\n")
    # Publish machine-readable step outputs.
    append(env.get("GITHUB_OUTPUT"), "".join(f"{k}={v}\n" for k, v in outputs.items()))


def main(env: Mapping[str, str] = os.environ, http: Http = request) -> int:
    """Run the size gate for the admitted pull request head.

    Args:
        env: Process environment with the GitHub Actions inputs.
        http: Request function, injectable so tests need no network.

    Returns:
        0 for the ``auto`` tier, otherwise 1.
    """
    # Validate untrusted inputs before any request is sent.
    number, head = env.get("PR_NUMBER", ""), env.get("HEAD_SHA", "")
    repo = env.get("GITHUB_REPOSITORY", "")
    outputs = {"tier": "invalid", "total": ""}
    if not NUMBER.match(number) or not SHA.match(head) or not REPOSITORY.match(repo):
        message = (
            f"Invalid input: PR_NUMBER {number!r}, HEAD_SHA {head!r}, GITHUB_REPOSITORY {repo!r}."
        )
        finish(env, message, error=True, outputs=outputs)
        return 1
    api = f"{env.get('GITHUB_API_URL') or 'https://api.github.com'}/repos/{repo}"
    token = env.get("GITHUB_TOKEN", "")

    # Read the live pull request; any failure leaves the size unknown.
    pull: Any = None
    reason = "pull request response had no valid additions and deletions"
    try:
        code, pull = http("GET", f"{api}/pulls/{number}", token, None)
        if not 200 <= code < 300:
            pull, reason = None, f"GitHub API returned HTTP {code}"
    except Exception as error:  # Every API failure means unknown size
        pull, reason = None, f"GitHub API request failed ({type(error).__name__})"

    # Refuse to judge a head this run was not started for.
    live = ref_sha(pull, "head")
    if pull is not None and live is None:
        pull, reason = None, "pull request response had no head SHA"
    if live is not None and live != head:
        message = (
            f"head-sha {head} is not the current PR head {live}; re-run for the current commit"
        )
        finish(env, message, error=True, outputs={**outputs, "tier": "stale"})
        return 1

    # Classify the totals without ever substituting zero; the job result is the size result.
    total = read_totals(pull)
    tier = "unknown" if total is None else classify(total)
    failed = tier != "auto"
    outputs = {"tier": tier, "total": "" if total is None else str(total)}
    finish(env, summary_for(tier, total, reason), error=failed, outputs=outputs)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
