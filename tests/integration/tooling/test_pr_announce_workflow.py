"""Verify the PR Discord announcer's triggers, job conditions and trusted ready route."""

import re
from types import SimpleNamespace

import pytest

from tests.pr_announce_support import WORKFLOW, announce_step, load_workflow

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
    for name, event, output in (
        ("PR_NUMBER", "pull_request.number", "number"),
        ("PR_TITLE", "pull_request.title", "title"),
        ("PR_AUTHOR", "pull_request.user.login", "author"),
        ("PR_URL", "pull_request.html_url", "url"),
    ):
        assert (
            step["env"][name] == f"${{{{ github.event.{event} || needs.ready.outputs.{output} }}}}"
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
