"""Check review-readiness decisions and outputs without network access."""

from pathlib import Path
from typing import Any

import pytest

from scripts import pr_review_ready as ready

pytestmark = pytest.mark.unit

HEAD, BASE, OTHER = "a" * 40, "b" * 40, "c" * 40
API = "https://api.github.com"
REPO = f"{API}/repos/o/r"
PULL_URL = f"{REPO}/pulls/7"
USER_URL = f"{API}/user"
MARKER = f"<!-- ai-reviewer:{HEAD}:{BASE} -->\n## AI review"
CI_URL = (
    f"{REPO}/actions/workflows/pr-checks.yml/runs?head_sha={HEAD}&event=pull_request&per_page=100"
)
JOBS_URL = f"{REPO}/actions/runs/9/jobs?filter=latest&per_page=100"


def reviews_url(page: int) -> str:
    """Return the reviews URL for one page.

    Args:
        page: One-based page number.

    Returns:
        The reviews page URL.
    """
    return f"{PULL_URL}/reviews?per_page=100&page={page}"


def pull(**changes: Any) -> dict[str, Any]:
    """Build an open, ready pull response at the admitted commits.

    Args:
        changes: Fields to replace.

    Returns:
        Pull response body.
    """
    return {
        "state": "open",
        "draft": False,
        "head": {"sha": HEAD},
        "base": {"sha": BASE},
        "title": "Fix\r\nthings",
        "user": {"login": "dev"},
        "html_url": "https://pr",
        **changes,
    }


def review(rid: int = 1, state: str = "APPROVED", login: str = "bot", **changes: Any) -> Any:
    """Build one review response.

    Args:
        rid: Review id.
        state: Review state.
        login: Reviewer login.
        changes: Fields to replace.

    Returns:
        Review body.
    """
    return {
        "id": rid,
        "state": state,
        "user": {"login": login},
        "commit_id": HEAD,
        "body": MARKER,
        "html_url": f"https://review/{rid}",
        **changes,
    }


def green_jobs(**conclusions: str) -> dict[str, Any]:
    """Build a jobs response where every required job succeeded unless overridden.

    Args:
        conclusions: Job name to conclusion overrides; ``"missing"`` drops the job.

    Returns:
        Jobs response body.
    """
    names = {n: conclusions.get(n, "success") for n in ready.REQUIRED_JOBS}
    return {"jobs": [{"name": n, "conclusion": c} for n, c in names.items() if c != "missing"]}


def responses(**changes: Any) -> dict[str, Any]:
    """Build the URL-keyed responses of an eligible pull request.

    Args:
        changes: URL to response replacements.

    Returns:
        URL to body, ``(code, body)`` tuple or exception.
    """
    run = {"id": 9, "head_sha": HEAD, "status": "completed", "conclusion": "success"}
    return {
        PULL_URL: pull(),
        USER_URL: {"login": "bot"},
        reviews_url(1): [review()],
        CI_URL: {
            "workflow_runs": [
                {**run, "id": 3, "conclusion": "failure"},
                {**run, "html_url": "https://ci"},
            ]
        },
        JOBS_URL: green_jobs(),
        **changes,
    }


class Fake:
    """Answer GET requests from URL-keyed responses and record each call."""

    def __init__(self, table: dict[str, Any], recheck: Any = None) -> None:
        """Store the responses.

        Args:
            table: URL to body, ``(code, body)`` tuple or exception to raise.
            recheck: Pull body returned on the second pull read, if any.
        """
        self.table, self.recheck = table, recheck
        self.calls: list[tuple[str, str]] = []

    def __call__(self, method: str, url: str, token: str, body: Any) -> tuple[int, Any]:
        """Record one request and return its scripted response.

        Args:
            method: HTTP method.
            url: Request URL.
            token: Bearer token.
            body: JSON payload.

        Returns:
            Scripted status code and body.

        Raises:
            Exception: The scripted failure for this URL.
        """
        # Record the call and pick the response, switching the pull on recheck.
        self.calls.append((url, token))
        answer = self.table[url]
        if url == PULL_URL and self.recheck is not None and len(self.calls) > 1:
            answer = self.recheck
        # Raise, pass through or wrap the scripted response.
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, tuple) else (200, answer)


def run(tmp_path: Path, fake: Fake, **overrides: str) -> tuple[int, str, dict[str, str]]:
    """Run the helper with a valid environment plus overrides.

    Args:
        tmp_path: Directory for the runner output and summary files.
        fake: Fake request function.
        overrides: Environment values to replace.

    Returns:
        Exit code, summary text and parsed step outputs.
    """
    # Assemble the Actions environment and run the helper.
    env = {
        "GITHUB_TOKEN": "read-token",
        "AI_REVIEWER_TOKEN": "ai-token",
        "GITHUB_REPOSITORY": "o/r",
        "PR_NUMBER": "7",
        "HEAD_SHA": HEAD,
        "BASE_SHA": BASE,
        "GITHUB_OUTPUT": str(tmp_path / "out"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        **overrides,
    }
    code = ready.main(env, fake)
    # Read back what the helper wrote for the runner.
    lines = (tmp_path / "out").read_text().splitlines()
    return code, (tmp_path / "summary").read_text(), dict(line.split("=", 1) for line in lines)


def test_approved_current_green_pull_is_eligible(tmp_path: Path) -> None:
    """A marked approval on the current head with green CI yields single-line outputs."""
    # Run against an approved, current and green pull request.
    fake = Fake(responses())
    code, summary, outputs = run(tmp_path, fake)
    # Expect every announcement output with the title flattened to one line.
    assert code == 0
    assert outputs == {
        "eligible": "true",
        "number": "7",
        "title": "Fix  things",
        "author": "dev",
        "url": "https://pr",
        "ci_url": "https://ci",
        "review_url": "https://review/1",
    }
    assert "ready for human review" in summary
    # The reviewer token reads only the login; every other read uses the read token.
    assert fake.calls[1] == (USER_URL, "ai-token")
    assert all(t == "read-token" for u, t in fake.calls if u != USER_URL)
    assert fake.calls[-1][0] == PULL_URL
    text = (tmp_path / "out").read_text() + summary
    assert "ai-token" not in text and "read-token" not in text


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({reviews_url(1): [review(state="CHANGES_REQUESTED")]}, "CHANGES_REQUESTED"),
        ({reviews_url(1): [review(state="DISMISSED")]}, "DISMISSED"),
        ({reviews_url(1): [review(state="COMMENTED")]}, "COMMENTED"),
        ({reviews_url(1): [review(body="## AI review")]}, "marker"),
        ({reviews_url(1): [review(body=f"<!-- ai-reviewer:{HEAD}:{OTHER} -->")]}, "marker"),
        ({reviews_url(1): [review(login="spoof")]}, "no AI review"),
        ({reviews_url(1): [review(commit_id=OTHER)]}, "no AI review"),
        ({reviews_url(1): []}, "no AI review"),
        ({reviews_url(1): [review(1), review(2, "CHANGES_REQUESTED")]}, "CHANGES_REQUESTED"),
        ({PULL_URL: pull(head={"sha": OTHER})}, "no longer at head"),
        ({PULL_URL: pull(base={"sha": OTHER})}, "no longer at head"),
        ({PULL_URL: pull(draft=True)}, "draft"),
        ({PULL_URL: pull(state="closed")}, "not open"),
        ({CI_URL: {"workflow_runs": []}}, "no CI run"),
        ({CI_URL: {"workflow_runs": [{"id": 9, "head_sha": OTHER}]}}, "no CI run"),
        # The newest run decides, even when an older run for the head succeeded.
        (
            {
                CI_URL: {
                    "workflow_runs": [
                        {"id": 3, "head_sha": HEAD, "status": "completed", "conclusion": "success"},
                        {"id": 9, "head_sha": HEAD, "status": "completed", "conclusion": "failure"},
                    ]
                }
            },
            "completed/failure",
        ),
        (
            {
                CI_URL: {
                    "workflow_runs": [
                        {
                            "id": 9,
                            "head_sha": HEAD,
                            "status": "completed",
                            "conclusion": "success",
                            "pull_requests": [{"number": 8}],
                        }
                    ]
                }
            },
            "another pull request",
        ),
        (
            {
                CI_URL: {
                    "workflow_runs": [
                        {"id": 9, "head_sha": HEAD, "status": "completed", "conclusion": "failure"}
                    ]
                }
            },
            "completed/failure",
        ),
        (
            {
                CI_URL: {
                    "workflow_runs": [
                        {"id": 9, "head_sha": HEAD, "status": "in_progress", "conclusion": None}
                    ]
                }
            },
            "in_progress",
        ),
        ({JOBS_URL: green_jobs(**{"Code Quality Check": "failure"})}, "Code Quality Check"),
        ({JOBS_URL: green_jobs(**{"Test Quality Check": "skipped"})}, "Test Quality Check"),
        ({JOBS_URL: green_jobs(**{"MacOS Tests (Unit/Integration)": "missing"})}, "MacOS"),
        ({JOBS_URL: {"jobs": []}}, "Code Quality Check"),
    ],
)
def test_unready_pull_is_skipped(tmp_path: Path, changes: dict[str, Any], reason: str) -> None:
    """Any unmet review, pull or CI condition skips with a notice and no announcement.

    Args:
        tmp_path: Directory for the runner output and summary files.
        changes: URL to response replacements that break one condition.
        reason: Text the skip notice must contain.
    """
    # Run against the broken condition.
    code, summary, outputs = run(tmp_path, Fake(responses(**changes)))
    # Expect a successful skip that names the reason and is not eligible.
    assert (code, outputs) == (0, {"eligible": "false"})
    assert summary.startswith("Not announcing readiness:") and reason in summary


def test_recheck_finding_new_head_skips(tmp_path: Path) -> None:
    """A pull that moves while evidence is read is not announced."""
    # Serve a new head only on the final recheck.
    fake = Fake(responses(), recheck=pull(head={"sha": OTHER}))
    code, summary, outputs = run(tmp_path, fake)
    # Expect a skip naming the moved head.
    assert (code, outputs) == (0, {"eligible": "false"})
    assert "no longer at head" in summary


def test_approval_on_second_review_page_counts(tmp_path: Path) -> None:
    """Reviews are read page by page until a short page."""
    # Fill page one with other users' reviews and put the approval on page two.
    full = [review(i, login="other") for i in range(100, 200)]
    fake = Fake(responses(**{reviews_url(1): full, reviews_url(2): [review(300)]}))
    code, _, outputs = run(tmp_path, fake)
    # Expect the page-two approval to be found.
    assert (code, outputs["review_url"]) == (0, "https://review/300")


@pytest.mark.parametrize(
    ("changes", "env"),
    [
        ({PULL_URL: OSError("down")}, {}),
        ({PULL_URL: (404, {})}, {}),
        ({PULL_URL: []}, {}),
        ({USER_URL: (401, {})}, {}),
        ({USER_URL: {"login": ""}}, {}),
        ({reviews_url(1): (500, None)}, {}),
        ({JOBS_URL: OSError("down")}, {}),
        # Malformed collections fail; valid empty ones skip in the test above.
        ({CI_URL: {}}, {}),
        ({CI_URL: {"workflow_runs": {}}}, {}),
        ({JOBS_URL: {}}, {}),
        ({JOBS_URL: {"jobs": None}}, {}),
        ({}, {"AI_REVIEWER_TOKEN": ""}),
        ({}, {"PR_NUMBER": "x"}),
        ({}, {"BASE_SHA": "B" * 40}),
    ],
)
def test_failures_block_announcement_with_error(
    tmp_path: Path, changes: dict[str, Any], env: dict[str, str]
) -> None:
    """Invalid input, missing reviewer token or a failed read exits 1 without eligibility.

    Args:
        tmp_path: Directory for the runner output and summary files.
        changes: URL to failing response replacements.
        env: Environment overrides that invalidate the input.
    """
    # Run against the failure.
    code, summary, outputs = run(tmp_path, Fake(responses(**changes)), **env)
    # Expect an error exit that is not eligible and echoes no token.
    assert (code, outputs) == (1, {"eligible": "false"})
    assert "ai-token" not in summary
