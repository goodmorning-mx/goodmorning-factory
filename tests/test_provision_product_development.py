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
        destination_uuid="bk8cw0gkswgo04o0s88sk0kg",
        dry_run=True,
        apply=False,
        confirm_product=None,
        confirm_database_start=None,
        postgres_readiness_interval=0.01,
        postgres_readiness_timeout=0.01,
        deploy_backend=False,
        confirm_backend_deployment=None,
        backend_deployment_timeout=0.05,
        backend_deployment_interval=0.01,
        backend_deployment_max_poll_errors=1,
        backend_runtime_timeout=0.05,
        backend_runtime_interval=0.01,
        backend_runtime_max_poll_errors=1,
        backend_smoke_timeout=0.05,
        backend_smoke_interval=0.01,
        backend_smoke_request_timeout=0.01,
        deploy_frontend=False,
        confirm_frontend_deployment=None,
        frontend_deployment_timeout=0.05,
        frontend_deployment_interval=0.01,
        frontend_deployment_max_poll_errors=1,
        frontend_runtime_timeout=0.05,
        frontend_runtime_interval=0.01,
        frontend_runtime_max_poll_errors=1,
        frontend_smoke_timeout=0.05,
        frontend_smoke_interval=0.01,
        frontend_smoke_request_timeout=0.01,
    )
    base.update(overrides)
    return argparse.Namespace(**base)


class FakeClient:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []
        self.mode = None

    def get(self, path):
        self.calls.append(("GET", path))
        if path not in self.payloads:
            raise pp.ProvisioningError(f"missing fixture for {path}", error_type="test")
        value = self.payloads[path]
        if isinstance(value, list) and value and isinstance(value[0], tuple):
            method, result = value.pop(0)
            assert method == "GET"
            value = result
        if isinstance(value, Exception):
            raise value
        return value

    def create_frontend_application(self, payload):
        self.calls.append(("POST", "/applications/private-github-app", payload))
        value = self.payloads.get("POST frontend /applications/private-github-app", pp.Response(201, {"uuid":"front-1", "name":"ledger-frontend-dev"}, {}))
        if isinstance(value, Exception):
            raise value
        return value

    def create_frontend_env(self, uuid, payload):
        self.calls.append(("POST", f"/applications/{uuid}/envs", payload))
        value = self.payloads.get(f"POST frontend /applications/{uuid}/envs", pp.Response(201, {"key": payload.get("key")}, {}))
        if isinstance(value, Exception):
            raise value
        if self.payloads.get("__mutate_envs__", True):
            self.payloads.setdefault(f"/applications/{uuid}/envs", [])
            self.payloads[f"/applications/{uuid}/envs"].append(dict(payload))
        return value

    def update_frontend_env(self, uuid, payload):
        self.calls.append(("PATCH", f"/applications/{uuid}/envs", payload))
        value = self.payloads.get(f"PATCH frontend /applications/{uuid}/envs", pp.Response(200, {"key": payload.get("key")}, {}))
        if isinstance(value, Exception):
            raise value
        if self.payloads.get("__mutate_envs__", True):
            envs = self.payloads.setdefault(f"/applications/{uuid}/envs", [])
            envs[:] = [item for item in envs if item.get("key") != payload.get("key")]
            envs.append(dict(payload))
        return value

    def create_backend_application(self, payload):
        self.calls.append(("POST", "/applications/private-github-app", payload))
        value = self.payloads.get("POST /applications/private-github-app", pp.Response(201, {"uuid":"api-1", "name":"ledger-api-dev"}, {}))
        if isinstance(value, Exception):
            raise value
        return value

    def create_backend_env(self, uuid, payload):
        self.calls.append(("POST", f"/applications/{uuid}/envs", payload))
        value = self.payloads.get(f"POST /applications/{uuid}/envs", pp.Response(201, {"key": payload.get("key")}, {}))
        if isinstance(value, Exception):
            raise value
        if self.payloads.get("__mutate_envs__", True):
            self.payloads.setdefault(f"/applications/{uuid}/envs", [])
            self.payloads[f"/applications/{uuid}/envs"].append(dict(payload))
        return value

    def update_backend_env(self, uuid, payload):
        self.calls.append(("PATCH", f"/applications/{uuid}/envs", payload))
        value = self.payloads.get(f"PATCH /applications/{uuid}/envs", pp.Response(200, {"key": payload.get("key")}, {}))
        if isinstance(value, Exception):
            raise value
        if self.payloads.get("__mutate_envs__", True):
            envs = self.payloads.setdefault(f"/applications/{uuid}/envs", [])
            envs[:] = [item for item in envs if item.get("key") != payload.get("key")]
            envs.append(dict(payload))
        return value

    def bind_backend_application_bindings(self, bindings):
        self.backend_application_bindings = dict(bindings)

    def bind_backend_application_uuid(self, uuid, bindings=None):
        self.expected_backend_application_uuid = str(uuid)

    def bind_frontend_application_bindings(self, bindings):
        self.frontend_application_bindings = dict(bindings)

    def bind_frontend_application_uuid(self, uuid, bindings=None):
        self.expected_frontend_application_uuid = str(uuid)

    def bind_backend_env_contract(self, desired_env):
        self.expected_backend_env_contract = desired_env

    def bind_frontend_env_contract(self, desired_env):
        self.expected_frontend_env_contract = desired_env

    def bind_backend_deployment_preconditions(self, ok):
        self.backend_deployment_preconditions_ok = ok

    def bind_frontend_deployment_preconditions(self, ok):
        self.frontend_deployment_preconditions_ok = ok

    def deploy_frontend(self, uuid):
        self.calls.append(("POST", "/deploy", {"uuid": str(uuid), "force": False}))
        value = self.payloads.get("POST frontend /deploy", pp.Response(200, {"deployments":[{"message":"queued","resource_uuid":str(uuid),"deployment_uuid":"front-dep-1"}]}, {}))
        if isinstance(value, list) and value and isinstance(value[0], tuple):
            method, result = value.pop(0)
            assert method == "POST"
            value = result
        if isinstance(value, Exception):
            raise value
        return value

    def deploy_backend(self, uuid):
        self.calls.append(("POST", "/deploy", {"uuid": str(uuid), "force": False}))
        value = self.payloads.get("POST /deploy", pp.Response(200, {"deployments":[{"message":"queued","resource_uuid":str(uuid),"deployment_uuid":"dep-1"}]}, {}))
        if isinstance(value, Exception):
            raise value
        return value

    def start_postgresql(self, uuid):
        self.calls.append(("POST", f"/databases/{uuid}/start"))
        value = self.payloads.get(f"POST /databases/{uuid}/start", pp.Response(200, {"message": "Database starting."}, {}))
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
            "/servers": [{"uuid": "server-1", "name": "development", "destinations": [{"uuid":"bk8cw0gkswgo04o0s88sk0kg", "name":"docker"}]}],
            "/github-apps": {
                "github_apps": [
                    {"uuid": "app-1", "name": "goodmorning-mx-coolify", "token": "hidden"}
                ]
            },
            "/applications": [{"uuid": "api-1", "name": "ledger-api-dev", "type": "application", "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1", "git_repository": "goodmorning-mx/ledger", "git_branch": "dev", "build_pack": "dockerfile", "base_directory": "/backend", "dockerfile_location": "/Dockerfile", "ports_exposes": "8000", "domains": "https://api.dev.ledger.goodmorning.mx", "github_app_uuid": "app-1"}],
            "/databases": [{"uuid": "db-1", "name": "ledger-postgres-dev", "type": "postgresql", "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1", "version": "16", "is_public": False, "status": "exited"}],
            "/databases/db-1": {"uuid": "db-1", "name": "ledger-postgres-dev", "type": "postgresql", "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1", "version": "16", "is_public": False, "status": "exited", "internal_db_url": "postgresql://user:p%40ss@host:5432/db"},
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
        self.assertEqual(backend["create_payload_preview"]["name"], "ledger-api-dev")
        self.assertEqual(backend["create_payload_preview"]["git_repository"], "goodmorning-mx/ledger")
        self.assertEqual(backend["create_payload_preview"]["destination_uuid"], "bk8cw0gkswgo04o0s88sk0kg")
        self.assertTrue(backend["create_payload_preview"]["instant_deploy"])
        self.assertTrue(backend["create_payload_preview"]["is_auto_deploy_enabled"])
        self.assertFalse(backend["create_payload_preview"]["autogenerate_domain"])
        self.assertEqual(backend["create_payload_preview"]["health_check_path"], "/health")
        self.assertEqual(frontend["create_payload_preview"]["ports_exposes"], "80")
        self.assertEqual(frontend["create_payload_preview"]["domains"], "https://dev.ledger.goodmorning.mx")
        self.assertIn("is_auto_deploy_enabled", backend["create_payload_preview"])
        self.assertTrue(backend["desired_state"]["deployment_policy"]["auto_deploy_enabled"])
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

    def test_resolves_only_supported_managed_backend_binding(self):
        secret_value = "test-only-openai-value"
        request = args(required_secrets_json=json.dumps([{
            "module": "gm-agent",
            "key": "OPENAI_API_KEY",
            "source": "GOODMORNING_MANAGED",
            "binding": "goodmorning/openai-default",
            "target": "backend",
        }]))
        with mock.patch.dict(os.environ, {"GOODMORNING_MANAGED_OPENAI_API_KEY": secret_value}, clear=True):
            self.assertEqual(pp.resolve_required_secrets(request), {"OPENAI_API_KEY": secret_value})

    def test_missing_managed_secret_fails_before_provisioning(self):
        request = args(required_secrets_json=json.dumps([{
            "module": "gm-agent",
            "key": "OPENAI_API_KEY",
            "source": "GOODMORNING_MANAGED",
            "binding": "goodmorning/openai-default",
            "target": "backend",
        }]))
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(pp.ProvisioningError, "Required GoodMorning managed secret is missing"):
                pp.resolve_required_secrets(request)

    def test_secret_value_is_added_as_runtime_backend_secret(self):
        desired = pp.desired_backend_env("ledger", "postgresql+psycopg://db", {"OPENAI_API_KEY": "test-only-openai-value"})
        openai = next(item for item in desired if item["key"] == "OPENAI_API_KEY")
        self.assertTrue(openai["secret"])
        self.assertTrue(openai["is_runtime"])
        self.assertEqual(pp.env_payload(openai)["is_shown_once"], True)

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

    def test_application_omitted_project_and_environment_uses_strict_server_fallback(self):
        application = {
            "uuid": "api-1", "name": "ledger-api-dev", "type": "application",
            "server_uuid": "server-1", "destination_uuid": "bk8cw0gkswgo04o0s88sk0kg",
            "github_app_uuid": "app-1", "git_repository": "goodmorning-mx/ledger",
            "git_branch": "dev", "build_pack": "dockerfile", "base_directory": "/backend",
            "dockerfile_location": "/Dockerfile", "ports_exposes": "8000",
            "domains": "https://api.dev.ledger.goodmorning.mx", "instant_deploy": True,
            "is_auto_deploy_enabled": True, "autogenerate_domain": False,
            "health_check_enabled": True, "health_check_path": "/health",
            "health_check_port": "8000", "health_check_method": "GET",
            "health_check_scheme": "http", "health_check_return_code": 200,
        }
        client = self.client(**{
            "/applications": [application],
            "/applications/api-1": application,
            "/applications/api-1/envs": [],
        })

        result = pp.provision(args(), client_factory=lambda: client)
        backend = result["backend_application"]

        self.assertEqual(backend["status"], "exists")
        self.assertFalse(backend["matches"][0]["scope_api_verified"])
        self.assertEqual(
            backend["matches"][0]["scope_verification_source"],
            pp.APPLICATION_SCOPE_FALLBACK_SOURCE,
        )
        self.assertEqual(
            backend["matches"][0]["scope_verification_reason"],
            pp.APPLICATION_SCOPE_FALLBACK_REASON,
        )

    def test_application_aliases_verify_fqdn_and_github_source_id(self):
        application = {
            "uuid": "api-1", "name": "ledger-api-dev", "server_uuid": "server-1",
            "destination_uuid": "destination-1", "source_id": 7,
            "git_repository": "goodmorning-mx/ledger", "git_branch": "dev",
            "build_pack": "dockerfile", "base_directory": "/backend",
            "dockerfile_location": "/Dockerfile", "ports_exposes": "8000",
            "fqdn": "https://api.dev.ledger.goodmorning.mx",
            "instant_deploy": True, "is_auto_deploy_enabled": True,
            "autogenerate_domain": False, "health_check_enabled": True,
            "health_check_path": "/health", "health_check_port": "8000",
            "health_check_method": "GET", "health_check_scheme": "http",
            "health_check_return_code": 200,
        }
        differences, unverified = pp.backend_application_differences(
            application,
            args(),
            {"destination_uuid": "destination-1", "github_app_uuid": "app-uuid", "github_app_id": 7},
        )

        self.assertEqual(differences, [])
        self.assertEqual(unverified, [])

    def test_dockerfile_application_is_not_misclassified_by_dormant_compose_fields(self):
        application = {
            "build_pack": "dockerfile",
            "type": "application",
            "docker_compose_location": "/docker-compose.yaml",
            "docker_compose_raw": "services: {}",
        }

        self.assertFalse(pp.is_compose_application(application))
        self.assertTrue(pp.is_compose_application({"build_pack": "dockercompose"}))
        self.assertTrue(pp.is_compose_application({"docker_compose_location": "/compose.yaml"}))

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
        self.assertEqual(compatible["status"], "exists")
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

    def managed_db(self, **overrides):
        db = {
            "uuid": "db-1",
            "name": "ledger-postgres-dev",
            "type": "postgresql",
            "version": "16",
            "is_public": False,
            "server_uuid": "server-1",
            "postgres_user": "ledger_dev_user",
            "postgres_db": "ledger_dev",
            "description": pp.FACTORY_POSTGRES_DESCRIPTION,
            "status": "exited",
        }
        db.update(overrides)
        return db

    def missing_project_payloads(self, **overrides):
        payloads = {
            "/projects": [],
            "/servers": [{"uuid": "server-1", "name": "development", "destinations": [{"uuid":"bk8cw0gkswgo04o0s88sk0kg", "name":"docker"}]}],
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
        self.assertEqual(result["status"], "readiness_unverified")
        self.assertEqual([call[0] for call in client.calls].count("POST"), 3)
        self.assertFalse(any("deploy" in call[1] or "start" in call[1] for call in client.calls))
        self.assertNotIn("FAKE_PASSWORD_MARKER", json.dumps(result))
        self.assertNotIn("fake:fake", json.dumps(result))

    def test_post_create_omitted_scope_same_uuid_is_created_verified(self):
        db = self.managed_db()
        client = FakeWriteClient(self.missing_project_payloads(**{
            "/projects": [{"uuid": "project-1", "name": "ledger"}],
            "/projects/project-1": {"environments": [{"uuid": "env-1", "name": "dev"}]},
            "/databases": [],
            "/databases/db-1": db,
        }), {"postgresql": {"uuid": "db-1", "name": "ledger-postgres-dev"}})
        with self.apply_env():
            result = pp.provision(apply_args(), client_factory=lambda: client)
        self.assertEqual(result["postgresql"]["status"], "created")
        self.assertFalse(result["postgresql"]["scope_api_verified"])
        self.assertEqual(result["postgresql"]["scope_verification_source"], pp.POSTGRES_SCOPE_FALLBACK_SOURCE)
        self.assertEqual(result["postgresql"]["scope_verification_reason"], pp.POSTGRES_SCOPE_FALLBACK_REASON)

    def test_rerun_reuses_managed_omitted_scope_without_second_post(self):
        db = self.managed_db(status="running:healthy", internal_db_url="postgresql://u:p@h:5432/d")
        client = FakeWriteClient(self.missing_project_payloads(**{
            "/projects": [{"uuid": "project-1", "name": "ledger"}],
            "/projects/project-1": {"environments": [{"uuid": "env-1", "name": "dev"}]},
            "/databases": [db],
            "/databases/db-1": db,
        }))
        with self.apply_env():
            result = pp.provision(apply_args(), client_factory=lambda: client)
        self.assertEqual(result["postgresql"]["status"], "exists")
        self.assertEqual([c[1] for c in client.calls if c[0] == "POST"].count("/databases/postgresql"), 0)
        self.assertEqual(result["postgresql_readiness"]["status"], "already_healthy")

    def test_postgresql_omitted_scope_different_server_is_out_of_scope(self):
        db = self.managed_db(server_uuid="other")
        client = FakeWriteClient(self.missing_project_payloads(**{"/projects": [{"uuid": "project-1", "name": "ledger"}], "/projects/project-1": {"environments": [{"uuid": "env-1", "name": "dev"}]}, "/databases": [db], "/databases/db-1": db}))
        result = pp.provision(args(), client_factory=lambda: client)
        self.assertEqual(result["postgresql"]["status"], "out_of_scope")

    def test_postgresql_contradictory_project_or_environment_is_out_of_scope(self):
        for override in ({"project_uuid": "other", "environment_uuid": "env-1"}, {"project_uuid": "project-1", "environment_uuid": "other"}):
            db = self.managed_db(**override)
            client = FakeWriteClient(self.missing_project_payloads(**{"/projects": [{"uuid": "project-1", "name": "ledger"}], "/projects/project-1": {"environments": [{"uuid": "env-1", "name": "dev"}]}, "/databases": [db], "/databases/db-1": db}))
            result = pp.provision(args(), client_factory=lambda: client)
            self.assertEqual(result["postgresql"]["status"], "out_of_scope")

    def test_postgresql_duplicate_exact_name_is_blocked(self):
        db1 = self.managed_db(uuid="db-1")
        db2 = self.managed_db(uuid="db-2")
        client = FakeWriteClient(self.missing_project_payloads(**{"/projects": [{"uuid": "project-1", "name": "ledger"}], "/projects/project-1": {"environments": [{"uuid": "env-1", "name": "dev"}]}, "/databases": [db1, db2]}))
        with self.assertRaises(pp.ProvisioningError):
            pp.provision(args(), client_factory=lambda: client)

    def test_postgresql_managed_identity_mismatches_are_not_reused(self):
        cases = [
            ({"type": "mysql"}, "incompatible"),
            ({"version": "15"}, "incompatible"),
            ({"is_public": True}, "incompatible"),
            ({"postgres_user": "wrong"}, "incompatible"),
            ({"description": "wrong"}, "incompatible"),
            ({"postgres_db": None}, "compatibility_unverified"),
        ]
        for override, expected in cases:
            db = self.managed_db(**override)
            client = FakeWriteClient(self.missing_project_payloads(**{"/projects": [{"uuid": "project-1", "name": "ledger"}], "/projects/project-1": {"environments": [{"uuid": "env-1", "name": "dev"}]}, "/databases": [db], "/databases/db-1": db}))
            result = pp.provision(args(), client_factory=lambda: client)
            self.assertEqual(result["postgresql"]["status"], expected)

    def test_postgresql_post_create_contradictory_uuid_is_created_unverified(self):
        client = FakeWriteClient(self.missing_project_payloads(**{
            "/projects": [{"uuid": "project-1", "name": "ledger"}],
            "/projects/project-1": {"environments": [{"uuid": "env-1", "name": "dev"}]},
            "/databases": [],
            "/databases/db-1": self.managed_db(uuid="other-db"),
        }), {"postgresql": {"uuid": "db-1", "name": "ledger-postgres-dev"}})
        with self.apply_env():
            result = pp.provision(apply_args(), client_factory=lambda: client)
        self.assertEqual(result["postgresql"]["status"], "created_unverified")

    def test_postgresql_write_outcome_unknown_does_not_retry_or_leak(self):
        secret = "postgresql://user:secret@host/db"
        err = pp.ProvisioningError(f"timeout {secret}", method="POST", path="/databases/postgresql", write_outcome_unknown=True, request_attempted=True)
        client = FakeWriteClient(self.missing_project_payloads(**{"/projects": [{"uuid": "project-1", "name": "ledger"}], "/projects/project-1": {"environments": [{"uuid": "env-1", "name": "dev"}]}}), {"postgresql": err})
        with self.apply_env():
            result = pp.provision(apply_args(), client_factory=lambda: client)
        self.assertEqual(result["postgresql"]["status"], "created_unverified")
        self.assertEqual([c[1] for c in client.calls if c[0] == "POST"].count("/databases/postgresql"), 1)
        self.assertNotIn(secret, json.dumps(result))

    def test_internal_db_url_recovery_from_verified_components_creates_backend(self):
        db = self.managed_db(status="running:healthy", postgres_password="p@ss% word", internal_db_url=None)
        api_detail = {"uuid":"api-1","name":"ledger-api-dev","type":"application","project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","destination_uuid":"bk8cw0gkswgo04o0s88sk0kg","git_repository":"goodmorning-mx/ledger","git_branch":"dev","build_pack":"dockerfile","base_directory":"/backend","dockerfile_location":"/Dockerfile","ports_exposes":"8000","domains":"https://api.dev.ledger.goodmorning.mx","github_app_uuid":"app-1","health_check_enabled":True,"health_check_path":"/health","health_check_port":"8000","health_check_method":"GET","health_check_scheme":"http","health_check_return_code":200}
        client = FakeWriteClient(self.missing_project_payloads(**{"/projects":[{"uuid":"project-1","name":"ledger"}],"/projects/project-1":{"environments":[{"uuid":"env-1","name":"dev"}]},"/databases":[db],"/databases/db-1":db,"/applications":[],"/applications/api-1":api_detail,"/applications/api-1/envs":[]}))
        env={"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES":"true","COOLIFY_ALLOW_BACKEND_ENV_WRITES":"true"}
        with mock.patch.dict(os.environ, env, clear=True):
            result=pp.provision(apply_args(confirm_backend_application="ledger-api-dev", confirm_backend_environment="ledger-api-dev"), client_factory=lambda: client)
        self.assertEqual(result["postgresql_readiness"]["status"], "already_healthy")
        self.assertTrue(result["postgresql_readiness"]["database_credentials_available"])
        self.assertEqual(result["postgresql_readiness"]["internal_db_url_source"], "verified_components")
        self.assertEqual(result["backend_application"]["status"], "created")
        db_env = next(call[2] for call in client.calls if call[0] == "POST" and call[1] == "/applications/api-1/envs" and call[2]["key"] == "DATABASE_URL")
        self.assertTrue(db_env["value"].startswith("postgresql+psycopg://ledger_dev_user:p%40ss%25%20word@db-1:5432/ledger_dev"))
        self.assertTrue(db_env["is_runtime"]); self.assertFalse(db_env["is_buildtime"]); self.assertTrue(db_env["is_shown_once"])
        dumped=json.dumps(result)
        self.assertNotIn("p@ss", dumped); self.assertNotIn("postgresql+psycopg://", dumped); self.assertNotIn("internal_db_url", dumped.replace("internal_db_url_available", "").replace("internal_db_url_source", ""))

    def test_missing_internal_url_component_fails_closed_without_applications(self):
        db = self.managed_db(status="running:healthy", postgres_password="")
        client = FakeWriteClient(self.missing_project_payloads(**{"/projects":[{"uuid":"project-1","name":"ledger"}],"/projects/project-1":{"environments":[{"uuid":"env-1","name":"dev"}]},"/databases":[db],"/databases/db-1":db,"/applications":[]}))
        env={"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES":"true","COOLIFY_ALLOW_FRONTEND_APPLICATION_WRITES":"true"}
        with mock.patch.dict(os.environ, env, clear=True):
            result=pp.provision(apply_args(confirm_backend_application="ledger-api-dev", confirm_frontend_application="ledger-frontend-dev"), client_factory=lambda: client)
        reasons=[b.get("reason") for b in result["blockers"]]
        self.assertIn("internal_db_url_unavailable", reasons)
        self.assertIn("database_url_driver_not_ready", reasons)
        self.assertFalse(any(c[0]=="POST" and c[1]=="/applications/private-github-app" for c in client.calls))
        self.assertEqual(result["frontend_application"]["status"], "blocked")

    def test_internal_url_api_field_and_redaction_recursive(self):
        raw="postgres://user:p%40ss@db-1:5432/ledger_dev"
        self.assertEqual(pp.prepare_psycopg_database_url(raw), "postgresql+psycopg://user:p%40ss@db-1:5432/ledger_dev")
        flags=pp.verify_internal_db_url_flags({"uuid":"db-1","internal_db_url":raw}, "db-1")
        self.assertTrue(flags["internal_db_url_available"]); self.assertEqual(flags["internal_db_url_source"], "api_field")
        red=pp.redact({"outer":[{"DATABASE_URL":"postgresql+psycopg://user:secret@db/db","nested":{"internal_db_url":raw,"postgres_password":"secret"}}]})
        self.assertNotIn("secret", json.dumps(red)); self.assertNotIn(raw, json.dumps(red))

    def test_verified_component_derivation_rejects_uuid_contradiction(self):
        db={"uuid":"other","postgres_user":"u","postgres_password":"p","postgres_db":"d"}
        url, source = pp.derived_internal_postgres_url(db, "db-1")
        self.assertIsNone(url); self.assertIsNone(source)

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
        self.assertEqual(result["status"], "blocked")
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
        self.assertEqual(result["status"], "blocked")
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
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["postgresql"]["status"], "created_unverified")

    def test_postgresql_detail_failure_is_created_unverified(self):
        client = FakeWriteClient(self.missing_project_payloads(**{
            "/projects": [{"uuid": "project-1", "name": "ledger"}],
            "/projects/project-1": {"environments": [{"uuid": "env-1", "name": "dev"}]},
            "/databases/db-1": pp.ProvisioningError("detail failed", error_type="coolify_api"),
        }))
        with self.apply_env():
            result = pp.provision(apply_args(), client_factory=lambda: client)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["postgresql"]["status"], "created_unverified")

    def test_postgresql_created_but_incompatible_requires_recreate(self):
        client = FakeWriteClient(self.missing_project_payloads(**{
            "/projects": [{"uuid": "project-1", "name": "ledger"}],
            "/projects/project-1": {"environments": [{"uuid": "env-1", "name": "dev"}]},
            "/databases/db-1": {"uuid": "db-1", "name": "ledger-postgres-dev", "database_type": "mysql", "version": "16", "is_public": False, "project_uuid": "project-1", "environment_uuid": "env-1", "server_uuid": "server-1"},
        }))
        with self.apply_env():
            result = pp.provision(apply_args(), client_factory=lambda: client)
        self.assertEqual(result["status"], "blocked")

    def test_second_execution_reuses_verified_postgresql_without_duplicate(self):
        client = FakeWriteClient(self.missing_project_payloads(**{
            "/projects": [{"uuid": "project-1", "name": "ledger"}],
            "/projects/project-1": {"environments": [{"uuid": "env-1", "name": "dev"}]},
            "/databases": [{"uuid": "db-1", "name": "ledger-postgres-dev"}],
        }))
        with self.apply_env():
            result = pp.provision(apply_args(), client_factory=lambda: client)
        self.assertEqual(result["status"], "readiness_unverified")
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
            "/servers": [{"uuid": "server-1", "name": "development", "destinations": [{"uuid":"bk8cw0gkswgo04o0s88sk0kg", "name":"docker"}]}],
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



class Stage3WriteOutcomeTests(unittest.TestCase):
    def payloads(self, **overrides):
        base = Stage3ProvisioningTests().missing_project_payloads(**{
            "/projects": [],
            "/projects/project-1": {"uuid": "project-1", "name": "ledger"},
            "/projects/project-1/environments": [],
            "/databases": [],
        })
        base.update(overrides)
        return base

    def apply_env(self):
        return mock.patch.dict(os.environ, {"COOLIFY_ALLOW_RESOURCE_WRITES": "true"})

    def test_project_post_http_500_is_outcome_unknown_partial_no_second_post(self):
        error = pp.ProvisioningError(
            "server failed after receiving request",
            error_type="coolify_api",
            http_status=500,
            method="POST",
            path="/projects",
            write_outcome_unknown=True,
        )
        client = FakeWriteClient(self.payloads(), {"project": error})
        with self.apply_env():
            result = pp.provision(apply_args(), client_factory=lambda: client)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any(action["write_outcome_unknown"] for action in result["actions"]))
        self.assertEqual([call for call in client.calls if call[0] == "POST" and call[1] == "/projects"], [("POST", "/projects", {"name": "ledger"})])

    def test_environment_post_timeout_is_outcome_unknown_partial_no_second_post(self):
        error = pp.ProvisioningError(
            "timeout after request attempt",
            error_type="network",
            method="POST",
            path="/projects/project-1/environments",
            write_outcome_unknown=True,
        )
        client = FakeWriteClient(self.payloads(**{"/projects": [{"uuid": "project-1", "name": "ledger"}]}), {"environment": error})
        with self.apply_env():
            result = pp.provision(apply_args(), client_factory=lambda: client)
        self.assertEqual(result["status"], "blocked")
        self.assertTrue(any(action["resource"] == "environment" and action["write_outcome_unknown"] for action in result["actions"]))
        self.assertEqual([call[1] for call in client.calls if call[0] == "POST"].count("/projects/project-1/environments"), 1)

    def test_postgresql_successful_response_with_invalid_json_is_outcome_unknown(self):
        error = pp.ProvisioningError(
            "Malformed JSON response for POST /databases/postgresql",
            error_type="coolify_api",
            method="POST",
            path="/databases/postgresql",
            write_outcome_unknown=True,
        )
        client = FakeWriteClient(self.payloads(**{
            "/projects": [{"uuid": "project-1", "name": "ledger"}],
            "/projects/project-1": {"uuid": "project-1", "name": "ledger", "environments": [{"uuid": "env-1", "name": "dev"}]},
        }), {"postgresql": error})
        with self.apply_env():
            result = pp.provision(apply_args(), client_factory=lambda: client)
        self.assertEqual(result["status"], "blocked")
        self.assertNotIn(result["postgresql"].get("status"), {"exists", "created"})
        self.assertEqual([call[1] for call in client.calls if call[0] == "POST"].count("/databases/postgresql"), 1)

    def test_project_409_rediscovery_no_message_parsing_no_second_post(self):
        conflict = pp.ProvisioningError("text says not a conflict number 500", error_type="coolify_api", http_status=409, method="POST", path="/projects")
        client = FakeWriteClient(self.payloads(**{"/projects": [[], [{"uuid": "project-1", "name": "ledger"}]]}), {"project": conflict})
        original_get = client.get
        counter = {"/projects": 0}
        def get(path):
            if path == "/projects":
                index = counter[path]
                counter[path] += 1
                return client.payloads[path][min(index, 1)]
            return original_get(path)
        client.get = get
        with self.apply_env():
            result = pp.provision(apply_args(), client_factory=lambda: client)
        self.assertTrue(result["project"].get("recovered_after_conflict"))
        self.assertEqual([call[1] for call in client.calls if call[0] == "POST"].count("/projects"), 1)

    def test_project_422_without_rediscovery_blocks_without_second_post(self):
        conflict = pp.ProvisioningError("unprocessable", error_type="coolify_api", http_status=422, method="POST", path="/projects")
        client = FakeWriteClient(self.payloads(), {"project": conflict})
        with self.apply_env():
            with self.assertRaises(pp.ProvisioningError):
                pp.provision(apply_args(), client_factory=lambda: client)
        self.assertEqual([call[1] for call in client.calls if call[0] == "POST"].count("/projects"), 1)

    def test_get_error_does_not_mark_write_unknown(self):
        client = FakeWriteClient(self.payloads(**{"/servers": pp.ProvisioningError("GET failed", error_type="network", method="GET", path="/servers")}))
        with self.assertRaises(pp.ProvisioningError) as raised:
            pp.provision(args(), client_factory=lambda: client)
        self.assertFalse(raised.exception.write_performed)
        self.assertFalse(raised.exception.write_outcome_unknown)
        self.assertFalse(any(call[0] == "POST" for call in client.calls))

    def test_safety_plan_reflects_dry_run_and_apply(self):
        dry_client = FakeWriteClient(self.payloads())
        dry = pp.provision(args(), client_factory=lambda: dry_client)
        self.assertFalse(dry["safety"]["writes_enabled"])
        self.assertEqual(dry["safety"]["allowed_post_operations"], [])
        self.assertNotIn("pending_contract_decisions", json.dumps(dry))

        apply_client = FakeWriteClient(self.payloads())
        with self.apply_env():
            applied = pp.provision(apply_args(), client_factory=lambda: apply_client)
        self.assertTrue(applied["safety"]["writes_enabled"])
        self.assertEqual(set(applied["safety"]["allowed_post_operations"]), {"project_create", "environment_create", "postgresql_create"})

    def test_documentation_declares_three_resource_canonical_architecture_and_legacy_separation(self):
        from pathlib import Path
        canonical = Path("docs/coolify-development-provisioning.md").read_text()
        legacy = Path("docs/legacy-coolify-compose-provisioning.md").read_text()
        self.assertIn("independent Coolify resources", canonical)
        self.assertIn("Backend Dockerfile Application", canonical)
        self.assertIn("Frontend Dockerfile Application", canonical)
        self.assertIn("Legacy Compose provisioning", canonical)
        self.assertIn("deprecated for new products", canonical)
        self.assertIn("Docker Compose", legacy)

class Stage9PostgresqlDiscoveryReadinessFixTests(unittest.TestCase):
    def client(self, db, **overrides):
        payloads = {
            "/projects": [{"uuid": "project-1", "name": "ledger"}],
            "/projects/project-1": {"uuid": "project-1", "name": "ledger", "environments": [{"uuid": "env-1", "name": "dev"}]},
            "/servers": [{"uuid": "server-1", "name": "development", "destinations": [{"uuid":"bk8cw0gkswgo04o0s88sk0kg", "name":"docker"}]}],
            "/github-apps": {"github_apps": [{"uuid": "app-1", "name": "goodmorning-mx-coolify"}]},
            "/applications": [],
            "/databases": [{k:v for k,v in db.items() if k != "detail_only"}],
            f"/databases/{db['uuid']}": dict(db),
        }
        payloads.update(overrides)
        return FakeClient(payloads)

    def factory_db(self, **overrides):
        db = {"uuid":"db-1","name":"ledger-postgres-dev","type":"postgresql","server_uuid":"server-1","version":"16","is_public":False,"status":"exited:unhealthy","postgres_user":"ledger_dev_user","postgres_db":"ledger_dev","description":pp.FACTORY_POSTGRES_DESCRIPTION}
        db.update(overrides)
        return db

    def pg_resource(self, result):
        return next(resource for resource in result["resources"] if resource["name"] == "ledger-postgres-dev")

    def discovery_pg(self, result):
        return next(item for item in result["discovery"]["existing_resources"]["classification"] if item["name"] == "ledger-postgres-dev")

    def test_managed_postgresql_omitted_scope_is_exists_everywhere(self):
        client = self.client(self.factory_db(status="running:healthy"))
        result = pp.provision(args(), client_factory=lambda: client)
        self.assertEqual(result["postgresql"]["status"], "exists")
        self.assertEqual(self.pg_resource(result)["status"], "exists")
        discovery = self.discovery_pg(result)
        self.assertEqual(discovery["status"], "exists")
        self.assertEqual(result["postgresql"]["uuid"], discovery["uuid"])
        self.assertFalse(result["postgresql"]["scope_api_verified"])
        self.assertFalse(discovery["scope_api_verified"])
        self.assertNotIn("scope_unverified", json.dumps([result["postgresql"], self.pg_resource(result), discovery]))

    def test_omitted_scope_metadata_uses_unambiguous_fields(self):
        result = pp.provision(args(), client_factory=lambda: self.client(self.factory_db(status="running:healthy")))
        postgresql = result["postgresql"]
        self.assertFalse(postgresql["scope_api_verified"])
        self.assertFalse(postgresql["project_scope_api_present"])
        self.assertFalse(postgresql["environment_scope_api_present"])
        self.assertTrue(postgresql["server_scope_api_matched"])
        self.assertNotIn('"status": "unverified"', json.dumps(postgresql))

    def test_contradictory_project_or_environment_is_out_of_scope_everywhere(self):
        for field_name in ("project_uuid", "environment_uuid"):
            with self.subTest(field_name=field_name):
                db = self.factory_db(**{field_name: "other"})
                result = pp.provision(args(), client_factory=lambda: self.client(db))
                self.assertEqual(result["postgresql"]["status"], "out_of_scope")
                self.assertEqual(self.pg_resource(result)["status"], "out_of_scope")
                self.assertEqual(self.discovery_pg(result)["status"], "out_of_scope")

    def test_exited_unhealthy_verified_factory_dry_run_would_start_safe_zero_post(self):
        client = self.client(self.factory_db())
        result = pp.provision(args(), client_factory=lambda: client)
        self.assertEqual(result["postgresql_readiness"]["status"], "would_start")
        self.assertEqual(result["postgresql_readiness"]["reason"], "verified_factory_postgresql_requires_start")
        self.assertTrue(result["plan_safe"])
        self.assertFalse(any(call[0] == "POST" for call in client.calls))

    def test_exited_unhealthy_unverified_never_would_start(self):
        result = pp.provision(args(), client_factory=lambda: self.client(self.factory_db(description="not-factory")))
        self.assertIn(result["postgresql_readiness"]["status"], {"blocked", "unhealthy"})
        self.assertNotEqual(result["postgresql_readiness"]["status"], "would_start")

    def test_running_unhealthy_and_failed_remain_terminal(self):
        for raw_status in ("running:unhealthy", "failed"):
            with self.subTest(raw_status=raw_status):
                result = pp.provision(args(), client_factory=lambda: self.client(self.factory_db(status=raw_status)))
                self.assertEqual(result["postgresql_readiness"]["status"], "unhealthy")

    def test_apply_exited_unhealthy_posts_once_then_polls(self):
        db = self.factory_db()
        client = self.client(db, **{"/databases/db-1": [("GET", db), ("GET", {**db, "status":"exited:unhealthy"}), ("GET", {**db, "status":"running:healthy", "internal_db_url":"postgresql://u:p@h:5432/d"})]})
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_DATABASE_START":"true"},clear=True):
            result = pp.provision(args(dry_run=False,apply=True,confirm_product="ledger",confirm_database_start="ledger-postgres-dev"), client_factory=lambda: client)
        self.assertEqual([call for call in client.calls if call[0] == "POST"], [("POST", "/databases/db-1/start")])
        self.assertGreaterEqual(len([call for call in client.calls if call == ("GET", "/databases/db-1")]), 2)
        self.assertEqual(result["postgresql_readiness"]["status"], "healthy")

    def test_start_accepted_but_still_exited_unhealthy_no_second_start(self):
        db = self.factory_db()
        client = self.client(db, **{"/databases/db-1": [("GET", db), ("GET", {**db, "status":"exited:unhealthy"}), ("GET", {**db, "status":"exited:unhealthy"})]})
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_DATABASE_START":"true"},clear=True):
            result = pp.provision(args(dry_run=False,apply=True,confirm_product="ledger",confirm_database_start="ledger-postgres-dev"), client_factory=lambda: client)
        self.assertNotIn(result["postgresql_readiness"]["status"], {"healthy", "already_healthy"})
        self.assertEqual(len([call for call in client.calls if call[0] == "POST"]), 1)

    def test_start_5xx_or_timeout_unknown_zero_retry(self):
        for exc in (pp.ProvisioningError("boom", http_status=500, method="POST", path="/databases/db-1/start", write_outcome_unknown=True), pp.ProvisioningError("timeout", error_type="network", method="POST", path="/databases/db-1/start", write_outcome_unknown=True)):
            with self.subTest(exc=exc.message):
                client = self.client(self.factory_db(), **{"POST /databases/db-1/start": exc})
                with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_DATABASE_START":"true"},clear=True):
                    result = pp.provision(args(dry_run=False,apply=True,confirm_product="ledger",confirm_database_start="ledger-postgres-dev"), client_factory=lambda: client)
                self.assertTrue(result["postgresql_readiness"]["write_outcome_unknown"])
                self.assertEqual(len([call for call in client.calls if call[0] == "POST"]), 1)

    def test_real_stage9_provision_plan_fixture_is_safe_and_redacted(self):
        client = self.client(self.factory_db())
        result = pp.provision(args(), client_factory=lambda: client)
        self.assertTrue(result["plan_safe"])
        self.assertEqual(result["exit_code"], 0)
        self.assertEqual(result["backend_application"]["status"], "would_create")
        self.assertEqual(result["frontend_application"]["status"], "planned_after_backend")
        rendered = json.dumps(result)
        self.assertNotIn("DATABASE_URL", rendered)
        self.assertNotIn("postgresql://", rendered)


if __name__ == "__main__":
    unittest.main()

class Stage4PostgresqlReadinessTests(unittest.TestCase):
    def client(self, status="exited", **overrides):
        base = ProductDevelopmentProvisionerTests().client()
        db = {"uuid":"db-1","name":"ledger-postgres-dev","type":"postgresql","project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","version":"16","is_public":False,"status":status,"internal_db_url":"postgresql://user:p%40ss@host:5432/db?sslmode=require"}
        base.payloads["/databases"] = [dict(db)]
        base.payloads["/databases/db-1"] = dict(db)
        base.payloads.update(overrides)
        return base

    def test_dry_run_stopped_would_start_zero_post(self):
        client=self.client("exited")
        result=pp.provision(args(), client_factory=lambda: client)
        self.assertEqual(result["postgresql_readiness"]["status"], "would_start")
        self.assertFalse(any(c[0]=="POST" for c in client.calls))

    def test_dry_run_healthy_already_healthy_zero_post(self):
        client=self.client("running:healthy")
        result=pp.provision(args(), client_factory=lambda: client)
        self.assertEqual(result["postgresql_readiness"]["status"], "already_healthy")
        self.assertTrue(result["postgresql_readiness"]["database_url_driver_ready"])
        self.assertFalse(any(c[0]=="POST" for c in client.calls))

    def test_apply_missing_database_start_gate_blocks_before_post(self):
        client=self.client("exited")
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true"},clear=True):
            result=pp.provision(args(dry_run=False,apply=True,confirm_product="ledger"), client_factory=lambda: client)
        self.assertEqual(result["postgresql_readiness"]["status"], "blocked")
        self.assertFalse(any(c[0]=="POST" for c in client.calls))

    def test_apply_wrong_database_start_confirm_blocks_before_post(self):
        client=self.client("exited")
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_DATABASE_START":"true"},clear=True):
            result=pp.provision(args(dry_run=False,apply=True,confirm_product="ledger",confirm_database_start="wrong"), client_factory=lambda: client)
        self.assertEqual(result["postgresql_readiness"]["status"], "blocked")
        self.assertFalse(any(c[0]=="POST" for c in client.calls))

    def test_start_with_all_gates_posts_once_then_get_polls(self):
        client=self.client("exited", **{"/databases/db-1":[("GET",{"uuid":"db-1","name":"ledger-postgres-dev","type":"postgresql","project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","version":"16","is_public":False,"status":"exited"}),("GET",{"uuid":"db-1","status":"exited"}),("GET",{"uuid":"db-1","status":"running:healthy","internal_db_url":"postgresql://u:p@h:5432/d"})]})
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_DATABASE_START":"true"},clear=True):
            result=pp.provision(args(dry_run=False,apply=True,confirm_product="ledger",confirm_database_start="ledger-postgres-dev"), client_factory=lambda: client)
        self.assertEqual(result["postgresql_readiness"]["status"], "healthy")
        self.assertEqual([c for c in client.calls if c[0]=="POST"],[('POST','/databases/db-1/start')])

    def test_start_http_500_is_partial_unknown_once(self):
        exc=pp.ProvisioningError("boom", http_status=500, method="POST", path="/databases/db-1/start", write_outcome_unknown=True)
        client=self.client("exited", **{"POST /databases/db-1/start": exc})
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_DATABASE_START":"true"},clear=True):
            result=pp.provision(args(dry_run=False,apply=True,confirm_product="ledger",confirm_database_start="ledger-postgres-dev"), client_factory=lambda: client)
        self.assertEqual(result["postgresql_readiness"]["status"], "partial")
        self.assertTrue(result["postgresql_readiness"]["write_outcome_unknown"])
        self.assertEqual(len([c for c in client.calls if c[0]=="POST"]),1)

    def test_status_normalization_exact(self):
        self.assertEqual(pp.classify_database_readiness_status("queued"), "starting")
        self.assertEqual(pp.classify_database_readiness_status("running:healthy"), "healthy")
        self.assertEqual(pp.classify_database_readiness_status("nothealthy"), "readiness_unverified")
        self.assertEqual(pp.classify_database_readiness_status("unhealthy-running"), "readiness_unverified")

    def test_psycopg_url_conversion_and_rejections(self):
        self.assertEqual(pp.prepare_psycopg_database_url("postgresql://user:p%40ss@host:5432/db"), "postgresql+psycopg://user:p%40ss@host:5432/db")
        self.assertEqual(pp.prepare_psycopg_database_url("postgres://user:pass@host:5432/db?sslmode=require"), "postgresql+psycopg://user:pass@host:5432/db?sslmode=require")
        for bad in ["mysql://u:p@h/db", "sqlite://x", "http://x", "postgresql://"]:
            with self.assertRaises(pp.ProvisioningError) as cm:
                pp.prepare_psycopg_database_url(bad)
            self.assertNotIn(bad, cm.exception.message)

    def test_internal_db_url_redacted_and_output_booleans_only(self):
        secret="postgresql://user:p%40ss@host:5432/db"
        red=pp.redact({"internal_db_url":secret,"nested":[{"message":f"DATABASE_URL={secret}"}],"body":{"password":"x"}})
        self.assertEqual(red["internal_db_url"], "[REDACTED]")
        self.assertNotIn(secret, json.dumps(red))
        client=self.client("running:healthy")
        result=pp.provision(args(), client_factory=lambda: client)
        self.assertNotIn(secret, json.dumps(result["postgresql_readiness"]).lower())

    def test_applications_remain_planned_and_client_blocks_forbidden_lifecycle(self):
        client=self.client("running:healthy")
        result=pp.provision(args(), client_factory=lambda: client)
        apps=[r for r in result["resources"] if r["kind"]=="application"]
        self.assertIn(apps[0]["status"], {"would_create", "compatibility_unverified", "exists"})
        self.assertEqual(apps[1]["status"], "planned_after_backend")
        real=pp.CoolifyClient("https://coolify.example","token")
        for method,path in [("POST","/applications"),("POST","/applications/a/start"),("POST","/databases/db-1/restart"),("POST","/databases/db-1/stop"),("PATCH","/databases/db-1")]:
            with self.assertRaises(pp.ProvisioningError):
                real.request(method,path,post_kind="postgresql_start")

class Stage4ReviewCorrectionTests(unittest.TestCase):
    def client(self, status="exited", **overrides):
        return Stage4PostgresqlReadinessTests().client(status, **overrides)

    def apply_env(self, database_start=True):
        env = {"COOLIFY_ALLOW_RESOURCE_WRITES": "true"}
        if database_start:
            env["COOLIFY_ALLOW_DATABASE_START"] = "true"
        return mock.patch.dict(os.environ, env, clear=True)

    def apply_args(self, **overrides):
        defaults = {"dry_run": False, "apply": True, "confirm_product": "ledger", "confirm_database_start": "ledger-postgres-dev", "postgres_readiness_interval": 0.01, "postgres_readiness_timeout": 0.02}
        defaults.update(overrides)
        return args(**defaults)

    def test_incomplete_database_start_gates_not_in_safety_metadata(self):
        client = self.client("exited")
        with mock.patch.dict(os.environ, {"COOLIFY_ALLOW_RESOURCE_WRITES": "true", "COOLIFY_ALLOW_DATABASE_START": "true"}, clear=True):
            result = pp.provision(self.apply_args(confirm_database_start="wrong"), client_factory=lambda: client)
        self.assertFalse(result["safety"]["database_start_enabled"])
        self.assertNotIn("postgresql_start", result["safety"]["allowed_post_operations"])
        self.assertFalse(any(call[0] == "POST" for call in client.calls))

    def test_complete_database_start_gates_in_safety_metadata(self):
        client = self.client("running:healthy")
        with self.apply_env():
            result = pp.provision(self.apply_args(), client_factory=lambda: client)
        self.assertTrue(result["safety"]["database_start_enabled"])
        self.assertIn("postgresql_start", result["safety"]["allowed_post_operations"])

    def test_blocked_and_unverified_apply_exit_nonzero(self):
        for readiness_status in ("blocked", "readiness_unverified", "unhealthy", "timeout", "partial"):
            with self.subTest(readiness_status=readiness_status), mock.patch.object(pp, "provision", return_value={"status": readiness_status}):
                self.assertEqual(pp.main(["--product","ledger","--repository","goodmorning-mx/ledger","--branch","dev","--coolify-project","ledger","--environment","dev","--server","development","--github-app","goodmorning-mx-coolify","--apply","--confirm-product","ledger"]), 2)
        with mock.patch.object(pp, "provision", return_value={"status": "dry_run", "postgresql_readiness": {"status": "would_start"}}):
            self.assertEqual(pp.main(["--product","ledger","--repository","goodmorning-mx/ledger","--branch","dev","--coolify-project","ledger","--environment","dev","--server","development","--github-app","goodmorning-mx-coolify","--dry-run"]), 0)

    def test_invalid_polling_args_fail_before_requests(self):
        for field, value in (("postgres_readiness_interval", 0), ("postgres_readiness_timeout", -1), ("postgres_readiness_interval", float("nan")), ("postgres_readiness_timeout", float("inf"))):
            client = self.client("exited")
            with self.subTest(field=field, value=value), self.assertRaises(pp.ProvisioningError):
                pp.provision(args(**{field: value}), client_factory=lambda: client)
            self.assertEqual(client.calls, [])

    def test_incompatible_out_of_scope_unverified_and_created_unverified_do_not_start(self):
        cases = [
            ({"version": "15"}, "blocked"),
            ({"project_uuid": "other"}, "blocked"),
            ({"version": None}, "blocked"),
        ]
        for overrides, expected_status in cases:
            db = {"uuid":"db-1","name":"ledger-postgres-dev","type":"postgresql","project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","version":"16","is_public":False,"status":"exited"}
            db.update(overrides)
            client = self.client("exited", **{"/databases": [db], "/databases/db-1": db})
            with self.apply_env():
                result = pp.provision(self.apply_args(), client_factory=lambda: client)
            self.assertEqual(result["status"], expected_status)
            self.assertFalse(any(call[0] == "POST" and call[1].endswith("/start") for call in client.calls))

    def test_starting_polls_without_start_and_transitions(self):
        client = self.client("starting", **{"/databases/db-1": [("GET", {"uuid":"db-1","name":"ledger-postgres-dev","type":"postgresql","project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","version":"16","is_public":False,"status":"starting"}), ("GET", {"uuid":"db-1","status":"starting"}), ("GET", {"uuid":"db-1","status":"running:healthy","internal_db_url":"postgres://u:p@h/db"})]})
        with self.apply_env():
            result = pp.provision(self.apply_args(), client_factory=lambda: client)
        self.assertEqual(result["postgresql_readiness"]["status"], "healthy")
        self.assertFalse(any(call[0] == "POST" for call in client.calls))

    def test_starting_to_unhealthy_no_restart_or_second_post(self):
        client = self.client("starting", **{"/databases/db-1": [("GET", {"uuid":"db-1","name":"ledger-postgres-dev","type":"postgresql","project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","version":"16","is_public":False,"status":"starting"}), ("GET", {"uuid":"db-1","status":"running:unhealthy"})]})
        with self.apply_env():
            result = pp.provision(self.apply_args(), client_factory=lambda: client)
        self.assertEqual(result["postgresql_readiness"]["status"], "unhealthy")
        self.assertFalse(any(call[0] == "POST" for call in client.calls))

    def test_start_accepted_verified_only_after_healthy(self):
        client = self.client("exited", **{"/databases/db-1": [("GET", {"uuid":"db-1","name":"ledger-postgres-dev","type":"postgresql","project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","version":"16","is_public":False,"status":"exited"}), ("GET", {"uuid":"db-1","status":"exited"}), ("GET", {"uuid":"db-1","status":"exited"}), ("GET", {"uuid":"db-1","status":"starting"}), ("GET", {"uuid":"db-1","status":"running:healthy","internal_db_url":"postgres://u:p@h/db"})]})
        with self.apply_env():
            result = pp.provision(self.apply_args(), client_factory=lambda: client)
        self.assertEqual(result["postgresql_readiness"]["status"], "healthy")
        start_actions = [a for a in result["actions"] if a["action"] == "start"]
        readiness_actions = [a for a in result["actions"] if a["action"] == "readiness"]
        self.assertEqual(len([c for c in client.calls if c[0] == "POST"]), 1)
        self.assertFalse(start_actions[0]["verified"])
        self.assertTrue(readiness_actions[0]["verified"])

    def test_start_accepted_unhealthy_not_verified(self):
        client = self.client("exited", **{"/databases/db-1": [("GET", {"uuid":"db-1","name":"ledger-postgres-dev","type":"postgresql","project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","version":"16","is_public":False,"status":"exited"}), ("GET", {"uuid":"db-1","status":"exited"}), ("GET", {"uuid":"db-1","status":"running:unhealthy"})]})
        with self.apply_env():
            result = pp.provision(self.apply_args(), client_factory=lambda: client)
        self.assertEqual(result["postgresql_readiness"]["status"], "unhealthy")
        self.assertFalse(any(a["action"] == "start" and a["verified"] for a in result["actions"]))
        self.assertEqual(len([c for c in client.calls if c[0] == "POST"]), 1)

    def test_polling_timeout_and_get_error(self):
        client = self.client("starting", **{"/databases/db-1": [("GET", {"uuid":"db-1","name":"ledger-postgres-dev","type":"postgresql","project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","version":"16","is_public":False,"status":"starting"}), ("GET", {"uuid":"db-1","status":"starting"}), ("GET", {"uuid":"db-1","status":"starting"}), ("GET", {"uuid":"db-1","status":"starting"}), ("GET", {"uuid":"db-1","status":"starting"})]})
        with self.apply_env():
            result = pp.provision(self.apply_args(postgres_readiness_timeout=0.02, postgres_readiness_interval=0.01), client_factory=lambda: client)
        self.assertEqual(result["postgresql_readiness"]["status"], "timeout")
        self.assertLessEqual(result["postgresql_readiness"]["poll_attempts"], 3)
        err = pp.ProvisioningError("GET failed", error_type="network", method="GET", path="/databases/db-1")
        client = self.client("starting", **{"/databases/db-1": [("GET", {"uuid":"db-1","name":"ledger-postgres-dev","type":"postgresql","project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","version":"16","is_public":False,"status":"starting"}), ("GET", err)]})
        with self.apply_env():
            result = pp.provision(self.apply_args(), client_factory=lambda: client)
        self.assertEqual(result["postgresql_readiness"]["status"], "readiness_unverified")
        self.assertFalse(result["postgresql_readiness"]["write_outcome_unknown"])

    def test_start_urlerror_timeout_and_invalid_json_are_partial_once(self):
        errors = [
            pp.ProvisioningError("timed out", error_type="network", method="POST", path="/databases/db-1/start", write_outcome_unknown=True),
            pp.ProvisioningError("url", error_type="network", method="POST", path="/databases/db-1/start", write_outcome_unknown=True),
            pp.ProvisioningError("bad json", error_type="coolify_api", method="POST", path="/databases/db-1/start", write_outcome_unknown=True),
        ]
        for exc in errors:
            client = self.client("exited", **{"POST /databases/db-1/start": exc})
            with self.apply_env():
                result = pp.provision(self.apply_args(), client_factory=lambda: client)
            self.assertEqual(result["postgresql_readiness"]["status"], "partial")
            self.assertTrue(result["postgresql_readiness"]["write_outcome_unknown"])
            self.assertEqual(len([c for c in client.calls if c[0] == "POST"]), 1)

    def test_start_conflict_rediscovery_and_422_blocked_no_text_parsing(self):
        conflict = pp.ProvisioningError("ignore this message", http_status=409, method="POST", path="/databases/db-1/start")
        client = self.client("exited", **{"POST /databases/db-1/start": conflict, "/databases/db-1": [("GET", {"uuid":"db-1","name":"ledger-postgres-dev","type":"postgresql","project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","version":"16","is_public":False,"status":"exited"}), ("GET", {"uuid":"db-1","status":"exited"}), ("GET", {"uuid":"db-1","status":"starting"}), ("GET", {"uuid":"db-1","status":"running:healthy","internal_db_url":"postgres://u:p@h/db"})]})
        with self.apply_env():
            result = pp.provision(self.apply_args(), client_factory=lambda: client)
        self.assertEqual(result["postgresql_readiness"]["status"], "healthy")
        self.assertEqual(len([c for c in client.calls if c[0] == "POST"]), 1)
        unverified = pp.ProvisioningError("starting healthy words must not matter", http_status=422, method="POST", path="/databases/db-1/start")
        client = self.client("exited", **{"POST /databases/db-1/start": unverified, "/databases/db-1": [("GET", {"uuid":"db-1","name":"ledger-postgres-dev","type":"postgresql","project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","version":"16","is_public":False,"status":"exited"}), ("GET", {"uuid":"db-1","status":"exited"}), ("GET", {"uuid":"db-1","status":"exited"})]})
        with self.apply_env():
            result = pp.provision(self.apply_args(), client_factory=lambda: client)
        self.assertEqual(result["postgresql_readiness"]["status"], "blocked")
        self.assertEqual(len([c for c in client.calls if c[0] == "POST"]), 1)

    def test_exited_unhealthy_is_exact_unhealthy_not_ambiguous(self):
        self.assertEqual(pp.classify_database_readiness_status("exited:unhealthy"), "unhealthy")
        self.assertEqual(pp.classify_database_readiness_status("exited-something-unknown"), "readiness_unverified")

    def test_psycopg_url_hardening(self):
        self.assertEqual(pp.prepare_psycopg_database_url("postgresql://user:p%25@host:5432/db?sslmode=require"), "postgresql+psycopg://user:p%25@host:5432/db?sslmode=require")
        self.assertEqual(pp.prepare_psycopg_database_url("postgres://user:pass@[::1]:5432/db?x=1"), "postgresql+psycopg://user:pass@[::1]:5432/db?x=1")
        for bad in ["postgresql://host", "postgresql:///db", "postgresql://[::1/db", " postgresql://u:p@h/db", "postgresql://u:p@h/db ", "mysql://u:p@h/db", "postgresql://u:p@h/db#frag"]:
            with self.assertRaises(pp.ProvisioningError) as raised:
                pp.prepare_psycopg_database_url(bad)
            self.assertNotIn(bad, raised.exception.message)
        self.assertEqual(pp.verify_internal_db_url_flags({"internal_db_url":"   "}), {"database_credentials_available": False, "internal_db_url_available": False, "database_url_driver_ready": False, "internal_db_url_source": None})

class Stage5BackendApplicationTests(ProductDevelopmentProvisionerTests):
    def healthy_client(self, **overrides):
        app = {"uuid":"api-1","name":"ledger-api-dev","type":"application","project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","destination_uuid":"bk8cw0gkswgo04o0s88sk0kg","github_app_uuid":"app-1","git_repository":"https://github.com/goodmorning-mx/ledger.git","git_branch":"dev","build_pack":"dockerfile","base_directory":"/backend","dockerfile_location":"/Dockerfile","ports_exposes":"8000","domains":"https://api.dev.ledger.goodmorning.mx","instant_deploy":True,"is_auto_deploy_enabled":True,"autogenerate_domain":False,"health_check_enabled":True,"health_check_path":"/health","health_check_port":"8000","health_check_method":"GET","health_check_scheme":"http","health_check_return_code":200}
        base={"/applications":[app],"/applications/api-1":app,"/applications/api-1/envs":[] ,"/databases/db-1":{"uuid":"db-1","name":"ledger-postgres-dev","type":"postgresql","project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","version":"16","is_public":False,"status":"healthy","internal_db_url":"postgresql://user:p%40ss@host:5432/db"},"/databases":[{"uuid":"db-1","name":"ledger-postgres-dev","type":"postgresql","project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","version":"16","is_public":False,"status":"healthy"}]}
        base.update(overrides)
        return self.client(**base)

    def test_stage5_dry_run_get_only_and_frontend_planned_later(self):
        c=self.healthy_client(**{"/applications":[]})
        r=pp.provision(args(), client_factory=lambda:c)
        self.assertEqual(r["backend_application"]["status"], "would_create")
        self.assertFalse(any(call[0] != "GET" for call in c.calls))
        self.assertEqual(r["resources"][2]["status"], "planned_after_backend")

    def test_stage5_existing_backend_compatible_exists(self):
        c=self.healthy_client()
        r=pp.provision(args(), client_factory=lambda:c)
        self.assertEqual(r["backend_application"]["status"], "exists")
        self.assertEqual(r["backend_environment"]["status"], "planned")

    def test_stage5_backend_incompatibilities(self):
        for field,value in [("git_repository","bad/repo"),("git_branch","main"),("github_app_uuid","wrong"),("base_directory","/api"),("dockerfile_location","/Dockerfile.api"),("ports_exposes","8001"),("domains","https://api.dev.ledger.goodmorning.mx:8000"),("is_auto_deploy_enabled",False),("health_check_path","/bad")]:
            app=self.healthy_client().payloads["/applications/api-1"].copy(); app[field]=value
            c=self.healthy_client(**{"/applications":[app],"/applications/api-1":app})
            r=pp.provision(args(), client_factory=lambda:c)
            self.assertIn(r["backend_application"]["status"], {"recreate_required","incompatible"})

    def test_stage5_duplicate_and_out_of_scope(self):
        app=self.healthy_client().payloads["/applications/api-1"].copy()
        c=self.healthy_client(**{"/applications":[app, {**app,"uuid":"api-2"}],"/applications/api-2":{**app,"uuid":"api-2"}})
        self.assertEqual(pp.provision(args(), client_factory=lambda:c)["backend_application"]["status"], "duplicate")
        out={**app,"project_uuid":"other"}
        c=self.healthy_client(**{"/applications":[out],"/applications/api-1":out})
        self.assertEqual(pp.provision(args(), client_factory=lambda:c)["backend_application"]["status"], "out_of_scope")

    def test_stage5_postgres_preconditions_block_apply(self):
        for db in [{"status":"unhealthy","internal_db_url":"postgresql://u:p@h/db"},{"status":"healthy"},{"status":"healthy","internal_db_url":"mysql://x"}]:
            full={"uuid":"db-1","name":"ledger-postgres-dev","type":"postgresql","project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","version":"16","is_public":False,**db}
            c=self.healthy_client(**{"/applications":[],"/databases/db-1":full})
            with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES":"true"}, clear=False):
                r=pp.provision(args(dry_run=False,apply=True,confirm_product="ledger",confirm_backend_application="ledger-api-dev"), client_factory=lambda:c)
            self.assertEqual(r["backend_application"]["status"], "blocked")
            self.assertFalse(any(call[0]=="POST" and call[1]=="/applications/private-github-app" for call in c.calls))

    def test_stage5_application_gate_allows_single_private_github_app_post(self):
        app=self.healthy_client().payloads["/applications/api-1"]
        c=self.healthy_client(**{"/applications":[[], [{**app}]],"/applications/api-1":app,"POST /applications/private-github-app":pp.Response(201,{"uuid":"api-1","name":"ledger-api-dev"},{})})
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES":"true"}, clear=False):
            r=pp.provision(args(dry_run=False,apply=True,confirm_product="ledger",confirm_backend_application="ledger-api-dev"), client_factory=lambda:c)
        self.assertEqual([call[1] for call in c.calls if call[0]=="POST"].count("/applications/private-github-app"),1)
        self.assertFalse(c.calls[-1][0] in {"PUT","DELETE"})

    def test_stage5_env_desired_payloads_and_secret_redaction(self):
        desired=pp.desired_backend_env("ledger","postgresql+psycopg://user:pass@host/db")
        self.assertEqual([d["key"] for d in desired],["DATABASE_URL","APP_ENV","CORS_ORIGINS"])
        self.assertEqual(desired[1]["value"],"dev")
        self.assertEqual(desired[2]["value"],"https://dev.ledger.goodmorning.mx")
        payload=pp.env_payload(desired[0])
        self.assertTrue(payload["is_runtime"]); self.assertFalse(payload["is_buildtime"]); self.assertTrue(payload["is_literal"])
        self.assertNotIn("postgresql+psycopg://user:pass", json.dumps(pp.redact(payload)))

    def test_stage5_env_secret_hidden_blocks_without_overwrite(self):
        c=self.healthy_client(**{"/applications/api-1/envs":[{"key":"DATABASE_URL","is_preview":False,"is_runtime":True,"is_buildtime":False,"is_literal":True,"is_multiline":False}]})
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES":"true","COOLIFY_ALLOW_BACKEND_ENV_WRITES":"true"}, clear=False):
            r=pp.provision(args(dry_run=False,apply=True,confirm_product="ledger",confirm_backend_application="ledger-api-dev",confirm_backend_environment="ledger-api-dev"), client_factory=lambda:c)
        self.assertEqual(r["backend_environment"]["status"],"secret_value_unverified")
        self.assertFalse(any(call[0] in {"POST","PATCH"} and call[1].endswith("/envs") and call[2]["key"]=="DATABASE_URL" for call in c.calls))

    def test_stage5_env_create_and_patch_gates(self):
        c=self.healthy_client()
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES":"true","COOLIFY_ALLOW_BACKEND_ENV_WRITES":"true"}, clear=False):
            pp.provision(args(dry_run=False,apply=True,confirm_product="ledger",confirm_backend_application="ledger-api-dev",confirm_backend_environment="ledger-api-dev"), client_factory=lambda:c)
        env_posts=[call for call in c.calls if call[0]=="POST" and call[1].endswith("/envs")]
        self.assertEqual({p[2]["key"] for p in env_posts}, {"DATABASE_URL","APP_ENV","CORS_ORIGINS"})
        c=self.healthy_client(**{"/applications/api-1/envs":[{"key":"APP_ENV","value":"prod","is_preview":False,"is_runtime":True,"is_buildtime":False,"is_literal":True,"is_multiline":False}]})
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES":"true"}, clear=False):
            r=pp.provision(args(dry_run=False,apply=True,confirm_product="ledger",confirm_backend_application="ledger-api-dev"), client_factory=lambda:c)
        self.assertTrue(any(b.get("reason")=="backend_env_gates_incomplete" for b in r["blockers"]))

    def test_stage5_client_allowlist_blocks_application_patch_bulk_and_lifecycle(self):
        mode=pp.ExecutionMode("apply","ledger","ledger",True,False,None,True,"ledger-api-dev",True,"ledger-api-dev")
        client=pp.CoolifyClient("https://coolify.example","token",mode=mode)
        for method,path,kind in [("PATCH","/applications/api-1","backend_env_update"),("POST","/applications/api-1/envs/bulk","backend_env_create"),("POST","/applications/api-1/restart",None),("DELETE","/applications/api-1",None)]:
            with self.assertRaises(pp.ProvisioningError): client.request(method,path,json_body={"key":"APP_ENV"},post_kind=kind)


    def test_stage9_explicit_destination_with_server_without_destinations(self):
        explicit = "rowwocok08sgg800ogkogkc4"
        client = self.client(**{"/servers": [{"uuid": "server-1", "name": "development"}], "/applications": []})
        result = pp.provision(args(destination_uuid=explicit), client_factory=lambda: client)
        self.assertEqual(result["selected_destination"]["uuid"], explicit)
        self.assertEqual(result["selected_destination"]["selection_source"], "explicit_operator_input")
        self.assertTrue(result["selected_destination"]["destination_uuid_present"])
        self.assertFalse(result["selected_destination"]["destination_scope_api_verified"])
        self.assertEqual(result["selected_destination"]["scope_verification_reason"], "public_api_does_not_expose_destination_listing")
        self.assertEqual(result["resources"][1]["create_payload_preview"]["destination_uuid"], explicit)
        self.assertEqual(result["resources"][2]["create_payload_preview"]["destination_uuid"], explicit)
        self.assertNotIn(("GET", "/destinations"), client.calls)
        self.assertFalse(any(call[0] in {"POST","PATCH","PUT","DELETE"} for call in client.calls))

    def test_stage9_destination_identifier_accepts_real_coolify_shapes_and_rejects_unsafe_shapes(self):
        for value in ("xs8e9kfps9c7dmes414gyt05", "bk8cw0gkswgo04o0s88sk0kg", "rowwocok08sgg800ogkogkc4"):
            self.assertEqual(pp.validate_coolify_destination_identifier(value), value)
        bad_values = (
            None,
            "",
            "   ",
            "bk8cw0gkswgo04o0s88sk0kg ",
            "bk8cw0gkswgo04o0s88sk0kg rowwocok08sgg800ogkogkc4",
            "bk8cw0gkswgo04o0s88sk0kg,rowwocok08sgg800ogkogkc4",
            '["bk8cw0gkswgo04o0s88sk0kg"]',
            '{"destination_uuid":"bk8cw0gkswgo04o0s88sk0kg"}',
            "https://coolify.example/destinations/bk8cw0gkswgo04o0s88sk0kg",
            "/destinations/bk8cw0gkswgo04o0s88sk0kg",
            "bk8cw0gkswgo04o0s88sk0kg?foo=bar",
            "bk8cw0gkswgo04o0s88sk0kg#fragment",
            "user:pass@bk8cw0gkswgo04o0s88sk0kg",
            "bk8cw0gkswgo04o0s88sk0kg\n",
            "bk8cw0gkswgo04o0s88sk0kg\t",
            "bk8cw0gkswgo04o0s88sk0kg" + chr(31),
            "a" * (pp.COOLIFY_IDENTIFIER_MAX_LENGTH + 1),
        )
        for value in bad_values:
            with self.subTest(value=value):
                with self.assertRaises(pp.ProvisioningError):
                    pp.validate_coolify_destination_identifier(value)

    def test_stage9_explicit_destination_still_checks_present_server_destinations(self):
        explicit = "rowwocok08sgg800ogkogkc4"
        client = self.client(**{"/servers": [{"uuid": "server-1", "name": "development", "destinations": [{"uuid":"bk8cw0gkswgo04o0s88sk0kg"}]}], "/applications": []})
        result = pp.provision(args(destination_uuid=explicit), client_factory=lambda: client)
        self.assertEqual(result["backend_application"]["status"], "blocked")
        self.assertTrue(any(b.get("reason") == "destination_not_on_selected_server" for b in result["blockers"]))

    def test_stage5_destination_selection(self):
        valid = "bk8cw0gkswgo04o0s88sk0kg"
        other = "rowwocok08sgg800ogkogkc4"
        blockers=[]; server={"uuid":"s","destinations":[]}
        self.assertEqual(pp.select_destination(server,args(destination_uuid=valid),blockers)["uuid"], valid); self.assertFalse(blockers)
        blockers=[]; server={"uuid":"s","destinations":[{"uuid":valid},{"uuid":other}]}
        self.assertIsNone(pp.select_destination(server,args(destination_uuid="xs8e9kfps9c7dmes414gyt05"),blockers)); self.assertTrue(blockers)
        blockers=[]; self.assertEqual(pp.select_destination(server,args(destination_uuid=other),blockers)["uuid"], other)

    def test_stage5_no_remote_process_terms_in_source(self):
        source=open("scripts/provision_product_development.py").read()
        for term in ("ssh ","rsync","scp ","docker -H"):
            self.assertNotIn(term, source)

    def bound_client(self):
        mode=pp.ExecutionMode("apply","ledger","ledger",True,False,None,True,"ledger-api-dev",True,"ledger-api-dev")
        client=pp.CoolifyClient("https://coolify.example","token",mode=mode)
        client.bind_backend_application_uuid("api-1", {"project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","destination_uuid":"bk8cw0gkswgo04o0s88sk0kg","github_app_uuid":"app-1","git_repository":"goodmorning-mx/ledger"})
        client.bind_backend_env_contract(pp.desired_backend_env("ledger", "postgresql+psycopg://user:pass@host/db"))
        return client

    def app_payload(self, **updates):
        payload=pp.backend_create_payload(args(), {"project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","destination_uuid":"bk8cw0gkswgo04o0s88sk0kg","github_app_uuid":"app-1"})
        payload.update(updates)
        return payload

    def env_payload_for(self, key="APP_ENV", **updates):
        desired={"key":key,"value":"dev","secret":False,"is_runtime":True,"is_buildtime":False,"is_preview":False,"is_literal":True,"is_multiline":False}
        if key == "DATABASE_URL":
            desired["value"]="postgresql+psycopg://user:pass@host/db"; desired["secret"]=True
        payload=pp.env_payload(desired); payload.update(updates); return payload

    def test_stage5_env_uuid_binding_exact_and_mismatch(self):
        client=self.bound_client()
        client._check_request("POST","/applications/api-1/envs",post_kind="backend_env_create",json_body=self.env_payload_for())
        with self.assertRaises(pp.ProvisioningError):
            client._check_request("POST","/applications/other/envs",post_kind="backend_env_create",json_body=self.env_payload_for())
        with self.assertRaises(pp.ProvisioningError):
            client._check_request("PATCH","/applications/other/envs",post_kind="backend_env_update",json_body=self.env_payload_for())

    def test_stage5_env_without_binding_and_rebind_rejected(self):
        mode=pp.ExecutionMode("apply","ledger","ledger",True,False,None,True,"ledger-api-dev",True,"ledger-api-dev")
        client=pp.CoolifyClient("https://coolify.example","token",mode=mode)
        with self.assertRaises(pp.ProvisioningError):
            client._check_request("POST","/applications/api-1/envs",post_kind="backend_env_create",json_body=self.env_payload_for())
        client.bind_backend_application_uuid("api-1", {})
        with self.assertRaises(pp.ProvisioningError):
            client.bind_backend_application_uuid("api-2", {})

    def test_stage5_env_payload_rejects_keys_extra_and_bad_flags(self):
        client=self.bound_client()
        for key in ("EVIL_KEY","VITE_API_URL","POSTGRES_PASSWORD"):
            with self.assertRaises(pp.ProvisioningError):
                client._check_request("POST","/applications/api-1/envs",post_kind="backend_env_create",json_body=self.env_payload_for(key))
        with self.assertRaises(pp.ProvisioningError):
            client._check_request("POST","/applications/api-1/envs",post_kind="backend_env_create",json_body={**self.env_payload_for(),"extra":"no"})
        for bad in ({"is_runtime":False},{"is_buildtime":True},{"is_preview":True},{"is_multiline":True},{"is_literal":False}):
            with self.assertRaises(pp.ProvisioningError):
                client._check_request("POST","/applications/api-1/envs",post_kind="backend_env_create",json_body=self.env_payload_for(**bad))

    def test_stage5_application_payload_rejects_extra_ported_domain_and_wrong_binding(self):
        mode=pp.ExecutionMode("apply","ledger","ledger",True,False,None,True,"ledger-api-dev",False,None)
        client=pp.CoolifyClient("https://coolify.example","token",mode=mode)
        client.bind_backend_application_bindings({"project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","destination_uuid":"bk8cw0gkswgo04o0s88sk0kg","github_app_uuid":"app-1","git_repository":"goodmorning-mx/ledger"})
        client._check_request("POST","/applications/private-github-app",post_kind="backend_application_create",json_body=self.app_payload())
        for payload in ({**self.app_payload(),"extra":"no"}, self.app_payload(domains="https://api.dev.ledger.goodmorning.mx:8000"), self.app_payload(project_uuid="other")):
            with self.assertRaises(pp.ProvisioningError):
                client._check_request("POST","/applications/private-github-app",post_kind="backend_application_create",json_body=payload)

    def test_stage5_post_env_verified_and_unverified_after_get(self):
        c=self.healthy_client()
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES":"true","COOLIFY_ALLOW_BACKEND_ENV_WRITES":"true"}, clear=False):
            r=pp.provision(args(dry_run=False,apply=True,confirm_product="ledger",confirm_backend_application="ledger-api-dev",confirm_backend_environment="ledger-api-dev"), client_factory=lambda:c)
        self.assertTrue(any(a["resource"]=="backend_environment" and a["verified"] for a in r["actions"]))
        c=self.healthy_client(**{"__mutate_envs__":False})
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES":"true","COOLIFY_ALLOW_BACKEND_ENV_WRITES":"true"}, clear=False):
            r=pp.provision(args(dry_run=False,apply=True,confirm_product="ledger",confirm_backend_application="ledger-api-dev",confirm_backend_environment="ledger-api-dev"), client_factory=lambda:c)
        self.assertEqual(r["backend_environment"]["variables"][0]["status"], "created_unverified")

    def test_stage5_patch_env_verified_and_unverified_after_get(self):
        existing={"key":"APP_ENV","value":"prod","is_preview":False,"is_runtime":True,"is_buildtime":False,"is_literal":True,"is_multiline":False}
        c=self.healthy_client(**{"/applications/api-1/envs":[existing]})
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES":"true","COOLIFY_ALLOW_BACKEND_ENV_WRITES":"true"}, clear=False):
            r=pp.provision(args(dry_run=False,apply=True,confirm_product="ledger",confirm_backend_application="ledger-api-dev",confirm_backend_environment="ledger-api-dev"), client_factory=lambda:c)
        self.assertTrue(any(a["action"]=="update_APP_ENV" and a["verified"] for a in r["actions"]))
        db_existing={**self.env_payload_for("DATABASE_URL"), "value":"postgresql+psycopg://user:p%40ss@host:5432/db"}
        c=self.healthy_client(**{"/applications/api-1/envs":[db_existing, existing],"__mutate_envs__":False})
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES":"true","COOLIFY_ALLOW_BACKEND_ENV_WRITES":"true"}, clear=False):
            r=pp.provision(args(dry_run=False,apply=True,confirm_product="ledger",confirm_backend_application="ledger-api-dev",confirm_backend_environment="ledger-api-dev"), client_factory=lambda:c)
        self.assertEqual(r["backend_environment"]["variables"][1]["status"], "updated_unverified")

    def test_stage5_secret_hidden_and_duplicate_after_write(self):
        c=self.healthy_client(**{"/applications/api-1/envs":[{"key":"DATABASE_URL","is_preview":False,"is_runtime":True,"is_buildtime":False,"is_literal":True,"is_multiline":False}]})
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES":"true","COOLIFY_ALLOW_BACKEND_ENV_WRITES":"true"}, clear=False):
            r=pp.provision(args(dry_run=False,apply=True,confirm_product="ledger",confirm_backend_application="ledger-api-dev",confirm_backend_environment="ledger-api-dev"), client_factory=lambda:c)
        self.assertEqual(r["backend_environment"]["status"], "secret_value_unverified")
        dup=[self.env_payload_for("APP_ENV"), self.env_payload_for("APP_ENV")]
        c=self.healthy_client(**{"/applications/api-1/envs":[self.env_payload_for("DATABASE_URL"), dup[0], dup[1]]})
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES":"true","COOLIFY_ALLOW_BACKEND_ENV_WRITES":"true"}, clear=False):
            r=pp.provision(args(dry_run=False,apply=True,confirm_product="ledger",confirm_backend_application="ledger-api-dev",confirm_backend_environment="ledger-api-dev"), client_factory=lambda:c)
        self.assertIn(r["backend_environment"]["status"], {"partial", "blocked"})

    def test_stage5_env_conflict_recovery_no_retry(self):
        for method, exc, key in [("POST", pp.ProvisioningError("conflict", http_status=409, method="POST", path="/applications/api-1/envs"), "DATABASE_URL"), ("PATCH", pp.ProvisioningError("conflict", http_status=422, method="PATCH", path="/applications/api-1/envs"), "APP_ENV")]:
            payloads={"/applications/api-1/envs":[self.env_payload_for("DATABASE_URL"), self.env_payload_for("APP_ENV"), self.env_payload_for("CORS_ORIGINS")]}
            if method == "POST": payloads["POST /applications/api-1/envs"]=exc; payloads["/applications/api-1/envs"]=[]
            else: payloads["PATCH /applications/api-1/envs"]=exc; payloads["/applications/api-1/envs"]=[{"key":"APP_ENV","value":"prod","is_preview":False,"is_runtime":True,"is_buildtime":False,"is_literal":True,"is_multiline":False}, self.env_payload_for("DATABASE_URL"), self.env_payload_for("CORS_ORIGINS")]
            c=self.healthy_client(**payloads)
            with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES":"true","COOLIFY_ALLOW_BACKEND_ENV_WRITES":"true"}, clear=False):
                r=pp.provision(args(dry_run=False,apply=True,confirm_product="ledger",confirm_backend_application="ledger-api-dev",confirm_backend_environment="ledger-api-dev"), client_factory=lambda:c)
            calls=[call for call in c.calls if call[0]==method and call[1].endswith("/envs")]
            self.assertLessEqual(len(calls), 1)

    def test_stage5_patch_unknown_errors_and_get_not_unknown(self):
        import io
        client=self.bound_client()
        def http_error(*_a, **_k):
            raise pp.urllib.error.HTTPError("url", 500, "boom", {}, io.BytesIO(b"{}"))
        with mock.patch("urllib.request.urlopen", http_error):
            with self.assertRaises(pp.ProvisioningError) as raised:
                client.update_backend_env("api-1", self.env_payload_for())
            self.assertTrue(raised.exception.request_attempted)
            self.assertTrue(raised.exception.write_outcome_unknown)
        def url_error(*_a, **_k):
            raise pp.urllib.error.URLError("timeout")
        with mock.patch("urllib.request.urlopen", url_error):
            with self.assertRaises(pp.ProvisioningError) as raised:
                client.update_backend_env("api-1", self.env_payload_for())
            self.assertTrue(raised.exception.write_outcome_unknown)
        class BadJson:
            status=200; headers={}
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b"{"
        with mock.patch("urllib.request.urlopen", lambda *_a, **_k: BadJson()):
            with self.assertRaises(pp.ProvisioningError) as raised:
                client.update_backend_env("api-1", self.env_payload_for())
            self.assertTrue(raised.exception.write_outcome_unknown)
        get_exc=pp.ProvisioningError("500", http_status=500, method="GET", path="/applications/api-1/envs")
        self.assertFalse(get_exc.write_outcome_unknown)

    def test_stage5_action_records_and_safety_output_no_secrets_no_patch_contradiction(self):
        c=self.healthy_client()
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES":"true","COOLIFY_ALLOW_BACKEND_ENV_WRITES":"true"}, clear=False):
            r=pp.provision(args(dry_run=False,apply=True,confirm_product="ledger",confirm_backend_application="ledger-api-dev",confirm_backend_environment="ledger-api-dev"), client_factory=lambda:c)
        text=json.dumps(r)
        self.assertNotIn("postgresql://user", text)
        self.assertIn("always_forbidden_methods", r["safety_gates"])
        self.assertIn("PATCH", r["safety_gates"]["conditionally_allowed_methods"])
        self.assertIn("backend_env_update", r["safety_gates"]["allowed_patch_operations"])

    def test_stage5_no_writes_after_unknown_and_lifecycle_blocked(self):
        c=self.healthy_client(**{"POST /applications/api-1/envs": pp.ProvisioningError("boom", method="POST", path="/applications/api-1/envs", write_outcome_unknown=True)})
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES":"true","COOLIFY_ALLOW_BACKEND_ENV_WRITES":"true"}, clear=False):
            r=pp.provision(args(dry_run=False,apply=True,confirm_product="ledger",confirm_backend_application="ledger-api-dev",confirm_backend_environment="ledger-api-dev"), client_factory=lambda:c)
        self.assertEqual(len([call for call in c.calls if call[0] in {"POST","PATCH"} and call[1].endswith("/envs")]), 1)
        client=self.bound_client()
        for method,path,kind in [("POST","/applications/api-1/envs/bulk","backend_env_create"),("PATCH","/applications/api-1","backend_env_update"),("POST","/deployments",None),("POST","/applications/api-1/restart",None),("DELETE","/applications/api-1",None)]:
            with self.assertRaises(pp.ProvisioningError): client._check_request(method,path,post_kind=kind,json_body=self.env_payload_for())

    def test_stage5_hidden_database_url_does_not_block_independent_envs(self):
        hidden={"key":"DATABASE_URL","is_preview":False,"is_runtime":True,"is_buildtime":False,"is_literal":True,"is_multiline":False}
        c=self.healthy_client(**{"/applications/api-1/envs":[hidden]})
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES":"true","COOLIFY_ALLOW_BACKEND_ENV_WRITES":"true"}, clear=False):
            r=pp.provision(args(dry_run=False,apply=True,confirm_product="ledger",confirm_backend_application="ledger-api-dev",confirm_backend_environment="ledger-api-dev"), client_factory=lambda:c)
        self.assertEqual(r["backend_environment"]["status"], "secret_value_unverified")
        self.assertFalse(any(call[0] in {"POST","PATCH"} and call[1].endswith("/envs") and call[2]["key"]=="DATABASE_URL" for call in c.calls))
        self.assertTrue(any(call[0]=="POST" and call[2]["key"]=="APP_ENV" for call in c.calls))
        self.assertTrue(any(call[0]=="POST" and call[2]["key"]=="CORS_ORIGINS" for call in c.calls))

    def test_stage5_database_url_created_hidden_still_reconciles_independent_envs(self):
        hidden={"key":"DATABASE_URL","is_preview":False,"is_runtime":True,"is_buildtime":False,"is_literal":True,"is_multiline":False}
        app_env=self.env_payload_for("APP_ENV")
        cors=self.env_payload_for("CORS_ORIGINS"); cors["value"]="https://dev.ledger.goodmorning.mx"
        c=self.healthy_client(**{"__mutate_envs__":False,"/applications/api-1/envs":[("GET", []), ("GET", [hidden]), ("GET", [hidden, app_env]), ("GET", [hidden, app_env, cors])]})
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES":"true","COOLIFY_ALLOW_BACKEND_ENV_WRITES":"true"}, clear=False):
            r=pp.provision(args(dry_run=False,apply=True,confirm_product="ledger",confirm_backend_application="ledger-api-dev",confirm_backend_environment="ledger-api-dev"), client_factory=lambda:c)
        self.assertEqual(r["backend_environment"]["status"], "secret_value_unverified")
        self.assertIn(r["status"], {"blocked", "partial"})
        self.assertEqual([call[2]["key"] for call in c.calls if call[0]=="POST" and call[1].endswith("/envs")], ["DATABASE_URL", "APP_ENV", "CORS_ORIGINS"])

    def test_stage5_database_url_conflict_hidden_zero_retry_and_continues(self):
        hidden={"key":"DATABASE_URL","is_preview":False,"is_runtime":True,"is_buildtime":False,"is_literal":True,"is_multiline":False}
        c=self.healthy_client(**{"__mutate_envs__":False,"POST /applications/api-1/envs":pp.ProvisioningError("conflict", http_status=409, method="POST", path="/applications/api-1/envs"),"/applications/api-1/envs":[("GET", []), ("GET", [hidden]), ("GET", [hidden, self.env_payload_for("APP_ENV")]), ("GET", [hidden, self.env_payload_for("APP_ENV"), {**self.env_payload_for("CORS_ORIGINS"), "value":"https://dev.ledger.goodmorning.mx"}])]})
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES":"true","COOLIFY_ALLOW_BACKEND_ENV_WRITES":"true"}, clear=False):
            pp.provision(args(dry_run=False,apply=True,confirm_product="ledger",confirm_backend_application="ledger-api-dev",confirm_backend_environment="ledger-api-dev"), client_factory=lambda:c)
        self.assertEqual(len([call for call in c.calls if call[0]=="POST" and call[1].endswith("/envs") and call[2]["key"]=="DATABASE_URL"]), 1)
        self.assertTrue(any(call[0]=="POST" and call[2]["key"]=="APP_ENV" for call in c.calls))

    def test_stage5_env_values_are_bound_exactly_and_errors_redacted(self):
        client=self.bound_client()
        for payload in [self.env_payload_for("APP_ENV", value="production"), {**self.env_payload_for("CORS_ORIGINS"), "value":"https://evil.example"}, {**self.env_payload_for("CORS_ORIGINS"), "value":"https://dev.ledger.goodmorning.mx:8000"}, self.env_payload_for("DATABASE_URL", value="mysql://u:p@h/db"), self.env_payload_for("DATABASE_URL", value="postgresql+psycopg://other/db")]:
            with self.assertRaises(pp.ProvisioningError) as raised:
                client._check_request("POST","/applications/api-1/envs",post_kind="backend_env_create",json_body=payload)
            self.assertNotIn("mysql://", raised.exception.message)
            self.assertNotIn("postgresql+psycopg://", raised.exception.message)
            self.assertNotIn("DATABASE_URL", raised.exception.message)
        mode=pp.ExecutionMode("apply","ledger","ledger",True,False,None,True,"ledger-api-dev",True,"ledger-api-dev")
        unbound=pp.CoolifyClient("https://coolify.example","token",mode=mode)
        unbound.bind_backend_application_uuid("api-1", {"git_repository":"goodmorning-mx/ledger"})
        with self.assertRaises(pp.ProvisioningError):
            unbound._check_request("POST","/applications/api-1/envs",post_kind="backend_env_create",json_body=self.env_payload_for())
        with self.assertRaises(pp.ProvisioningError):
            client.bind_backend_env_contract(pp.desired_backend_env("ledger", "postgresql+psycopg://different/db"))

    def test_stage5_application_repository_binding_exact_and_equivalent(self):
        mode=pp.ExecutionMode("apply","ledger","ledger",True,False,None,True,"ledger-api-dev",False,None)
        client=pp.CoolifyClient("https://coolify.example","token",mode=mode)
        bindings={"project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","destination_uuid":"bk8cw0gkswgo04o0s88sk0kg","github_app_uuid":"app-1","git_repository":"owner/repo"}
        client.bind_backend_application_bindings(bindings)
        for repo in ("owner/repo", "https://github.com/owner/repo", "https://github.com/owner/repo.git"):
            payload=pp.backend_create_payload(args(repository="owner/repo"), bindings); payload["git_repository"]=repo
            client._check_request("POST","/applications/private-github-app",post_kind="backend_application_create",json_body=payload)
        bad=pp.backend_create_payload(args(repository="owner/repo"), bindings); bad["git_repository"]="other/repo"
        with self.assertRaises(pp.ProvisioningError):
            client._check_request("POST","/applications/private-github-app",post_kind="backend_application_create",json_body=bad)
        with self.assertRaises(pp.ProvisioningError):
            client.bind_backend_application_bindings({**bindings,"git_repository":"other/repo"})


class Stage6FrontendApplicationTests(ProductDevelopmentProvisionerTests):
    def frontend_app(self, **overrides):
        data = {"uuid":"front-1", "name":"ledger-frontend-dev", "type":"application", "project_uuid":"project-1", "environment_uuid":"env-1", "server_uuid":"server-1", "destination_uuid":"bk8cw0gkswgo04o0s88sk0kg", "github_app_uuid":"app-1", "git_repository":"goodmorning-mx/ledger", "git_branch":"dev", "build_pack":"dockerfile", "base_directory":"/frontend", "dockerfile_location":"/Dockerfile", "ports_exposes":"80", "domains":"https://dev.ledger.goodmorning.mx", "instant_deploy":True, "is_auto_deploy_enabled":True, "autogenerate_domain":False, "health_check_enabled":True, "health_check_path":"/", "health_check_port":"80", "health_check_method":"GET", "health_check_scheme":"http", "health_check_return_code":200}
        data.update(overrides)
        return data



    def backend_app(self, **overrides):
        data = {"uuid":"api-1", "name":"ledger-api-dev", "type":"application", "project_uuid":"project-1", "environment_uuid":"env-1", "server_uuid":"server-1", "destination_uuid":"bk8cw0gkswgo04o0s88sk0kg", "github_app_uuid":"app-1", "git_repository":"goodmorning-mx/ledger", "git_branch":"dev", "build_pack":"dockerfile", "base_directory":"/backend", "dockerfile_location":"/Dockerfile", "ports_exposes":"8000", "domains":"https://api.dev.ledger.goodmorning.mx", "instant_deploy":True, "is_auto_deploy_enabled":True, "autogenerate_domain":False, "health_check_enabled":True, "health_check_path":"/health", "health_check_port":"8000", "health_check_method":"GET", "health_check_scheme":"http", "health_check_return_code":200}
        data.update(overrides)
        return data

    def apply_frontend_env_mode(self):
        return pp.ExecutionMode('apply','ledger','ledger',True,False,None,False,None,False,None,True,'ledger-frontend-dev',True,'ledger-frontend-dev')

    def test_stage6_existing_frontend_blocks_env_when_backend_not_verified_statuses(self):
        front={"status":"exists","uuid":"front-1"}
        bad_backends = [
            {"status":"absent"}, {"status":"incompatible","uuid":"api-1"}, {"status":"duplicate","uuid":"api-1"},
            {"status":"out_of_scope","uuid":"api-1"}, {"status":"scope_unverified","uuid":"api-1"},
            {"status":"compatibility_unverified","uuid":"api-1"}, {"status":"created_unverified","uuid":"api-1"},
            {"status":"write_outcome_unknown","uuid":"api-1"}, {"status":"exists"},
        ]
        for backend in bad_backends:
            c=self.client(**{"/applications/front-1/envs":[]})
            actions=[]
            result=pp.reconcile_frontend_environment(c,args(),front,backend,self.apply_frontend_env_mode(),actions)
            self.assertEqual(result["status"], "blocked", backend)
            self.assertTrue(any(b.get("reason")=="backend_application_not_verified" for b in result["blockers"]), backend)
            self.assertFalse(any(call[0] in {"POST","PATCH"} and call[1].endswith("/envs") for call in c.calls), backend)
            self.assertEqual(actions, [])

    def test_stage6_existing_frontend_dry_run_env_plans_after_backend_without_writes(self):
        c=self.client(**{"/applications/front-1/envs":[]})
        result=pp.reconcile_frontend_environment(c,args(),{"status":"exists","uuid":"front-1"},{"status":"absent"},pp.ExecutionMode('dry_run','ledger',None,False),[])
        self.assertEqual(result["status"], "planned_after_backend")
        self.assertFalse(any(call[0] in {"POST","PATCH"} for call in c.calls))

    def test_stage6_existing_frontend_env_writes_require_backend_precondition_despite_gates(self):
        front=self.frontend_app(); api=self.backend_app(base_directory="/wrong")
        c=self.client(**{"/applications":[front,api], "/applications/front-1":front, "/applications/api-1":api, "/applications/front-1/envs":[]})
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES":"true","COOLIFY_ALLOW_BACKEND_ENV_WRITES":"true","COOLIFY_ALLOW_FRONTEND_APPLICATION_WRITES":"true","COOLIFY_ALLOW_FRONTEND_ENV_WRITES":"true"}, clear=False):
            result=pp.provision(args(dry_run=False, apply=True, confirm_product="ledger", confirm_backend_application="ledger-api-dev", confirm_backend_environment="ledger-api-dev", confirm_frontend_application="ledger-frontend-dev", confirm_frontend_environment="ledger-frontend-dev"), client_factory=lambda:c)
        self.assertEqual(result["frontend_application"]["status"], "exists")
        self.assertEqual(result["backend_application"]["status"], "recreate_required")
        self.assertEqual(result["frontend_environment"]["status"], "blocked")
        self.assertFalse(any(call[0] in {"POST","PATCH"} and call[1].endswith("/envs") and call[2].get("key")=="VITE_API_URL" for call in c.calls))
        self.assertNotIn("frontend_env_create", result["safety"]["allowed_post_operations"])
        self.assertNotIn("frontend_env_update", result["safety"]["allowed_patch_operations"])

    def test_stage6_existing_frontend_backend_exists_allows_vite_reconcile(self):
        exact={"key":"VITE_API_URL","value":"https://api.dev.ledger.goodmorning.mx","is_runtime":False,"is_buildtime":True,"is_preview":False,"is_literal":True,"is_multiline":False}
        c=self.client(**{"/applications/front-1/envs":[exact]})
        result=pp.reconcile_frontend_environment(c,args(),{"status":"exists","uuid":"front-1"},{"status":"exists","uuid":"api-1"},self.apply_frontend_env_mode(),[])
        self.assertEqual(result["status"], "exists")
        self.assertEqual(result["variables"][0]["status"], "exists")

    def test_stage6_secret_value_unverified_backend_env_does_not_block_frontend_env(self):
        front=self.frontend_app(); api=self.backend_app()
        hidden_db={"key":"DATABASE_URL","is_preview":False,"is_runtime":True,"is_buildtime":False,"is_literal":True,"is_multiline":False}
        db={"uuid":"db-1","name":"ledger-postgres-dev","type":"postgresql","project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","version":"16","is_public":False,"status":"healthy","internal_db_url":"postgresql://user:p%40ss@host:5432/db"}
        c=self.client(**{"/applications":[api,front], "/applications/api-1":api, "/applications/front-1":front, "/applications/api-1/envs":[hidden_db], "/applications/front-1/envs":[], "/databases":[db], "/databases/db-1":db})
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES":"true","COOLIFY_ALLOW_BACKEND_ENV_WRITES":"true","COOLIFY_ALLOW_FRONTEND_APPLICATION_WRITES":"true","COOLIFY_ALLOW_FRONTEND_ENV_WRITES":"true"}, clear=False):
            result=pp.provision(args(dry_run=False, apply=True, confirm_product="ledger", confirm_backend_application="ledger-api-dev", confirm_backend_environment="ledger-api-dev", confirm_frontend_application="ledger-frontend-dev", confirm_frontend_environment="ledger-frontend-dev"), client_factory=lambda:c)
        self.assertEqual(result["frontend_application"]["status"], "exists")
        self.assertTrue(any(call[0]=="POST" and call[1]=="/applications/front-1/envs" and call[2]["key"]=="VITE_API_URL" for call in c.calls))
        self.assertTrue(any(b.get("reason")=="secret_value_unverified" for b in result["blockers"]))

    def test_stage6_allowed_operations_reflect_backend_preconditions(self):
        front=self.frontend_app(); api_bad=self.backend_app(git_branch="main")
        c=self.client(**{"/applications":[front,api_bad], "/applications/front-1":front, "/applications/api-1":api_bad, "/applications/front-1/envs":[]})
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_FRONTEND_APPLICATION_WRITES":"true","COOLIFY_ALLOW_FRONTEND_ENV_WRITES":"true"}, clear=False):
            result=pp.provision(args(dry_run=False, apply=True, confirm_product="ledger", confirm_frontend_application="ledger-frontend-dev", confirm_frontend_environment="ledger-frontend-dev"), client_factory=lambda:c)
        self.assertNotIn("frontend_application_create", result["safety"]["allowed_post_operations"])
        self.assertNotIn("frontend_env_create", result["safety"]["allowed_post_operations"])
        self.assertNotIn("frontend_env_update", result["safety"]["allowed_patch_operations"])
        self.assertFalse(result["safety"]["frontend_backend_precondition_met"])

    def test_stage6_and_stage7_allowlist_contracts_are_separated(self):
        stage5_posts={"project_create","environment_create","postgresql_create","postgresql_start","backend_application_create","backend_env_create"}
        stage6_posts=stage5_posts | {"frontend_application_create","frontend_env_create"}
        stage7_posts=stage6_posts | {"backend_deploy"}
        stage8_posts=stage7_posts | {"frontend_deploy"}
        self.assertTrue(stage6_posts <= pp.ALLOWED_POST_KINDS)
        self.assertTrue(stage7_posts <= pp.ALLOWED_POST_KINDS)
        self.assertEqual(pp.ALLOWED_POST_KINDS, stage8_posts)
        self.assertEqual(pp.ALLOWED_PATCH_KINDS, {"backend_env_update","frontend_env_update"})

    def test_stage6_dry_run_get_only_and_three_resources(self):
        c=self.client(**{"/applications":[self.client().payloads["/applications"][0]]})
        r=pp.provision(args(), client_factory=lambda:c)
        self.assertEqual(len(r["resources"]),3)
        self.assertFalse(any(call[0] in {"POST","PATCH"} for call in c.calls))
        self.assertIn(r["frontend_application"]["status"], {"planned_after_backend","would_create"})

    def test_stage6_frontend_compatible_duplicate_and_incompatible_cases(self):
        context={"project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","destination_uuid":"bk8cw0gkswgo04o0s88sk0kg","github_app_uuid":"app-1","git_repository":"goodmorning-mx/ledger"}
        self.assertEqual(pp.classify_frontend_application([self.frontend_app()], args(), context)["status"], "exists")
        self.assertEqual(pp.classify_frontend_application([self.frontend_app(), self.frontend_app(uuid="front-2")], args(), context)["status"], "duplicate")
        for field,value in (("git_repository","bad/repo"),("git_branch","main"),("github_app_uuid","bad"),("destination_uuid","bad"),("base_directory","/web"),("dockerfile_location","Dockerfile"),("ports_exposes","3000"),("domains","https://dev.ledger.goodmorning.mx:80"),("is_auto_deploy_enabled",False),("health_check_path","/health")):
            self.assertEqual(pp.classify_frontend_application([self.frontend_app(**{field:value})], args(), context)["status"], "incompatible", field)

    def test_stage6_frontend_payload_and_domain_validation(self):
        context={"project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","destination_uuid":"bk8cw0gkswgo04o0s88sk0kg","github_app_uuid":"app-1","git_repository":"goodmorning-mx/ledger"}
        payload=pp.frontend_create_payload(args(), context)
        self.assertEqual(set(payload), pp.APPROVED_FRONTEND_APPLICATION_FIELDS)
        self.assertEqual(payload["ports_exposes"], "80")
        self.assertEqual(payload["domains"], "https://dev.ledger.goodmorning.mx")
        mode=pp.ExecutionMode('apply','ledger','ledger',True,False,None,False,None,False,None,True,'ledger-frontend-dev')
        client=pp.CoolifyClient('https://coolify.example','unit-test-token',mode=mode)
        client.bind_frontend_application_bindings(context)
        client._check_request('POST','/applications/private-github-app',post_kind='frontend_application_create',json_body=payload)
        bad=dict(payload); bad['domains']='https://dev.ledger.goodmorning.mx:3000'
        with self.assertRaises(pp.ProvisioningError): client._check_request('POST','/applications/private-github-app',post_kind='frontend_application_create',json_body=bad)
        bad=dict(payload); bad['extra']=True
        with self.assertRaises(pp.ProvisioningError): client._check_request('POST','/applications/private-github-app',post_kind='frontend_application_create',json_body=bad)

    def test_stage6_apply_gates_and_post_then_verify(self):
        front=self.frontend_app()
        api={**self.client().payloads["/applications"][0], "destination_uuid":"bk8cw0gkswgo04o0s88sk0kg", "instant_deploy":True, "is_auto_deploy_enabled":True, "autogenerate_domain":False, "health_check_enabled":True, "health_check_path":"/health", "health_check_port":"8000", "health_check_method":"GET", "health_check_scheme":"http", "health_check_return_code":200}
        c=self.client(**{"/applications":[api], "/applications/api-1":api, "/applications/front-1":front, "/applications/front-1/envs":[]})
        with mock.patch.dict(os.environ,{"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_FRONTEND_APPLICATION_WRITES":"true","COOLIFY_ALLOW_FRONTEND_ENV_WRITES":"true"}, clear=False):
            r=pp.provision(args(dry_run=False, apply=True, confirm_product="ledger", confirm_frontend_application="ledger-frontend-dev", confirm_frontend_environment="ledger-frontend-dev"), client_factory=lambda:c)
        posts=[call for call in c.calls if call[0]=="POST" and call[1]=="/applications/private-github-app"]
        self.assertEqual(len(posts),1)
        self.assertEqual(posts[0][2], pp.frontend_create_payload(args(), {"project_uuid":"project-1","environment_uuid":"env-1","environment_name":"dev","server_uuid":"server-1","github_app_uuid":"app-1","destination_uuid":"bk8cw0gkswgo04o0s88sk0kg","git_repository":"goodmorning-mx/ledger"}))
        self.assertIn(("GET","/applications/front-1"), c.calls)

    def test_stage6_vite_api_url_env_contract_and_writes(self):
        desired=pp.desired_frontend_env("ledger", "https://api.dev.ledger.goodmorning.mx")[0]
        self.assertEqual(pp.frontend_env_payload(desired), {"key":"VITE_API_URL","value":"https://api.dev.ledger.goodmorning.mx","is_runtime":False,"is_buildtime":True,"is_preview":False,"is_literal":True,"is_multiline":False})
        for url in ("http://api.dev.ledger.goodmorning.mx","https://api.dev.ledger.goodmorning.mx:80","https://localhost","https://api.dev.ledger.goodmorning.mx/path"):
            with self.assertRaises(pp.ProvisioningError): pp.desired_frontend_env("ledger", url)
        mode=pp.ExecutionMode('apply','ledger','ledger',True,False,None,False,None,False,None,True,'ledger-frontend-dev',True,'ledger-frontend-dev')
        client=pp.CoolifyClient('https://coolify.example','unit-test-token',mode=mode)
        client.bind_backend_application_uuid('api-1', {})
        client.bind_frontend_application_uuid('front-1', {})
        client.bind_frontend_env_contract([desired])
        client._check_request('POST','/applications/front-1/envs',post_kind='frontend_env_create',json_body=pp.frontend_env_payload(desired))
        client._check_request('PATCH','/applications/front-1/envs',post_kind='frontend_env_update',json_body=pp.frontend_env_payload(desired))
        with self.assertRaises(pp.ProvisioningError): client._check_request('POST','/applications/api-1/envs',post_kind='frontend_env_create',json_body=pp.frontend_env_payload(desired))
        bad=pp.frontend_env_payload(desired); bad['key']='DATABASE_URL'
        with self.assertRaises(pp.ProvisioningError): client._check_request('POST','/applications/front-1/envs',post_kind='frontend_env_create',json_body=bad)
        bad=pp.frontend_env_payload(desired); bad['is_runtime']=True
        with self.assertRaises(pp.ProvisioningError): client._check_request('POST','/applications/front-1/envs',post_kind='frontend_env_create',json_body=bad)
        bad=pp.frontend_env_payload(desired); bad['is_shown_once']=True
        with self.assertRaises(pp.ProvisioningError): client._check_request('POST','/applications/front-1/envs',post_kind='frontend_env_create',json_body=bad)

    def test_stage6_env_reconcile_absent_exact_duplicate_and_patch(self):
        front={"status":"exists","uuid":"front-1"}; back={"status":"exists","uuid":"api-1"}
        c=self.client(**{"/applications/front-1/envs":[]})
        res=pp.reconcile_frontend_environment(c,args(),front,back,pp.ExecutionMode('dry_run','ledger',None,False),[])
        self.assertEqual(res["variables"][0]["action"], "would_create")
        exact={"key":"VITE_API_URL","value":"https://api.dev.ledger.goodmorning.mx","is_runtime":False,"is_buildtime":True,"is_preview":False,"is_literal":True,"is_multiline":False}
        c=self.client(**{"/applications/front-1/envs":[exact]})
        self.assertEqual(pp.reconcile_frontend_environment(c,args(),front,back,pp.ExecutionMode('dry_run','ledger',None,False),[])["variables"][0]["status"], "exists")
        c=self.client(**{"/applications/front-1/envs":[exact,dict(exact)]})
        self.assertEqual(pp.reconcile_frontend_environment(c,args(),front,back,pp.ExecutionMode('dry_run','ledger',None,False),[])["status"], "planned")
        mode=pp.ExecutionMode('apply','ledger','ledger',True,False,None,False,None,False,None,True,'ledger-frontend-dev',True,'ledger-frontend-dev')
        c=self.client(**{"/applications/front-1/envs":[{**exact,"value":"https://api.dev.other.goodmorning.mx"}]})
        actions=[]; res=pp.reconcile_frontend_environment(c,args(),front,back,mode,actions)
        self.assertTrue(any(call[0]=="PATCH" for call in c.calls))
        self.assertIn(res["variables"][-1]["status"], {"patched","updated_unverified"})

    def test_stage6_allowlist_blocks_forbidden_endpoints_and_uuid_isolation(self):
        mode=pp.ExecutionMode('apply','ledger','ledger',True,False,None,False,None,False,None,True,'ledger-frontend-dev',True,'ledger-frontend-dev')
        client=pp.CoolifyClient('https://coolify.example','unit-test-token',mode=mode)
        client.bind_frontend_application_uuid('front-1', {})
        client.bind_frontend_env_contract(pp.desired_frontend_env('ledger','https://api.dev.ledger.goodmorning.mx'))
        for method,path,kind in [('PATCH','/applications/front-1','frontend_env_update'),('POST','/applications/front-1/envs/bulk','frontend_env_create'),('POST','/applications/front-1/start','frontend_env_create'),('POST','/deployments','frontend_env_create'),('DELETE','/applications/front-1',None),('PUT','/applications/front-1',None)]:
            with self.assertRaises(pp.ProvisioningError): client._check_request(method,path,post_kind=kind,json_body=pp.frontend_env_payload(pp.desired_frontend_env('ledger','https://api.dev.ledger.goodmorning.mx')[0]))
        client.bind_backend_application_uuid('api-1', {})
        with self.assertRaises(pp.ProvisioningError): client.bind_frontend_application_uuid('api-1', {})

class Stage7BackendDeploymentTests(unittest.TestCase):
    def base_client(self, **overrides):
        db_url = "postgresql://user:p%40ss@postgres:5432/ledger_dev"
        app = {"uuid":"api-1","id":"api-1","name":"ledger-api-dev","type":"application","project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","destination_uuid":"bk8cw0gkswgo04o0s88sk0kg","git_repository":"goodmorning-mx/ledger","git_branch":"dev","build_pack":"dockerfile","base_directory":"/backend","dockerfile_location":"/Dockerfile","ports_exposes":"8000","domains":"https://api.dev.ledger.goodmorning.mx","github_app_uuid":"app-1","instant_deploy":True,"is_auto_deploy_enabled":True,"autogenerate_domain":False,"health_check_enabled":True,"health_check_path":"/health","health_check_port":"8000","health_check_method":"GET","health_check_scheme":"http","health_check_return_code":200,"status":"running:healthy"}
        payloads={
            "/projects":[{"uuid":"project-1","name":"ledger"}],
            "/projects/project-1":{"uuid":"project-1","name":"ledger","environments":[{"uuid":"env-1","name":"dev"}]},
            "/servers":[{"uuid":"server-1","name":"development","destinations":[{"uuid":"bk8cw0gkswgo04o0s88sk0kg","name":"docker"}]}],
            "/github-apps":{"github_apps":[{"uuid":"app-1","name":"goodmorning-mx-coolify"}]},
            "/applications":[app, {**app,"uuid":"front-1","id":"front-1","name":"ledger-frontend-dev","base_directory":"/frontend","ports_exposes":"80","domains":"https://dev.ledger.goodmorning.mx","health_check_path":"/","health_check_port":"80"}],
            "/applications/api-1": app,
            "/applications/front-1": {**app,"uuid":"front-1","id":"front-1","name":"ledger-frontend-dev","base_directory":"/frontend","ports_exposes":"80","domains":"https://dev.ledger.goodmorning.mx","health_check_path":"/","health_check_port":"80"},
            "/databases":[{"uuid":"db-1","name":"ledger-postgres-dev","type":"postgresql","project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","version":"16","is_public":False,"status":"running:healthy"}],
            "/databases/db-1":{"uuid":"db-1","name":"ledger-postgres-dev","type":"postgresql","project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","version":"16","is_public":False,"status":"running:healthy","internal_db_url":db_url},
            "/applications/api-1/envs":[
                {"key":"DATABASE_URL","value":pp.prepare_psycopg_database_url(db_url),"is_runtime":True,"is_buildtime":False,"is_preview":False,"is_literal":True,"is_multiline":False},
                {"key":"APP_ENV","value":"dev","is_runtime":True,"is_buildtime":False,"is_preview":False,"is_literal":True,"is_multiline":False},
                {"key":"CORS_ORIGINS","value":"https://dev.ledger.goodmorning.mx","is_runtime":True,"is_buildtime":False,"is_preview":False,"is_literal":True,"is_multiline":False},
            ],
            "/applications/front-1/envs":[{"key":"VITE_API_URL","value":"https://api.dev.ledger.goodmorning.mx","is_runtime":False,"is_buildtime":True,"is_preview":False,"is_literal":True,"is_multiline":False}],
            "/deployments/applications/api-1?skip=0&take=10":{"count":0,"deployments":[]},
            "/deployments/dep-1":[("GET",{"deployment_uuid":"dep-1","application_name":"ledger-api-dev","status":"queued"}),("GET",{"deployment_uuid":"dep-1","application_name":"ledger-api-dev","status":"in_progress"}),("GET",{"deployment_uuid":"dep-1","application_name":"ledger-api-dev","status":"finished"})],
        }
        payloads.update(overrides)
        return FakeClient(payloads)

    def deploy_args(self, **kw):
        d=dict(dry_run=False, apply=True, deploy_backend=True, confirm_product="ledger", confirm_backend_deployment="ledger-api-dev")
        d.update(kw)
        return args(**d)

    def gates(self):
        return {"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_DEPLOYMENTS":"true","COOLIFY_ALLOW_BACKEND_DEPLOYMENT":"true"}

    def test_stage7_dry_run_and_apply_without_flag_never_post_deploy(self):
        client=self.base_client()
        result=pp.provision(args(deploy_backend=True), client_factory=lambda: client)
        self.assertEqual(result["backend_deployment"]["status"], "would_deploy")
        self.assertFalse(any(c[0]=="POST" and c[1]=="/deploy" for c in client.calls))
        client=self.base_client()
        with mock.patch.dict(os.environ, self.gates(), clear=True):
            result=pp.provision(self.deploy_args(deploy_backend=False), client_factory=lambda: client)
        self.assertEqual(result["backend_deployment"]["status"], "not_requested")
        self.assertFalse(any(c[0]=="POST" and c[1]=="/deploy" for c in client.calls))

    def test_stage7_success_payload_poll_runtime_and_redacted_output(self):
        client=self.base_client()
        class Resp:
            status=200; url="https://api.dev.ledger.goodmorning.mx/health"; headers={"Content-Type":"application/json"}
            def read(self,n=-1): return b'{"service":"ledger-api","status":"ok","environment":"dev"}'
            def __enter__(self): return self
            def __exit__(self,*a): return False
            def getcode(self): return 200
        with mock.patch.dict(os.environ, self.gates(), clear=True), mock.patch.object(pp.urllib.request, "urlopen", return_value=Resp()) as smoke:
            result=pp.provision(self.deploy_args(), client_factory=lambda: client)
        self.assertEqual(result["backend_deployment"]["status"], "finished")
        self.assertTrue(result["backend_deployment"]["verified"])
        self.assertTrue(result["backend_runtime_readiness"]["healthy"])
        self.assertTrue(result["backend_smoke_check"]["body_verified"])
        self.assertIn(("POST","/deploy", {"uuid":"api-1","force":False}), client.calls)
        self.assertNotIn("DATABASE_URL", json.dumps(result))
        self.assertTrue(smoke.called)

    def test_stage7_gates_and_preflight_block(self):
        client=self.base_client()
        with self.assertRaises(pp.ProvisioningError):
            pp.provision(self.deploy_args(), client_factory=lambda: client)
        result={"backend_deployment":{"status":"gates_incomplete"}}
        self.assertEqual(result["backend_deployment"]["status"], "gates_incomplete")
        self.assertFalse(any(c[0]=="POST" and c[1]=="/deploy" for c in client.calls))
        client=self.base_client(**{"/databases/db-1": {"uuid":"db-1","name":"ledger-postgres-dev","type":"postgresql","project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","version":"16","is_public":False,"status":"running:unhealthy","internal_db_url":"postgresql://u:p@h:5432/d"}})
        with mock.patch.dict(os.environ, self.gates(), clear=True):
            result=pp.provision(self.deploy_args(), client_factory=lambda: client)
        self.assertEqual(result["backend_deployment"]["status"], "blocked_by_preconditions")

    def test_stage7_env_secret_app_env_cors_blockers(self):
        for envs in ([{"key":"DATABASE_URL","value":"********","is_runtime":True,"is_buildtime":False,"is_preview":False,"is_literal":True,"is_multiline":False}],
                     [{"key":"DATABASE_URL","value":pp.prepare_psycopg_database_url("postgresql://u:p@h:5432/d"),"is_runtime":True,"is_buildtime":False,"is_preview":False,"is_literal":True,"is_multiline":False},{"key":"APP_ENV","value":"prod","is_runtime":True,"is_buildtime":False,"is_preview":False,"is_literal":True,"is_multiline":False}],
                     [{"key":"DATABASE_URL","value":pp.prepare_psycopg_database_url("postgresql://u:p@h:5432/d"),"is_runtime":True,"is_buildtime":False,"is_preview":False,"is_literal":True,"is_multiline":False},{"key":"APP_ENV","value":"dev","is_runtime":True,"is_buildtime":False,"is_preview":False,"is_literal":True,"is_multiline":False},{"key":"CORS_ORIGINS","value":"https://bad","is_runtime":True,"is_buildtime":False,"is_preview":False,"is_literal":True,"is_multiline":False}]):
            client=self.base_client(**{"/applications/api-1/envs": envs})
            with mock.patch.dict(os.environ, self.gates(), clear=True):
                result=pp.provision(self.deploy_args(), client_factory=lambda: client)
            self.assertEqual(result["backend_deployment"]["status"], "blocked_by_preconditions")

    def test_stage7_allowlist_blocks_forbidden_deploy_shapes(self):
        mode=pp.ExecutionMode("apply","ledger","ledger",True,deploy_backend=True,allow_deployments=True,allow_backend_deployment=True,confirm_backend_deployment="ledger-api-dev")
        client=pp.CoolifyClient("https://coolify.example","token",mode=mode)
        client.bind_backend_application_uuid("api-1", {})
        client.bind_backend_deployment_preconditions(True)
        for body in ({"uuid":"api-1","force":True},{"uuid":"api-1,api-2","force":False},{"uuid":"api-2","force":False},{"uuid":"api-1","force":False,"tag":"x"}):
            with self.assertRaises(pp.ProvisioningError): client._check_request("POST","/deploy",post_kind="backend_deploy",json_body=body)
        for method,path in (("GET","/deploy"),("POST","/applications/api-1/start"),("POST","/applications/api-1/restart"),("POST","/applications/api-1/stop"),("POST","/deployments/dep-1/cancel")):
            with self.assertRaises(pp.ProvisioningError): client._check_request(method,path)

    def test_stage7_response_recovery_active_and_poll_failures(self):
        self.assertEqual(pp.parse_deploy_response({"deployments":[{"resource_uuid":"api-1","deployment_uuid":"dep"}]},"api-1"), "dep")
        for data in ({"deployments":[]},{"deployments":[{},{}]},{"deployments":[{"resource_uuid":"other","deployment_uuid":"dep"}]},{"deployments":[{"resource_uuid":"api-1"}]}):
            with self.assertRaises(pp.ProvisioningError): pp.parse_deploy_response(data,"api-1")
        client=self.base_client(**{"/deployments/applications/api-1?skip=0&take=10":{"count":1,"deployments":[{"deployment_uuid":"old","status":"in_progress","resource_uuid":"api-1"}]},"/deployments/old":{"deployment_uuid":"old","application_name":"ledger-api-dev","status":"finished"}})
        with mock.patch.dict(os.environ, self.gates(), clear=True):
            result=pp.provision(self.deploy_args(), client_factory=lambda: client)
        self.assertTrue(result["backend_deployment"]["adopted_active"])
        self.assertFalse(any(c[0]=="POST" and c[1]=="/deploy" for c in client.calls))
        for status in ("failed","cancelled-by-user","mystery"):
            st = status if status != "mystery" else "deployment_status_unverified"
            client=self.base_client(**{"/deployments/dep-1":{"deployment_uuid":"dep-1","application_name":"ledger-api-dev","status":status}})
            with mock.patch.dict(os.environ, self.gates(), clear=True):
                result=pp.provision(self.deploy_args(), client_factory=lambda: client)
            self.assertEqual(result["backend_deployment"]["status"], st)

    def test_stage7_runtime_smoke_and_argument_guards(self):
        for val in (0, -1, float("inf"), float("nan")):
            with self.assertRaises(pp.ProvisioningError): pp.validate_stage7_timing_args(args(backend_deployment_timeout=val))
        self.assertEqual(pp.verify_backend_runtime(self.base_client(**{"/applications/api-1":{"uuid":"api-1","name":"ledger-api-dev","status":"running:unhealthy"}}), args(), "api-1")["status"], "running:unhealthy")
        self.assertEqual(pp.verify_backend_runtime(self.base_client(**{"/applications/api-1":{"uuid":"api-1","name":"ledger-api-dev","status":"weird"}}), args(), "api-1")["status"], "runtime_status_unverified")
        for body in ({}, {"status":"ok","environment":"prod","service":"x"}, {"status":"bad","environment":"dev","service":"x"}, {"status":"ok","environment":"dev","service":""}):
            class Resp:
                status=200; url="https://api.dev.ledger.goodmorning.mx/health"; headers={"Content-Type":"application/json"}
                def read(self,n=-1): return json.dumps(body).encode()
                def __enter__(self): return self
                def __exit__(self,*a): return False
                def getcode(self): return 200
            r=pp.run_backend_smoke(args(), opener=lambda req,timeout: Resp(), sleeper=lambda s: None, clock=iter([0,1]).__next__)
            self.assertIn(r["status"], {"smoke_failed","smoke_timeout"})

class Stage7AuditCorrectionTests(unittest.TestCase):
    def a(self, **kw):
        return args(**kw)

    def env_row(self, key, **overrides):
        desired = {"is_runtime": True, "is_buildtime": False, "is_preview": False, "is_literal": True, "is_multiline": False}
        row = {"key": key, "status": "exists", "value_verified": True, "desired_flags": desired}
        row.update(overrides)
        return row

    def preflight_ok(self, variables, status="exists"):
        pg={"name":"ledger-postgres-dev","status":"exists"}
        ready={"status":"healthy","internal_db_url_available":True,"database_url_driver_ready":True}
        backend={"name":"ledger-api-dev","status":"exists","uuid":"api-1"}
        return pp.backend_deployment_preflight(self.a(), pg, ready, backend, {"status":status,"variables":variables}, [])

    def test_stage7_preflight_rejects_empty_subset_duplicate_extra_unverified_and_secret_envs(self):
        valid=[self.env_row("DATABASE_URL"), self.env_row("APP_ENV"), self.env_row("CORS_ORIGINS")]
        cases={
            "empty": [],
            "only_database": [self.env_row("DATABASE_URL")],
            "missing_app_env": [self.env_row("DATABASE_URL"), self.env_row("CORS_ORIGINS")],
            "missing_cors": [self.env_row("DATABASE_URL"), self.env_row("APP_ENV")],
            "duplicate_key": valid + [self.env_row("APP_ENV")],
            "extra_key": valid + [self.env_row("EXTRA")],
            "value_verified_absent": [self.env_row("DATABASE_URL", value_verified=None), self.env_row("APP_ENV"), self.env_row("CORS_ORIGINS")],
            "value_verified_false": [self.env_row("DATABASE_URL", value_verified=False), self.env_row("APP_ENV"), self.env_row("CORS_ORIGINS")],
            "secret_value_unverified": [self.env_row("DATABASE_URL", status="secret_value_unverified"), self.env_row("APP_ENV"), self.env_row("CORS_ORIGINS")],
            "bad_flags": [self.env_row("DATABASE_URL", desired_flags={"is_runtime": False}), self.env_row("APP_ENV"), self.env_row("CORS_ORIGINS")],
        }
        for name, variables in cases.items():
            with self.subTest(name=name):
                ok, blockers = self.preflight_ok(variables)
                self.assertFalse(ok)
                self.assertTrue(blockers)
        ok, blockers = self.preflight_ok(valid)
        self.assertTrue(ok)
        self.assertEqual(blockers, [])

    def test_stage7_deployment_history_fails_closed_for_invalid_shapes_and_get_errors(self):
        invalid_payloads = [None, [], {"count": "0", "deployments": []}, {"count": 0}, {"count": 1, "deployments": ["bad"]}]
        for payload in invalid_payloads:
            with self.subTest(payload=payload):
                client = FakeClient({"/deployments/applications/api-1?skip=0&take=10": payload})
                with self.assertRaises(pp.ProvisioningError):
                    pp.deployment_history(client, "api-1")
        client = FakeClient({"/deployments/applications/api-1?skip=0&take=10": pp.ProvisioningError("boom")})
        with self.assertRaises(pp.ProvisioningError):
            pp.deployment_history(client, "api-1")

    def test_stage7_recovery_candidates_are_deduped_and_backend_scoped(self):
        before={"old"}
        self.assertEqual(pp.recover_new_deployment_candidates(before, [{"deployment_uuid":"new","resource_uuid":"api-1"}], "api-1"), ["new"])
        self.assertEqual(pp.recover_new_deployment_candidates(before, [{"deployment_uuid":"old","resource_uuid":"api-1"}], "api-1"), [])
        self.assertEqual(pp.recover_new_deployment_candidates(before, [{"deployment_uuid":"new"},{"deployment_uuid":"new"}], "api-1"), ["new"])
        self.assertEqual(pp.recover_new_deployment_candidates(before, [{"deployment_uuid":"bad","resource_uuid":"other"}], "api-1"), [])
        self.assertEqual(pp.recover_new_deployment_candidates(before, [{"resource_uuid":"api-1"}], "api-1"), [])
        self.assertEqual(pp.recover_new_deployment_candidates(before, [{"deployment_uuid":"one"},{"deployment_uuid":"two"}], "api-1"), ["one","two"])

    def test_stage7_ambiguous_2xx_deploy_response_uses_recovery_once_without_second_post(self):
        base=Stage7BackendDeploymentTests().base_client
        client=base(**{
            "POST /deploy": pp.Response(200, {"deployments": []}, {}),
            "/deployments/applications/api-1?skip=0&take=10": [("GET", {"count":0,"deployments":[]}), ("GET", {"count":1,"deployments":[{"deployment_uuid":"dep-new","resource_uuid":"api-1"}]})],
            "/deployments/dep-new": {"deployment_uuid":"dep-new","resource_uuid":"api-1","application_name":"ledger-api-dev","status":"finished"},
        })
        class Resp:
            status=200; url="https://api.dev.ledger.goodmorning.mx/health"; headers={"Content-Type":"application/json"}
            def read(self,n=-1): return b'{"service":"ledger-api","status":"ok","environment":"dev"}'
            def __enter__(self): return self
            def __exit__(self,*a): return False
            def getcode(self): return 200
        with mock.patch.dict(os.environ, Stage7BackendDeploymentTests().gates(), clear=True), mock.patch.object(pp.urllib.request, "urlopen", return_value=Resp()):
            result=pp.provision(Stage7BackendDeploymentTests().deploy_args(), client_factory=lambda: client)
        self.assertTrue(result["backend_deployment"]["recovered_after_unknown"])
        self.assertEqual(result["backend_deployment"]["deployment_uuid"], "dep-new")
        self.assertEqual([c for c in client.calls if c[0]=="POST" and c[1]=="/deploy"], [("POST","/deploy",{"uuid":"api-1","force":False})])

    def test_stage7_ambiguous_2xx_recovery_none_many_or_invalid_history_stays_unknown(self):
        scenarios={
            "none": [("GET", {"count":0,"deployments":[]}), ("GET", {"count":0,"deployments":[]})],
            "many": [("GET", {"count":0,"deployments":[]}), ("GET", {"count":2,"deployments":[{"deployment_uuid":"a"},{"deployment_uuid":"b"}]})],
            "invalid_after": [("GET", {"count":0,"deployments":[]}), ("GET", {"count":0})],
        }
        for name, history in scenarios.items():
            with self.subTest(name=name):
                client=Stage7BackendDeploymentTests().base_client(**{"POST /deploy": pp.Response(200, {"deployments": []}, {}), "/deployments/applications/api-1?skip=0&take=10": history})
                with mock.patch.dict(os.environ, Stage7BackendDeploymentTests().gates(), clear=True):
                    result=pp.provision(Stage7BackendDeploymentTests().deploy_args(), client_factory=lambda: client)
                self.assertIn(result["backend_deployment"]["status"], {"deployment_outcome_unknown", "ambiguous_deployment_recovery"})
                self.assertTrue(result["backend_deployment"]["write_outcome_unknown"])
                self.assertEqual(len([c for c in client.calls if c[0]=="POST" and c[1]=="/deploy"]), 1)

    def test_stage7_polling_checks_deployment_and_backend_identity(self):
        base_args=self.a(backend_deployment_timeout=0.02, backend_deployment_interval=0.01, backend_deployment_max_poll_errors=0)
        cases={
            "deployment_uuid_mismatch": {"deployment_uuid":"other","resource_uuid":"api-1","application_name":"ledger-api-dev","status":"finished"},
            "backend_uuid_mismatch": {"deployment_uuid":"dep","resource_uuid":"other","application_name":"ledger-api-dev","status":"finished"},
            "application_name_mismatch": {"deployment_uuid":"dep","resource_uuid":"api-1","application_name":"other","status":"finished"},
            "deployment_status_unverified": {"deployment_uuid":"dep","resource_uuid":"api-1","application_name":"ledger-api-dev","status":"weird"},
        }
        for expected, payload in cases.items():
            client=FakeClient({"/deployments/dep": payload})
            result=pp.poll_backend_deployment(client, base_args, "dep", "api-1", sleeper=lambda s: None, clock=iter([0,0]).__next__)
            self.assertEqual(result["status"], expected)
        result=pp.poll_backend_deployment(FakeClient({"/deployments/dep": []}), base_args, "dep", "api-1", sleeper=lambda s: None, clock=iter([0,0]).__next__)
        self.assertEqual(result["status"], "deployment_status_unverified")
        result=pp.poll_backend_deployment(FakeClient({"/deployments/dep": pp.ProvisioningError("boom")}), base_args, "dep", "api-1", sleeper=lambda s: None, clock=iter([0,0]).__next__)
        self.assertEqual(result["status"], "deployment_poll_failed")
        for status in ("finished", "failed", "cancelled-by-user"):
            result=pp.poll_backend_deployment(FakeClient({"/deployments/dep": {"deployment_uuid":"dep","resource_uuid":"api-1","application_name":"ledger-api-dev","status":status}}), base_args, "dep", "api-1", sleeper=lambda s: None, clock=iter([0,0]).__next__)
            self.assertEqual(result["status"], status)

    def test_stage7_runtime_polling_transient_errors_timeout_and_terminals(self):
        a=self.a(backend_runtime_timeout=0.03, backend_runtime_interval=0.01, backend_runtime_max_poll_errors=1)
        client=FakeClient({"/applications/api-1": [("GET", {"uuid":"api-1","name":"ledger-api-dev","status":"starting"}), ("GET", {"uuid":"api-1","name":"ledger-api-dev","status":"running:healthy"})]})
        r=pp.verify_backend_runtime(client,a,"api-1",sleeper=lambda s: None,clock=iter([0,0,0]).__next__)
        self.assertTrue(r["healthy"])
        for status in ("running:unhealthy","unhealthy","exited","stopped","failed","exited:unhealthy","mystery"):
            client=FakeClient({"/applications/api-1":{"uuid":"api-1","name":"ledger-api-dev","status":status}})
            r=pp.verify_backend_runtime(client,a,"api-1",sleeper=lambda s: None,clock=iter([0,0]).__next__)
            self.assertFalse(r["healthy"])
        client=FakeClient({"/applications/api-1": pp.ProvisioningError("boom")})
        r=pp.verify_backend_runtime(client,a,"api-1",sleeper=lambda s: None,clock=iter([0,0,0]).__next__)
        self.assertEqual(r["status"], "runtime_poll_failed")
        client=FakeClient({"/applications/api-1":{"uuid":"api-1","name":"ledger-api-dev","status":"starting"}})
        r=pp.verify_backend_runtime(client,a,"api-1",sleeper=lambda s: None,clock=iter([0,1]).__next__)
        self.assertEqual(r["status"], "runtime_timeout")

    def test_stage7_smoke_requires_exact_final_url_after_redirect(self):
        valid_body={"service":"ledger-api","status":"ok","environment":"dev"}
        final_urls=[
            "https://other.goodmorning.mx/health",
            "http://api.dev.ledger.goodmorning.mx/health",
            "https://api.dev.ledger.goodmorning.mx:8000/health",
            "https://api.dev.ledger.goodmorning.mx/other",
            "https://api.dev.ledger.goodmorning.mx/health?x=1",
            "https://api.dev.ledger.goodmorning.mx/health#frag",
            "https://api.dev.ledger.goodmorning.mx/health",
        ]
        for final_url in final_urls:
            with self.subTest(final_url=final_url):
                class Resp:
                    status=200; url=final_url; headers={"Content-Type":"application/json"}
                    def read(self,n=-1): return json.dumps(valid_body).encode()
                    def __enter__(self): return self
                    def __exit__(self,*a): return False
                    def getcode(self): return 200
                r=pp.run_backend_smoke(self.a(), opener=lambda req, timeout: Resp(), sleeper=lambda s: None, clock=iter([0,0]).__next__)
                if final_url == "https://api.dev.ledger.goodmorning.mx/health":
                    self.assertEqual(r["status"], "passed")
                else:
                    self.assertEqual(r["status"], "smoke_failed")

    def test_stage7_adopted_active_failure_is_global_partial_when_user_requested(self):
        client=Stage7BackendDeploymentTests().base_client(**{
            "/deployments/applications/api-1?skip=0&take=10":{"count":1,"deployments":[{"deployment_uuid":"old","status":"in_progress","resource_uuid":"api-1"}]},
            "/deployments/old":{"deployment_uuid":"old","resource_uuid":"api-1","application_name":"ledger-api-dev","status":"failed"},
        })
        with mock.patch.dict(os.environ, Stage7BackendDeploymentTests().gates(), clear=True):
            result=pp.provision(Stage7BackendDeploymentTests().deploy_args(), client_factory=lambda: client)
        self.assertTrue(result["backend_deployment"]["user_requested"])
        self.assertTrue(result["backend_deployment"]["adopted_active"])
        self.assertEqual(result["status"], "partial")

    def deploy_client_for_request(self):
        mode=pp.ExecutionMode("apply","ledger","ledger",True,deploy_backend=True,allow_deployments=True,allow_backend_deployment=True,confirm_backend_deployment="ledger-api-dev")
        client=pp.CoolifyClient("https://coolify.example","unit-test-token",mode=mode)
        client.bind_backend_application_uuid("api-1", {})
        client.bind_backend_deployment_preconditions(True)
        return client

    def test_stage7_client_request_marks_2xx_ambiguous_writes_as_performed_unknown(self):
        class Resp:
            def __init__(self, status, body):
                self.status=status; self.headers={}; self.body=body
            def __enter__(self): return self
            def __exit__(self,*a): return False
            def getcode(self): return self.status
            def read(self): return self.body
        cases=[
            ("bad_json", lambda *_a, **_k: Resp(200,b"{"), True, True),
            ("unexpected_202", lambda *_a, **_k: Resp(202,b'{"accepted":true}'), True, True),
            ("http_500", lambda *_a, **_k: (_ for _ in ()).throw(pp.urllib.error.HTTPError("url",500,"boom",{},__import__("io").BytesIO(b"{}"))), False, True),
            ("url_error", lambda *_a, **_k: (_ for _ in ()).throw(pp.urllib.error.URLError("timeout")), False, True),
        ]
        for name, opener, write_performed, write_unknown in cases:
            with self.subTest(name=name), mock.patch("urllib.request.urlopen", opener):
                with self.assertRaises(pp.ProvisioningError) as raised:
                    self.deploy_client_for_request().deploy_backend("api-1")
                self.assertTrue(raised.exception.request_attempted)
                self.assertEqual(raised.exception.write_performed, write_performed)
                self.assertEqual(raised.exception.write_outcome_unknown, write_unknown)

    def test_stage7_client_request_get_bad_json_is_not_write_unknown(self):
        class Resp:
            status=200; headers={}
            def __enter__(self): return self
            def __exit__(self,*a): return False
            def getcode(self): return 200
            def read(self): return b"{"
        with mock.patch("urllib.request.urlopen", lambda *_a, **_k: Resp()):
            with self.assertRaises(pp.ProvisioningError) as raised:
                self.deploy_client_for_request().get("/deployments/dep-1")
        self.assertTrue(raised.exception.request_attempted)
        self.assertFalse(raised.exception.write_performed)
        self.assertFalse(raised.exception.write_outcome_unknown)

    def test_stage7_2xx_invalid_json_and_unexpected_status_recover_without_second_post(self):
        for name, exc in {
            "json_invalid": pp.ProvisioningError("bad json", http_status=200, method="POST", path="/deploy", write_performed=True, write_outcome_unknown=True, request_attempted=True),
            "unexpected_202": pp.ProvisioningError("unexpected", http_status=202, method="POST", path="/deploy", write_performed=True, write_outcome_unknown=True, request_attempted=True),
        }.items():
            with self.subTest(name=name):
                client=Stage7BackendDeploymentTests().base_client(**{
                    "POST /deploy": exc,
                    "/deployments/applications/api-1?skip=0&take=10": [("GET", {"count":0,"deployments":[]}), ("GET", {"count":1,"deployments":[{"deployment_uuid":"dep-new","resource_uuid":"api-1"}]})],
                    "/deployments/dep-new": {"deployment_uuid":"dep-new","resource_uuid":"api-1","application_name":"ledger-api-dev","status":"finished"},
                })
                with mock.patch.dict(os.environ, Stage7BackendDeploymentTests().gates(), clear=True), mock.patch.object(pp, "run_backend_smoke", return_value=pp.smoke_check_result("ledger","passed",http_status=200,body_verified=True,attempts=1)):
                    result=pp.provision(Stage7BackendDeploymentTests().deploy_args(), client_factory=lambda: client)
                self.assertTrue(result["backend_deployment"]["recovered_after_unknown"])
                self.assertFalse(result["backend_deployment"]["write_outcome_unknown"])
                self.assertEqual(len([c for c in client.calls if c[0]=="POST" and c[1]=="/deploy"]), 1)
                request_actions=[a for a in result["actions"] if a["resource"]=="backend_deployment" and a["action"]=="request"]
                recovery_actions=[a for a in result["actions"] if a["resource"]=="backend_deployment" and a["action"]=="recover_unknown"]
                self.assertEqual(len(request_actions), 1)
                self.assertTrue(request_actions[0]["write_performed"])
                self.assertTrue(request_actions[0]["write_outcome_unknown"])
                self.assertEqual(len(recovery_actions), 1)

    def test_stage7_2xx_invalid_json_recovery_none_or_many_stays_partial_with_action(self):
        histories={
            "none": [("GET", {"count":0,"deployments":[]}), ("GET", {"count":0,"deployments":[]})],
            "many": [("GET", {"count":0,"deployments":[]}), ("GET", {"count":2,"deployments":[{"deployment_uuid":"a"},{"deployment_uuid":"b"}]})],
        }
        for name, history in histories.items():
            with self.subTest(name=name):
                client=Stage7BackendDeploymentTests().base_client(**{
                    "POST /deploy": pp.ProvisioningError("bad json", http_status=200, method="POST", path="/deploy", write_performed=True, write_outcome_unknown=True, request_attempted=True),
                    "/deployments/applications/api-1?skip=0&take=10": history,
                })
                with mock.patch.dict(os.environ, Stage7BackendDeploymentTests().gates(), clear=True):
                    result=pp.provision(Stage7BackendDeploymentTests().deploy_args(), client_factory=lambda: client)
                self.assertEqual(result["status"], "partial")
                self.assertTrue(result["backend_deployment"]["write_outcome_unknown"])
                request_actions=[a for a in result["actions"] if a["resource"]=="backend_deployment" and a["action"]=="request"]
                self.assertEqual(len(request_actions), 1)
                self.assertTrue(request_actions[0]["write_performed"])
                self.assertTrue(request_actions[0]["write_outcome_unknown"])

    def test_stage7_action_record_preserves_write_not_performed_for_5xx_or_network(self):
        for name, exc in {
            "http_500": pp.ProvisioningError("500", http_status=500, method="POST", path="/deploy", write_performed=False, write_outcome_unknown=True, request_attempted=True),
            "network": pp.ProvisioningError("url", error_type="network", method="POST", path="/deploy", write_performed=False, write_outcome_unknown=True, request_attempted=True),
        }.items():
            with self.subTest(name=name):
                client=Stage7BackendDeploymentTests().base_client(**{
                    "POST /deploy": exc,
                    "/deployments/applications/api-1?skip=0&take=10": [("GET", {"count":0,"deployments":[]}), ("GET", {"count":0,"deployments":[]})],
                })
                with mock.patch.dict(os.environ, Stage7BackendDeploymentTests().gates(), clear=True):
                    result=pp.provision(Stage7BackendDeploymentTests().deploy_args(), client_factory=lambda: client)
                request_actions=[a for a in result["actions"] if a["resource"]=="backend_deployment" and a["action"]=="request"]
                self.assertEqual(len(request_actions), 1)
                self.assertFalse(request_actions[0]["write_performed"])
                self.assertTrue(request_actions[0]["write_outcome_unknown"])

class Stage8FrontendDeploymentTests(Stage6FrontendApplicationTests):
    def healthy_db(self):
        return {"uuid":"db-1","name":"ledger-postgres-dev","type":"postgresql","project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","version":"16","is_public":False,"status":"healthy","internal_db_url":"postgresql://user:p%40ss@host:5432/db"}

    def backend_envs(self):
        return [
            {"key":"DATABASE_URL","value":"postgresql+psycopg://user:p%40ss@host:5432/db","is_runtime":True,"is_buildtime":False,"is_preview":False,"is_literal":True,"is_multiline":False,"is_shown_once":True},
            {"key":"APP_ENV","value":"dev","is_runtime":True,"is_buildtime":False,"is_preview":False,"is_literal":True,"is_multiline":False},
            {"key":"CORS_ORIGINS","value":"https://dev.ledger.goodmorning.mx","is_runtime":True,"is_buildtime":False,"is_preview":False,"is_literal":True,"is_multiline":False},
        ]

    def frontend_envs(self):
        return [{"key":"VITE_API_URL","value":"https://api.dev.ledger.goodmorning.mx","is_runtime":False,"is_buildtime":True,"is_preview":False,"is_literal":True,"is_multiline":False}]

    def deploy_client(self, **overrides):
        api=self.backend_app(status="running:healthy"); front=self.frontend_app(status="running:healthy"); db=self.healthy_db()
        payloads={
            "/applications":[api,front], "/applications/api-1":api, "/applications/front-1":front,
            "/applications/api-1/envs":self.backend_envs(), "/applications/front-1/envs":self.frontend_envs(),
            "/databases":[db], "/databases/db-1":db,
            "/deployments/applications/api-1?skip=0&take=10":{"deployments":[],"count":0},
            "/deployments/applications/front-1?skip=0&take=10":{"deployments":[],"count":0},
            "/deployments/dep-1":{"deployment_uuid":"dep-1","resource_uuid":"api-1","application_name":"ledger-api-dev","status":"finished"},
            "/deployments/front-dep-1":{"deployment_uuid":"front-dep-1","resource_uuid":"front-1","application_name":"ledger-frontend-dev","status":"finished"},
        }
        payloads.update(overrides)
        return self.client(**payloads)

    def deploy_args(self, **overrides):
        base=dict(dry_run=False, apply=True, confirm_product="ledger", deploy_frontend=True, confirm_frontend_deployment="ledger-frontend-dev")
        base.update(overrides)
        return args(**base)

    def gate_env(self):
        return {"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_DEPLOYMENTS":"true","COOLIFY_ALLOW_FRONTEND_DEPLOYMENT":"true"}

    def test_stage8_allowlist_payload_binding_and_forbidden_shapes(self):
        mode=pp.ExecutionMode(name="apply", product="ledger", confirm_product="ledger", allow_resource_writes=True, deploy_frontend=True, allow_deployments=True, allow_frontend_deployment=True, confirm_frontend_deployment="ledger-frontend-dev")
        client=pp.CoolifyClient("https://coolify.example","token",mode=mode)
        client.bind_backend_application_uuid("api-1", {})
        client.bind_frontend_application_uuid("front-1", {})
        client.bind_frontend_deployment_preconditions(True)
        client._check_request("POST","/deploy",post_kind="frontend_deploy",json_body={"uuid":"front-1","force":False})
        for payload in ({"uuid":"api-1","force":False},{"uuid":"","force":False},{"uuid":"front-1,other","force":False},{"uuid":"front-1","force":True},{"uuid":"front-1","force":False,"tag":"x"}):
            with self.subTest(payload=payload):
                with self.assertRaises(pp.ProvisioningError): client._check_request("POST","/deploy",post_kind="frontend_deploy",json_body=payload)
        with self.assertRaises(pp.ProvisioningError): client._check_request("GET","/deploy")
        with self.assertRaises(pp.ProvisioningError): client._check_request("POST","/applications/front-1/start")

    def test_stage8_frontend_only_deploys_once_with_backend_dependency_verified(self):
        c=self.deploy_client()
        with mock.patch.dict(os.environ,self.gate_env(),clear=False), mock.patch.object(pp,"run_backend_smoke",return_value=pp.smoke_check_result("ledger","passed",http_status=200,body_verified=True,attempts=1)), mock.patch.object(pp,"run_frontend_smoke",return_value=pp.frontend_smoke_check_result("ledger","passed",http_status=200,html_verified=True,root_element_verified=True,attempts=1)):
            r=pp.provision(self.deploy_args(), client_factory=lambda:c)
        self.assertEqual(r["frontend_deployment"]["status"],"finished")
        self.assertTrue(r["frontend_runtime_readiness"]["healthy"])
        self.assertTrue(r["frontend_smoke_check"]["root_element_verified"])
        self.assertEqual([call for call in c.calls if call[0]=="POST" and call[1]=="/deploy"], [("POST","/deploy",{"uuid":"front-1","force":False})])

    def test_stage8_both_flags_backend_posts_before_frontend_and_backend_failures_block(self):
        env={**self.gate_env(),"COOLIFY_ALLOW_BACKEND_DEPLOYMENT":"true"}
        c=self.deploy_client()
        with mock.patch.dict(os.environ,env,clear=False), mock.patch.object(pp,"run_backend_smoke",return_value=pp.smoke_check_result("ledger","passed",http_status=200,body_verified=True,attempts=1)), mock.patch.object(pp,"run_frontend_smoke",return_value=pp.frontend_smoke_check_result("ledger","passed",http_status=200,html_verified=True,root_element_verified=True,attempts=1)):
            pp.provision(self.deploy_args(deploy_backend=True,confirm_backend_deployment="ledger-api-dev"), client_factory=lambda:c)
        posts=[call[2]["uuid"] for call in c.calls if call[0]=="POST" and call[1]=="/deploy"]
        self.assertEqual(posts,["api-1","front-1"])
        c2=self.deploy_client(**{"/deployments/dep-1":{"deployment_uuid":"dep-1","resource_uuid":"api-1","application_name":"ledger-api-dev","status":"failed"}})
        with mock.patch.dict(os.environ,env,clear=False), mock.patch.object(pp,"run_backend_smoke",return_value=pp.smoke_check_result("ledger","passed",http_status=200,body_verified=True,attempts=1)):
            r=pp.provision(self.deploy_args(deploy_backend=True,confirm_backend_deployment="ledger-api-dev"), client_factory=lambda:c2)
        self.assertEqual([call[2]["uuid"] for call in c2.calls if call[0]=="POST" and call[1]=="/deploy"],["api-1"])
        self.assertEqual(r["frontend_deployment"]["status"],"blocked_by_preconditions")

    def test_stage8_history_recovery_poll_runtime_smoke_fail_closed_cases(self):
        with self.subTest("active adoption"):
            c=self.deploy_client(**{"/deployments/applications/front-1?skip=0&take=10":{"deployments":[{"deployment_uuid":"active-1","resource_uuid":"front-1","status":"queued"}],"count":1}, "/deployments/active-1":{"deployment_uuid":"active-1","resource_uuid":"front-1","application_name":"ledger-frontend-dev","status":"finished"}})
            with mock.patch.dict(os.environ,self.gate_env(),clear=False), mock.patch.object(pp,"run_backend_smoke",return_value=pp.smoke_check_result("ledger","passed",http_status=200,body_verified=True,attempts=1)), mock.patch.object(pp,"run_frontend_smoke",return_value=pp.frontend_smoke_check_result("ledger","passed",http_status=200,html_verified=True,root_element_verified=True,attempts=1)):
                r=pp.provision(self.deploy_args(), client_factory=lambda:c)
            self.assertTrue(r["frontend_deployment"]["adopted_active"])
            self.assertFalse(any(call[0]=="POST" and call[1]=="/deploy" for call in c.calls))
        for status, expected in [("failed","failed"),("cancelled-by-user","cancelled-by-user"),("bogus","deployment_status_unverified")]:
            with self.subTest(status=status):
                c=self.deploy_client(**{"/deployments/front-dep-1":{"deployment_uuid":"front-dep-1","resource_uuid":"front-1","application_name":"ledger-frontend-dev","status":status}})
                with mock.patch.dict(os.environ,self.gate_env(),clear=False), mock.patch.object(pp,"run_backend_smoke",return_value=pp.smoke_check_result("ledger","passed",http_status=200,body_verified=True,attempts=1)):
                    r=pp.provision(self.deploy_args(), client_factory=lambda:c)
                self.assertEqual(r["frontend_deployment"]["status"], expected)

    def test_stage8_frontend_smoke_html_root_and_redirect_guards(self):
        class Resp:
            def __init__(self, status=200, ctype="text/html", body=b'<!doctype html><html><body><div id="root"></div></body></html>', url="https://dev.ledger.goodmorning.mx/"):
                self.status=status; self.headers={"Content-Type":ctype}; self._body=body; self.url=url
            def __enter__(self): return self
            def __exit__(self,*a): return False
            def getcode(self): return self.status
            def read(self,n=-1): return self._body
        cases=[(Resp(), True), (Resp(status=500), False), (Resp(ctype="application/json"), False), (Resp(body=b""), False), (Resp(body=b"<div id='root'></div>"), False), (Resp(body=b"<html></html>"), False), (Resp(body=b"<html><body><div id='other'></div></body></html>"), False), (Resp(body=b"<html><body><div data-id='root'></div></body></html>"), False), (Resp(body=b"<html><body>" + b"x"*(1024*1024+1) + b"<div id='root'></div></body></html>"), False), (Resp(url="https://evil.example/"), False), (Resp(url="http://dev.ledger.goodmorning.mx/"), False), (Resp(url="https://dev.ledger.goodmorning.mx:80/"), False), (Resp(url="https://dev.ledger.goodmorning.mx/path"), False), (Resp(url="https://dev.ledger.goodmorning.mx/?q=1"), False)]
        for resp, ok in cases:
            with self.subTest(url=resp.url,status=resp.status,ctype=resp.headers["Content-Type"]):
                tick = {"v": 0}
                def clock():
                    tick["v"] += 0.001
                    return tick["v"]
                r=pp.run_frontend_smoke(args(), opener=lambda req, timeout: resp, sleeper=lambda s: None, clock=clock)
                self.assertEqual(r["status"] == "passed", ok)
                if ok:
                    self.assertTrue(r["html_verified"])
                    self.assertTrue(r["root_element_verified"])

    def test_stage8_frontend_ambiguous_2xx_uses_recovery_once(self):
        ambiguous_shapes = [
            ("missing_deployments", {}),
            ("empty_deployments", {"deployments": []}),
            ("two_deployments", {"deployments": [{"resource_uuid":"front-1","deployment_uuid":"a"}, {"resource_uuid":"front-1","deployment_uuid":"b"}]}),
            ("backend_resource_uuid", {"deployments": [{"resource_uuid":"api-1","deployment_uuid":"dep-x"}]}),
            ("missing_deployment_uuid", {"deployments": [{"resource_uuid":"front-1"}]}),
        ]
        for name, body in ambiguous_shapes:
            with self.subTest(name=name):
                c=self.deploy_client(**{
                    "POST frontend /deploy": pp.Response(200, body, {}),
                    "/deployments/applications/front-1?skip=0&take=10": [
                        ("GET", {"deployments": [], "count": 0}),
                        ("GET", {"deployments": [{"deployment_uuid":"recovered-front","resource_uuid":"front-1","status":"queued"}], "count": 1}),
                    ],
                    "/deployments/recovered-front": {"deployment_uuid":"recovered-front","resource_uuid":"front-1","application_name":"ledger-frontend-dev","status":"finished"},
                })
                with mock.patch.dict(os.environ,self.gate_env(),clear=False), mock.patch.object(pp,"run_backend_smoke",return_value=pp.smoke_check_result("ledger","passed",http_status=200,body_verified=True,attempts=1)), mock.patch.object(pp,"run_frontend_smoke",return_value=pp.frontend_smoke_check_result("ledger","passed",http_status=200,html_verified=True,root_element_verified=True,attempts=1)):
                    r=pp.provision(self.deploy_args(), client_factory=lambda:c)
                posts=[call for call in c.calls if call[0]=="POST" and call[1]=="/deploy"]
                self.assertEqual(len(posts), 1)
                self.assertEqual(r["frontend_deployment"]["status"], "finished")
                self.assertTrue(r["frontend_deployment"]["recovered_after_unknown"])
                request_actions=[a for a in r["actions"] if a["resource"]=="frontend_deployment" and a["action"]=="request"]
                self.assertEqual(len(request_actions), 1)
                self.assertTrue(request_actions[0]["write_performed"])
                self.assertTrue(request_actions[0]["write_outcome_unknown"])
                self.assertTrue(any(a["resource"]=="frontend_deployment" and a["action"]=="recover_unknown" for a in r["actions"]))

    def test_stage8_frontend_ambiguous_2xx_recovery_fail_closed(self):
        recovery_cases = [
            ("zero", {"deployments": [], "count": 0}, "frontend_deployment_outcome_unknown"),
            ("many", {"deployments": [{"deployment_uuid":"a","resource_uuid":"front-1"},{"deployment_uuid":"b","resource_uuid":"front-1"}], "count": 2}, "ambiguous_frontend_deployment_recovery"),
            ("invalid", {"count": 0}, "frontend_deployment_outcome_unknown"),
        ]
        for name, recovery, expected in recovery_cases:
            with self.subTest(name=name):
                c=self.deploy_client(**{
                    "POST frontend /deploy": pp.Response(200, {}, {}),
                    "/deployments/applications/front-1?skip=0&take=10": [("GET", {"deployments": [], "count": 0}), ("GET", recovery)],
                })
                with mock.patch.dict(os.environ,self.gate_env(),clear=False), mock.patch.object(pp,"run_backend_smoke",return_value=pp.smoke_check_result("ledger","passed",http_status=200,body_verified=True,attempts=1)):
                    r=pp.provision(self.deploy_args(), client_factory=lambda:c)
                self.assertEqual(r["frontend_deployment"]["status"], expected)
                self.assertTrue(r["frontend_deployment"]["write_outcome_unknown"])
                self.assertEqual(len([call for call in c.calls if call[0]=="POST" and call[1]=="/deploy"]), 1)
                self.assertFalse(any("cancel" in call[1] or "rollback" in call[1] for call in c.calls if len(call) > 1))

    def test_stage8_frontend_env_observed_exactness_and_values(self):
        base = self.frontend_envs()[0]
        cases = [
            ("only", [base], True),
            ("database", [base, {"key":"DATABASE_URL","value":"secret","is_preview":False}], False),
            ("app_env", [base, {"key":"APP_ENV","value":"dev","is_preview":False}], False),
            ("arbitrary", [base, {"key":"SECRET","value":"hidden","is_preview":False}], False),
            ("duplicate", [base, base], False),
            ("absent", [], False),
            ("wrong_value", [{**base,"value":"https://api.dev.other.goodmorning.mx"}], False),
            ("ported", [{**base,"value":"https://api.dev.ledger.goodmorning.mx:8000"}], False),
            ("path_query", [{**base,"value":"https://api.dev.ledger.goodmorning.mx/health?x=1"}], False),
            ("bad_flags", [{**base,"is_runtime":True}], False),
        ]
        for name, envs, ok in cases:
            with self.subTest(name=name):
                c=self.deploy_client(**{"/applications/front-1/envs": envs})
                frontend_env=pp.reconcile_frontend_environment(c,args(),{"status":"exists","uuid":"front-1"},{"status":"exists","uuid":"api-1"},pp.ExecutionMode("dry_run","ledger",None,False),[])
                observed=frontend_env["observed"]
                self.assertNotIn("value_of_extra_key", json.dumps(observed).lower())
                bflags={"is_runtime":True,"is_buildtime":False,"is_preview":False,"is_literal":True,"is_multiline":False}
                backend_env={"status":"planned","variables":[{"key":k,"status":"exists","value_verified":True,"desired_flags":bflags} for k in ("DATABASE_URL","APP_ENV","CORS_ORIGINS")]}
                preflight_ok, blockers=pp.frontend_deployment_preflight(args(), {"name":"ledger-postgres-dev","status":"exists"}, {"status":"healthy","internal_db_url_available":True,"database_url_driver_ready":True}, {"name":"ledger-api-dev","status":"exists","uuid":"api-1"}, backend_env, {"name":"ledger-frontend-dev","status":"exists","uuid":"front-1"}, frontend_env, {"healthy":True,"verified":True}, {"body_verified":True}, [], require_live=False)
                self.assertEqual(preflight_ok, ok, blockers)

    def test_stage8_frontend_env_observed_after_create_and_patch(self):
        desired=self.frontend_envs()[0]
        with self.subTest("create"):
            c=self.deploy_client(**{"/applications/front-1/envs": []})
            mode=pp.ExecutionMode(name="apply", product="ledger", confirm_product="ledger", allow_resource_writes=True, allow_frontend_application_writes=True, confirm_frontend_application="ledger-frontend-dev", allow_frontend_env_writes=True, confirm_frontend_environment="ledger-frontend-dev")
            r=pp.reconcile_frontend_environment(c,args(),{"status":"exists","uuid":"front-1"},{"status":"exists","uuid":"api-1"},mode,[])
            self.assertEqual(r["observed"]["observed_variable_name_counts"], {"VITE_API_URL": 1})
            self.assertTrue(r["observed"]["vite_api_url_value_matches"])
        with self.subTest("patch"):
            c=self.deploy_client(**{"/applications/front-1/envs": [{**desired,"value":"https://api.dev.other.goodmorning.mx"}]})
            mode=pp.ExecutionMode(name="apply", product="ledger", confirm_product="ledger", allow_resource_writes=True, allow_frontend_application_writes=True, confirm_frontend_application="ledger-frontend-dev", allow_frontend_env_writes=True, confirm_frontend_environment="ledger-frontend-dev")
            r=pp.reconcile_frontend_environment(c,args(),{"status":"exists","uuid":"front-1"},{"status":"exists","uuid":"api-1"},mode,[])
            self.assertEqual(r["observed"]["observed_variable_name_counts"], {"VITE_API_URL": 1})
            self.assertTrue(r["observed"]["vite_api_url_value_matches"])

    def test_stage8_dry_run_frontend_plans_live_verification_without_smoke(self):
        c=self.deploy_client()
        with mock.patch.object(pp,"run_backend_smoke") as backend_smoke, mock.patch.object(pp,"run_frontend_smoke") as frontend_smoke:
            r=pp.provision(args(deploy_frontend=True), client_factory=lambda:c)
        self.assertEqual(r["status"], "dry_run")
        self.assertIn(r["frontend_deployment"]["status"], {"would_deploy", "would_verify_backend_then_deploy"})
        self.assertFalse(any(call[0] in {"POST","PATCH"} for call in c.calls))
        backend_smoke.assert_not_called(); frontend_smoke.assert_not_called()
        c_bad=self.deploy_client(**{"/applications/front-1/envs": []})
        r_bad=pp.provision(args(deploy_frontend=True), client_factory=lambda:c_bad)
        self.assertEqual(r_bad["status"], "dry_run")
        self.assertEqual(r_bad["frontend_deployment"]["status"], "blocked_by_preconditions")
        self.assertFalse(any(call[0] in {"POST","PATCH"} for call in c_bad.calls))

    def test_stage8_incomplete_gates_do_not_run_live_smoke_or_deploy(self):
        gate_cases = [
            ("missing_deployments_env", {"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_FRONTEND_DEPLOYMENT":"true"}, self.deploy_args()),
            ("missing_frontend_env", {"COOLIFY_ALLOW_RESOURCE_WRITES":"true","COOLIFY_ALLOW_DEPLOYMENTS":"true"}, self.deploy_args()),
            ("wrong_confirm", self.gate_env(), self.deploy_args(confirm_frontend_deployment="wrong-frontend-dev")),
            ("no_deploy_flag", self.gate_env(), self.deploy_args(deploy_frontend=False, confirm_frontend_deployment="ledger-frontend-dev")),
            ("missing_base_write_gate", {"COOLIFY_ALLOW_DEPLOYMENTS":"true","COOLIFY_ALLOW_FRONTEND_DEPLOYMENT":"true"}, self.deploy_args()),
        ]
        for name, env, a in gate_cases:
            with self.subTest(name=name):
                c=self.deploy_client()
                with mock.patch.dict(os.environ,env,clear=True), mock.patch.object(pp,"run_backend_smoke") as backend_smoke, mock.patch.object(pp,"run_frontend_smoke") as frontend_smoke:
                    if name == "missing_base_write_gate":
                        with self.assertRaises(pp.ProvisioningError):
                            pp.provision(a, client_factory=lambda:c)
                    else:
                        r=pp.provision(a, client_factory=lambda:c)
                        if getattr(a,"deploy_frontend",False):
                            self.assertEqual(r["frontend_deployment"]["status"], "gates_incomplete")
                        else:
                            self.assertEqual(r["frontend_deployment"]["status"], "not_requested")
                backend_smoke.assert_not_called(); frontend_smoke.assert_not_called()
                self.assertFalse(any(call[0]=="POST" and call[1]=="/deploy" for call in c.calls))

class Stage9PostgresqlDependencyPlanningTests(unittest.TestCase):
    def new_product_client(self, **overrides):
        payloads = {
            "/projects": [{"uuid": "project-1", "name": "ledger"}],
            "/projects/project-1": {"uuid": "project-1", "name": "ledger", "environments": [{"uuid": "env-1", "name": "dev"}]},
            "/servers": [{"uuid": "server-1", "name": "development", "destinations": [{"uuid":"bk8cw0gkswgo04o0s88sk0kg", "name":"docker"}]}],
            "/github-apps": {"github_apps": [{"uuid": "app-1", "name": "goodmorning-mx-coolify"}]},
            "/applications": [],
            "/databases": [],
        }
        payloads.update(overrides)
        return FakeClient(payloads)

    def test_stage9_new_product_dry_run_plans_postgresql_backend_frontend_get_only(self):
        client = self.new_product_client()
        result = pp.provision(args(), client_factory=lambda: client)
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["postgresql"]["status"], "would_create")
        self.assertEqual(result["postgresql_readiness"]["status"], "would_start")
        self.assertEqual(result["postgresql_readiness"]["reason"], "postgresql_creation_planned")
        self.assertFalse(result["postgresql_readiness"]["request_attempted"])
        self.assertFalse(result["postgresql_readiness"]["write_performed"])
        self.assertFalse(result["postgresql_readiness"]["internal_db_url_available"])
        self.assertFalse(result["postgresql_readiness"]["database_url_driver_ready"])
        self.assertEqual(result["backend_application"]["status"], "would_create")
        self.assertEqual(result["backend_application"]["reason"], "postgresql_dependency_planned")
        self.assertEqual(result["backend_application"]["create_payload_preview"]["destination_uuid"], "bk8cw0gkswgo04o0s88sk0kg")
        self.assertEqual(result["backend_environment"], {"status": "planned", "variables": [], "blockers": []})
        self.assertEqual(result["frontend_application"]["status"], "planned_after_backend")
        self.assertEqual(result["frontend_application"]["reason"], "backend_dependency_planned")
        frontend_resource = next(resource for resource in result["resources"] if resource["name"] == "ledger-frontend-dev")
        self.assertEqual(frontend_resource["status"], "planned_after_backend")
        self.assertEqual(frontend_resource["reason"], "backend_dependency_planned")
        self.assertEqual(frontend_resource["provisioning"]["reason"], "backend_dependency_planned")
        self.assertFalse(any(call[0] in {"POST", "PATCH"} for call in client.calls))
        self.assertFalse(any(call[1] == "/databases/${postgres.uuid}" for call in client.calls))
        self.assertFalse(any(call[1].endswith("/start") for call in client.calls))

    def test_stage9_postgresql_unsafe_plan_statuses_block_backend(self):
        db = {"uuid":"db-1","name":"ledger-postgres-dev","type":"postgresql","project_uuid":"project-1","environment_uuid":"env-1","server_uuid":"server-1","version":"16","is_public":False,"status":"running:unhealthy"}
        unsafe_cases = ([db, {**db, "uuid":"db-2"}], [{**db, "version":"15"}], [{**db, "project_uuid":"other"}], [db])
        for databases in unsafe_cases:
            client = self.new_product_client(**{"/databases": databases, "/databases/db-1": db})
            if len(databases) > 1:
                with self.assertRaises(pp.ProvisioningError):
                    pp.provision(args(), client_factory=lambda: client)
                continue
            result = pp.provision(args(), client_factory=lambda: client)
            self.assertNotEqual(result["backend_application"].get("status"), "would_create")
            self.assertTrue(result["blockers"])
