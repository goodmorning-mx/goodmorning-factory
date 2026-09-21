import os
import base64
import unittest
from unittest.mock import patch

from scripts import advanced_product_contract as contract
from scripts import reconcile_advanced_product as advanced
try:
    from test_advanced_product_contract import manifest, production_manifest
except ModuleNotFoundError:
    from tests.test_advanced_product_contract import manifest, production_manifest


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


class ExistingAppPlanClient(FakeClient):
    def get(self, path):
        if path == "/projects":
            return [{"uuid": "project-1", "name": "helloworldv2"}]
        if path == "/projects/project-1":
            return {"environments": [{"uuid": "environment-1", "name": "dev"}]}
        if path == "/applications":
            return [
                {
                    "uuid": "api-1",
                    "name": "helloworldv2-api-dev",
                    "git_repository": "goodmorning-mx/helloworldv2",
                    "git_branch": "dev",
                    "build_pack": "dockerfile",
                    "base_directory": "/",
                    "dockerfile_location": "/backend/Dockerfile",
                    "ports_exposes": "8000",
                }
            ]
        if path == "/applications/api-1":
            return self.get("/applications")[0]
        return super().get(path)


class ReconcileAdvancedProductTests(unittest.TestCase):
    def test_resolve_deployment_uuid_uses_latest_persisted_record_after_deduplication(self):
        class DeploymentHistoryClient:
            def get(self, path):
                self.path = path
                return {"count": 2, "deployments": [{"deployment_uuid": "persisted-new"}, {"deployment_uuid": "persisted-old"}]}

        client = DeploymentHistoryClient()
        self.assertEqual(
            advanced._resolve_deployment_uuid(client, "app-uuid", "returned-not-persisted"),
            "persisted-new",
        )
        self.assertEqual(client.path, "/deployments/applications/app-uuid?skip=0&take=10")

    def test_deployment_timeout_is_configurable_and_has_operational_default(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(advanced._deployment_timeout_seconds(), 900)
        with patch.dict(os.environ, {"COOLIFY_DEPLOYMENT_TIMEOUT_SECONDS": "42.5"}):
            self.assertEqual(advanced._deployment_timeout_seconds(), 42.5)
        with patch.dict(os.environ, {"COOLIFY_DEPLOYMENT_TIMEOUT_SECONDS": "0"}):
            with self.assertRaises(advanced.ReconcileError):
                advanced._deployment_timeout_seconds()

    def test_repository_identity_accepts_coolify_url_forms(self):
        for value in (
            "goodmorning-mx/balletpourtous",
            "https://github.com/goodmorning-mx/balletpourtous.git",
            "ssh://git@github.com/goodmorning-mx/balletpourtous.git",
            "git@github.com:goodmorning-mx/balletpourtous.git",
        ):
            self.assertEqual(
                advanced._normalize_repository(value),
                "goodmorning-mx/balletpourtous",
            )

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

    def test_plan_does_not_resolve_or_write_existing_application_environment(self):
        client = ExistingAppPlanClient()
        value = contract.validate_manifest(manifest())
        value["applications"] = [value["applications"][0]]
        result = advanced.reconcile(
            value,
            client=client,
            server_name="localhost",
            destination_uuid="destination",
            confirm_product=None,
            deploy=False,
        )
        self.assertIn(
            "would_reconcile",
            [action["action"] for action in result["actions"]],
        )
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

    def test_production_plan_uses_production_scope_and_prod_names(self):
        client = FakeClient()
        result = advanced.reconcile(
            contract.validate_manifest(production_manifest()),
            client=client,
            server_name="localhost",
            destination_uuid="destination",
            confirm_product=None,
            deploy=False,
        )

        self.assertEqual(result["status"], "planned")
        self.assertFalse(result["legacy_retained"])
        self.assertIn(
            "helloworldv2-mobile-prod",
            result["resources"]["applications"],
        )
        self.assertFalse(client.writes)

    def test_production_apply_requires_its_own_switch_and_environment(self):
        client = FakeClient()
        client.apply = True
        value = contract.validate_manifest(production_manifest())

        with patch.dict(
            os.environ,
            {"COOLIFY_ALLOW_ADVANCED_WRITES": "true"},
            clear=True,
        ), self.assertRaisesRegex(
            advanced.ReconcileError,
            "COOLIFY_ALLOW_ADVANCED_PRODUCTION_WRITES",
        ):
            advanced.reconcile(
                value,
                client=client,
                server_name="localhost",
                destination_uuid="destination",
                confirm_product="helloworldv2",
                confirm_environment="production",
                deploy=False,
            )

        with patch.dict(
            os.environ,
            {"COOLIFY_ALLOW_ADVANCED_PRODUCTION_WRITES": "true"},
            clear=True,
        ), self.assertRaisesRegex(
            advanced.ReconcileError,
            "exact --confirm-environment",
        ):
            advanced.reconcile(
                value,
                client=client,
                server_name="localhost",
                destination_uuid="destination",
                confirm_product="helloworldv2",
                confirm_environment="dev",
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
        self.assertEqual(payload["custom_network_aliases"], "helloworldv2-mobile-dev")
        self.assertTrue(payload["is_auto_deploy_enabled"])

    def test_private_application_clears_stale_proxy_labels(self):
        value = contract.validate_manifest(manifest())
        private_frontend = next(
            item for item in value["applications"] if item["role"] == "worker"
        )
        payload = advanced._application_payload(
            value,
            private_frontend,
            {
                "project_uuid": "project",
                "environment_uuid": "environment",
                "server_uuid": "server",
                "destination_uuid": "destination",
                "github_app_uuid": "github",
            },
        )

        self.assertEqual(payload["domains"], "")
        self.assertEqual(payload["custom_labels"], "")

    def test_production_payload_is_pinned_to_main_and_production(self):
        value = contract.validate_manifest(production_manifest())
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

        self.assertEqual(payload["name"], "helloworldv2-mobile-prod")
        self.assertEqual(payload["git_branch"], "main")
        self.assertEqual(payload["environment_name"], "production")

    def test_payload_preserves_resources_v2_base_directory(self):
        value = contract.validate_manifest(manifest())
        api = next(item for item in value["applications"] if item["role"] == "api")
        api["base_directory"] = "/backend"
        api["dockerfile"] = "/Dockerfile"

        payload = advanced._application_payload(
            value,
            api,
            {
                "project_uuid": "project",
                "environment_uuid": "environment",
                "server_uuid": "server",
                "destination_uuid": "destination",
                "github_app_uuid": "github",
            },
        )

        self.assertEqual(payload["base_directory"], "/backend")
        self.assertEqual(payload["dockerfile_location"], "/Dockerfile")

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

    def test_psycopg_database_driver_is_normalized_without_exposing_credentials(self):
        values = advanced._environment_values(
            {
                "environment": [
                    {
                        "key": "DATABASE_URL",
                        "source": "database_url",
                        "resource": "db",
                        "driver": "psycopg",
                    }
                ]
            },
            {"db": "postgresql://user:secret@database/app"},
            {},
            {},
        )

        self.assertEqual(
            values[0]["value"],
            "postgresql+psycopg://user:secret@database/app",
        )
        self.assertNotIn(
            "user:secret",
            str(advanced._redacted({"environment": values})),
        )

    def test_generated_secret_is_created_once_then_reused(self):
        app = {
            "environment": [
                {"key": "MCP_AUTH_TOKEN", "source": "generated_secret"}
            ]
        }
        with patch("scripts.reconcile_advanced_product.secrets.token_urlsafe", return_value="new-secret") as generator:
            created = advanced._environment_values(app, {}, {}, {}, {})
            reused = advanced._environment_values(
                app,
                {},
                {},
                {},
                {"MCP_AUTH_TOKEN": {"key": "MCP_AUTH_TOKEN", "real_value": "existing-secret"}},
            )

        self.assertEqual(created[0]["value"], "new-secret")
        self.assertEqual(reused[0]["value"], "existing-secret")
        self.assertTrue(created[0]["is_shown_once"])
        generator.assert_called_once_with(48)

    def test_generated_secret_does_not_reuse_coolify_literal_quotes(self):
        values = advanced._environment_values(
            {"environment": [{"key": "MCP_AUTH_TOKEN", "source": "generated_secret"}]},
            {},
            {},
            {},
            {"MCP_AUTH_TOKEN": {"key": "MCP_AUTH_TOKEN", "real_value": "'existing-secret'"}},
        )

        self.assertEqual(values[0]["value"], "existing-secret")

    def test_generated_secret_refuses_rotation_when_real_value_is_unavailable(self):
        app = {
            "environment": [
                {"key": "MCP_AUTH_TOKEN", "source": "generated_secret"}
            ]
        }
        with self.assertRaises(advanced.ReconcileError):
            advanced._environment_values(
                app,
                {},
                {},
                {},
                {"MCP_AUTH_TOKEN": {"key": "MCP_AUTH_TOKEN", "value": "********"}},
            )

    def test_managed_secret_resolves_only_from_factory_binding_environment(self):
        app = {
            "environment": [
                {"key": "OPENAI_API_KEY", "source": "managed_secret", "binding": "goodmorning/openai-default"}
            ]
        }
        with patch.dict(os.environ, {"GOODMORNING_MANAGED_OPENAI_API_KEY": "secret-value"}, clear=True):
            values = advanced._environment_values(app, {}, {}, {})
        self.assertEqual(values[0]["value"], "secret-value")
        self.assertTrue(values[0]["is_shown_once"])

    def test_stripe_managed_secret_bindings_resolve_from_factory_environment(self):
        app = {
            "environment": [
                {"key": "STRIPE_SECRET_KEY", "source": "managed_secret", "binding": "goodmorning/stripe-secret-key"},
                {"key": "STRIPE_WEBHOOK_SECRET", "source": "managed_secret", "binding": "goodmorning/stripe-webhook-secret"},
            ]
        }
        with patch.dict(os.environ, {
            "GOODMORNING_MANAGED_STRIPE_SECRET_KEY": "test-only-stripe-key",
            "GOODMORNING_MANAGED_STRIPE_WEBHOOK_SECRET": "test-only-webhook-secret",
        }, clear=True):
            values = advanced._environment_values(app, {}, {}, {})
        self.assertEqual([item["key"] for item in values], ["STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"])
        self.assertTrue(all(item["is_shown_once"] for item in values))

    def test_adoption_does_not_clear_undeclared_existing_volume_options(self):
        value = contract.validate_manifest(manifest())
        value["migration"] = {
            "from_topology": "resources-v2", "mode": "adopt-in-place", "retain": True,
            "resource_names": {"applications": ["helloworldv2-api-dev"], "databases": [], "redis": []},
            "preserve": ["volumes"],
        }
        app = value["applications"][0]
        payload = advanced._application_payload(value, {**app, "persistent_volume": None}, {
            "project_uuid": "project", "environment_uuid": "environment", "server_uuid": "server",
            "destination_uuid": "destination", "github_app_uuid": "github",
        })
        found = {"custom_docker_run_options": "--mount source=legacy-volume,target=/app/data"}
        self.assertNotIn(
            "custom_docker_run_options",
            advanced._application_mutable_changes(found, payload, {**app, "persistent_volume": None}),
        )

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

    def test_explicitly_cleared_domains_do_not_fall_through_to_legacy_fqdn(self):
        response = {"domains": "", "fqdn": "https://legacy.example"}
        self.assertEqual(advanced._application_domains(response), "")
        self.assertEqual(advanced._normalized_domain_list(advanced._application_domains(response)), [])


if __name__ == "__main__":
    unittest.main()
