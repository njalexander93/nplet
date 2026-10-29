"""Verify the AI review workflow admits every review only after its head's CI passed."""

from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import pr_review_admit, pr_review_ready
from tests.support import ROOT

pytestmark = pytest.mark.integration
WORKFLOW = ROOT / ".github/workflows/ai-review.yml"
NUMBER = "${{ github.event.workflow_run.pull_requests[0].number || inputs.pr-number }}"
ADMIT_ENV = {
    "GITHUB_TOKEN": "${{ github.token }}",
    "PR_NUMBER": NUMBER,
    "HEAD_SHA": "${{ github.event.workflow_run.head_sha || inputs.head-sha }}",
}


def load() -> dict[Any, Any]:
    """Parse the AI review workflow.

    Returns:
        The decoded workflow mapping.
    """
    return yaml.safe_load(WORKFLOW.read_text())


def test_triggers_inputs_and_concurrency() -> None:
    """Require CI-completion and dispatch triggers, the head-sha input and no workflow concurrency.

    Raises:
        AssertionError: A trigger, input or concurrency setting drifted.
    """
    # Read the trigger block; YAML may decode the bare ``on`` key as True.
    workflow = load()
    trigger = workflow.get("on", workflow.get(True))
    assert set(trigger) == {"workflow_run", "workflow_dispatch"}
    assert trigger["workflow_run"] == {"workflows": ["PR Pre-Merge Check"], "types": ["completed"]}
    # Require both dispatch inputs.
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"pr-number", "head-sha"}
    assert inputs["head-sha"]["required"] is True
    assert inputs["head-sha"]["type"] == "string"
    # Rejected CI completions must not cancel an admitted review.
    assert "concurrency" not in workflow


def test_admit_job_runs_trusted_read_only_admission() -> None:
    """Require read-only permissions, a base checkout, a trusted size recount and admission.

    Raises:
        AssertionError: Admission gains access, checks out the head, miswires env or
            lets a trigger skip the size or CI requirement.
    """
    # Inspect the admission job's permissions and secret use; no size job remains.
    jobs = load()["jobs"]
    assert "size" not in jobs
    admit = jobs["admit"]
    assert admit["permissions"] == {
        "actions": "read",
        "contents": "read",
        "pull-requests": "read",
    }
    assert "secrets." not in yaml.safe_dump(admit)
    # Only a successful CI run with a PR association (never a fork) or a dispatch proceeds.
    assert " ".join(admit["if"].split()) == (
        "github.event_name == 'workflow_dispatch' || "
        "(github.event.workflow_run.conclusion == 'success' && "
        "github.event.workflow_run.pull_requests[0].number)"
    )
    # Require the trusted base checkout, then unconditional size and admission steps.
    checkout, size, step = admit["steps"]
    assert checkout["uses"].startswith("actions/checkout@11d5960a326750d5838078e36cf38b85af677262")
    assert checkout["with"] == {"persist-credentials": False}
    assert size["run"] == "python3 scripts/pr_size_gate.py" and "if" not in size
    assert size["env"] == ADMIT_ENV
    assert step["run"] == "python3 -m scripts.pr_review_admit" and "if" not in step
    assert step["env"] == ADMIT_ENV
    assert admit["outputs"] == {
        k: f"${{{{ steps.admit.outputs.{k} }}}}" for k in ("ready", "head", "base")
    }


def test_reviewer_jobs_depend_on_admission_and_forward_shas() -> None:
    """Require admission and ready eligibility, per-PR cancellation and forwarded SHAs.

    Raises:
        AssertionError: A review can start without admission, ready check or bound SHAs.
    """
    # Require the dependency graph.
    jobs = load()["jobs"]
    resolve, review = jobs["resolve-reviewer"], jobs["review"]
    assert resolve["needs"] == "admit"
    assert review["needs"] == ["admit", "resolve-reviewer"]
    # Require the exact eligibility expression.
    condition = " ".join(resolve["if"].split())
    assert condition == (
        "github.event_name == 'workflow_dispatch' || needs.admit.outputs.ready == 'true'"
    )
    # Only an admitted review joins the per-PR cancelling group.
    assert review["concurrency"] == {"group": f"ai-review-{NUMBER}", "cancel-in-progress": True}
    # Require the manual number converted for the reviewer's typed number input.
    assert review["with"]["pr-number"] == (
        "${{ github.event.workflow_run.pull_requests[0].number || fromJSON(inputs.pr-number) }}"
    )
    # Require the reviewer to receive the admitted head and base.
    assert review["with"]["expected-head-sha"] == "${{ needs.admit.outputs.head }}"
    assert review["with"]["expected-base-sha"] == "${{ needs.admit.outputs.base }}"
    # Require secrets only in the reviewer and announcer calls.
    for name, job in jobs.items():
        assert ("secrets." in yaml.safe_dump(job)) == (name in ("review", "notify"))


def test_notify_job_calls_local_announcer_after_review() -> None:
    """Require the ready announcement to follow a review with the admitted SHAs only.

    Raises:
        AssertionError: The announcer runs without a review, receives other SHAs,
            more secrets or write permissions, or is not the local workflow.
    """
    # Require the dependency graph and the review-ran condition.
    notify = load()["jobs"]["notify"]
    assert notify["needs"] == ["admit", "review"]
    assert notify["if"] == "needs.review.result == 'success'"
    # Require the local reusable announcer with read-only permissions.
    assert notify["uses"] == "./.github/workflows/pr-announce.yml"
    assert notify["permissions"] == {
        "actions": "read",
        "contents": "read",
        "pull-requests": "read",
    }
    # Require the same PR number as the review and the admitted SHAs.
    assert notify["with"] == {
        "pr-number": (
            "${{ github.event.workflow_run.pull_requests[0].number || fromJSON(inputs.pr-number) }}"
        ),
        "head-sha": "${{ needs.admit.outputs.head }}",
        "base-sha": "${{ needs.admit.outputs.base }}",
    }
    # Require exactly the two named secrets, never inherited ones.
    assert notify["secrets"] == {
        "DISCORD_PR_WEBHOOK_URL": "${{ secrets.DISCORD_PR_WEBHOOK_URL }}",
        "AI_REVIEWER_TOKEN": "${{ secrets.AI_REVIEWER_TOKEN }}",
    }


def test_workflow_env_names_match_admission_contract(tmp_path: Path) -> None:
    """Run admission with exactly the env names the workflow passes.

    Args:
        tmp_path: Disposable runner output files.

    Raises:
        AssertionError: The script ignores an env name the workflow sets.
    """
    # Build the env from the workflow's own names plus the runner-provided ones.
    head, base = "a" * 40, "b" * 40
    values = {"GITHUB_TOKEN": "t", "PR_NUMBER": "7", "HEAD_SHA": head}
    assert set(values) == set(ADMIT_ENV)
    output = tmp_path / "out"
    env = {**values, "GITHUB_REPOSITORY": "o/r", "GITHUB_OUTPUT": str(output)}
    calls: list[str] = []
    run = {"id": 9, "head_sha": head, "status": "completed", "conclusion": "success"}
    jobs = [{"name": name, "conclusion": "success"} for name in pr_review_ready.REQUIRED_JOBS]

    def http(method: str, url: str, token: str, body: dict[str, str] | None) -> tuple[int, Any]:
        """Answer the pull, run and job reads without network.

        Args:
            method: HTTP method.
            url: Request URL.
            token: Bearer token.
            body: JSON payload.

        Returns:
            Scripted status code and body.
        """
        # Record the call, then answer it by URL.
        assert method == "GET" and token == "t" and body is None
        calls.append(url)
        if url.endswith("/pulls/7"):
            return 200, {
                "head": {"sha": head},
                "base": {"sha": base},
                "state": "open",
                "draft": False,
            }
        return 200, {"jobs": jobs} if "/jobs" in url else {"workflow_runs": [run]}

    # Run admission and check each env value reached its use.
    assert pr_review_admit.main(env, http) == 0
    assert calls[0].endswith("/repos/o/r/pulls/7") and f"head_sha={head}" in calls[1]
    assert output.read_text() == f"head={head}\nbase={base}\nready=true\n"
