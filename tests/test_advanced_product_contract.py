import json
import tempfile
import unittest
from pathlib import Path

from scripts import advanced_product_contract as contract
from scripts.resources_v3_migration import migrate_v2_manifest


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


def production_manifest():
    value = manifest()
    value["topology"] = "resources-v3-production"
    value["branch"] = "main"
    value["confirm_production"] = True
    value["promotion_policy"] = "auto-on-main"
    value.pop("legacy")
    value["applications"][0]["environment"] = [
        {
            "key": "DATABASE_URL",
            "source": "database_url",
            "resource": "timescaledb",
        }
    ]
    value["applications"][0]["domain"] = (
        "https://api.helloworldv2.goodmorning.mx"
    )
    value["applications"][1]["domain"] = (
        "https://mobile.helloworldv2.goodmorning.mx"
    )
    return value


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

    def test_production_manifest_is_normalized_to_prod_resources(self):
        result = contract.validate_manifest(production_manifest())

        self.assertEqual(result["topology"], "resources-v3-production")
        self.assertEqual(result["branch"], "main")
        self.assertEqual(
            contract.resource_names(result)["applications"],
            [
                "helloworldv2-api-prod",
                "helloworldv2-mobile-prod",
                "helloworldv2-ingestion-prod",
            ],
        )

    def test_production_requires_exact_release_authorization(self):
        mutations = (
            ("branch", "dev"),
            ("confirm_production", False),
            ("promotion_policy", "manual"),
        )
        for key, replacement in mutations:
            value = production_manifest()
            value[key] = replacement
            with self.subTest(key=key), self.assertRaises(contract.ContractError):
                contract.validate_manifest(value)

    def test_connections_compile_into_environment_references(self):
        value = manifest()
        value["connections"] = [
            {"target": "api", "key": "CACHE_URL", "source": {"type": "redis", "resource": "redis"}},
            {"target": "mobile", "key": "API_URL", "source": {"type": "application", "resource": "api"}},
        ]
        normalized = contract.validate_manifest(value)
        api = next(app for app in normalized["applications"] if app["name"] == "api")
        mobile = next(app for app in normalized["applications"] if app["name"] == "mobile")
        self.assertIn({"key": "CACHE_URL", "source": "redis_url", "resource": "redis"}, api["environment"])
        self.assertIn({"key": "API_URL", "source": "service_url", "resource": "api"}, mobile["environment"])

    def test_dependencies_are_typed_and_normalized(self):
        value = manifest()
        value["applications"][1]["dependencies"] = ["api", {"type": "database", "name": "timescaledb"}]
        normalized = contract.validate_manifest(value)
        mobile = next(app for app in normalized["applications"] if app["name"] == "mobile")
        self.assertEqual(
            mobile["dependencies"],
            [{"type": "application", "name": "api"}, {"type": "database", "name": "timescaledb"}],
        )

    def test_managed_secret_is_a_reference_without_a_literal(self):
        value = manifest()
        value["applications"][0]["environment"] = [{
            "key": "OPENAI_API_KEY", "source": "managed_secret", "binding": "GOODMORNING_MANAGED_OPENAI_API_KEY",
        }]
        normalized = contract.validate_manifest(value)
        self.assertEqual(normalized["applications"][0]["environment"][0]["source"], "managed_secret")

        value["applications"][0]["environment"][0]["binding"] = "goodmorning/openai-default"
        self.assertEqual(
            contract.validate_manifest(value)["applications"][0]["environment"][0]["binding"],
            "goodmorning/openai-default",
        )

    def test_application_dependency_cycles_are_rejected(self):
        value = manifest()
        value["applications"][0]["dependencies"] = ["mobile"]
        value["applications"][1]["dependencies"] = ["api"]
        with self.assertRaises(contract.ContractError):
            contract.validate_manifest(value)

    def test_v2_migration_is_additive_and_v3_valid(self):
        legacy = {
            "topology": "resources-v2", "product": "simple", "repository": "goodmorning-mx/simple",
            "branch": "dev", "development_domain": "dev.simple.goodmorning.mx",
            "coolify_github_app_name": "goodmorning-mx-coolify", "backend_base_directory": "/backend",
            "frontend_base_directory": "/frontend", "backup": manifest()["backup"],
        }
        migrated = migrate_v2_manifest(legacy, resource_uuids={"simple-api-dev": "api-uuid"})
        self.assertEqual(migrated["topology"], "resources-v3")
        self.assertTrue(migrated["migration"]["retain"])
        self.assertEqual(migrated["migration"]["resource_uuids"]["simple-api-dev"], "api-uuid")
        self.assertEqual(contract.resource_names(migrated)["applications"], ["simple-api-dev", "simple-frontend-dev"])

        legacy["repository"] = "goodmorning-mx/factory-smoke-test"
        migrated = migrate_v2_manifest(legacy)
        self.assertEqual(migrated["repository"], "goodmorning-mx/factory-smoke-test")

    def test_v2_production_migration_uses_the_v3_production_contract(self):
        legacy = {
            "topology": "resources-v2-production", "product": "simple", "repository": "goodmorning-mx/simple",
            "branch": "main", "production_domain": "simple.goodmorning.mx",
            "coolify_github_app_name": "goodmorning-mx-coolify", "backend_base_directory": "/backend",
            "frontend_base_directory": "/frontend", "backup": manifest()["backup"],
            "confirm_production": True, "promotion_policy": "auto-on-main",
        }
        migrated = migrate_v2_manifest(legacy)
        self.assertEqual(migrated["topology"], "resources-v3-production")
        self.assertEqual(contract.resource_names(migrated)["applications"], ["simple-api-prod", "simple-frontend-prod"])

    def test_production_rejects_development_domains_and_legacy_imports(self):
        value = production_manifest()
        value["applications"][0]["domain"] = (
            "https://api.dev.helloworldv2.goodmorning.mx"
        )
        with self.assertRaises(contract.ContractError):
            contract.validate_manifest(value)

        value = production_manifest()
        value["legacy"] = {
            "application_uuid": "legacy-uuid",
            "retain": True,
            "environment_keys": [],
        }
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

    def test_resources_v2_adoption_can_omit_legacy_descriptor(self):
        value = manifest()
        value.pop("legacy")
        value["applications"][0]["environment"] = [
            {
                "key": "DATABASE_URL",
                "source": "database_url",
                "resource": "timescaledb",
                "driver": "psycopg",
            },
            {"key": "MCP_AUTH_TOKEN", "source": "generated_secret"},
        ]

        result = contract.validate_manifest(value)

        self.assertIsNone(result["legacy"])
        self.assertEqual(
            result["applications"][0]["environment"][0]["driver"],
            "psycopg",
        )

    def test_legacy_source_is_rejected_without_legacy_descriptor(self):
        value = manifest()
        value.pop("legacy")
        with self.assertRaises(contract.ContractError):
            contract.validate_manifest(value)

    def test_generated_secret_cannot_contain_a_literal_or_non_sensitive_key(self):
        for entry in (
            {
                "key": "MCP_AUTH_TOKEN",
                "source": "generated_secret",
                "value": "must-not-be-committed",
            },
            {"key": "APP_NAME", "source": "generated_secret"},
        ):
            value = manifest()
            value["applications"][0]["environment"] = [entry]
            with self.subTest(entry=entry), self.assertRaises(contract.ContractError):
                contract.validate_manifest(value)

    def test_application_base_directory_defaults_to_root_and_accepts_reviewed_path(self):
        value = manifest()
        value["applications"][0]["base_directory"] = "/backend"

        result = contract.validate_manifest(value)

        self.assertEqual(result["applications"][0]["base_directory"], "/backend")
        self.assertEqual(result["applications"][1]["base_directory"], "/")

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

    def test_eventosarias_catalog_adopts_v2_and_adds_separate_mcp_apps(self):
        value = contract.load_manifest(
            Path(__file__).resolve().parents[1] / "products/eventosarias.json"
        )

        self.assertIsNone(value["legacy"])
        self.assertEqual(
            contract.resource_names(value),
            {
                "applications": [
                    "eventosarias-api-dev",
                    "eventosarias-frontend-dev",
                    "eventosarias-mcp-dev",
                    "eventosarias-mcp-write-dev",
                ],
                "databases": ["eventosarias-postgres-dev"],
                "redis": [],
            },
        )
        mcp = next(item for item in value["applications"] if item["name"] == "mcp")
        self.assertEqual(mcp["base_directory"], "/backend")
        self.assertEqual(mcp["dockerfile"], "/Dockerfile.mcp")
        self.assertIn(
            {"key": "MCP_AUTH_TOKEN", "source": "generated_secret"},
            mcp["environment"],
        )
        write_mcp = next(
            item for item in value["applications"] if item["name"] == "mcp-write"
        )
        self.assertEqual(write_mcp["base_directory"], "/backend")
        self.assertEqual(write_mcp["dockerfile"], "/Dockerfile.mcp-write")
        self.assertEqual(write_mcp["port"], 8002)
        self.assertIn(
            {"key": "MCP_WRITE_OAUTH_SECRET", "source": "generated_secret"},
            write_mcp["environment"],
        )

    def test_eventosarias_production_catalog_is_an_exact_v3_promotion(self):
        value = contract.load_manifest(
            Path(__file__).resolve().parents[1] / "production/eventosarias.json"
        )

        self.assertEqual(value["topology"], "resources-v3-production")
        self.assertEqual(value["branch"], "main")
        self.assertTrue(value["confirm_production"])
        self.assertEqual(value["promotion_policy"], "auto-on-main")
        self.assertEqual(
            contract.resource_names(value),
            {
                "applications": [
                    "eventosarias-api-prod",
                    "eventosarias-frontend-prod",
                    "eventosarias-mcp-prod",
                    "eventosarias-mcp-write-prod",
                ],
                "databases": ["eventosarias-postgres-prod"],
                "redis": [],
            },
        )
        write_mcp = next(
            item for item in value["applications"] if item["name"] == "mcp-write"
        )
        self.assertEqual(
            write_mcp["domain"],
            "https://mcp-write.eventosarias.goodmorning.mx",
        )
        self.assertIn(
            {"key": "MCP_WRITE_OAUTH_SECRET", "source": "generated_secret"},
            write_mcp["environment"],
        )

    def test_advanced_workflow_separates_plan_and_apply_selections(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/provision-coolify-advanced-development.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("ADVANCED_PLAN_MANIFESTS", workflow)
        self.assertIn("ADVANCED_APPLY_MANIFESTS", workflow)
        self.assertIn("steps.select.outputs.apply_selected == 'true'", workflow)
        self.assertIn(
            "Control-plane implementation changed; selecting the full resources-v3 catalog.",
            workflow,
        )

    def test_advanced_workflow_accepts_only_verified_release_dispatch(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/provision-coolify-advanced-development.yml"
        ).read_text(encoding="utf-8")

        required = (
            "repository_dispatch:",
            "- reconcile-advanced-product",
            "actions/create-github-app-token@v2",
            "EVENT_SOURCE_RUN_ID:",
            'test "${EVENT_SOURCE_BRANCH}" = "dev"',
            'test "${current_sha}" = "${EVENT_SOURCE_SHA}"',
            'test "$(jq -r \'.name\' <<<"${run_json}")" = "CI"',
            'test "$(jq -r \'.event\' <<<"${run_json}")" = "push"',
            'test "$(jq -r \'.conclusion\' <<<"${run_json}")" = "success"',
            'test "$(jq -r \'.head_sha\' <<<"${run_json}")" = "${EVENT_SOURCE_SHA}"',
            'requested="${{ github.event.client_payload.manifest_path }}"',
        )
        for marker in required:
            self.assertIn(marker, workflow)

    def test_advanced_production_workflow_is_sha_bound_and_fail_closed(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github/workflows/promote-advanced-product-production.yml"
        ).read_text(encoding="utf-8")

        required = (
            "- promote-advanced-product",
            "environment: production",
            "ref: dev",
            'test "${source_branch}" = "main"',
            'test "${current_sha}" = "${source_sha}"',
            'test "$(jq -r \'.name\' <<<"${run_json}")" = "CI"',
            'test "$(jq -r \'.event\' <<<"${run_json}")" = "push"',
            'test "$(jq -r \'.conclusion\' <<<"${run_json}")" = "success"',
            'test "$(jq -r \'.head_sha\' <<<"${run_json}")" = "${source_sha}"',
            'test "$(jq -er \'.topology\' "${manifest}")" = "resources-v3-production"',
            "COOLIFY_ALLOW_ADVANCED_PRODUCTION_WRITES: \"true\"",
            "--confirm-environment production",
            "Smoke every public production application",
            "--retry 30 --retry-delay 10 --retry-all-errors",
            "Verify product database migration through the API",
            "factory-migration-probe-${{ steps.authorize.outputs.source_sha }}",
            'test "${status}" = "404"',
        )
        for marker in required:
            self.assertIn(marker, workflow)
        self.assertEqual(workflow.count("--backup-now true"), 2)


if __name__ == "__main__":
    unittest.main()
