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
        if path not in self.payloads:
            raise pp.ProvisioningError(f"missing fixture for {path}", error_type="test")
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
            "/applications": [{"uuid": "api-1", "name": "ledger-api-dev", "type": "application", "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1", "git_repository": "goodmorning-mx/ledger", "git_branch": "dev", "build_pack": "dockerfile", "base_directory": "/backend", "dockerfile_location": "/Dockerfile", "ports_exposes": "8000", "domains": "https://api.dev.ledger.goodmorning.mx", "github_app_uuid": "app-1"}],
            "/databases": [{"uuid": "db-1", "name": "ledger-postgres-dev", "type": "postgresql", "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1"}],
        }
        payloads.update(payload_overrides)
        return FakeClient(payloads)

    def test_builds_expected_three_resource_dry_run_plan(self):
        client = self.client()
        result = pp.provision(args(), client_factory=lambda: client)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["mode"], "dry_run")
        self.assertFalse(any(call[0] == "POST" for call in client.calls))
        for expected_call in (
            ("GET", "/projects"),
            ("GET", "/projects/project-1"),
            ("GET", "/servers"),
            ("GET", "/github-apps"),
            ("GET", "/applications"),
            ("GET", "/applications/api-1"),
            ("GET", "/databases"),
        ):
            self.assertIn(expected_call, client.calls)
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
                "project_uuid": "project-1",
                "server_uuid": "server-1",
                "environment_name": "dev",
                "github_app_uuid": "app-1",
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

    def test_environment_selection_plans_dev_when_only_production_exists(self):
        client = self.client(
            **{
                "/projects/project-1": {
                    "uuid": "project-1",
                    "name": "ledger",
                    "environments": [{"uuid": "env-prod", "name": "production"}],
                }
            }
        )
        result = pp.provision(args(), client_factory=lambda: client)
        self.assertEqual(result["environment_resource"]["status"], "would_create")
        self.assertNotEqual(result["environment_resource"].get("uuid"), "env-prod")

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
                    {"uuid": "api-1", "name": "ledger-api-dev", "type": "application", "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1"},
                    {"uuid": "api-2", "name": "ledger-api-dev", "type": "application", "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1"},
                    {"uuid": "frontend-1", "name": "ledger-frontend-dev", "build_pack": "dockercompose", "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1"},
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
                "/applications": [{"uuid": "bad", "name": "ledger-postgres-dev", "type": "application", "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1"}],
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

    def test_selection_does_not_accept_uuid_or_slug_instead_of_name(self):
        client = self.client(
            **{
                "/projects": [{"uuid": "ledger", "slug": "ledger", "name": "not-ledger"}],
            }
        )
        result = pp.provision(args(), client_factory=lambda: client)
        self.assertEqual(result["project"]["status"], "would_create")
        self.assertNotEqual(result["project"].get("uuid"), "ledger")

        client = self.client(
            **{
                "/servers": [{"uuid": "development", "slug": "development", "name": "not-development"}],
            }
        )
        with self.assertRaisesRegex(pp.ProvisioningError, "server named exactly 'development'"):
            pp.provision(args(), client_factory=lambda: client)

        client = self.client(
            **{
                "/github-apps": {"github_apps": [{"uuid": "goodmorning-mx-coolify", "slug": "goodmorning-mx-coolify", "name": "not-app"}]},
            }
        )
        with self.assertRaisesRegex(pp.ProvisioningError, "GitHub App named exactly 'goodmorning-mx-coolify'"):
            pp.provision(args(), client_factory=lambda: client)

    def test_same_name_in_other_project_is_out_of_scope_not_duplicate(self):
        client = self.client(
            **{
                "/applications": [
                    {"uuid": "api-1", "name": "ledger-api-dev", "type": "application", "project_uuid": "other", "environment_uuid": "env-1", "server_uuid": "server-1"},
                    {"uuid": "api-2", "name": "ledger-api-dev", "type": "application", "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1", "git_repository": "goodmorning-mx/ledger", "git_branch": "dev", "build_pack": "dockerfile", "base_directory": "/backend", "dockerfile_location": "/Dockerfile", "ports_exposes": "8000", "domains": "https://api.dev.ledger.goodmorning.mx", "github_app_uuid": "app-1"},
                ],
                "/databases": [],
            }
        )
        result = pp.provision(args(), client_factory=lambda: client)
        api = next(item for item in result["discovery"]["existing_resources"]["classification"] if item["name"] == "ledger-api-dev")
        self.assertEqual(api["status"], "found")
        self.assertTrue(any(match.get("status") == "out_of_scope" for match in api["matches"]))

    def test_application_contradictions_are_incompatible_and_missing_fields_are_unverified(self):
        client = self.client(
            **{
                "/applications": [{"uuid": "api-1", "name": "ledger-api-dev", "type": "application", "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1", "git_repository": "goodmorning-mx/ledger", "git_branch": "main"}],
                "/databases": [],
            }
        )
        result = pp.provision(args(), client_factory=lambda: client)
        api = next(item for item in result["discovery"]["existing_resources"]["classification"] if item["name"] == "ledger-api-dev")
        self.assertEqual(api["status"], "incompatible")
        self.assertIn({"field": "git_branch", "expected": "dev", "actual": "main"}, api["differences"])

        client = self.client(
            **{
                "/applications": [{"uuid": "api-1", "name": "ledger-api-dev", "type": "application", "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1"}],
                "/databases": [],
            }
        )
        result = pp.provision(args(), client_factory=lambda: client)
        api = next(item for item in result["discovery"]["existing_resources"]["classification"] if item["name"] == "ledger-api-dev")
        self.assertEqual(api["status"], "compatibility_unverified")

    def test_legacy_compose_product_dev_is_reported_separately(self):
        client = self.client(
            **{
                "/applications": [{"uuid": "old", "name": "ledger-dev", "build_pack": "dockercompose", "docker_compose_location": "/compose.yaml", "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1"}],
                "/databases": [],
            }
        )
        result = pp.provision(args(), client_factory=lambda: client)
        legacy = result["discovery"]["existing_resources"]["legacy_compose"]
        self.assertEqual(legacy[0]["status"], "legacy_compose_migration_required")
        self.assertEqual(legacy[0]["resource"]["name"], "ledger-dev")

    def test_http_error_redacts_sensitive_non_json_and_json_bodies(self):
        message = pp.safe_http_body('password=hunter2 token=abc123 Authorization Bearer bearer-secret DATABASE_URL=postgresql://user:pass@db:5432/app')
        for forbidden in ("hunter2", "abc123", "bearer-secret", "user:pass"):
            self.assertNotIn(forbidden, message)
        json_message = pp.safe_http_body('{"message":"password=hunter2 DATABASE_URL=postgresql://user:pass@db/app","error":"Bearer bearer-secret","items":["token=nested-token"],"nested":{"token":"other-token"}}')
        for forbidden in ("other-token", "user:pass", "hunter2", "bearer-secret", "nested-token"):
            self.assertNotIn(forbidden, json_message)
        string_json = pp.safe_http_body('"secret=string-secret https://user:pass@example.test/path"')
        self.assertNotIn("string-secret", string_json)
        self.assertNotIn("user:pass", string_json)
    def test_candidate_listing_is_hydrated_before_classification(self):
        client = self.client(
            **{
                "/applications": [{"uuid": "api-min", "name": "ledger-api-dev"}],
                "/applications/api-min": {"uuid": "api-min", "name": "ledger-api-dev", "type": "application", "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1", "git_repository": "goodmorning-mx/ledger", "git_branch": "dev", "build_pack": "dockerfile", "base_directory": "/backend", "dockerfile_location": "/Dockerfile", "ports_exposes": "8000", "domains": "https://api.dev.ledger.goodmorning.mx", "github_app_uuid": "app-1"},
                "/databases": [],
            }
        )
        result = pp.provision(args(), client_factory=lambda: client)
        self.assertIn(("GET", "/applications/api-min"), client.calls)
        api = next(item for item in result["discovery"]["existing_resources"]["classification"] if item["name"] == "ledger-api-dev")
        self.assertEqual(api["status"], "found")

    def test_legacy_compose_scope_is_classified(self):
        client = self.client(
            **{
                "/applications": [
                    {"uuid": "old-other", "name": "ledger-dev", "build_pack": "dockercompose", "project_uuid": "other", "environment_uuid": "env-1", "server_uuid": "server-1"},
                    {"uuid": "old-unknown", "name": "ledger-dev", "build_pack": "dockercompose"},
                ],
                "/databases": [],
            }
        )
        result = pp.provision(args(), client_factory=lambda: client)
        statuses = [item["status"] for item in result["discovery"]["existing_resources"]["legacy_compose"]]
        self.assertIn("out_of_scope", statuses)
        self.assertIn("scope_unverified", statuses)

    def test_postgres_compatibility_cases(self):
        def status_for(database):
            client = self.client(**{"/applications": [], "/databases": [database]})
            result = pp.provision(args(), client_factory=lambda: client)
            return next(item for item in result["discovery"]["existing_resources"]["classification"] if item["name"] == "ledger-postgres-dev")

        compatible = status_for({"uuid": "db", "name": "ledger-postgres-dev", "type": "postgres", "version": "16", "is_public": False, "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1"})
        self.assertEqual(compatible["status"], "found")
        mysql = status_for({"uuid": "db", "name": "ledger-postgres-dev", "type": "mysql", "version": "16", "is_public": False, "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1"})
        self.assertEqual(mysql["status"], "incompatible")
        public = status_for({"uuid": "db", "name": "ledger-postgres-dev", "type": "postgresql", "version": "16", "is_public": True, "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1"})
        self.assertEqual(public["status"], "incompatible")
        version = status_for({"uuid": "db", "name": "ledger-postgres-dev", "type": "postgresql", "version": "15", "is_public": False, "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1"})
        self.assertEqual(version["status"], "incompatible")
        unknown = status_for({"uuid": "db", "name": "ledger-postgres-dev", "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1"})
        self.assertEqual(unknown["status"], "compatibility_unverified")

    def test_application_normalization_accepts_equivalent_values_and_rejects_contradictions(self):
        client = self.client(
            **{
                "/applications": [{"uuid": "api", "name": "ledger-api-dev", "type": "application", "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1", "git_repository": "https://github.com/goodmorning-mx/ledger", "git_branch": "dev", "build_pack": "dockerfile", "base_directory": "/backend", "dockerfile_location": "/Dockerfile", "ports_exposes": [8000], "domains": ["https://api.dev.ledger.goodmorning.mx/"], "github_app_uuid": "app-1"}],
                "/databases": [],
            }
        )
        result = pp.provision(args(), client_factory=lambda: client)
        api = next(item for item in result["discovery"]["existing_resources"]["classification"] if item["name"] == "ledger-api-dev")
        self.assertEqual(api["status"], "found")

        client = self.client(
            **{
                "/applications": [{"uuid": "api", "name": "ledger-api-dev", "type": "application", "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1", "git_repository": "goodmorning-mx/other", "git_branch": "dev", "build_pack": "dockerfile", "base_directory": "/backend", "dockerfile_location": "/Dockerfile", "ports_exposes": "8000", "domains": "https://api.dev.ledger.goodmorning.mx", "github_app_uuid": "app-1"}],
                "/databases": [],
            }
        )
        result = pp.provision(args(), client_factory=lambda: client)
        api = next(item for item in result["discovery"]["existing_resources"]["classification"] if item["name"] == "ledger-api-dev")
        self.assertEqual(api["status"], "incompatible")


    def test_base_url_validation_rejects_credentials_paths_query_fragment_and_http(self):
        for url in (
            "https://user:pass@coolify.example",
            "https://coolify.example/api",
            "https://coolify.example?x=1",
            "https://coolify.example#frag",
            "http://coolify.example",
        ):
            with self.assertRaises(pp.ProvisioningError):
                pp.validate_base_url(url)
        self.assertEqual(pp.validate_base_url("https://coolify.example/"), "https://coolify.example")

    def test_single_character_product_slug_is_valid(self):
        pp.validate_args(args(product="a", repository="goodmorning-mx/a"))

class FakeWriteClient(FakeClient):
    def __init__(self, payloads, responses=None):
        super().__init__(payloads)
        self.responses = responses or {}
        self.mode = None

    def create_project(self, payload):
        self.calls.append(("POST", "/projects", pp.redact(payload)))
        value = self.responses.get("project", {"uuid": "project-1", "name": payload["name"]})
        if isinstance(value, Exception):
            raise value
        return pp.Response(self.responses.get("project_status", 201), value, {})

    def create_environment(self, project_uuid, payload):
        self.calls.append(("POST", f"/projects/{project_uuid}/environments", pp.redact(payload)))
        value = self.responses.get("environment", {"uuid": "env-1", "name": "dev"})
        if isinstance(value, Exception):
            raise value
        return pp.Response(self.responses.get("environment_status", 201), value, {})

    def create_postgresql(self, payload):
        self.calls.append(("POST", "/databases/postgresql", pp.redact(payload)))
        value = self.responses.get("postgresql", {"uuid": "db-1", "name": payload["name"]})
        if isinstance(value, Exception):
            raise value
        return pp.Response(self.responses.get("postgresql_status", 201), value, {})


def apply_args(**overrides):
    return args(dry_run=False, apply=True, confirm_product="ledger", **overrides)


class Stage3ProvisioningTests(unittest.TestCase):
    def apply_env(self):
        return mock.patch.dict(os.environ, {"COOLIFY_ALLOW_RESOURCE_WRITES": "true"})

    def missing_project_payloads(self, **overrides):
        payloads = {
            "/projects": [],
            "/servers": [{"uuid": "server-1", "name": "development"}],
            "/github-apps": {"github_apps": [{"uuid": "app-1", "name": "goodmorning-mx-coolify"}]},
            "/projects/project-1": {"uuid": "project-1", "name": "ledger", "environments": []},
            "/projects/project-1/environments": [],
            "/databases": [],
            "/databases/db-1": {
                "uuid": "db-1",
                "name": "ledger-postgres-dev",
                "database_type": "standalone-postgresql",
                "image": "postgres:16-alpine",
                "is_public": False,
                "project_uuid": "project-1",
                "environment_uuid": "env-1",
                "server_uuid": "server-1",
                "postgres_user": "ledger_dev_user",
                "postgres_db": "ledger_dev",
                "internal_db_url": "postgres://fake:fake@fake/db",
            },
            "/applications": [],
        }
        payloads.update(overrides)
        return payloads

    def test_dry_run_missing_everything_plans_without_post_or_password(self):
        client = FakeWriteClient(self.missing_project_payloads())
        result = pp.provision(args(), client_factory=lambda: client)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["project"]["status"], "would_create")
        self.assertEqual(result["environment_resource"]["status"], "would_create")
        postgres = next(resource for resource in result["resources"] if resource["kind"] == "database")
        self.assertEqual(postgres["status"], "would_create")
        self.assertFalse(any(call[0] == "POST" for call in client.calls))
        self.assertNotIn("fake:fake", json.dumps(result))

    def test_apply_creates_project_environment_and_verified_postgresql(self):
        client = FakeWriteClient(self.missing_project_payloads(**{
            "/projects": [],
            "/projects/project-1": {"uuid": "project-1", "name": "ledger"},
            "/projects/project-1/environments": [],
            "/databases": [],
        }))
        with self.apply_env(), mock.patch.object(pp, "generate_postgres_password", return_value="FAKE_PASSWORD_MARKER"):
            result = pp.provision(apply_args(), client_factory=lambda: client)
        self.assertEqual(result["status"], "created")
        self.assertEqual([call[0] for call in client.calls].count("POST"), 3)
        self.assertFalse(any("deploy" in call[1] or "start" in call[1] for call in client.calls))
        self.assertNotIn("FAKE_PASSWORD_MARKER", json.dumps(result))
        self.assertNotIn("fake:fake", json.dumps(result))

    def test_apply_gates_fail_before_any_post(self):
        for confirm, env in (("ledger", {}), (None, {"COOLIFY_ALLOW_RESOURCE_WRITES": "true"}), ("other", {"COOLIFY_ALLOW_RESOURCE_WRITES": "true"})):
            client = FakeWriteClient(self.missing_project_payloads())
            with mock.patch.dict(os.environ, env, clear=True):
                with self.assertRaises(pp.ProvisioningError):
                    pp.provision(args(dry_run=False, apply=True, confirm_product=confirm), client_factory=lambda: client)
            self.assertFalse(any(call[0] == "POST" for call in client.calls))

    def test_conflict_recovery_uses_http_status_not_message_text(self):
        conflict = pp.ProvisioningError("body mentions 409", error_type="coolify_api", http_status=500, method="POST", path="/projects")
        client = FakeWriteClient(self.missing_project_payloads(), {"project": conflict})
        with self.apply_env():
            with self.assertRaises(pp.ProvisioningError):
                pp.provision(apply_args(), client_factory=lambda: client)
        self.assertEqual([call for call in client.calls if call[0] == "POST"], [("POST", "/projects", {"name": "ledger"})])

    def test_project_create_without_uuid_is_verified_by_discovery(self):
        client = FakeWriteClient(self.missing_project_payloads(**{
            "/projects": [[], [{"uuid": "project-1", "name": "ledger"}]],
            "/projects/project-1": {"environments": [{"uuid": "env-1", "name": "dev"}]},
            "/databases": [{"uuid": "db-1", "name": "ledger-postgres-dev", "database_type": "postgresql", "version": "16", "is_public": False, "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1"}],
        }), {"project": {"name": "ledger"}})
        original_get = client.get
        counters = {"/projects": 0}
        def sequenced(path):
            if path == "/projects" and isinstance(client.payloads[path], list) and client.payloads[path] and isinstance(client.payloads[path][0], list):
                index = counters[path]
                counters[path] += 1
                return client.payloads[path][min(index, len(client.payloads[path]) - 1)]
            return original_get(path)
        client.get = sequenced
        with self.apply_env():
            result = pp.provision(apply_args(), client_factory=lambda: client)
        self.assertEqual(result["project"]["status"], "created")

    def test_project_create_wrong_name_becomes_partial(self):
        client = FakeWriteClient(self.missing_project_payloads(), {"project": {"uuid": "project-1", "name": "wrong"}})
        with self.apply_env():
            result = pp.provision(apply_args(), client_factory=lambda: client)
        self.assertEqual(result["status"], "partial")
        self.assertTrue(result["blockers"])

    def test_environment_create_without_uuid_is_verified_by_discovery(self):
        client = FakeWriteClient(self.missing_project_payloads(**{
            "/projects": [{"uuid": "project-1", "name": "ledger"}],
            "/projects/project-1": {"environments": [{"uuid": "env-1", "name": "dev"}]},
            "/databases": [],
        }), {"environment": {"name": "dev"}})
        with self.apply_env():
            result = pp.provision(apply_args(), client_factory=lambda: client)
        self.assertIn(result["environment_resource"]["status"], {"exists", "created"})

    def test_environment_create_wrong_name_is_partial_no_second_post(self):
        client = FakeWriteClient(self.missing_project_payloads(**{"/projects": [{"uuid": "project-1", "name": "ledger"}]}), {"environment": {"uuid": "env-prod", "name": "production"}})
        with self.apply_env():
            result = pp.provision(apply_args(), client_factory=lambda: client)
        self.assertEqual(result["status"], "partial")
        self.assertEqual([call[1] for call in client.calls if call[0] == "POST"].count("/projects/project-1/environments"), 1)

    def test_postgresql_202_uses_discovery_and_detail(self):
        client = FakeWriteClient(self.missing_project_payloads(**{
            "/projects": [{"uuid": "project-1", "name": "ledger"}],
            "/projects/project-1": {"environments": [{"uuid": "env-1", "name": "dev"}]},
            "/databases": [[], [{"uuid": "db-1", "name": "ledger-postgres-dev"}]],
        }), {"postgresql": {"name": "ledger-postgres-dev"}, "postgresql_status": 202})
        original_get = client.get
        count = {"/databases": 0}
        def sequenced(path):
            if path == "/databases":
                index = count[path]
                count[path] += 1
                return client.payloads[path][min(index, 1)]
            return original_get(path)
        client.get = sequenced
        with self.apply_env():
            result = pp.provision(apply_args(), client_factory=lambda: client)
        self.assertEqual(result["postgresql"]["status"], "created")

    def test_postgresql_created_with_incomplete_detail_is_created_unverified_partial(self):
        client = FakeWriteClient(self.missing_project_payloads(**{
            "/projects": [{"uuid": "project-1", "name": "ledger"}],
            "/projects/project-1": {"environments": [{"uuid": "env-1", "name": "dev"}]},
            "/databases/db-1": {"uuid": "db-1", "name": "ledger-postgres-dev"},
        }))
        with self.apply_env():
            result = pp.provision(apply_args(), client_factory=lambda: client)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["postgresql"]["status"], "created_unverified")

    def test_postgresql_detail_failure_is_created_unverified(self):
        client = FakeWriteClient(self.missing_project_payloads(**{
            "/projects": [{"uuid": "project-1", "name": "ledger"}],
            "/projects/project-1": {"environments": [{"uuid": "env-1", "name": "dev"}]},
            "/databases/db-1": pp.ProvisioningError("detail failed", error_type="coolify_api"),
        }))
        with self.apply_env():
            result = pp.provision(apply_args(), client_factory=lambda: client)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["postgresql"]["status"], "created_unverified")

    def test_postgresql_created_but_incompatible_requires_recreate(self):
        client = FakeWriteClient(self.missing_project_payloads(**{
            "/projects": [{"uuid": "project-1", "name": "ledger"}],
            "/projects/project-1": {"environments": [{"uuid": "env-1", "name": "dev"}]},
            "/databases/db-1": {"uuid": "db-1", "name": "ledger-postgres-dev", "database_type": "mysql", "version": "16", "is_public": False, "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1"},
        }))
        with self.apply_env():
            result = pp.provision(apply_args(), client_factory=lambda: client)
        self.assertEqual(result["status"], "recreate_required")

    def test_second_execution_reuses_verified_postgresql_without_duplicate(self):
        client = FakeWriteClient(self.missing_project_payloads(**{
            "/projects": [{"uuid": "project-1", "name": "ledger"}],
            "/projects/project-1": {"environments": [{"uuid": "env-1", "name": "dev"}]},
            "/databases": [{"uuid": "db-1", "name": "ledger-postgres-dev"}],
        }))
        with self.apply_env():
            result = pp.provision(apply_args(), client_factory=lambda: client)
        self.assertEqual(result["status"], "exists")
        self.assertFalse(any(call[0] == "POST" for call in client.calls))

    def test_postgres_version_parser_accepts_only_exact_major_16(self):
        accepted = ["16", 16, "postgres:16", "postgres:16-alpine"]
        rejected = ["15", "116", "postgres:15", "postgres:16.1", "mysql:16"]
        for value in accepted:
            self.assertEqual(pp.postgres_major_version(value), "16")
        for value in rejected:
            self.assertNotEqual(pp.postgres_major_version(value), "16")
        differences, unverified = pp.postgres_differences({"type": "postgresql", "image": "postgres:16.1", "is_public": False})
        self.assertTrue(differences)
        self.assertFalse(unverified)

    def test_operational_urls_are_visible_but_connection_urls_are_redacted(self):
        value = pp.redact({
            "COOLIFY_BASE_URL": "https://coolify.example",
            "VITE_API_URL": "https://api.dev.ledger.goodmorning.mx",
            "internal_db_url": "postgres://user:pass@db/app",
            "DATABASE_URL": "postgresql://user:pass@db/app",
        })
        self.assertEqual(value["COOLIFY_BASE_URL"], "https://coolify.example")
        self.assertEqual(value["VITE_API_URL"], "https://api.dev.ledger.goodmorning.mx")
        self.assertEqual(value["internal_db_url"], "[REDACTED]")
        self.assertEqual(value["DATABASE_URL"], "[REDACTED]")

    def test_main_returns_two_for_partial_blocked_and_recreate_required(self):
        for status in ("partial", "blocked", "recreate_required", "created_unverified"):
            with mock.patch.object(pp, "provision", return_value={"status": status}):
                with mock.patch("sys.stdout"):
                    code = pp.main([
                        "--product", "ledger", "--repository", "goodmorning-mx/ledger", "--branch", "dev",
                        "--coolify-project", "ledger", "--environment", "dev", "--server", "development",
                        "--github-app", "goodmorning-mx-coolify", "--dry-run",
                    ])
            self.assertEqual(code, 2)


class Stage3FinalCorrectionsTests(unittest.TestCase):
    def client_for_dry_run(self, **overrides):
        payloads = {
            "/servers": [{"uuid": "server-1", "name": "development"}],
            "/github-apps": {"github_apps": [{"uuid": "app-1", "name": "goodmorning-mx-coolify"}]},
            "/projects": [{"uuid": "project-1", "name": "ledger"}],
            "/projects/project-1": {"uuid": "project-1", "name": "ledger", "environments": [{"uuid": "env-1", "name": "dev"}]},
            "/databases": [],
            "/applications": [],
        }
        payloads.update(overrides)
        return FakeWriteClient(payloads)

    def assert_dry_run_get_only(self, client, result):
        self.assertEqual(result["status"], "dry_run")
        self.assertFalse(any(call[0] == "POST" for call in client.calls))

    def test_no_duplicate_top_level_functions_in_provisioner(self):
        import ast
        import collections
        from pathlib import Path
        tree = ast.parse(Path("scripts/provision_product_development.py").read_text())
        names = [node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
        duplicates = sorted(name for name, count in collections.Counter(names).items() if count > 1)
        self.assertEqual(duplicates, [])

    def test_dry_run_missing_project_with_no_projects(self):
        client = self.client_for_dry_run(**{"/projects": []})
        result = pp.provision(args(), client_factory=lambda: client)
        self.assert_dry_run_get_only(client, result)
        self.assertEqual(result["project"]["status"], "would_create")
        self.assertEqual(result["environment_resource"]["status"], "would_create")

    def test_dry_run_missing_project_with_other_projects(self):
        client = self.client_for_dry_run(**{"/projects": [{"uuid": "other", "name": "other-product"}]})
        result = pp.provision(args(), client_factory=lambda: client)
        self.assert_dry_run_get_only(client, result)
        self.assertEqual(result["project"]["status"], "would_create")
        self.assertEqual(result["environment_resource"]["status"], "would_create")
        self.assertEqual(result["postgresql"]["status"], "would_create")

    def test_dry_run_project_exists_dev_absent(self):
        client = self.client_for_dry_run(**{
            "/projects/project-1": {"uuid": "project-1", "name": "ledger", "environments": []},
            "/projects/project-1/environments": [],
        })
        result = pp.provision(args(), client_factory=lambda: client)
        self.assert_dry_run_get_only(client, result)
        self.assertEqual(result["environment_resource"]["status"], "would_create")

    def test_dry_run_project_exists_only_production(self):
        client = self.client_for_dry_run(**{
            "/projects/project-1": {"uuid": "project-1", "name": "ledger", "environments": [{"uuid": "prod", "name": "production"}]},
            "/projects/project-1/environments": [{"uuid": "prod", "name": "production"}],
        })
        result = pp.provision(args(), client_factory=lambda: client)
        self.assert_dry_run_get_only(client, result)
        self.assertEqual(result["environment_resource"]["status"], "would_create")

    def test_dry_run_project_exists_other_environments(self):
        client = self.client_for_dry_run(**{
            "/projects/project-1": {"uuid": "project-1", "name": "ledger", "environments": [{"uuid": "qa", "name": "qa"}]},
            "/projects/project-1/environments": [{"uuid": "qa", "name": "qa"}],
        })
        result = pp.provision(args(), client_factory=lambda: client)
        self.assert_dry_run_get_only(client, result)
        self.assertEqual(result["environment_resource"]["status"], "would_create")

    def test_dry_run_project_and_dev_exist_postgresql_missing(self):
        client = self.client_for_dry_run()
        result = pp.provision(args(), client_factory=lambda: client)
        self.assert_dry_run_get_only(client, result)
        self.assertEqual(result["postgresql"]["status"], "would_create")

    def test_dry_run_everything_exists(self):
        client = self.client_for_dry_run(**{
            "/databases": [{"uuid": "db-1", "name": "ledger-postgres-dev", "type": "postgresql", "version": "16", "is_public": False, "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1"}],
            "/databases/db-1": {"uuid": "db-1", "name": "ledger-postgres-dev", "type": "postgresql", "version": "16", "is_public": False, "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1"},
        })
        result = pp.provision(args(), client_factory=lambda: client)
        self.assert_dry_run_get_only(client, result)
        self.assertEqual(result["postgresql"]["status"], "exists")

    def test_dry_run_homonymous_postgresql_out_of_scope(self):
        client = self.client_for_dry_run(**{
            "/databases": [{"uuid": "db-1", "name": "ledger-postgres-dev", "type": "postgresql", "version": "16", "is_public": False, "project_uuid": "other", "environment_uuid": "env-1", "server_uuid": "server-1"}],
            "/databases/db-1": {"uuid": "db-1", "name": "ledger-postgres-dev", "type": "postgresql", "version": "16", "is_public": False, "project_uuid": "other", "environment_uuid": "env-1", "server_uuid": "server-1"},
        })
        result = pp.provision(args(), client_factory=lambda: client)
        self.assert_dry_run_get_only(client, result)
        self.assertEqual(result["postgresql"]["status"], "out_of_scope")

    def test_postgres_engine_normalization_is_strict(self):
        for value in ("postgres", "postgresql", "standalone-postgresql"):
            self.assertIsNotNone(pp.normalize_postgres_engine(value))
            self.assertTrue(pp.is_postgres_engine(value))
        for value in ("notpostgres", "postgres-compatible", "mysql", "mariadb", "ambiguous-postgresql-like"):
            self.assertIsNone(pp.normalize_postgres_engine(value))
            self.assertFalse(pp.is_postgres_engine(value))

    def test_main_dry_run_missing_project_with_other_projects_returns_zero(self):
        result = {
            "status": "dry_run",
            "project": {"status": "would_create"},
            "environment_resource": {"status": "would_create"},
            "postgresql": {"status": "would_create"},
        }
        with mock.patch.object(pp, "provision", return_value=result):
            with mock.patch("sys.stdout") as stdout:
                code = pp.main([
                    "--product", "ledger", "--repository", "goodmorning-mx/ledger", "--branch", "dev",
                    "--coolify-project", "ledger", "--environment", "dev", "--server", "development",
                    "--github-app", "goodmorning-mx-coolify", "--dry-run",
                ])
        self.assertEqual(code, 0)
        printed = "".join(call.args[0] for call in stdout.write.call_args_list if call.args)
        payload = json.loads(printed)
        self.assertEqual(payload["status"], "dry_run")
        self.assertEqual(payload["project"]["status"], "would_create")



if __name__ == "__main__":
    unittest.main()
