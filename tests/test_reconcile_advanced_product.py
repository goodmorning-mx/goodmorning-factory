import os
import base64
import unittest
from unittest.mock import patch

from scripts import advanced_product_contract as contract
from scripts import reconcile_advanced_product as advanced
try:
    from test_advanced_product_contract import manifest
except ModuleNotFoundError:
    from tests.test_advanced_product_contract import manifest


class FakeClient:
    apply = False

    def __init__(self):
        self.writes = []

    def get(self, path):
        values = {
            "/servers": [{"uuid": "server-1", "name": "localhost"}],
            "/projects": [],
            "/github-apps": [{"uuid": "github-1", "name": "goodmorning-mx-coolify"}],
            "/applications/legacy-uuid": {"uuid": "legacy-uuid"},
            "/applications/legacy-uuid/envs": [],
        }
        return values.get(path, [])


class ReconcileAdvancedProductTests(unittest.TestCase):
    def test_plan_is_read_only_and_lists_every_resource(self):
        client = FakeClient()
        result = advanced.reconcile(
            contract.validate_manifest(manifest()),
            client=client,
            server_name="localhost",
            destination_uuid="destination",
            confirm_product=None,
            deploy=False,
        )
        self.assertEqual(result["status"], "planned")
        self.assertTrue(result["legacy_retained"])
        self.assertIn("helloworldv2-mobile-dev", result["resources"]["applications"])
        self.assertFalse(client.writes)

    def test_apply_requires_write_switch(self):
        client = FakeClient()
        client.apply = True
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(advanced.ReconcileError):
            advanced.reconcile(
                contract.validate_manifest(manifest()),
                client=client,
                server_name="localhost",
                destination_uuid="destination",
                confirm_product="helloworldv2",
                deploy=False,
            )

    def test_apply_requires_exact_confirmation(self):
        client = FakeClient()
        client.apply = True
        with patch.dict(os.environ, {"COOLIFY_ALLOW_ADVANCED_WRITES": "true"}, clear=True), self.assertRaises(advanced.ReconcileError):
            advanced.reconcile(
                contract.validate_manifest(manifest()),
                client=client,
                server_name="localhost",
                destination_uuid="destination",
                confirm_product="other",
                deploy=False,
            )

    def test_payload_keeps_mobile_as_independent_dockerfile_application(self):
        value = contract.validate_manifest(manifest())
        mobile = next(item for item in value["applications"] if item["role"] == "mobile")
        payload = advanced._application_payload(
            value,
            mobile,
            {
                "project_uuid": "project",
                "environment_uuid": "environment",
                "server_uuid": "server",
                "destination_uuid": "destination",
                "github_app_uuid": "github",
            },
        )
        self.assertEqual(payload["dockerfile_location"], "/mobile/Dockerfile")
        self.assertEqual(payload["ports_exposes"], "8060")
        self.assertEqual(payload["custom_docker_run_options"], "")
        self.assertTrue(payload["is_auto_deploy_enabled"])

    def test_environment_values_are_not_returned_in_actions(self):
        app = {
            "environment": [
                {"key": "APIKEY", "source": "legacy_env", "legacy_key": "APIKEY"},
                {"key": "DATABASE_URL", "source": "database_url", "resource": "db"},
            ]
        }
        values = advanced._environment_values(
            app,
            {"db": "postgresql://user:secret@database/app"},
            {},
            {"APIKEY": "top-secret"},
        )
        self.assertEqual([item["key"] for item in values], ["APIKEY", "DATABASE_URL"])
        self.assertNotIn("top-secret", str(advanced._redacted({"environment": values})))

    def test_empty_literal_is_a_valid_environment_value(self):
        values = advanced._environment_values(
            {"environment": [{"key": "BASE_PATH", "source": "literal", "value": ""}]},
            {},
            {},
            {},
        )
        self.assertEqual(values[0]["value"], "")

    def test_reviewed_redis_config_is_base64_encoded_for_coolify_api(self):
        self.assertEqual(
            base64.b64decode(advanced._redis_conf()).decode("utf-8"),
            "appendonly yes\nmaxmemory 256mb\nmaxmemory-policy allkeys-lru\n",
        )

    def test_scope_check_accepts_omitted_collection_fields(self):
        context = {"project_uuid": "project", "environment_uuid": "environment"}
        self.assertFalse(advanced._scope_conflicts({"uuid": "resource"}, context))
        self.assertFalse(
            advanced._scope_conflicts(
                {"project_uuid": "project", "environment_uuid": "environment"},
                context,
            )
        )
        self.assertTrue(
            advanced._scope_conflicts({"project_uuid": "another-project"}, context)
        )


if __name__ == "__main__":
    unittest.main()
