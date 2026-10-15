import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import scripts.prepare_software_backup_restore_drill as drill


class SoftwareProductionRestoreDrillTests(unittest.TestCase):
    def manifest(self, directory, *, product="software", production=True):
        data = {
            "topology": "resources-v3-production" if production else "resources-v3",
            "product": product,
            "branch": "main" if production else "dev",
            "backup": {
                "enabled": True,
                "storage_uuid": "c1rrntk8wv4cf3tl3wqviwp3",
                "frequency": "0 2 * * *",
                "local_retention_count": 2,
                "local_retention_days": 2,
                "s3_retention_count": 30,
                "s3_retention_days": 30,
            },
            "databases": [{"name": "postgres", "backup": True}],
        }
        path = Path(directory) / "manifest.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def configure_read_only_coolify(self):
        client = mock.Mock()
        client.call.side_effect = [
            [{"uuid": "db-1", "name": "software-postgres-prod", "type": "postgresql", "is_public": False}],
            {"uuid": "db-1", "image": "postgres:16-alpine"},
            [{"uuid": "backup-1"}],
            {"executions": [
                {"status": "success", "filename": "software-old.dump", "created_at": "2026-10-14T00:00:00Z"},
                {"status": "failed", "filename": "software-failed.dump", "created_at": "2026-10-16T00:00:00Z"},
                {"status": "success", "filename": "software-latest.dump", "created_at": "2026-10-15T00:00:00Z"},
            ]},
        ]
        return client

    def test_resolves_latest_successful_backup_with_read_only_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest = self.manifest(directory)
            output = Path(directory) / "github-output"
            client = self.configure_read_only_coolify()
            with (
                mock.patch.object(drill, "CoolifyClient", return_value=client),
                mock.patch.object(drill, "required_env", side_effect=lambda key: key),
                mock.patch.object(drill, "verify_object", return_value={"key": "software/software-latest.dump", "size": 1234}) as verify,
            ):
                result = drill.prepare(manifest, output)

            self.assertEqual(result, {"size": 1234, "filename": "software-latest.dump"})
            self.assertEqual(output.read_text(), "backup_key=software/software-latest.dump\nbackup_size=1234\nbackup_name=software-latest.dump\n")
            self.assertEqual([call.args[0] for call in client.call.call_args_list], ["GET"] * 4)
            verify.assert_called_once_with("software-latest.dump")

    def test_rejects_non_software_or_non_production_manifest(self):
        for product, production in (("other-product", True), ("software", False)):
            with self.subTest(product=product, production=production), tempfile.TemporaryDirectory() as directory:
                manifest = self.manifest(directory, product=product, production=production)
                with self.assertRaises(ValueError):
                    drill.prepare(manifest, Path(directory) / "output")

    def test_restore_workflow_is_manual_and_targets_only_ephemeral_database(self):
        workflow = Path(".github/workflows/software-production-backup-restore-drill.yml").read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("github.ref == 'refs/heads/dev'", workflow)
        self.assertIn("inputs.confirmation == 'restore-drill:software:production'", workflow)
        self.assertIn("image: postgres:16-alpine", workflow)
        self.assertIn("127.0.0.1:54329:5432", workflow)
        self.assertIn("pg_restore --no-owner --no-privileges --exit-on-error", workflow)
        self.assertIn("RESTORE_DATABASE_URL: postgresql://restore_drill@127.0.0.1:54329/software_restore", workflow)
        self.assertNotRegex(workflow, r"(?m)^\s+DATABASE_URL:")
        self.assertNotRegex(workflow, r"(?m)^\s*(?:POST|PATCH|PUT|DELETE)\s+/databases")
        self.assertIn("if: always()", workflow)


if __name__ == "__main__":
    unittest.main()
