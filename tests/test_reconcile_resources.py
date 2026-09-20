import json
import tempfile
import unittest
from pathlib import Path

from scripts.reconcile_resources import normalize_manifest


class ReconcileResourcesCompatibilityTests(unittest.TestCase):
    def test_v2_manifest_is_normalized_into_the_v3_contract(self):
        value = {
            "topology": "resources-v2",
            "product": "legacy-product",
            "repository": "goodmorning-mx/factory-smoke-test",
            "branch": "dev",
            "development_domain": "dev.legacy-product.goodmorning.mx",
            "coolify_github_app_name": "goodmorning-mx-coolify",
            "backup": {
                "enabled": True, "storage_uuid": "bk8cw0gkswgo04o0s88sk0kg",
                "frequency": "0 3 * * *", "local_retention_count": 1,
                "local_retention_days": 1, "s3_retention_count": 7,
                "s3_retention_days": 7, "timeout_seconds": 1800,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "legacy.json")
            path.write_text(json.dumps(value), encoding="utf-8")
            normalized = normalize_manifest(path)
        self.assertEqual(normalized["topology"], "resources-v3")
        self.assertEqual(normalized["repository"], value["repository"])

    def test_v2_production_manifest_is_normalized_for_the_existing_promotion_flow(self):
        value = {
            "topology": "resources-v2-production", "product": "legacy-product",
            "repository": "goodmorning-mx/legacy-product", "branch": "main",
            "production_domain": "legacy-product.goodmorning.mx",
            "coolify_github_app_name": "goodmorning-mx-coolify",
            "confirm_production": True, "promotion_policy": "auto-on-main",
            "backup": {
                "enabled": True, "storage_uuid": "bk8cw0gkswgo04o0s88sk0kg",
                "frequency": "0 3 * * *", "local_retention_count": 1,
                "local_retention_days": 1, "s3_retention_count": 7,
                "s3_retention_days": 7, "timeout_seconds": 1800,
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "legacy-production.json")
            path.write_text(json.dumps(value), encoding="utf-8")
            normalized = normalize_manifest(path)
        self.assertEqual(normalized["topology"], "resources-v3-production")


if __name__ == "__main__":
    unittest.main()
