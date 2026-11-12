#!/usr/bin/env python3
"""Unit tests for the secure Google registration activation helper."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location(
    "google_signup_activate", Path(__file__).with_name("activate.py")
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

GOOD_ID = "123456789-abc123.apps.googleusercontent.com"
GOOD_SECRET = "GOCSPX-mock-test-secret"


class GoogleSignupActivationTests(unittest.TestCase):
    def test_valid_web_client_and_env(self):
        self.assertEqual(
            module.validate_credentials({"client_id": GOOD_ID, "client_secret": GOOD_SECRET}),
            (GOOD_ID, GOOD_SECRET),
        )
        text = module.render_env(GOOD_ID, GOOD_SECRET)
        self.assertIn("GOOGLE_REDIRECT_URI=https://abit.ai/oauth/google/callback\n", text)
        self.assertIn("ENABLE_OAUTH_SIGNUP=true\n", text)
        self.assertIn("OAUTH_MERGE_ACCOUNTS_BY_EMAIL=false\n", text)
        self.assertIn("OAUTH_AUTO_REDIRECT=false\n", text)
        self.assertNotIn("ENABLE_SIGNUP=true", text)

    def test_malformed_credentials_and_env_injection_rejected(self):
        for client_id in (
            "", "not-google.apps.example.com", "id.apps.googleusercontent.com\nENABLE_SIGNUP=true",
        ):
            with self.assertRaises(ValueError):
                module.validate_credentials(
                    {"client_id": client_id, "client_secret": GOOD_SECRET}
                )
        for secret in ("", "bad", "secret\nAUTH_TRUSTED_EMAIL_HEADER=x", "hidden key"):
            with self.assertRaises(ValueError):
                module.render_env(GOOD_ID, secret)

    def test_credentials_file_must_be_private_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "credentials.json"
            path.write_text(json.dumps({"client_id": GOOD_ID, "client_secret": GOOD_SECRET}))
            os.chmod(path, 0o644)
            with self.assertRaises(ValueError):
                module.load_credentials(path)
            os.chmod(path, 0o600)
            self.assertEqual(module.load_credentials(path), (GOOD_ID, GOOD_SECRET))
            symlink = Path(directory) / "link"
            symlink.symlink_to(path)
            with self.assertRaises(ValueError):
                module.load_credentials(symlink)

    def test_atomic_write_and_restore_preserve_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "oauth.env"
            module.atomic_write(path, b"safe\n", 0o600)
            self.assertEqual(path.read_bytes(), b"safe\n")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            module.restore(path, b"original\n", 0o600)
            self.assertEqual(path.read_bytes(), b"original\n")
            module.restore(path, None, 0o600)
            self.assertFalse(path.exists())


    def test_service_identity_guard(self):
        expected = (
            "MainPID=123\nExecStart={ path=/srv/abit-dev-linux/abit-ai/venv/bin/python;"
            " argv[]=/srv/abit-dev-linux/abit-ai/venv/bin/python"
            " /srv/abit-dev-linux/abit-ai/deployment/run-production.py }\n"
        )
        with patch.object(module.subprocess, "run", return_value=SimpleNamespace(stdout=expected)):
            module.verify_service_identity()
        with patch.object(module.subprocess, "run", return_value=SimpleNamespace(stdout="MainPID=0\n")):
            with self.assertRaises(RuntimeError):
                module.verify_service_identity()


if __name__ == "__main__":
    unittest.main()