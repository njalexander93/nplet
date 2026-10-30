"""Check PR size tiers, job results and outputs without network access."""

import io
import json
import runpy
from pathlib import Path
from typing import Any

import pytest

from scripts import pr_size_gate as gate

pytestmark = pytest.mark.unit

HEAD = "a" * 40
BASE = "b" * 40


class Fake:
    """Record requests and answer them with a scripted pull response."""

    def __init__(self, pull: Any = None, *, pull_code: int = 200) -> None:
        """Store the scripted response.

        Args:
            pull: Pull response body, or an exception instance to raise on GET.
            pull_code: HTTP status returned for the pull GET.
        """
        self.pull, self.pull_code = pull, pull_code
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
        # Answer with the scripted pull response.
        if isinstance(self.pull, Exception):
            raise self.pull
        return self.pull_code, self.pull


def pull(additions: object = 0, deletions: object = 0, head: str = HEAD) -> dict[str, Any]:
    """Build a pull request response.

    Args:
        additions: Reported additions.
        deletions: Reported deletions.
        head: Live head SHA.

    Returns:
        Minimal pull response body.
    """
    return {
        "additions": additions,
        "deletions": deletions,
        "head": {"sha": head},
        "base": {"sha": BASE},
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
    ("additions", "deletions", "tier", "code"),
    [
        (0, 0, "auto", 0),
        (100, 100, "auto", 0),
        (1000, 0, "auto", 0),
        (1000, 1, "auto", 0),
        (0, 2000, "auto", 0),
        (2000, 1, "over", 1),
    ],
)
def test_boundaries_select_tier_result_and_outputs(
    tmp_path: Path, additions: int, deletions: int, tier: str, code: int
) -> None:
    """Additions and deletions are summed and mapped to the tier boundaries.

    Args:
        tmp_path: Disposable directory supplied by pytest.
        additions: Reported additions.
        deletions: Reported deletions.
        tier: Expected tier.
        code: Expected exit code, which decides the job result.
    """
    # Run the gate against the scripted totals.
    fake = Fake(pull(additions, deletions))
    result, summary, outputs = run(tmp_path, fake)
    total = additions + deletions
    # Check the single read, outputs and exit code; nothing is written to GitHub.
    assert result == code
    assert fake.calls == [("GET", "https://api.github.com/repos/o/r/pulls/7", "t", None)]
    assert outputs == {"tier": tier, "total": str(total)}
    assert f"{total:,} lines" in summary


def test_texts_match_the_approved_wording() -> None:
    """Every tier uses the fixed summary wording."""
    # Check the human-approved over-limit text exactly.
    assert gate.summary_for("over", 2001, "") == (
        "PR size check failed: this PR changes 2,001 lines (additions + deletions), "
        "over the 2,000-line limit.\n"
        "AI review was skipped. Re-evaluate the scope and split this into smaller PRs."
    )
    # Check the remaining tiers.
    assert gate.summary_for("auto", 5, "") == (
        "This PR changes 5 lines; AI review runs once the other CI checks pass."
    )
    assert gate.summary_for("unknown", None, "boom") == (
        "Could not determine PR size: boom. AI review was not run."
    )


@pytest.mark.parametrize(
    "fake",
    [
        Fake(OSError("network")),
        Fake(pull(1, 1), pull_code=502),
        Fake(pull(None, 1)),
        Fake({"deletions": 1, "head": {"sha": HEAD}}),
        Fake(pull(-1, 1)),
        Fake(pull(1.0, 1)),
        Fake(pull("1", 1)),
        Fake(pull(True, 1)),
        Fake(pull(1, False)),
        Fake(["not", "a", "pull"]),
        Fake({"additions": 1, "deletions": 1, "head": "x"}),
    ],
)
def test_unreadable_size_fails(tmp_path: Path, fake: Fake) -> None:
    """Unreadable sizes fail the job without substituting a zero total.

    Args:
        tmp_path: Disposable directory supplied by pytest.
        fake: Scripted failing or invalid response.
    """
    # Run the gate against the unreadable response.
    code, summary, outputs = run(tmp_path, fake)
    # Check the failure and outputs.
    assert code == 1
    assert outputs == {"tier": "unknown", "total": ""}
    assert summary.startswith("Could not determine PR size: ")


def test_stale_head_fails(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A head that is no longer current fails the job.

    Args:
        tmp_path: Disposable directory supplied by pytest.
        capsys: Pytest fixture capturing annotations.
    """
    # Run the gate when the live head has moved.
    fake = Fake(pull(1, 1, head="c" * 40))
    code, summary, outputs = run(tmp_path, fake)
    # Check the stale failure.
    assert code == 1
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
        == "::notice::This PR changes 2 lines; AI review runs once the other CI checks pass.\n"
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


@pytest.mark.parametrize(
    "body",
    [pull(1, 1, head="A" * 40), pull(1, 1, head="a" * 41), {**pull(1, 1), "head": None}],
)
def test_malformed_head_makes_size_unknown(tmp_path: Path, body: dict[str, Any]) -> None:
    """Valid totals with a missing or malformed head SHA never pass.

    Args:
        tmp_path: Disposable directory supplied by pytest.
        body: Pull response with a bad head ref.
    """
    # Run the gate against the malformed head.
    code, summary, outputs = run(tmp_path, Fake(body))
    # Check the failure and its reason.
    assert code == 1
    assert summary.startswith(
        "Could not determine PR size: pull request response had no head SHA. "
        "AI review was not run.\n"
    )
    assert outputs == {"tier": "unknown", "total": ""}
