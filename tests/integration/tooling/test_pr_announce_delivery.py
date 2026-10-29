"""Verify the PR Discord announcer's payloads, validation, retries and redaction.

Each test runs the workflow's unmodified announcer script against a local fake
Discord webhook through ``tests.pr_announce_support``.
"""

import json
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from tests.pr_announce_support import (
    CANARY,
    CI_URL,
    REVIEW_URL,
    WEBHOOK,
    FakeDiscord,
    announce,
    output,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def discord() -> Iterator[FakeDiscord]:
    """Serve a fake Discord webhook on an ephemeral loopback port.

    Yields:
        The running fake, whose requests and responses tests inspect and script.
    """
    # Start the server on a free 127.0.0.1 port in a background thread.
    fake = FakeDiscord()
    server = ThreadingHTTPServer(("127.0.0.1", 0), fake.handler())
    # A short poll interval keeps shutdown from adding half a second per test.
    thread = threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
    )
    thread.start()
    fake.address = f"127.0.0.1:{server.server_address[1]}"
    # Stop and release the port even when the test fails.
    try:
        yield fake
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize(
    ("webhook", "query"),
    [
        (WEBHOOK, {"wait": ["true"]}),
        (f"{WEBHOOK}?thread_id=99&wait=false", {"thread_id": ["99"], "wait": ["true"]}),
        (f"https://discordapp.com/api/webhooks/4242/{CANARY}", {"wait": ["true"]}),
    ],
)
def test_announcement_posts_pr_embed_once(
    tmp_path: Path, discord: FakeDiscord, webhook: str, query: dict[str, list[str]]
) -> None:
    """Post one embed carrying the PR title, link, number and author.

    Args:
        tmp_path: Directory for the extracted script.
        discord: Fake webhook endpoint recording the request.
        webhook: Accepted webhook form, with or without an existing query.
        query: Query the endpoint must receive, always forcing wait=true.

    Raises:
        AssertionError: The request is missing, repeated or malformed, or output leaks.
    """
    # Run a successful delivery.
    result = announce(tmp_path, discord, webhook=webhook)
    assert result.returncode == 0, output(result)
    assert "Announced opened pull request #17 in Discord." in result.stdout
    assert CANARY not in output(result)
    # Require exactly one request to the webhook path with the forced wait flag.
    assert len(discord.requests) == 1
    request = discord.requests[0]
    assert request["path"] == f"/api/webhooks/4242/{CANARY}"
    assert request["query"] == query
    assert "Python-urllib" not in request["headers"]["User-Agent"]
    # Require the embed to carry the PR data with mentions disabled.
    body = request["json"]
    embed = body["embeds"][0]
    assert embed["title"] == "Add widget registry"
    assert embed["url"] == "https://github.com/example/repo/pull/17"
    values = {field["name"]: field["value"] for field in embed["fields"]}
    assert values == {"Pull request": "#17", "Author": "octo-dev"}
    assert "opened" in body["content"]
    assert body["allowed_mentions"] == {"parse": []}


def test_merge_announcement_names_merger_and_differs_from_opened(
    tmp_path: Path, discord: FakeDiscord
) -> None:
    """Post a merge message that names who merged it and looks unlike an opening.

    Args:
        tmp_path: Directory for the extracted script.
        discord: Fake webhook endpoint recording both requests.

    Raises:
        AssertionError: The merge message omits the merger, says opened, or shares
            the opened message's color.
    """
    # Announce the same PR as opened and then as merged.
    opened = announce(tmp_path, discord)
    merged = announce(tmp_path, discord, kind="merged", merged_by="maint-lead")
    assert opened.returncode == 0, output(opened)
    assert merged.returncode == 0, output(merged)
    assert "Announced merged pull request #17 in Discord." in merged.stdout
    assert CANARY not in output(merged)
    # Require the merge embed to keep the PR data and add the merger.
    opened_body, merged_body = (request["json"] for request in discord.requests)
    embed = merged_body["embeds"][0]
    assert embed["title"] == "Add widget registry"
    assert embed["url"] == "https://github.com/example/repo/pull/17"
    values = {field["name"]: field["value"] for field in embed["fields"]}
    assert values == {"Pull request": "#17", "Author": "octo-dev", "Merged by": "maint-lead"}
    assert "merged by maint-lead" in merged_body["content"]
    assert "opened" not in merged_body["content"]
    assert merged_body["allowed_mentions"] == {"parse": []}
    # Require visually distinct colors for the two kinds.
    assert embed["color"] != opened_body["embeds"][0]["color"]


def test_review_request_names_reviewer_and_differs_from_other_kinds(
    tmp_path: Path, discord: FakeDiscord
) -> None:
    """Post one review-request message naming the reviewer, requester and PR.

    Args:
        tmp_path: Directory for the extracted script.
        discord: Fake webhook endpoint recording the opened, merged and review posts.

    Raises:
        AssertionError: The review message is repeated, omits the reviewer, PR or
            requester, enables mentions, or looks like an opened or merged message.
    """
    # Announce the same PR as opened, merged and then as a review request.
    opened = announce(tmp_path, discord)
    merged = announce(tmp_path, discord, kind="merged", merged_by="maint-lead")
    review = announce(
        tmp_path, discord, kind="review", reviewer="octo-reviewer", requested_by="maint-lead"
    )
    for result in (opened, merged, review):
        assert result.returncode == 0, output(result)
    assert "Announced review request on pull request #17 in Discord." in review.stdout
    assert CANARY not in output(review)
    # Require exactly one review post carrying the PR data and the single reviewer.
    assert len(discord.requests) == 3
    opened_body, merged_body, review_body = (request["json"] for request in discord.requests)
    embed = review_body["embeds"][0]
    assert embed["title"] == "Add widget registry"
    assert embed["url"] == "https://github.com/example/repo/pull/17"
    values = {field["name"]: field["value"] for field in embed["fields"]}
    assert values == {
        "Pull request": "#17",
        "Author": "octo-dev",
        "Reviewer": "octo-reviewer",
        "Requested by": "maint-lead",
    }
    assert review_body["content"] == (
        "Review requested from octo-reviewer on pull request #17 by octo-dev"
    )
    assert review_body["allowed_mentions"] == {"parse": []}
    # Require the review kind to read and look unlike opened and merged posts.
    assert "opened" not in review_body["content"]
    assert "merged" not in review_body["content"]
    colors = {body["embeds"][0]["color"] for body in (opened_body, merged_body, review_body)}
    assert len(colors) == 3


def test_team_review_request_identifies_the_team(tmp_path: Path, discord: FakeDiscord) -> None:
    """Post a team review request that names the team rather than a user.

    Args:
        tmp_path: Directory for the extracted script.
        discord: Fake webhook endpoint recording the request.

    Raises:
        AssertionError: The team request fails, omits the team, or invents a reviewer.
    """
    # Announce a request whose event carries only requested_team.
    result = announce(
        tmp_path, discord, kind="review", team="core-maintainers", requested_by="maint-lead"
    )
    assert result.returncode == 0, output(result)
    # Require the team to be named and labelled as a team, with no user reviewer.
    body = discord.requests[0]["json"]
    values = {field["name"]: field["value"] for field in body["embeds"][0]["fields"]}
    assert values == {
        "Pull request": "#17",
        "Author": "octo-dev",
        "Team": "core-maintainers",
        "Requested by": "maint-lead",
    }
    assert body["content"] == (
        "Review requested from team core-maintainers on pull request #17 by octo-dev"
    )
    assert body["allowed_mentions"] == {"parse": []}


def test_each_review_request_names_only_its_own_reviewer(
    tmp_path: Path, discord: FakeDiscord
) -> None:
    """Name only the reviewer carried by each event across consecutive requests.

    Args:
        tmp_path: Directory for the extracted script.
        discord: Fake webhook endpoint recording both requests.

    Raises:
        AssertionError: A later request repeats an earlier reviewer or lists both.
    """
    # Request a first reviewer, then a second (or a re-request) as a separate event.
    first = announce(tmp_path, discord, kind="review", reviewer="alice", requested_by="maint-lead")
    second = announce(tmp_path, discord, kind="review", reviewer="bob", requested_by="maint-lead")
    assert first.returncode == 0, output(first)
    assert second.returncode == 0, output(second)
    # Require each post to name its own reviewer and never the other one.
    first_body, second_body = (request["json"] for request in discord.requests)
    for body, own, other in ((first_body, "alice", "bob"), (second_body, "bob", "alice")):
        values = {field["name"]: field["value"] for field in body["embeds"][0]["fields"]}
        assert values["Reviewer"] == own
        assert own in body["content"]
        assert other not in json.dumps(body)


@pytest.mark.parametrize(
    ("reviewer", "team", "requested_by", "message"),
    [
        ("", "", "maint-lead", "must name exactly one of PR_REVIEWER or PR_REVIEW_TEAM"),
        ("octo-reviewer", "core", "maint-lead", "must name exactly one of PR_REVIEWER"),
        ("octo-reviewer", "", "", "metadata is missing: PR_REQUESTED_BY"),
    ],
)
def test_invalid_review_data_fails_before_any_request(
    tmp_path: Path,
    discord: FakeDiscord,
    reviewer: str,
    team: str,
    requested_by: str,
    message: str,
) -> None:
    """Refuse to post a review request without exactly one target and a requester.

    Args:
        tmp_path: Directory for the extracted script.
        discord: Fake webhook endpoint that must stay unused.
        reviewer: Requested reviewer login passed by the workflow.
        team: Requested team name passed by the workflow.
        requested_by: Requester login passed by the workflow.
        message: Expected diagnostic fragment.

    Raises:
        AssertionError: The job passes, sends a request or leaks the webhook URL.
    """
    # Run a review request with inconsistent event data.
    result = announce(
        tmp_path, discord, kind="review", reviewer=reviewer, team=team, requested_by=requested_by
    )
    # Require an annotated failure without any delivery or URL echo.
    assert result.returncode != 0
    assert "::error::" in result.stdout
    assert message in result.stdout
    assert CANARY not in output(result)
    assert discord.requests == []


def test_untrusted_team_name_stays_inert_data(tmp_path: Path, discord: FakeDiscord) -> None:
    """Send a hostile team name as literal text that pings nobody.

    Args:
        tmp_path: Directory for the extracted script.
        discord: Fake webhook endpoint recording the request.

    Raises:
        AssertionError: The team name is altered, breaks the workflow command
            output, or mentions are not suppressed.
    """
    # Deliver a team name containing mention syntax and a forged annotation line.
    team = "core @everyone <@123>\n::error::forged"
    result = announce(tmp_path, discord, kind="review", team=team, requested_by="maint-lead")
    assert result.returncode == 0, output(result)
    # Require the name to reach Discord verbatim with every mention type disabled.
    body = discord.requests[0]["json"]
    values = {field["name"]: field["value"] for field in body["embeds"][0]["fields"]}
    assert values["Team"] == team
    assert f"team {team}" in body["content"]
    assert body["allowed_mentions"] == {"parse": []}
    # Require the job log to neither echo the name nor emit a forged annotation.
    assert "::error::" not in result.stdout
    assert "@everyone" not in output(result)


@pytest.mark.parametrize(
    ("kind", "merged_by", "message"),
    [
        ("merged", "", "metadata is missing: PR_MERGED_BY"),
        ("", "", "ANNOUNCE_KIND must be 'opened', 'merged', 'review' or 'ready'"),
        ("closed", "maint-lead", "ANNOUNCE_KIND must be 'opened', 'merged', 'review' or 'ready'"),
    ],
)
def test_invalid_event_data_fails_before_any_request(
    tmp_path: Path, discord: FakeDiscord, kind: str, merged_by: str, message: str
) -> None:
    """Refuse to post when the kind is unknown or a merge lacks its merger.

    Args:
        tmp_path: Directory for the extracted script.
        discord: Fake webhook endpoint that must stay unused.
        kind: Announcement kind passed by the workflow.
        merged_by: Merger login passed by the workflow.
        message: Expected diagnostic fragment.

    Raises:
        AssertionError: The job passes, sends a request or leaks the webhook URL.
    """
    # Run with inconsistent event data.
    result = announce(tmp_path, discord, kind=kind, merged_by=merged_by)
    # Require an annotated failure without any delivery or URL echo.
    assert result.returncode != 0
    assert "::error::" in result.stdout
    assert message in result.stdout
    assert CANARY not in output(result)
    assert discord.requests == []


def test_ready_announcement_links_ci_and_review(tmp_path: Path, discord: FakeDiscord) -> None:
    """Post one ready message linking the passing CI run and the AI approval.

    Args:
        tmp_path: Directory for the extracted script.
        discord: Fake webhook endpoint recording the request.

    Raises:
        AssertionError: The ready message omits a link, enables mentions, leaves a
            long title unclipped or leaks the webhook URL.
    """
    # Announce a ready PR whose hostile title is too long and carries mentions.
    title = "@everyone <@123> " + "x" * 300
    result = announce(
        tmp_path, discord, title=title, kind="ready", ci_url=CI_URL, review_url=REVIEW_URL
    )
    assert result.returncode == 0, output(result)
    assert "Announced ready pull request #17 in Discord." in result.stdout
    assert CANARY not in output(result)
    assert "@everyone" not in output(result)
    # Require one post with the PR data and both links, mentions disabled.
    assert len(discord.requests) == 1
    body = discord.requests[0]["json"]
    embed = body["embeds"][0]
    assert body["content"] == "Ready for human review: pull request #17 by octo-dev"
    assert embed["url"] == "https://github.com/example/repo/pull/17"
    assert embed["fields"] == [
        {"name": "Pull request", "value": "#17", "inline": True},
        {"name": "Author", "value": "octo-dev", "inline": True},
        {"name": "CI run", "value": f"[View run]({CI_URL})", "inline": True},
        {"name": "AI review", "value": f"[View review]({REVIEW_URL})", "inline": True},
    ]
    assert body["allowed_mentions"] == {"parse": []}
    # Require the title clipped but otherwise literal.
    assert embed["title"] == title[:255] + "…"


@pytest.mark.parametrize(
    ("ci_url", "review_url", "missing"),
    [
        ("", REVIEW_URL, "PR_CI_URL"),
        (CI_URL, "", "PR_REVIEW_URL"),
        ("", "", "PR_CI_URL, PR_REVIEW_URL"),
    ],
)
def test_ready_without_links_fails_before_any_request(
    tmp_path: Path, discord: FakeDiscord, ci_url: str, review_url: str, missing: str
) -> None:
    """Refuse to post a ready message without both evidence links.

    Args:
        tmp_path: Directory for the extracted script.
        discord: Fake webhook endpoint that must stay unused.
        ci_url: CI run URL passed by the workflow.
        review_url: AI approval URL passed by the workflow.
        missing: Expected missing-metadata list.

    Raises:
        AssertionError: The job passes, sends a request or leaks the webhook URL.
    """
    # Run a ready announcement lacking a link.
    result = announce(tmp_path, discord, kind="ready", ci_url=ci_url, review_url=review_url)
    # Require an annotated failure without any delivery or URL echo.
    assert result.returncode != 0
    assert f"metadata is missing: {missing}." in result.stdout
    assert CANARY not in output(result)
    assert discord.requests == []


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Ping @everyone @here <@123> <@&456>", "Ping @everyone @here <@123> <@&456>"),
        ("x" * 300, "x" * 255 + "…"),
        ("\U0001f680" * 200, "\U0001f680" * 127 + "…"),
    ],
)
def test_untrusted_title_stays_inert_data(
    tmp_path: Path, discord: FakeDiscord, title: str, expected: str
) -> None:
    """Send hostile or oversized titles as literal, bounded text that pings nobody.

    Args:
        tmp_path: Directory for the extracted script.
        discord: Fake webhook endpoint recording the request.
        title: Untrusted pull request title.
        expected: Embed title Discord must receive.

    Raises:
        AssertionError: The title is altered unexpectedly, exceeds 256 characters,
            or mentions are not suppressed.
    """
    # Deliver the untrusted title.
    result = announce(tmp_path, discord, title=title)
    assert result.returncode == 0, output(result)
    # Require literal, length-bounded data with every mention type disabled.
    body = discord.requests[0]["json"]
    assert body["embeds"][0]["title"] == expected
    assert len(body["embeds"][0]["title"].encode("utf-16-le")) // 2 <= 256
    assert body["allowed_mentions"] == {"parse": []}
    assert title not in result.stdout


@pytest.mark.parametrize(
    ("webhook", "message"),
    [
        (None, "secret is missing or empty"),
        ("", "secret is missing or empty"),
        (f"https://example.com/api/webhooks/4242/{CANARY}", "is not a Discord webhook URL"),
        (f"http://discord.com/api/webhooks/4242/{CANARY}", "is not a Discord webhook URL"),
        (
            f"https://discord.com.evil.example/api/webhooks/4242/{CANARY}",
            "is not a Discord webhook URL",
        ),
    ],
)
def test_invalid_secret_fails_before_any_request(
    tmp_path: Path, discord: FakeDiscord, webhook: str | None, message: str
) -> None:
    """Reject a missing or non-Discord secret with a named fix and no echo.

    Args:
        tmp_path: Directory for the extracted script.
        discord: Fake webhook endpoint that must stay unused.
        webhook: Missing, empty or lookalike secret value.
        message: Expected diagnostic fragment.

    Raises:
        AssertionError: The job passes, sends a request, omits the secret name,
            or prints the secret value.
    """
    # Run with the invalid secret.
    result = announce(tmp_path, discord, webhook=webhook)
    # Require an annotated failure that names the secret but never its value.
    assert result.returncode != 0
    assert "::error::" in result.stdout
    assert message in result.stdout
    assert "DISCORD_PR_WEBHOOK_URL" in result.stdout
    assert CANARY not in output(result)
    assert discord.requests == []


@pytest.mark.parametrize(
    ("status", "discord_message", "hint"),
    [
        (404, "Unknown Webhook", "webhook was deleted or DISCORD_PR_WEBHOOK_URL is wrong"),
        (400, f"Invalid body for /api/webhooks/4242/{CANARY}", "rejected the message payload"),
    ],
)
def test_rejected_request_fails_without_retry(
    tmp_path: Path, discord: FakeDiscord, status: int, discord_message: str, hint: str
) -> None:
    """Fail a client-error response once, with an actionable hint and redacted detail.

    Args:
        tmp_path: Directory for the extracted script.
        discord: Fake webhook endpoint answering with a client error.
        status: Non-retryable HTTP status.
        discord_message: Discord error text, possibly echoing the webhook path.
        hint: Expected remediation fragment.

    Raises:
        AssertionError: The failure retries, passes, lacks guidance or leaks the URL.
    """
    # Answer the only attempt with a Discord JSON error.
    discord.responses.append((status, {}, {"message": discord_message, "code": 10015}))
    result = announce(tmp_path, discord)
    # Require one attempt and a status-specific, redacted annotation.
    assert result.returncode != 0
    assert len(discord.requests) == 1
    assert f"::error::Discord announcement failed with HTTP {status}." in result.stdout
    assert "code=10015" in result.stdout
    assert hint in result.stdout
    assert CANARY not in output(result)


def test_rate_limit_retries_once_after_discord_delay(tmp_path: Path, discord: FakeDiscord) -> None:
    """Retry a 429 once after Discord's body retry_after, then succeed.

    Args:
        tmp_path: Directory for the extracted script.
        discord: Fake webhook endpoint that throttles the first attempt.

    Raises:
        AssertionError: The retry is missing, waits for the wrong delay or changes data.
    """
    # Throttle once; the body delay must win over the larger header.
    discord.responses.append(
        (429, {"Retry-After": "5"}, {"message": "rate limited", "retry_after": 0.01})
    )
    result = announce(tmp_path, discord)
    # Require success on the identical second request after the recorded delay.
    assert result.returncode == 0, output(result)
    assert len(discord.requests) == 2
    assert discord.requests[0]["json"] == discord.requests[1]["json"]
    assert "fake-sleep 0.01" in result.stderr
    assert "Announced opened pull request #17 in Discord." in result.stdout
    assert CANARY not in output(result)


def test_retry_delay_is_capped(tmp_path: Path, discord: FakeDiscord) -> None:
    """Bound an excessive Discord retry_after to the workflow's maximum delay.

    Args:
        tmp_path: Directory for the extracted script.
        discord: Fake webhook endpoint requesting a long wait.

    Raises:
        AssertionError: The requested delay is not capped at ten seconds.
    """
    # Request a one-minute wait, then accept the retry.
    discord.responses.append((429, {}, {"message": "rate limited", "retry_after": 60}))
    result = announce(tmp_path, discord)
    # Require the capped delay and a successful second attempt.
    assert result.returncode == 0, output(result)
    assert "fake-sleep 10" in result.stderr
    assert len(discord.requests) == 2
    assert CANARY not in output(result)


def test_repeated_server_error_fails_after_two_attempts(
    tmp_path: Path, discord: FakeDiscord
) -> None:
    """Stop after two 5xx responses and advise a re-run.

    Args:
        tmp_path: Directory for the extracted script.
        discord: Fake webhook endpoint failing both attempts.

    Raises:
        AssertionError: The job passes, retries more than once, or leaks the URL.
    """
    # Fail both attempts with Retry-After: 0.
    discord.responses.extend([(500, {"Retry-After": "0"}, {}), (500, {"Retry-After": "0"}, {})])
    result = announce(tmp_path, discord)
    # Require exactly two attempts and a re-run hint.
    assert result.returncode != 0
    assert len(discord.requests) == 2
    assert "fake-sleep 0" in result.stderr
    assert "::error::Discord announcement failed with HTTP 500." in result.stdout
    assert "re-run this job" in result.stdout
    assert CANARY not in output(result)


def test_network_failure_retries_and_redacts_reason(tmp_path: Path) -> None:
    """Retry an unreachable Discord once and report a reason without the URL.

    Args:
        tmp_path: Directory for the extracted script.

    Raises:
        AssertionError: The job passes, skips the retry, or echoes the webhook URL
            embedded in the network error.
    """
    # Raise a connection error whose text contains the full webhook URL.
    result = announce(tmp_path, None)
    # Require one retry, a typed failure annotation and redaction of the URL.
    assert result.returncode != 0
    assert result.stdout.count("Could not reach Discord") == 2
    assert "::error::Could not reach Discord (ConnectionRefusedError" in result.stdout
    assert "<redacted>" in result.stdout
    assert CANARY not in output(result)
