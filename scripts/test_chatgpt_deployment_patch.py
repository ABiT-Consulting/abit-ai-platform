"""Regression tests for task-only deployment patches; no live files are modified."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "chatgpt_deployment", ROOT / "deployment/windows/deploy-chatgpt-project-controls.py"
)
assert SPEC is not None and SPEC.loader is not None
DEPLOYMENT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DEPLOYMENT)


class ScopedDeploymentTests(unittest.TestCase):
    def test_exact_baseline_updates_to_source(self):
        result, mode = DEPLOYMENT.scoped_patch("old\n", "new\n", "old\n")
        self.assertEqual(result, "new\n")
        self.assertEqual(mode, "source-matches-baseline")

    def test_insertion_is_idempotent_with_unrelated_customization(self):
        base = "def redact():\n    # existing behavior\n    return response\n"
        desired = "redact_payload = True\n" + base
        customization = "# unrelated tenant customization\n"
        first, _ = DEPLOYMENT.scoped_patch(base, desired, customization + base)
        self.assertEqual(first, customization + desired)
        second, _ = DEPLOYMENT.scoped_patch(base, desired, first)
        self.assertEqual(second, first)
        self.assertEqual(second.count("redact_payload = True"), 1)

    def test_already_applied_change_is_not_repeated(self):
        result, mode = DEPLOYMENT.scoped_patch("old\n", "new\n", "new\n")
        self.assertEqual(result, "new\n")
        self.assertEqual(mode, "already-current")

    def test_conflict_inside_requested_change_is_rejected(self):
        with self.assertRaises(RuntimeError):
            DEPLOYMENT.scoped_patch(
                "def run():\n    return original\n",
                "def run():\n    return updated\n",
                "# installed customization\ndef run():\n    return unrelated\n",
            )

    def test_no_requested_change_preserves_installed_customization(self):
        base = "def run():\n    return original\n"
        installed = "# unrelated customization\n" + base
        result, _ = DEPLOYMENT.scoped_patch(base, base, installed)
        self.assertEqual(result, installed)


if __name__ == "__main__":
    unittest.main()
