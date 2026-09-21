import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import scripts.clone_product as clone


class CloneProductTests(unittest.TestCase):
    def test_derive_manifest_rewrites_identity_and_replaces_backup_storage(self):
        source = json.loads(Path("products/helloworld.json").read_text())
        result = clone.derive_manifest("helloworld", "hello-copy", source, "storageuuid1234567890")
        self.assertEqual(result["product"], "hello-copy")
        self.assertEqual(result["repository"], "goodmorning-mx/hello-copy")
        self.assertEqual(result["development_domain"], "dev.hello-copy.goodmorning.mx")
        self.assertEqual(result["backup"]["storage_uuid"], "storageuuid1234567890")
        self.assertFalse(result.get("legacy"))
        self.assertFalse(result.get("migration"))

    def test_missing_managed_secret_fails_before_repository_creation(self):
        source = json.loads(Path("products/helloworld.json").read_text())
        with mock.patch.dict(clone.os.environ, {}, clear=True):
            with self.assertRaises(clone.CloneError):
                clone.required_secret_bindings(source)

    def test_legacy_environment_bindings_are_refused(self):
        source = json.loads(Path("products/helloworldv2.json").read_text())
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.json"
            path.write_text(json.dumps(source))
            with self.assertRaisesRegex(clone.CloneError, "legacy or migration"):
                clone.load_source_manifest("helloworldv2", path)

    def test_data_paths_are_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            (repo / ".git").mkdir()
            with mock.patch.object(clone.subprocess, "check_output", return_value="backend/schema.sql\n"):
                with self.assertRaises(clone.CloneError):
                    clone.reject_data_paths(repo)

    def test_coolify_preflight_requires_factory_connection(self):
        with mock.patch.dict(clone.os.environ, {}, clear=True):
            with self.assertRaisesRegex(clone.CloneError, "COOLIFY_BASE_URL"):
                clone.coolify_preflight("hello-copy", {"applications": [], "databases": [], "redis": []})

    def test_existing_destination_is_not_reused_without_explicit_empty_recovery(self):
        with mock.patch.object(clone, "github", return_value={"size": 0}):
            with self.assertRaisesRegex(clone.CloneError, "already exists"):
                clone.require_destination_absent("hello-copy")

    def test_empty_destination_recovery_requires_no_branches(self):
        with mock.patch.object(clone, "github", side_effect=[{"size": 0}, []]):
            self.assertEqual(clone.require_destination_absent("hello-copy", recover_empty=True), {"size": 0})

    def test_pushed_partial_destination_recovery_requires_exact_marker(self):
        repo = {"size": 10, "private": True, "description": "clone", "default_branch": "main"}
        with mock.patch.object(clone, "github", side_effect=[repo, [{"name": "main"}, {"name": "dev"}]]):
            self.assertEqual(
                clone.require_destination_absent("hello-copy", recover_empty=True, description="clone"),
                repo,
            )


if __name__ == "__main__":
    unittest.main()
