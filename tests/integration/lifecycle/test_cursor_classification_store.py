"""Verify registered-only classification store inspection without metadata creation."""

import pytest

from nplet.lifecycle import task_workspace as core
from tests.support import Fixture

pytestmark = pytest.mark.integration


class ClassificationStoreTests(Fixture):
    """Check read-only registration inspection before any issue or binding collection exists."""

    def test_registered_only_inspection_creates_no_collections(self) -> None:
        """Permit a registered parent snapshot without opening absent local mutable metadata.

        Raises:
            AssertionError: Inspection creates metadata or exposes an unavailable collection.
        """
        # Fixture setup registers both worktrees without creating an issue or binding.
        task = self.root / ".task"
        assert not (task / ".control/issues").exists()
        assert not (task / ".bindings").exists()
        before = {
            str(path.relative_to(task)): path.read_bytes()
            for path in task.rglob("*")
            if path.is_file()
        }
        with core.Store(self.req("diagnose"), create=False, bindings=False) as store:
            assert store.issues_present is False
            # A positive lookup must fail closed rather than inventing an empty issue collection.
            with pytest.raises(core.WorkspaceError) as missing_issues:
                _ = store.issues
            assert missing_issues.value.code == "RECOVERY_REQUIRED"
            with pytest.raises(core.WorkspaceError) as forbidden_bindings:
                store.binding()
            assert forbidden_bindings.value.code == "RECOVERY_REQUIRED"
        after = {
            str(path.relative_to(task)): path.read_bytes()
            for path in task.rglob("*")
            if path.is_file()
        }
        assert after == before
        assert not (task / ".control/issues").exists()
        assert not (task / ".bindings").exists()
