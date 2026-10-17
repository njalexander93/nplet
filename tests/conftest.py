"""Attach bounded handled package errors to failed pytest and unittest reports only."""

import json
from collections import deque
from collections.abc import Generator

import pytest

from tests.support import fixture_diagnostics
from tests.types import JsonObject

_DIAGNOSTICS = pytest.StashKey[deque[JsonObject]]()


@pytest.hookimpl(wrapper=True)
def pytest_runtest_protocol(
    item: pytest.Item, nextitem: pytest.Item | None
) -> Generator[None, bool | None, bool | None]:
    """Observe package exceptions throughout setup, call, subtests and teardown.

    Args:
        item: Test whose reports may need supplemental diagnostics.
        nextitem: Following test supplied by pytest's protocol contract.

    Yields:
        Control to pytest's original test protocol without changing its result.

    Returns:
        The original protocol result.
    """
    # Keep the same observer alive while every test phase and subtest report runs.
    with fixture_diagnostics() as diagnostics:
        item.stash[_DIAGNOSTICS] = diagnostics
        try:
            return (yield)
        # Remove the per-item reference even when pytest propagates an internal error.
        finally:
            del item.stash[_DIAGNOSTICS]


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    """Supplement failed reports without replacing outcome, assertion or traceback.

    Args:
        item: Test owning the current bounded exception buffer.
        call: Original pytest phase or unittest subtest call information.

    Yields:
        Control to pytest's original report construction.

    Returns:
        The same report with a diagnostic section only when it failed.
    """
    # Let pytest and its unittest adapter construct the authoritative report first.
    report = yield
    diagnostics = item.stash.get(_DIAGNOSTICS, None)
    # Successful reports remain quiet; failed subtest reports receive the same section.
    if report.failed and diagnostics:
        report.sections.append(
            ("Handled package exceptions", "\n".join(json.dumps(record) for record in diagnostics))
        )
    return report
