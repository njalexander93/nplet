"""Run the PR Discord announcer script against a local fake Discord webhook.

The workflow's inline script runs unmodified in a subprocess. A test-only wrapper
replaces urllib's transport and ``time.sleep`` before executing it, so requests
reach a local fake Discord server and retries finish instantly. The production
script has no test branch.
"""

import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import yaml

from tests.support import ROOT

WORKFLOW = ROOT / ".github/workflows/pr-announce.yml"
CANARY = "leak-canary-5c1e7d"
WEBHOOK = f"https://discord.com/api/webhooks/4242/{CANARY}"
OFFLINE = "offline"
CI_URL = "https://github.com/example/repo/actions/runs/99"
REVIEW_URL = "https://github.com/example/repo/pull/17#pullrequestreview-5"

# Swap urllib's transport for a proxy-free opener aimed at the fake server, record
# sleeps instead of waiting, then run the unmodified announcer as __main__.
WRAPPER = """
import sys, time, urllib.error, urllib.parse, urllib.request

address, script = sys.argv[1], sys.argv[2]
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

def redirected(request, *args, **kwargs):
    if address == "offline":
        raise urllib.error.URLError(ConnectionRefusedError(61, "refused " + request.full_url))
    parts = urllib.parse.urlsplit(request.full_url)
    request.full_url = urllib.parse.urlunsplit(("http", address, parts.path, parts.query, ""))
    return opener.open(request, *args, **kwargs)

urllib.request.urlopen = redirected
time.sleep = lambda seconds: print(f"fake-sleep {seconds:g}", file=sys.stderr)
with open(script, encoding="utf-8") as handle:
    code = compile(handle.read(), script, "exec")
exec(code, {"__name__": "__main__"})
"""


class FakeDiscord:
    """Scripted stand-in for a Discord execute-webhook endpoint.

    Attributes:
        address: ``host:port`` of the running server, set by the fixture.
        responses: Queued ``(status, headers, body)`` replies, served in order.
        requests: Recorded path, query, headers and JSON body of each POST.
    """

    def __init__(self) -> None:
        """Start with no queued replies, so every POST receives a 200 message."""
        self.address = ""
        self.responses: list[tuple[int, dict[str, str], dict[str, Any]]] = []
        self.requests: list[dict[str, Any]] = []

    def handler(self) -> type[BaseHTTPRequestHandler]:
        """Build a request handler class bound to this fake's queue and log.

        Returns:
            Handler class that records each POST and replays the next reply.
        """
        fake = self

        class Handler(BaseHTTPRequestHandler):
            """Record one webhook POST and answer with the next scripted reply."""

            def do_POST(self) -> None:
                """Record the request, then send the queued or default reply."""
                # Record the request exactly as the announcer sent it.
                length = int(self.headers.get("Content-Length", "0"))
                path, _, query = self.path.partition("?")
                fake.requests.append(
                    {
                        "path": path,
                        "query": parse_qs(query),
                        "headers": dict(self.headers),
                        "json": json.loads(self.rfile.read(length)),
                    }
                )
                # Reply with the next scripted response, or a Discord-like success.
                status, headers, body = (
                    fake.responses.pop(0) if fake.responses else (200, {}, {"id": "1"})
                )
                encoded = json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                for name, value in headers.items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(encoded)

            def log_message(self, format: str, *args: Any) -> None:
                """Silence per-request logging in test output.

                Args:
                    format: Ignored log format string.
                    *args: Ignored log arguments.
                """

        return Handler


def load_workflow() -> dict[Any, Any]:
    """Parse the announcer workflow file.

    Returns:
        Workflow mapping; PyYAML reads the bare ``on`` key as boolean ``True``.
    """
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def announce_step() -> dict[str, Any]:
    """Return the single step of the single announcer job.

    Returns:
        Step mapping containing the inline Python script and its env.
    """
    # Require the ready check then the announcer, which has exactly one step.
    jobs = load_workflow()["jobs"]
    assert list(jobs) == ["ready", "announce"]
    steps = jobs["announce"]["steps"]
    assert len(steps) == 1
    return steps[0]


def announce(
    tmp_path: Path,
    discord: FakeDiscord | None,
    webhook: str | None = WEBHOOK,
    title: str = "Add widget registry",
    kind: str = "opened",
    merged_by: str = "",
    reviewer: str = "",
    team: str = "",
    requested_by: str = "",
    ci_url: str = "",
    review_url: str = "",
) -> subprocess.CompletedProcess[str]:
    """Run the workflow's announcer script through the redirecting wrapper.

    Args:
        tmp_path: Directory receiving the extracted script.
        discord: Fake server to receive requests, or None for a network failure.
        webhook: Value for the webhook env var, or None to leave it unset.
        title: Pull request title passed as event data.
        kind: Announcement kind the workflow derives from the event action.
        merged_by: Merger login; empty as on an opened event.
        reviewer: Requested reviewer login; empty unless a user review request.
        team: Requested team name; empty unless a team review request.
        requested_by: Login of who requested the review; empty unless a review
            request.
        ci_url: Passing CI run URL; empty unless a ready announcement.
        review_url: AI approval URL; empty unless a ready announcement.

    Returns:
        Completed process with captured stdout and stderr.
    """
    # Extract the run body verbatim so the test executes the shipped program.
    script = tmp_path / "announce.py"
    script.write_text(announce_step()["run"], encoding="utf-8")
    # Pass event data only through env, as the workflow does, without host leftovers.
    # Absent event fields arrive as empty strings, matching GitHub's expression result.
    env = {
        key: value for key, value in os.environ.items() if not key.startswith(("DISCORD_", "PR_"))
    }
    env.update(
        ANNOUNCE_KIND=kind,
        PR_MERGED_BY=merged_by,
        PR_REVIEWER=reviewer,
        PR_REVIEW_TEAM=team,
        PR_REQUESTED_BY=requested_by,
        PR_CI_URL=ci_url,
        PR_REVIEW_URL=review_url,
        PR_NUMBER="17",
        PR_TITLE=title,
        PR_AUTHOR="octo-dev",
        PR_URL="https://github.com/example/repo/pull/17",
        PYTHONIOENCODING="utf-8",
    )
    if webhook is not None:
        env["DISCORD_WEBHOOK_URL"] = webhook
    # Execute the script in a fresh interpreter, never inside the test process.
    address = discord.address if discord is not None else OFFLINE
    return subprocess.run(
        [sys.executable, "-c", WRAPPER, address, str(script)],
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )


def output(result: subprocess.CompletedProcess[str]) -> str:
    """Join both captured streams for content assertions.

    Args:
        result: Completed announcer process.

    Returns:
        Combined stdout and stderr text.
    """
    return result.stdout + result.stderr
