"""Verify the PR Discord announcer's trigger safety and webhook delivery behavior.

Delivery tests run the workflow's unmodified announcer script against a local
fake Discord webhook through ``tests.pr_announce_support``.
"""

import json
import re
import threading
from collections.abc import Iterator
from http.server import ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.pr_announce_support import (
    CANARY,
    CI_URL,
    REVIEW_URL,
    WEBHOOK,
    WORKFLOW,
    FakeDiscord,
    announce,
    announce_step,
    load_workflow,
    output,
)

pytestmark = pytest.mark.integration
JOB_IF = (
    "${{ !cancelled() && (github.event.action == 'opened' || "
    "(github.event.action == 'closed' && github.event.pull_request.merged == true) || "
    "github.event.action == 'review_requested' || "
    "needs.ready.outputs.eligible == 'true') }}"
)
KIND = (
    "${{ needs.ready.outputs.eligible == 'true' && 'ready' || "
    "github.event.action == 'closed' && 'merged' || "
    "github.event.action == 'review_requested' && 'review' || 'opened' }}"
)
READY_ENV = {
    "GITHUB_TOKEN": "${{ github.token }}",
    "AI_REVIEWER_TOKEN": "${{ secrets.AI_REVIEWER_TOKEN }}",
    "PR_NUMBER": "${{ inputs.pr-number }}",
    "HEAD_SHA": "${{ inputs.head-sha }}",
    "BASE_SHA": "${{ inputs.base-sha }}",
}
READY_OUTPUTS = ("eligible", "number", "title", "author", "url", "ci_url", "review_url")
# Actions that must never announce, so they must stay out of the trigger types.
UNSUBSCRIBED = ("review_request_removed", "assigned", "unassigned")
# Expressions the evaluator accepts: event and needs paths, !cancelled(), quoted
# lowercase/underscore literals, comparisons, logical operators, parentheses and
# booleans. Anything else fails the test.
EXPRESSION = re.compile(
    r"(?:(?:github|needs)(?:\.\w+)+|!cancelled\(\)|'[a-z_]*'|==|&&|\|\||[()]|true|false|\s+)+"
)


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


def evaluate(expression: str, action: str, merged: bool, eligible: str = "") -> object:
    """Evaluate a simple GitHub Actions expression against a pull request event.

    Only the subset used by the announcer is supported. GitHub's ``&&`` and ``||``
    return operand values like Python's ``and`` and ``or``, so a token-for-token
    translation preserves both boolean conditions and ``a && 'x' || 'y'`` selection.
    The run is never cancelled, so ``!cancelled()`` is true.

    Args:
        expression: Condition or value, with or without ``${{ }}`` delimiters.
        action: Pull request event action, such as ``opened`` or ``closed``.
        merged: Value of ``github.event.pull_request.merged``.
        eligible: Ready job ``eligible`` output; empty when that job was skipped.

    Returns:
        Expression result as GitHub would compute it.

    Raises:
        AssertionError: The expression uses syntax outside the supported subset.
    """
    # Strip optional delimiters and reject anything beyond the supported grammar.
    text = expression.strip().removeprefix("${{").removesuffix("}}").strip()
    assert EXPRESSION.fullmatch(text), text
    # Translate operators and literals to their Python equivalents.
    # Bare booleans become Python's; the quoted 'true' output literal stays a string.
    python = text.replace("!cancelled()", " True ").replace("&&", " and ").replace("||", " or ")
    python = re.sub(r"(?<!')\b(true|false)\b(?!')", lambda m: m[1].title(), python)
    # Evaluate with only the event and ready-job contexts in scope.
    event = SimpleNamespace(action=action, pull_request=SimpleNamespace(merged=merged))
    ready = SimpleNamespace(outputs=SimpleNamespace(eligible=eligible))
    context = {
        "__builtins__": {},
        "github": SimpleNamespace(event=event),
        "needs": SimpleNamespace(ready=ready),
    }
    return eval(python, context)  # noqa: S307 - grammar-checked, repository-owned text


def test_workflow_trigger_and_permissions_cannot_run_pr_code() -> None:
    """Pin the pull_request_target safety envelope of the announcer workflow.

    Raises:
        AssertionError: The trigger widens, permissions appear, an action or checkout
            is used, or event expressions reach the script body.
    """
    # Require the opened/closed/review_requested pull_request_target trigger, the
    # typed reusable route and an empty token scope.
    workflow = load_workflow()
    trigger = workflow.get("on", workflow.get(True))
    assert trigger == {
        "pull_request_target": {"types": ["opened", "closed", "review_requested"]},
        "workflow_call": {
            "inputs": {
                "pr-number": {"required": True, "type": "number"},
                "head-sha": {"required": True, "type": "string"},
                "base-sha": {"required": True, "type": "string"},
            },
            "secrets": {
                "DISCORD_PR_WEBHOOK_URL": {"required": True},
                "AI_REVIEWER_TOKEN": {"required": True},
            },
        },
    }
    assert workflow["permissions"] == {}
    # Require one bounded Linux job that cannot regain permissions or run actions.
    job = workflow["jobs"]["announce"]
    assert job["name"] == "Announce PR in Discord"
    assert job["runs-on"] == "ubuntu-24.04"
    assert 0 < job["timeout-minutes"] <= 10
    assert "permissions" not in job
    assert job["needs"] == "ready"
    step = announce_step()
    assert all("uses" not in item for item in job["steps"])
    assert step["shell"] == "python"
    # Require metadata to arrive only as env data and the secret to be the named one.
    assert "${{" not in step["run"]
    assert step["env"]["DISCORD_WEBHOOK_URL"] == "${{ secrets.DISCORD_PR_WEBHOOK_URL }}"
    # The AI reviewer token reaches only the ready check, never the announcer.
    text = WORKFLOW.read_text(encoding="utf-8")
    assert re.findall(r"secrets\.\w+", text) == [
        "secrets.AI_REVIEWER_TOKEN",
        "secrets.DISCORD_PR_WEBHOOK_URL",
    ]
    assert set(step["env"]) == {
        "DISCORD_WEBHOOK_URL",
        "ANNOUNCE_KIND",
        "PR_NUMBER",
        "PR_TITLE",
        "PR_AUTHOR",
        "PR_URL",
        "PR_MERGED_BY",
        "PR_REVIEWER",
        "PR_REVIEW_TEAM",
        "PR_REQUESTED_BY",
        "PR_CI_URL",
        "PR_REVIEW_URL",
    }
    # Require PR data to fall back to the ready job's outputs on the reusable route.
    for name, event, field in (
        ("PR_NUMBER", "pull_request.number", "number"),
        ("PR_TITLE", "pull_request.title", "title"),
        ("PR_AUTHOR", "pull_request.user.login", "author"),
        ("PR_URL", "pull_request.html_url", "url"),
    ):
        assert (
            step["env"][name] == f"${{{{ github.event.{event} || needs.ready.outputs.{field} }}}}"
        )
    assert step["env"]["PR_CI_URL"] == "${{ needs.ready.outputs.ci_url }}"
    assert step["env"]["PR_REVIEW_URL"] == "${{ needs.ready.outputs.review_url }}"
    assert step["env"]["PR_MERGED_BY"] == "${{ github.event.pull_request.merged_by.login }}"
    # Require review data to come from the event's own single target and sender.
    assert step["env"]["PR_REVIEWER"] == "${{ github.event.requested_reviewer.login }}"
    assert step["env"]["PR_REVIEW_TEAM"] == "${{ github.event.requested_team.name }}"
    assert step["env"]["PR_REQUESTED_BY"] == "${{ github.event.sender.login }}"


@pytest.mark.parametrize(
    ("action", "merged", "kind"),
    [
        ("opened", False, "opened"),
        ("closed", True, "merged"),
        ("closed", False, None),
        ("reopened", False, None),
        ("edited", False, None),
        ("synchronize", False, None),
        ("review_requested", False, "review"),
        ("review_request_removed", False, None),
        ("assigned", False, None),
        ("unassigned", False, None),
    ],
)
def test_job_runs_only_for_opened_merged_or_review_requested_prs(
    action: str, merged: bool, kind: str | None
) -> None:
    """Announce opened PRs, merged PRs and review requests, and skip other actions.

    Args:
        action: Pull request event action GitHub would deliver.
        merged: Whether the pull request is merged in that event.
        kind: Expected announcement kind, or None when the job must be skipped.

    Raises:
        AssertionError: The job condition or derived kind diverges from the policy.
    """
    # Pin the exact expressions so the evaluator checks what GitHub will run.
    job = load_workflow()["jobs"]["announce"]
    assert job["if"] == JOB_IF
    assert announce_step()["env"]["ANNOUNCE_KIND"] == KIND
    # Require removals and assignee changes to stay outside the subscribed types.
    workflow = load_workflow()
    types = workflow.get("on", workflow.get(True))["pull_request_target"]["types"]
    assert not set(UNSUBSCRIBED) & set(types)
    # Require the job to run exactly for opened, merged and review-requested events.
    runs = evaluate(job["if"], action, merged)
    assert runs is (kind is not None)
    # For running jobs, require the env to carry the matching announcement kind.
    if kind is not None:
        assert evaluate(KIND, action, merged) == kind


@pytest.mark.parametrize(
    ("action", "eligible", "kind"),
    [
        ("completed", "true", "ready"),
        ("", "true", "ready"),
        ("completed", "false", None),
        ("", "false", None),
        ("completed", "", None),
    ],
)
def test_reusable_route_announces_only_an_eligible_ready_check(
    action: str, eligible: str, kind: str | None
) -> None:
    """Announce a caller's run only when the ready check reported eligibility.

    Args:
        action: Caller event action: ``completed`` for a CI completion, empty for a
            manual dispatch.
        eligible: Ready job ``eligible`` output; empty when it failed to write one.
        kind: Expected announcement kind, or None when the job must be skipped.

    Raises:
        AssertionError: An ineligible or failed check announces, or an eligible one
            is not announced as ready.
    """
    # Evaluate the pinned condition and kind for the caller's event.
    job = load_workflow()["jobs"]["announce"]
    assert evaluate(job["if"], action, False, eligible) is (kind is not None)
    if kind is not None:
        assert evaluate(KIND, action, False, eligible) == kind


def test_ready_job_runs_trusted_helper_only_on_reusable_route() -> None:
    """Pin the ready job's skip condition, read-only scope, checkout and helper wiring.

    Raises:
        AssertionError: The job runs on pull_request_target, gains write access,
            checks out PR code, keeps credentials or miswires the helper.
    """
    # Require a skip on pull_request_target, where no head input exists.
    job = load_workflow()["jobs"]["ready"]
    assert job["if"] == "inputs.head-sha != ''"
    assert job["runs-on"] == "ubuntu-24.04"
    assert 0 < job["timeout-minutes"] <= 10
    assert job["permissions"] == {
        "actions": "read",
        "contents": "read",
        "pull-requests": "read",
    }
    assert job["outputs"] == {
        name: f"${{{{ steps.check.outputs.{name} }}}}" for name in READY_OUTPUTS
    }
    # Require the trusted default checkout without a PR ref or persisted token.
    checkout, check = job["steps"]
    assert checkout["uses"].startswith("actions/checkout@11d5960a326750d5838078e36cf38b85af677262")
    assert checkout["with"] == {"persist-credentials": False}
    # Require the helper module and the env it reads.
    assert check["id"] == "check"
    assert check["run"] == "python3 -m scripts.pr_review_ready"
    assert check["env"] == READY_ENV


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
