"""Verify PR scheduling policy and independent collectors with disposable failures."""

import json
import shlex
import sys
from pathlib import Path

import pytest
import yaml

from scripts import dev
from tests.support import ROOT

pytestmark = pytest.mark.integration
WORKFLOW = ROOT / ".github/workflows/pr-checks.yml"


def test_workflow_exposes_six_checks_without_masking_failed_steps() -> None:
    """Require explicit continuation, native platforms and named size and quality results.

    Raises:
        AssertionError: A failed check is masked or prevents a later required check.
    """
    # Load the actual PR workflow job and dependency graph.
    jobs = yaml.safe_load(WORKFLOW.read_text())["jobs"]
    # Require the six named checks and failure-preserving step conditions.
    assert set(jobs) == {"size", "quality", "tests", "coverage"}
    assert jobs["quality"]["name"] == "Code Quality Check"
    assert jobs["coverage"]["name"] == "Test Quality Check"
    tests = jobs["tests"]
    assert tests["name"] == "${{ matrix.name }} Tests (Unit/Integration)"
    assert tests["strategy"]["fail-fast"] is False
    assert {item["name"] for item in tests["strategy"]["matrix"]["include"]} == {
        "Linux",
        "Windows",
        "MacOS",
    }
    assert "needs" not in jobs["quality"] and "needs" not in tests
    # The size check starts at once, reads only, and runs the base revision's gate.
    size = jobs["size"]
    assert size["name"] == "PR Size Check" and "needs" not in size
    assert size["permissions"] == {"contents": "read", "pull-requests": "read"}
    checkout, gate = size["steps"]
    assert checkout["with"] == {
        "ref": "${{ github.event.pull_request.base.sha }}",
        "persist-credentials": False,
    }
    assert gate["run"] == "python3 scripts/pr_size_gate.py"
    assert gate["env"] == {
        "GITHUB_TOKEN": "${{ github.token }}",
        "PR_NUMBER": "${{ github.event.pull_request.number }}",
        "HEAD_SHA": "${{ github.event.pull_request.head.sha }}",
    }
    assert "secrets." not in WORKFLOW.read_text()
    assert jobs["coverage"]["needs"] == "tests"
    assert jobs["coverage"]["if"] == "${{ !cancelled() }}"
    download_names = {
        step["with"]["name"]
        for step in jobs["coverage"]["steps"]
        if "download-artifact" in step.get("uses", "")
    }
    assert download_names == {
        "python-tests-linux",
        "python-tests-windows",
        "python-tests-macos",
        "python-tooling-linux",
        "python-tooling-windows",
        "python-tooling-macos",
    }
    # Inspect every CI job for its required setup and evidence-retention behavior.
    for job in jobs.values():
        assert "continue-on-error" not in job
        # Inspect each job step for contributor-runner and retention conventions.
        for step in job["steps"]:
            assert "continue-on-error" not in step
            # Verify each Python runner step uses the pinned project command.
            if step.get("run", "").startswith("poetry run python scripts/"):
                assert step["if"] == "${{ !cancelled() }}"


@pytest.mark.parametrize("job", ["quality", "tests"])
def test_workflow_collectors_retain_failure_and_run_later_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, job: str
) -> None:
    """Run the actual workflow collector arguments independently after an injected failure.

    Args:
        tmp_path: Disposable evidence directory and marker files.
        monkeypatch: Scoped command plan and checkout identity, never real platform evidence.
        job: Sequential quality checks or split native test suites.

    Raises:
        AssertionError: A failure is lost, a later check is absent, or suite data collides.
    """
    # Read the actual workflow job steps and install a disposable command failure.
    steps = yaml.safe_load(WORKFLOW.read_text())["jobs"][job]["steps"]
    selected = [
        step
        for step in steps
        if step.get("run", "").startswith("poetry run python scripts/dev.py ")
    ]
    tasks = [shlex.split(step["run"])[4] for step in selected]
    # Require later collection steps to run while the job still fails.
    assert tasks == (
        ["validate-config", "format-check", "lint", "type-check", "ticket-references"]
        if job == "quality"
        else ["test-unit", "test-integration", "test-tooling-unit"]
    )
    state = {"sha": "fixture", "status": "", "tracked_digest": "bytes"}
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev, "identity", lambda: dict(state))
    monkeypatch.setattr(dev, "checkout_state", lambda: dict(state))
    commands = {}
    # Verify the native suite steps preserve the expected command order.
    for index, task in enumerate(tasks):
        program = (
            "import os; from pathlib import Path; "
            f"Path({str(tmp_path / task)!r}).touch(); "
            "print(os.environ.get('NPLET_COVERAGE_CONTEXT', 'quality')); "
            f"raise SystemExit({7 if index == 0 else 0})"
        )
        commands[task] = [[sys.executable, "-c", program]]
    monkeypatch.setattr(dev, "COMMANDS", commands)
    results = []
    contexts = []
    for step, task in zip(selected, tasks, strict=True):
        # This checks GitHub's explicit scheduling condition separately from collector behavior.
        assert step["if"] == "${{ !cancelled() }}"
        arguments = shlex.split(step["run"].replace("$RUNNER_TEMP", tmp_path.as_posix()))[4:]
        monkeypatch.setattr(sys, "argv", ["dev.py", *arguments])
        results.append(dev.main())
        evidence = Path(arguments[arguments.index("--evidence-dir") + 1])
        manifest = json.loads((evidence / "manifest.json").read_text())
        assert manifest["commands"][0]["exit_code"] == results[-1]
        assert (tmp_path / task).is_file()
        # Apply native-suite requirements only to the matrix test job.
        if job == "tests":
            command = manifest["commands"][0]["command"]
            assert "--cov" in command if task != "test-tooling-unit" else "--cov=scripts" in command
            assert "--cov-fail-under=0" in command
            assert any(arg.startswith("--junitxml=") for arg in command)
            contexts.append((evidence / f"{task}-0.log").read_text().strip())
    assert results == [7, *([0] * (len(tasks) - 1))]
    # Require the native-test job to upload its evidence even after a suite failure.
    if job == "tests":
        assert contexts[0] != contexts[1]


@pytest.mark.parametrize("task", ["test-unit", "test-integration"])
def test_split_collector_rejects_candidate_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, task: str
) -> None:
    """Fail a successful subset process if its source identity changes while it runs.

    Args:
        tmp_path: Disposable evidence and subprocess directory.
        monkeypatch: Scoped candidate identity and real harmless subprocess.
        task: Subset collector whose source boundary is checked.

    Raises:
        AssertionError: A changed candidate receives successful suite evidence.
    """
    # Inject changed checkout identity into a split collector run.
    state = {"sha": "fixture", "status": "", "tracked_digest": "before"}
    monkeypatch.setattr(dev, "ROOT", tmp_path)
    monkeypatch.setattr(dev, "identity", lambda: dict(state))
    monkeypatch.setattr(dev, "checkout_state", lambda: {**state, "tracked_digest": "after"})
    monkeypatch.setattr(dev, "COMMANDS", {task: [[sys.executable, "-c", "pass"]]})
    monkeypatch.setattr(
        sys, "argv", ["dev.py", task, "--platform-coverage", "--evidence-dir", str(tmp_path)]
    )
    # Require its receipt to report the candidate mismatch.
    assert dev.main() == 1
    assert json.loads((tmp_path / "manifest.json").read_text())["candidate_unchanged"] is False
