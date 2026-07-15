import argparse
import json
import os
import unittest
from unittest import mock

import scripts.provision_product_development as pp


def args(**overrides):
    base = dict(
        product="ledger",
        repository="goodmorning-mx/ledger",
        branch="dev",
        coolify_project="ledger",
        environment="dev",
        server="development",
        github_app="goodmorning-mx-coolify",
        dry_run=True,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


class FakeClient:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    def get(self, path):
        self.calls.append(("GET", path))
        value = self.payloads[path]
        if isinstance(value, Exception):
            raise value
        return value


class ProductDevelopmentProvisionerTests(unittest.TestCase):
    def client(self, **payload_overrides):
        payloads = {
            "/projects": [{"uuid": "project-1", "name": "ledger"}],
            "/projects/project-1": {
                "uuid": "project-1",
                "name": "ledger",
                "environments": [{"uuid": "env-1", "name": "dev"}],
            },
            "/servers": [{"uuid": "server-1", "name": "development"}],
            "/github-apps": {
                "github_apps": [
                    {"uuid": "app-1", "name": "goodmorning-mx-coolify", "token": "hidden"}
                ]
            },
            "/applications": [{"uuid": "api-1", "name": "ledger-api-dev", "type": "application"}],
            "/databases": [{"uuid": "db-1", "name": "ledger-postgres-dev", "type": "postgresql"}],
        }
        payloads.update(payload_overrides)
        return FakeClient(payloads)

    def test_builds_expected_three_resource_dry_run_plan(self):
        client = self.client()
        result = pp.provision(args(), client_factory=lambda: client)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["mode"], "read_only_plan")
        self.assertEqual(
            client.calls,
            [
                ("GET", "/projects"),
                ("GET", "/projects/project-1"),
                ("GET", "/servers"),
                ("GET", "/github-apps"),
                ("GET", "/applications"),
                ("GET", "/databases"),
            ],
        )
        postgres, backend, frontend = result["resources"]
        self.assertEqual(postgres["desired_state"]["version"], "16")
        self.assertFalse(postgres["desired_state"]["is_public"])
        self.assertIsNone(postgres["desired_state"]["domain"])
        self.assertEqual(
            backend["create_payload_preview"],
            {
                "name": "ledger-api-dev",
                "git_repository": "goodmorning-mx/ledger",
                "git_branch": "dev",
                "build_pack": "dockerfile",
                "base_directory": "/backend",
                "dockerfile_location": "/Dockerfile",
                "ports_exposes": "8000",
                "domains": "https://api.dev.ledger.goodmorning.mx",
                "instant_deploy": False,
            },
        )
        self.assertEqual(frontend["create_payload_preview"]["ports_exposes"], "80")
        self.assertEqual(frontend["create_payload_preview"]["domains"], "https://dev.ledger.goodmorning.mx")
        self.assertNotIn("auto_deploy", backend["create_payload_preview"])
        self.assertFalse(backend["desired_state"]["deployment_policy"]["auto_deploy_enabled"])
        self.assertEqual(result["discovery"]["github_app"], {"name": "goodmorning-mx-coolify", "uuid": "app-1"})

    def test_rejects_any_non_dev_branch_or_environment(self):
        with self.assertRaisesRegex(pp.ProvisioningError, "Only branch 'dev'"):
            pp.validate_args(args(branch="main"))
        with self.assertRaisesRegex(pp.ProvisioningError, "Only environment 'dev'"):
            pp.validate_args(args(environment="prod"))

    def test_requires_dry_run(self):
        with self.assertRaisesRegex(pp.ProvisioningError, "Only --dry-run"):
            pp.validate_args(args(dry_run=False))

    def test_selects_project_environment_server_and_github_app_by_exact_unique_name(self):
        client = self.client(
            **{
                "/projects": [
                    {"uuid": "wrong", "name": "ledger-old"},
                    {"uuid": "project-1", "name": "ledger"},
                ],
                "/servers": [
                    {"uuid": "wrong", "name": "development-2"},
                    {"uuid": "server-1", "name": "development"},
                ],
            }
        )
        result = pp.provision(args(), client_factory=lambda: client)
        self.assertEqual(result["discovery"]["project"]["uuid"], "project-1")
        self.assertEqual(result["discovery"]["server"]["uuid"], "server-1")

    def test_environment_selection_fails_when_only_production_exists(self):
        client = self.client(
            **{
                "/projects/project-1": {
                    "uuid": "project-1",
                    "name": "ledger",
                    "environments": [{"uuid": "env-prod", "name": "production"}],
                }
            }
        )
        with self.assertRaisesRegex(pp.ProvisioningError, "environment named exactly 'dev'"):
            pp.provision(args(), client_factory=lambda: client)

    def test_duplicate_exact_match_is_error(self):
        client = self.client(**{"/projects": [{"uuid": "1", "name": "ledger"}, {"uuid": "2", "name": "ledger"}]})
        with self.assertRaisesRegex(pp.ProvisioningError, "multiple Coolify project"):
            pp.provision(args(), client_factory=lambda: client)

    def test_missing_environment_can_be_discovered_from_environment_endpoint(self):
        client = self.client(
            **{
                "/projects/project-1": {"uuid": "project-1", "name": "ledger"},
                "/projects/project-1/environments": [{"uuid": "env-1", "name": "dev"}],
            }
        )
        result = pp.provision(args(), client_factory=lambda: client)
        self.assertIn(("GET", "/projects/project-1/environments"), client.calls)
        self.assertEqual(result["discovery"]["environment"]["uuid"], "env-1")

    def test_existing_resource_discovery_classifies_found_absent_duplicate_incompatible_and_legacy_compose(self):
        client = self.client(
            **{
                "/applications": [
                    {"uuid": "api-1", "name": "ledger-api-dev", "type": "application"},
                    {"uuid": "api-2", "name": "ledger-api-dev", "type": "application"},
                    {"uuid": "frontend-1", "name": "ledger-frontend-dev", "build_pack": "dockercompose"},
                    {"uuid": "other", "name": "ledger-worker-dev", "type": "application"},
                    {"uuid": "ignored", "name": "another-api-dev", "type": "application"},
                ],
                "/databases": [],
            }
        )
        result = pp.provision(args(), client_factory=lambda: client)
        classifications = {
            item["name"]: item["status"]
            for item in result["discovery"]["existing_resources"]["classification"]
        }
        self.assertEqual(classifications["ledger-postgres-dev"], "absent")
        self.assertEqual(classifications["ledger-api-dev"], "duplicate")
        self.assertEqual(classifications["ledger-frontend-dev"], "legacy_compose_migration_required")
        self.assertEqual(
            result["discovery"]["existing_resources"]["unexpected_same_product_prefix"][0]["name"],
            "ledger-worker-dev",
        )

    def test_existing_resource_discovery_classifies_incompatible_same_name(self):
        client = self.client(
            **{
                "/applications": [{"uuid": "bad", "name": "ledger-postgres-dev", "type": "application"}],
                "/databases": [],
            }
        )
        result = pp.provision(args(), client_factory=lambda: client)
        classifications = {
            item["name"]: item["status"]
            for item in result["discovery"]["existing_resources"]["classification"]
        }
        self.assertEqual(classifications["ledger-postgres-dev"], "incompatible")

    def test_client_refuses_write_methods(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            client = pp.CoolifyClient("https://coolify.example", "secret-token")
            for method in ("POST", "PATCH", "PUT", "DELETE"):
                with self.assertRaisesRegex(pp.ProvisioningError, "Refusing non-read-only"):
                    client.request(method, "/applications")

    def test_redacts_env_token_and_secret_like_keys_without_redacting_operational_fields(self):
        with mock.patch.dict(os.environ, {"COOLIFY_API_TOKEN": "secret-token"}):
            self.assertEqual(pp.redact("Bearer secret-token"), "Bearer [REDACTED]")
            self.assertEqual(
                pp.redact(
                    {
                        "api_token": "secret-token",
                        "git_repository": "goodmorning-mx/ledger",
                        "git_branch": "dev",
                        "base_directory": "/backend",
                        "dockerfile_location": "/Dockerfile",
                        "ports_exposes": "8000",
                        "domains": "https://api.dev.ledger.goodmorning.mx",
                    }
                ),
                {
                    "api_token": "[REDACTED]",
                    "git_repository": "goodmorning-mx/ledger",
                    "git_branch": "dev",
                    "base_directory": "/backend",
                    "dockerfile_location": "/Dockerfile",
                    "ports_exposes": "8000",
                    "domains": "https://api.dev.ledger.goodmorning.mx",
                },
            )

    def test_main_prints_structured_json(self):
        expected = {"status": "dry_run", "resources": []}
        with mock.patch.object(pp, "provision", return_value=expected):
            with mock.patch("sys.stdout") as stdout:
                code = pp.main(
                    [
                        "--product",
                        "ledger",
                        "--repository",
                        "goodmorning-mx/ledger",
                        "--branch",
                        "dev",
                        "--coolify-project",
                        "ledger",
                        "--environment",
                        "dev",
                        "--server",
                        "development",
                        "--github-app",
                        "goodmorning-mx-coolify",
                        "--dry-run",
                    ]
                )
        self.assertEqual(code, 0)
        printed = "".join(call.args[0] for call in stdout.write.call_args_list if call.args)
        self.assertEqual(json.loads(printed), expected)


if __name__ == "__main__":
    unittest.main()
