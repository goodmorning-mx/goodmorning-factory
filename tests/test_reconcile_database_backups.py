import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import scripts.reconcile_database_backups as rb


class BackupContractTests(unittest.TestCase):
    def manifest(self, **updates):
        value = {
            "topology": "resources-v2",
            "product": "ledger",
            "branch": "dev",
            "backup": {
                "enabled": True,
                "storage_uuid": "c1rrntk8wv4cf3tl3wqviwp3",
                "frequency": "0 3 * * *",
                "local_retention_count": 1,
                "local_retention_days": 1,
                "s3_retention_count": 7,
                "s3_retention_days": 7,
            },
        }
        value.update(updates)
        return value

    def write_manifest(self, value):
        handle = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8")
        json.dump(value, handle)
        handle.close()
        self.addCleanup(Path(handle.name).unlink)
        return Path(handle.name)

    def test_manifest_is_dev_resources_v2_with_explicit_retention(self):
        loaded = rb.load_manifest(self.write_manifest(self.manifest()))
        self.assertEqual(loaded["backup"]["s3_retention_count"], 7)
        for mutation in (
            {"branch": "main"},
            {"topology": "compose-v1"},
            {"backup": {"enabled": False}},
        ):
            with self.assertRaises(rb.BackupError):
                rb.load_manifest(self.write_manifest(self.manifest(**mutation)))

    def test_database_selection_is_exact_private_postgres(self):
        selected = rb.select_database(
            [{"uuid": "db-1", "name": "ledger-postgres-dev", "type": "postgresql", "is_public": False}],
            "ledger",
        )
        self.assertEqual(selected["uuid"], "db-1")
        for databases in (
            [],
            [
                {"uuid": "1", "name": "ledger-postgres-dev", "type": "postgresql"},
                {"uuid": "2", "name": "ledger-postgres-dev", "type": "postgresql"},
            ],
            [{"uuid": "1", "name": "ledger-postgres-dev", "type": "postgresql", "is_public": True}],
        ):
            with self.assertRaises(rb.BackupError):
                rb.select_database(databases, "ledger")

    def test_database_engine_is_validated_from_detail(self):
        self.assertEqual(
            rb.validate_postgres_database(
                {"uuid": "1", "image": "timescale/timescaledb:latest-pg16"},
                "ledger-timescaledb-dev",
            )["uuid"],
            "1",
        )
        with self.assertRaisesRegex(rb.BackupError, "not PostgreSQL"):
            rb.validate_postgres_database(
                {"uuid": "1", "type": "mysql"},
                "ledger-timescaledb-dev",
            )

    def test_resources_v3_selects_the_declared_backed_up_database(self):
        value = self.manifest(
            topology="resources-v3",
            databases=[
                {
                    "name": "timescaledb",
                    "engine": "timescaledb",
                    "backup": True,
                }
            ],
        )
        loaded = rb.load_manifest(self.write_manifest(value))
        self.assertEqual(loaded["_backup_database_name"], "ledger-timescaledb-dev")
        selected = rb.select_database(
            [
                {
                    "uuid": "db-advanced",
                    "name": "ledger-timescaledb-dev",
                    "type": "postgresql",
                    "image": "timescale/timescaledb:latest-pg16",
                    "is_public": False,
                }
            ],
            "ledger",
            loaded["_backup_database_name"],
        )
        self.assertEqual(selected["uuid"], "db-advanced")

    def test_payload_is_s3_enabled_and_retained(self):
        backup = self.manifest()["backup"]
        payload = rb.desired_backup_payload(backup, "storage-1", True)
        self.assertTrue(payload["save_s3"])
        self.assertTrue(payload["backup_now"])
        self.assertEqual(payload["database_backup_retention_amount_s3"], 7)
        self.assertEqual(payload["database_backup_retention_amount_locally"], 1)
        self.assertFalse(payload["dump_all"])

    def test_manifest_requires_stable_storage_uuid(self):
        for storage_uuid in ("", "UPPERCASE", "too-short"):
            value = self.manifest()
            value["backup"]["storage_uuid"] = storage_uuid
            with self.assertRaisesRegex(rb.BackupError, "storage_uuid"):
                rb.load_manifest(self.write_manifest(value))

    def test_success_with_s3_warning_is_rejected(self):
        class Client:
            def call(self, _method, _path):
                return {"executions": [{"uuid": "new", "status": "success", "filename": "x.dump", "message": "Warning: S3 upload failed: denied"}]}

        with mock.patch("scripts.reconcile_database_backups.time.monotonic", side_effect=[0, 1]):
            with self.assertRaisesRegex(rb.BackupError, "S3 upload failed"):
                rb.wait_for_backup(Client(), "db", "backup", set(), 30, sleeper=lambda _seconds: None)

    def test_object_verification_matches_exact_backup_basename(self):
        listing = {"Contents": [{"Key": "databases/team/product/backup.dump", "Size": 42}]}
        completed = subprocess.CompletedProcess(args=[], returncode=0, stdout=json.dumps(listing), stderr="")
        environment = {
            "OCI_S3_ENDPOINT": "https://namespace.example.test",
            "OCI_S3_BUCKET": "bucket",
            "OCI_S3_REGION": "mx-queretaro-1",
            "OCI_S3_ACCESS_KEY": "access",
            "OCI_S3_SECRET_KEY": "secret",
        }
        with mock.patch.dict(os.environ, environment, clear=True):
            result = rb.verify_object("/tmp/backup.dump", runner=mock.Mock(return_value=completed))
        self.assertEqual(result, {"key": "databases/team/product/backup.dump", "size": 42})


if __name__ == "__main__":
    unittest.main()
