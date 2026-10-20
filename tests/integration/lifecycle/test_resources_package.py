"""Verify framework Markdown resources load from the installed package outside the checkout."""

import hashlib
import json
import subprocess

import pytest

from nplet.adapters import codex as hook
from tests.support import ROOT, Fixture

pytestmark = pytest.mark.integration

RESOURCES = ROOT / "src/nplet/resources"
FOLDERS = ("organization", "templates", "roles", "profiles")


class ResourcePackageIntegrationTests(Fixture):
    """Compare package-loaded framework resources with the checkout's source files."""

    def test_framework_markdown_loads_from_package_with_checkout_digests(self) -> None:
        """Enumerate framework Markdown through importlib.resources and match source bytes.

        Raises:
            AssertionError: The subprocess fails, a resource is missing or extra,
                a digest differs, or a required README is absent.
        """
        # Enumerate resources in an isolated interpreter whose cwd is outside the checkout.
        script = """
import hashlib
import json
import sys
from importlib.resources import files

base = files('nplet').joinpath('resources')
found = {}

def walk(node, prefix):
    for child in node.iterdir():
        name = prefix + '/' + child.name
        if child.is_dir():
            walk(child, name)
        elif child.name.endswith('.md'):
            found[name] = hashlib.sha256(child.read_bytes()).hexdigest()

for folder in json.loads(sys.argv[1]):
    walk(base.joinpath(folder), folder)
print(json.dumps(found))
"""
        result = subprocess.run(
            [hook.PYTHON, "-I", "-c", script, json.dumps(FOLDERS)],
            cwd=self.root,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads(result.stdout)
        # Build the expected inventory independently from the checkout's source tree.
        expected = {
            path.relative_to(RESOURCES).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for folder in FOLDERS
            for path in (RESOURCES / folder).rglob("*.md")
        }
        # Require the same file set, identical bytes and every folder's README.
        self.assertEqual(observed, expected)
        for folder in FOLDERS:
            self.assertIn(f"{folder}/README.md", observed)

    def test_cursor_guidance_loads_as_exact_installed_markdown(self) -> None:
        """Read the child guidance from installed resources outside the repository cwd.

        Raises:
            AssertionError: Isolated resource loading fails or differs from source bytes.
        """
        # Load the new prose through the same package boundary used by the adapter.
        script = (
            "import json; from importlib.resources import files; "
            "print(json.dumps(files('nplet').joinpath("
            "'resources/task_workspace/cursor_child.md').read_text(encoding='utf-8')))"
        )
        result = subprocess.run(
            [hook.PYTHON, "-I", "-c", script], cwd=self.root, capture_output=True, text=True
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads(result.stdout)
        expected = (RESOURCES / "task_workspace/cursor_child.md").read_text(encoding="utf-8")
        self.assertEqual(observed, expected)
        self.assertIn("templates grant no authority.", observed)
