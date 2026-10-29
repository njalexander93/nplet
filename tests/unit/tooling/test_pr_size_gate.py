"""Check PR size tiers, commit status publication and job outputs without network access."""

import io
import json
import runpy
from pathlib import Path
from typing import Any
from unittest.mock import ANY

import pytest

from scripts import pr_size_gate as gate

pytestmark = pytest.mark.unit

HEAD = "a" * 40
BASE = "b" * 40
STATUS_URL = f"https://api.github.com/repos/o/r/statuses/{HEAD}"


class Fake:
    """Record requests and answer them with scripted pull and status responses."""

    def __init__(self, pull: Any = None, *, pull_code: int = 200, status_code: int = 201) -> None:
        """Store the scripted responses.

        Args:
            pull: Pull response body, or an exception instance to raise on GET.
            pull_code: HTTP status returned for the pull GET.
            status_code: HTTP status returned for the status POST, or 0 to raise.
        """
        self.pull, self.pull_code, self.status_code = pull, pull_code, status_code
        self.calls: list[tuple[str, str, str, dict[str, str] | None]] = []

    def __call__(
        self, method: str, url: str, token: str, body: dict[str, str] | None
    ) -> tuple[int, Any]:
        """Record one request and return its scripted response.

        Args:
            method: HTTP method.
            url: Request URL.
            token: Bearer token.
            body: JSON payload.

        Returns:
            Scripted status code and body.

        Raises:
            Exception: The scripted failure for this request.
        """
        # Record the call for later assertions.
        self.calls.append((method, url, token, body))
        # Answer the GET with the scripted pull response.
        if method == "GET":
            if isinstance(self.pull, Exception):
                raise self.pull
            return self.pull_code, self.pull
        # Answer the POST with the scripted status outcome.
        if not self.status_code:
            raise OSError("down")
        return self.status_code, {}

    @property
    def statuses(self) -> list[dict[str, str] | None]:
        """Return the bodies of every status POST."""
        return [c[3] for c in self.calls if c[0] == "POST"]


def pull(
    additions: object = 0,
    deletions: object = 0,
    head: str = HEAD,
    target: str = "main",
    draft: bool = False,
) -> dict[str, Any]:
    """Build a pull request response.

    Args:
        additions: Reported additions.
        deletions: Reported deletions.
        head: Live head SHA.
        target: Base branch; the repository default branch is ``main``.
        draft: Whether GitHub reports the PR as a draft.

    Returns:
        Minimal pull response body.
    """
    return {
        "additions": additions,
        "deletions": deletions,
        "head": {"sha": head},
        "base": {"sha": BASE, "ref": target, "repo": {"default_branch": "main"}},
        "draft": draft,
    }


def run(tmp_path: Path, fake: Fake, **overrides: str) -> tuple[int, str, dict[str, str]]:
    """Run the gate with a valid environment plus overrides.

    Args:
        tmp_path: Directory for the runner output and summary files.
        fake: Fake request function.
        overrides: Environment values to replace.

    Returns:
        Exit code, summary text and parsed step outputs.
    """
    # Assemble the Actions environment.
    env = {
        "GITHUB_TOKEN": "t",
        "GITHUB_REPOSITORY": "o/r",
        "PR_NUMBER": "7",
        "HEAD_SHA": HEAD,
        "RUN_URL": "https://run",
        "GITHUB_OUTPUT": str(tmp_path / "out"),
        "GITHUB_STEP_SUMMARY": str(tmp_path / "summary"),
        **overrides,
    }
    code = gate.main(env, fake)
    # Read back what the gate wrote for the runner.
    out = tmp_path / "out"
    lines = out.read_text().splitlines() if out.exists() else []
    summary = tmp_path / "summary"
    return (
        code,
        summary.read_text() if summary.exists() else "",
        dict(line.split("=", 1) for line in lines),
    )


@pytest.mark.parametrize(
    ("additions", "deletions", "tier", "state", "code"),
    [
        (0, 0, "auto", "success", 0),
        (100, 100, "auto", "success", 0),
        (1000, 0, "auto", "success", 0),
        (1000, 1, "auto", "success", 0),
        (0, 2000, "auto", "success", 0),
        (2000, 1, "over", "failure", 1),
    ],
)
def test_boundaries_select_tier_status_and_outputs(
    tmp_path: Path, additions: int, deletions: int, tier: str, state: str, code: int
) -> None:
    """Additions and deletions are summed and mapped to the tier boundaries.

    Args:
        tmp_path: Disposable directory supplied by pytest.
        additions: Reported additions.
        deletions: Reported deletions.
        tier: Expected tier.
        state: Expected commit status state.
        code: Expected exit code.
    """
    # Run the gate against the scripted totals.
    fake = Fake(pull(additions, deletions))
    result, summary, outputs = run(tmp_path, fake)
    total = additions + deletions
    # Check the status, outputs and exit code.
    assert result == code
    assert fake.calls[0][:3] == ("GET", "https://api.github.com/repos/o/r/pulls/7", "t")
    assert fake.calls[1][1] == STATUS_URL
    assert fake.statuses[0] == {
        "state": state,
        "context": "PR Size Check",
        "description": gate.status_for(tier, total)[1],
        "target_url": "https://run",
    }
    assert outputs == {
        "tier": tier,
        "total": str(total),
        "head": HEAD,
        "base": BASE,
        "draft": "false",
    }
    assert f"{total:,} lines" in summary


def test_texts_match_the_approved_wording() -> None:
    """Every tier uses the fixed summary and status wording."""
    # Check the human-approved over-limit text exactly.
    assert gate.summary_for("over", 2001, "") == (
        "PR size check failed: this PR changes 2,001 lines (additions + deletions), "
        "over the 2,000-line limit.\n"
        "AI review was skipped. Re-evaluate the scope and split this into smaller PRs."
    )
    assert gate.status_for("over", 2001) == (
        "failure",
        "PR changes 2,001 lines, over the 2,000-line limit. AI review skipped. "
        "Re-evaluate and split into smaller PRs.",
    )
    # Check the remaining tiers.
    assert gate.summary_for("auto", 5, "") == (
        "This PR changes 5 lines; AI review runs automatically."
    )
    assert gate.summary_for("unknown", None, "boom") == (
        "Could not determine PR size: boom. AI review was not run."
    )
    assert gate.status_for("auto", 1000) == ("success", "1,000 changed lines (limit 2,000)")
    assert gate.status_for("unknown", None) == (
        "failure",
        "Could not determine PR size. AI review was not run.",
    )


def test_descriptions_fit_the_github_limit_at_large_totals() -> None:
    """Descriptions stay within 140 characters for a nine-digit-scale total."""
    # Check each tier's description length.
    for tier in ("auto", "over", "unknown"):
        assert len(gate.status_for(tier, 9_999_999)[1]) <= 140


UNKNOWN_STATUS = {
    "state": "failure",
    "context": "PR Size Check",
    "description": "Could not determine PR size. AI review was not run.",
    "target_url": "https://run",
}


@pytest.mark.parametrize(
    ("fake", "posted"),
    [
        (Fake(OSError("network")), True),
        (Fake(pull(1, 1), pull_code=502), True),
        (Fake(pull(None, 1)), True),
        (Fake({"deletions": 1, "head": {"sha": HEAD}}), True),
        (Fake(pull(-1, 1)), True),
        (Fake(pull(1.0, 1)), True),
        (Fake(pull("1", 1)), True),
        (Fake(pull(True, 1)), True),
        (Fake(pull(1, False)), True),
        (Fake(["not", "a", "pull"]), True),
        (Fake({"additions": 1, "deletions": 1, "head": "x"}), True),
        (Fake(pull(None, 1, target="release")), False),
        (Fake({**pull(1, 1, target="release"), "head": {}}), False),
    ],
)
def test_unreadable_size_fails_and_posts_unless_the_base_is_known_non_default(
    tmp_path: Path, fake: Fake, posted: bool
) -> None:
    """Unreadable sizes fail without a zero total; only a known non-default base skips posting.

    Args:
        tmp_path: Disposable directory supplied by pytest.
        fake: Scripted failing or invalid response.
        posted: False only when the response names a non-default base.
    """
    # Run the gate against the unreadable response.
    code, summary, outputs = run(tmp_path, fake)
    # Check the failure, outputs and that branch identity alone decides posting.
    assert code == 1
    assert fake.statuses == ([UNKNOWN_STATUS] if posted else [])
    assert outputs == {"tier": "unknown", "total": "", "head": HEAD, "base": "", "draft": ANY}
    assert summary.startswith("Could not determine PR size: ")
    assert ("not posted" in summary) is not posted


def test_stale_head_posts_no_status(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A head that is no longer current fails without a status.

    Args:
        tmp_path: Disposable directory supplied by pytest.
        capsys: Pytest fixture capturing annotations.
    """
    # Run the gate when the live head has moved.
    fake = Fake(pull(1, 1, head="c" * 40))
    code, summary, outputs = run(tmp_path, fake)
    # Check the stale failure and that no status was posted.
    assert code == 1
    assert fake.statuses == []
    assert summary == (
        f"head-sha {HEAD} is not the current PR head {'c' * 40}; re-run for the current commit\n"
    )
    assert outputs["tier"] == "stale"
    assert capsys.readouterr().out.startswith("::error::head-sha ")


@pytest.mark.parametrize(
    "overrides",
    [
        {"PR_NUMBER": "0"},
        {"PR_NUMBER": "-3"},
        {"PR_NUMBER": "x"},
        {"PR_NUMBER": "07"},
        {"PR_NUMBER": "\u00b2"},
        {"HEAD_SHA": "A" * 40},
        {"HEAD_SHA": "a" * 39},
        {"GITHUB_REPOSITORY": "o/r/../x"},
    ],
)
def test_invalid_input_sends_no_requests(tmp_path: Path, overrides: dict[str, str]) -> None:
    """Malformed inputs fail before any request.

    Args:
        tmp_path: Disposable directory supplied by pytest.
        overrides: Invalid environment values.
    """
    # Run the gate with the invalid input.
    fake = Fake(pull())
    code, summary, outputs = run(tmp_path, fake, **overrides)
    # Check that nothing was requested.
    assert code == 1
    assert fake.calls == []
    assert summary.startswith("Invalid input:")
    assert outputs["tier"] == "invalid"


@pytest.mark.parametrize("status_code", [0, 422])
def test_status_post_failure_fails_the_gate(
    tmp_path: Path, status_code: int, capsys: pytest.CaptureFixture[str]
) -> None:
    """A rejected or failed status POST exits 1 even for the auto tier.

    Args:
        tmp_path: Disposable directory supplied by pytest.
        status_code: Status POST outcome, 0 meaning an exception.
        capsys: Pytest fixture capturing annotations.
    """
    # Run the gate with a failing status endpoint.
    code, summary, _ = run(tmp_path, Fake(pull(1, 1), status_code=status_code))
    # Check the failure is reported.
    assert code == 1
    assert summary.endswith(f"Could not post the PR Size Check status for {HEAD}.\n")
    assert capsys.readouterr().out.startswith("::error::")


def test_runs_without_runner_files_and_honours_api_url(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Outside Actions the gate only annotates and uses the configured API base.

    Args:
        capsys: Pytest fixture capturing annotations.
    """
    # Run the gate without runner files and with an enterprise API URL.
    fake = Fake(pull(1, 1))
    env = {"GITHUB_REPOSITORY": "o/r", "PR_NUMBER": "7", "HEAD_SHA": HEAD}
    code = gate.main({**env, "GITHUB_API_URL": "https://ghe/api/v3"}, fake)
    # Check the notice and request URL.
    assert code == 0
    assert fake.calls[0][1] == "https://ghe/api/v3/repos/o/r/pulls/7"
    assert (
        capsys.readouterr().out
        == "::notice::This PR changes 2 lines; AI review runs automatically.\n"
    )


def test_request_sends_authenticated_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default request function authenticates and decodes JSON bodies.

    Args:
        monkeypatch: Pytest fixture that restores the patched opener.
    """
    # Replace the network opener with a recorder.
    sent: list[Any] = []

    class Response(io.BytesIO):
        """Stand in for an HTTP response."""

        status = 201

    def urlopen(outgoing: Any, timeout: int) -> Response:
        """Record the request and return a canned body.

        Args:
            outgoing: Prepared request.
            timeout: Socket timeout in seconds.

        Returns:
            Canned response, empty for GET.
        """
        sent.append(outgoing)
        return Response(b"" if outgoing.get_method() == "GET" else b'{"ok": true}')

    monkeypatch.setattr(gate.urllib.request, "urlopen", urlopen)
    # Send a POST with a body and a GET without one.
    assert gate.request("POST", "https://x/s", "tok", {"a": "b"}) == (201, {"ok": True})
    assert gate.request("GET", "https://x/p", "tok", None) == (201, None)
    # Check headers and payload.
    assert sent[0].get_header("Authorization") == "Bearer tok"
    assert json.loads(sent[0].data) == {"a": "b"}
    assert sent[1].data is None


def test_entry_point_exits_with_main_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The executable entry exits with the gate result.

    Args:
        monkeypatch: Pytest fixture that restores the patched environment.
        capsys: Pytest fixture capturing annotations.
    """
    # Run the script with invalid input so no request is attempted.
    for name in ("PR_NUMBER", "HEAD_SHA", "GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(SystemExit) as result:
        runpy.run_path(gate.__file__, run_name="__main__")
    # Check the failing exit and annotation.
    assert result.value.code == 1
    assert capsys.readouterr().out.startswith("::error::Invalid input:")


BRANCHES = {"ref": "main", "repo": {"default_branch": "main"}}
OTHER = {"ref": "release", "repo": {"default_branch": "main"}}


@pytest.mark.parametrize(
    ("body", "reason", "posted"),
    [
        ({**pull(1, 1), "base": BRANCHES}, "no base SHA", True),
        ({**pull(1, 1), "base": {**BRANCHES, "sha": "B" * 40}}, "no base SHA", True),
        ({**pull(1, 1), "base": {**BRANCHES, "sha": "b" * 39}}, "no base SHA", True),
        ({**pull(1, 1), "base": OTHER}, "no base SHA", False),
        ({**pull(1, 1), "base": None}, "no base SHA", True),
        (pull(1, 1, head="A" * 40), "no head SHA", True),
        (pull(1, 1, head="a" * 41), "no head SHA", True),
        (pull(1, 1, target=""), "no base or default branch", True),
        ({**pull(1, 1), "base": {"sha": BASE, "ref": "main"}}, "no base or default branch", True),
        (
            {**pull(1, 1), "base": {"sha": BASE, "ref": "main", "repo": None}},
            "no base or default branch",
            True,
        ),
    ],
)
def test_malformed_refs_make_size_unknown(
    tmp_path: Path, body: dict[str, Any], reason: str, posted: bool
) -> None:
    """Valid totals with a missing or malformed ref are never admitted.

    A known non-default base keeps its branch identity, so it posts no shared status;
    an unknown base still posts the failure.

    Args:
        tmp_path: Disposable directory supplied by pytest.
        body: Pull response with a bad ref.
        reason: Expected reason fragment in the summary.
        posted: False only when the response names a non-default base.
    """
    # Run the gate against the malformed ref.
    fake = Fake(body)
    code, summary, outputs = run(tmp_path, fake)
    # Check the failure, reason, outputs and posting by branch identity alone.
    assert code == 1
    assert fake.statuses == ([UNKNOWN_STATUS] if posted else [])
    assert summary.startswith(
        f"Could not determine PR size: pull request response had {reason}. AI review was not run.\n"
    )
    assert outputs == {"tier": "unknown", "total": "", "head": HEAD, "base": "", "draft": ANY}


@pytest.mark.parametrize(("additions", "tier", "code"), [(5, "auto", 0), (2001, "over", 1)])
def test_non_default_base_posts_no_status(
    tmp_path: Path, additions: int, tier: str, code: int
) -> None:
    """A PR into another branch keeps its own tier and exit code but never sets the shared status.

    Args:
        tmp_path: Disposable directory supplied by pytest.
        additions: Reported additions.
        tier: Expected tier.
        code: Expected exit code.
    """
    # Run the gate for a PR whose base is not the default branch.
    fake = Fake(pull(additions, 0, target="release"))
    result, summary, outputs = run(tmp_path, fake)
    # Check that only the pull was read and the PR's own result is intact.
    assert [c[0] for c in fake.calls] == ["GET"]
    assert result == code
    assert outputs == {
        "tier": tier,
        "total": str(additions),
        "head": HEAD,
        "base": BASE,
        "draft": "false",
    }
    assert summary.endswith(
        "PR Size Check not posted: base release is not the default branch main.\n"
    )


def test_unreadable_recount_replaces_an_earlier_success(tmp_path: Path) -> None:
    """A recount that cannot read the PR overwrites the head's earlier pass with a failure.

    Args:
        tmp_path: Disposable directory supplied by pytest.
    """
    # Pass once, then recount the same head against an unreadable response.
    first, second = Fake(pull(5, 0)), Fake(OSError("network"))
    assert run(tmp_path, first)[0] == 0
    assert run(tmp_path, second)[0] == 1
    # Check that both runs posted to the same head, the second as a failure.
    assert [c[1] for c in first.calls + second.calls if c[0] == "POST"] == [STATUS_URL] * 2
    assert [s["state"] for s in first.statuses + second.statuses if s] == ["success", "failure"]


@pytest.mark.parametrize("body", [pull(1, 1, draft=True), {**pull(1, 1), "draft": None}])
def test_draft_or_unreported_draft_is_not_eligible(tmp_path: Path, body: dict[str, Any]) -> None:
    """A draft PR, or one whose draft flag is missing, is reported as a draft.

    Args:
        tmp_path: Disposable directory supplied by pytest.
        body: Pull response with a draft or invalid draft flag.
    """
    # Size still passes, but the review job reads the draft output and skips.
    code, _, outputs = run(tmp_path, Fake(body))
    assert (code, outputs["tier"], outputs["draft"]) == (0, "auto", "true")
