"""Verify the AI review workflow gates every review on the PR size check."""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import pr_size_gate
from tests.support import ROOT

pytestmark = pytest.mark.integration
WORKFLOW = ROOT / ".github/workflows/ai-review.yml"
NUMBER = "${{ github.event.pull_request.number || inputs.pr-number }}"
GATE_ENV = {
    "GITHUB_TOKEN": "${{ github.token }}",
    "PR_NUMBER": NUMBER,
    "HEAD_SHA": "${{ github.event.pull_request.head.sha || inputs.head-sha }}",
    "RUN_URL": (
        "${{ github.server_url }}/${{ github.repository }}/actions/runs/${{ github.run_id }}"
    ),
}


def load() -> dict[Any, Any]:
    """Parse the AI review workflow.

    Returns:
        The decoded workflow mapping.
    """
    return yaml.safe_load(WORKFLOW.read_text())


def test_triggers_inputs_and_concurrency() -> None:
    """Require PR and dispatch triggers, the head-sha input and per-PR cancellation.

    Raises:
        AssertionError: A trigger, input or concurrency setting drifted.
    """
    # Read the trigger block; YAML may decode the bare ``on`` key as True.
    workflow = load()
    trigger = workflow.get("on", workflow.get(True))
    assert trigger["pull_request_target"]["types"] == [
        "opened",
        "reopened",
        "ready_for_review",
        "synchronize",
        "edited",
    ]
    # Require both dispatch inputs.
    inputs = trigger["workflow_dispatch"]["inputs"]
    assert set(inputs) == {"pr-number", "head-sha"}
    assert inputs["head-sha"]["required"] is True
    assert inputs["head-sha"]["type"] == "string"
    # Require one cancelling group per PR; edits that keep the base get a unique group.
    assert workflow["concurrency"] == {
        "group": f"ai-review-{NUMBER}${{{{ github.event.action == 'edited' && "
        "!github.event.changes.base && format('-edit-{0}', github.run_id) || '' }}",
        "cancel-in-progress": True,
    }


def test_size_job_runs_trusted_gate_without_secrets() -> None:
    """Require least-privilege permissions, a base checkout and the gate env wiring.

    Raises:
        AssertionError: The size job gains access, checks out the head or miswires env.
    """
    # Inspect the size job's permissions and secret use.
    size = load()["jobs"]["size"]
    assert size["permissions"] == {
        "contents": "read",
        "pull-requests": "read",
        "statuses": "write",
    }
    assert "secrets." not in yaml.safe_dump(size)
    # Edits recount only when the base changed.
    assert size["if"] == "github.event.action != 'edited' || github.event.changes.base"
    # Require the trusted base checkout and the gate step wiring.
    checkout, gate = size["steps"]
    assert checkout["uses"].startswith("actions/checkout@11d5960a326750d5838078e36cf38b85af677262")
    assert checkout["with"] == {"persist-credentials": False}
    assert gate["run"] == "python3 scripts/pr_size_gate.py"
    assert gate["env"] == GATE_ENV
    assert size["outputs"] == {
        k: f"${{{{ steps.gate.outputs.{k} }}}}" for k in ("tier", "head", "base")
    }


def test_reviewer_jobs_depend_on_size_tier_and_forward_shas() -> None:
    """Require tier-gated eligibility and expected SHAs forwarded to the reviewer.

    Raises:
        AssertionError: A review can start without the size tier or the bound SHAs.
    """
    # Require the dependency graph.
    jobs = load()["jobs"]
    resolve, review = jobs["resolve-reviewer"], jobs["review"]
    assert resolve["needs"] == "size"
    assert review["needs"] == ["size", "resolve-reviewer"]
    # Require the exact eligibility expression.
    condition = " ".join(resolve["if"].split())
    assert condition == (
        "(needs.size.outputs.tier == 'auto' && "
        "!github.event.pull_request.draft && "
        "github.event.pull_request.head.repo.full_name == github.repository) || "
        "(github.event_name == 'workflow_dispatch' && "
        "(needs.size.outputs.tier == 'auto' || needs.size.outputs.tier == 'manual'))"
    )
    # Require the reviewer to receive the admitted head and base.
    assert review["with"]["expected-head-sha"] == "${{ needs.size.outputs.head }}"
    assert review["with"]["expected-base-sha"] == "${{ needs.size.outputs.base }}"
    # Require secrets only in the reviewer call.
    for name, job in jobs.items():
        assert ("secrets." in yaml.safe_dump(job)) == (name == "review")


def test_workflow_env_names_match_gate_contract(tmp_path: Path) -> None:
    """Run the gate with exactly the env names the workflow passes.

    Args:
        tmp_path: Disposable runner output files.

    Raises:
        AssertionError: The script ignores an env name the workflow sets.
    """
    # Build the env from the workflow's own names plus the runner-provided ones.
    head, base = "a" * 40, "b" * 40
    values = {"GITHUB_TOKEN": "t", "PR_NUMBER": "7", "HEAD_SHA": head, "RUN_URL": "https://run"}
    assert set(values) == set(GATE_ENV)
    output = tmp_path / "out"
    env = {**values, "GITHUB_REPOSITORY": "o/r", "GITHUB_OUTPUT": str(output)}
    calls: list[tuple[str, str, str, dict[str, str] | None]] = []

    def http(method: str, url: str, token: str, body: dict[str, str] | None) -> tuple[int, Any]:
        """Answer the pull GET and status POST without network.

        Args:
            method: HTTP method.
            url: Request URL.
            token: Bearer token.
            body: JSON payload.

        Returns:
            Scripted status code and body.
        """
        # Record the call, then answer it by method.
        calls.append((method, url, token, body))
        pull = {"head": {"sha": head}, "base": {"sha": base}, "additions": 3, "deletions": 2}
        return (200, pull) if method == "GET" else (201, None)

    # Run the gate and check each env value reached its use.
    assert pr_size_gate.main(env, http) == 0
    (_, get_url, token, _), (_, post_url, _, body) = calls
    assert get_url.endswith("/repos/o/r/pulls/7") and token == "t"
    assert post_url.endswith(f"/statuses/{head}") and body["target_url"] == "https://run"
    assert re.findall(r"^(\w+)=(.*)$", output.read_text(), re.M) == [
        ("tier", "auto"),
        ("total", "5"),
        ("head", head),
        ("base", base),
    ]
