import json
import tempfile
import unittest
from pathlib import Path

from scripts import advanced_product_contract as contract


def manifest():
    return {
        "topology": "resources-v3",
        "product": "helloworldv2",
        "repository": "goodmorning-mx/helloworldv2",
        "branch": "dev",
        "coolify_github_app_name": "goodmorning-mx-coolify",
        "legacy": {
            "application_uuid": "legacy-uuid",
            "retain": True,
            "environment_keys": ["APIKEY", "APISECRET"],
        },
        "applications": [
            {
                "name": "api", "role": "api", "dockerfile": "/backend/Dockerfile",
                "port": 8000, "domain": "https://api.dev.helloworldv2.goodmorning.mx",
                "health_path": "/health", "auto_deploy": True,
                "persistent_volume": {"name": "helloworldv2-backend-data", "mount_path": "/app/data"},
                "environment": [
                    {"key": "DATABASE_URL", "source": "database_url", "resource": "timescaledb"},
                    {"key": "REDIS_URL", "source": "redis_url", "resource": "redis"},
                    {"key": "APIKEY", "source": "legacy_env"},
                ],
            },
            {
                "name": "mobile", "role": "mobile", "dockerfile": "/mobile/Dockerfile",
                "port": 8060, "domain": "https://mobile.dev.helloworldv2.goodmorning.mx",
                "health_path": "/health", "auto_deploy": True, "environment": [],
            },
            {
                "name": "ingestion", "role": "worker", "dockerfile": "/backend/Dockerfile.worker",
                "port": 8000, "domain": None, "health_path": "/health",
                "auto_deploy": True, "environment": [],
            },
        ],
        "databases": [
            {
                "name": "timescaledb", "engine": "timescaledb", "version": "16",
                "image": "timescale/timescaledb:latest-pg16", "public": False, "backup": True,
            }
        ],
        "redis": [
            {
                "name": "redis", "version": "7.4", "image": "redis:7.4-alpine",
                "public": False, "persistent": True,
            }
        ],
        "backup": {
            "enabled": True,
            "storage_uuid": "bk8cw0gkswgo04o0s88sk0kg",
            "frequency": "0 3 * * *",
            "local_retention_count": 1,
            "local_retention_days": 1,
            "s3_retention_count": 7,
            "s3_retention_days": 7,
            "timeout_seconds": 1800,
        },
    }


class AdvancedProductContractTests(unittest.TestCase):
    def test_valid_manifest_is_normalized(self):
        result = contract.validate_manifest(manifest())
        self.assertEqual(result["topology"], "resources-v3")
        self.assertEqual(
            contract.resource_names(result)["applications"],
            [
                "helloworldv2-api-dev",
                "helloworldv2-mobile-dev",
                "helloworldv2-ingestion-dev",
            ],
        )

    def test_resources_v2_is_rejected(self):
        value = manifest()
        value["topology"] = "resources-v2"
        with self.assertRaises(contract.ContractError):
            contract.validate_manifest(value)

    def test_public_database_and_redis_are_rejected(self):
        for section in ("databases", "redis"):
            value = manifest()
            value[section][0]["public"] = True
            with self.subTest(section=section), self.assertRaises(contract.ContractError):
                contract.validate_manifest(value)

    def test_sensitive_literal_is_rejected(self):
        value = manifest()
        value["applications"][0]["environment"].append(
            {"key": "APISECRET", "source": "literal", "value": "not-allowed"}
        )
        with self.assertRaises(contract.ContractError):
            contract.validate_manifest(value)

    def test_legacy_secret_must_be_allowlisted(self):
        value = manifest()
        value["applications"][0]["environment"].append(
            {"key": "CTRADER_ACCESS_TOKEN", "source": "legacy_env"}
        )
        with self.assertRaises(contract.ContractError):
            contract.validate_manifest(value)

    def test_mobile_domain_must_stay_in_product_namespace(self):
        value = manifest()
        value["applications"][1]["domain"] = "https://mobile.example.com"
        with self.assertRaises(contract.ContractError):
            contract.validate_manifest(value)

    def test_load_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "product.json"
            path.write_text(json.dumps(manifest()), encoding="utf-8")
            self.assertEqual(contract.load_manifest(path)["product"], "helloworldv2")


if __name__ == "__main__":
    unittest.main()
