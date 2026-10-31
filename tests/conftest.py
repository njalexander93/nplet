"""Keep every test away from the user's real vault exemption policy."""

from pathlib import Path

import pytest

from nplet.adapters import repository_exemptions


@pytest.fixture(autouse=True)
def isolated_exemption_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Relocate the in-process exemption policy to an absent disposable path.

    Args:
        tmp_path: Disposable test directory.
        monkeypatch: Scoped attribute override.

    Returns:
        The relocated policy path, which a test may create.
    """
    policy = tmp_path / "nplet-policy" / "policy.json"
    monkeypatch.setattr(repository_exemptions, "POLICY", policy)
    return policy
