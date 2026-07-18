import argparse
import io
import json
import os
from pathlib import Path
import unittest
import urllib.error
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import scripts.provision_coolify_development as pc


def args(**kw):
    base = dict(
        product=pc.DEFAULT_PRODUCT,
        repository=pc.DEFAULT_REPOSITORY,
        branch=pc.DEFAULT_BRANCH,
        compose=pc.DEFAULT_COMPOSE,
        domain=pc.DEFAULT_DOMAIN,
        environment=pc.DEFAULT_ENVIRONMENT,
        dry_run=True,
        deploy=False,
        coolify_github_app_name=pc.DEFAULT_COOLIFY_GITHUB_APP_NAME,
        server=pc.DEFAULT_SERVER_NAME,
        destination_uuid="bk8cw0gkswgo04o0s88sk0kg",
    )
    base.update(kw)
    return argparse.Namespace(**base)


class CoolifyDevelopmentProvisioningTests(unittest.TestCase):
    def api_side_effect(self, calls, existing=None):
        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs.get("body")))
            if path == "/projects":
                return pc.Response(
                    200, [{"uuid": "project-1", "name": "factory-smoke-test"}], {}
                )
            if path == "/projects/project-1":
                return pc.Response(
                    200,
                    {
                        "uuid": "project-1",
                        "name": "factory-smoke-test",
                        "environments": [{"name": "dev", "uuid": "env-dev"}],
                    },
                    {},
                )
            if path == "/projects/project-1/environments":
                return pc.Response(200, [{"name": "dev", "uuid": "env-dev"}], {})
            if path == "/projects/new-project":
                return pc.Response(
                    200,
                    {
                        "uuid": "new-project",
                        "name": pc.DEFAULT_PRODUCT,
                        "environments": [{"name": "dev", "uuid": "env-dev"}],
                    },
                    {},
                )
            if path == "/servers":
                return pc.Response(
                    200, [{"uuid": "server-1", "name": "development"}], {}
                )
            if path == "/github-apps":
                return pc.Response(
                    200,
                    {
                        "github_apps": [
                            {
                                "uuid": "github-app-7",
                                "name": pc.DEFAULT_COOLIFY_GITHUB_APP_NAME,
                                "html_url": "https://github.com",
                            }
                        ]
                    },
                    {},
                )
            if path == "/applications":
                return pc.Response(200, [] if existing is None else [existing], {})
            if path in ("/applications/app-1/envs", "/applications/existing-app/envs"):
                if method == "GET":
                    return pc.Response(200, [], {})
                self.assertIn(
                    kwargs["body"],
                    [
                        {"key": key, "value": value}
                        for key, value in pc.desired_environment_variables(
                            args()
                        ).items()
                    ],
                )
                return pc.Response(201, kwargs["body"], {})
            if path == "/applications/private-github-app":
                self.assertFalse(kwargs["body"]["instant_deploy"])
                self.assertTrue(kwargs["body"]["is_auto_deploy_enabled"])
                self.assertFalse(kwargs["body"]["autogenerate_domain"])
                self.assertEqual(kwargs["body"]["environment_uuid"], "env-dev")
                self.assertEqual(
                    kwargs["body"]["destination_uuid"],
                    "bk8cw0gkswgo04o0s88sk0kg",
                )
                self.assertEqual(
                    kwargs["body"]["docker_compose_domains"],
                    [
                        {
                            "name": "frontend",
                            "domain": "https://dev.factory-smoke-test.goodmorning.mx",
                        },
                        {
                            "name": "backend",
                            "domain": "https://api.dev.factory-smoke-test.goodmorning.mx:8000",
                        },
                    ],
                )
                return pc.Response(
                    201, {"uuid": "app-1", "name": "factory-smoke-test-dev"}, {}
                )
            if path == "/applications/app-1" or path == "/applications/existing-app":
                if method == "GET":
                    return pc.Response(
                        200,
                        {
                            "uuid": path.rsplit("/", 1)[-1],
                            "docker_compose_raw": "services: {}",
                        },
                        {},
                    )
                self.assertEqual(method, "PATCH")
                self.assertIn(
                    kwargs["body"],
                    [
                        pc.source_patch_payload(
                            args(),
                            {"uuid": "github-app-7", "name": pc.DEFAULT_COOLIFY_GITHUB_APP_NAME},
                        ),
                        pc.domain_patch_payload(args()),
                    ],
                )
                return pc.Response(200, {"uuid": path.rsplit("/", 1)[-1]}, {})
            raise AssertionError((method, path))

        return fake_request

    def test_dry_run_discovers_resources_without_creating(self):
        calls = []
        with mock.patch.object(pc, "request", side_effect=self.api_side_effect(calls)):
            result = pc.provision(args())
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(
            result["would_create"]["git_repository"],
            f"https://github.com/{pc.DEFAULT_REPOSITORY}",
        )
        self.assertEqual(
            result["would_create"]["docker_compose_location"], pc.DEFAULT_COMPOSE
        )
        self.assertEqual(
            result["would_create"]["docker_compose_domains"],
            [
                {
                    "name": "frontend",
                    "domain": "https://dev.factory-smoke-test.goodmorning.mx",
                },
                {
                    "name": "backend",
                    "domain": "https://api.dev.factory-smoke-test.goodmorning.mx:8000",
                },
            ],
        )
        self.assertNotIn("would_patch", result)
        self.assertIn(("GET", "/github-apps"), [(m, p) for m, p, _ in calls])
        self.assertNotIn(("GET", "/sources/github"), [(m, p) for m, p, _ in calls])
        self.assertNotIn(
            ("POST", "/applications/private-github-app"), [(m, p) for m, p, _ in calls]
        )
        self.assertNotIn(
            ("PATCH", "/applications/app-1"), [(m, p) for m, p, _ in calls]
        )

    def test_docker_compose_domains_are_derived_from_development_domain(self):
        self.assertEqual(
            pc.docker_compose_domains("https://dev.example.goodmorning.mx"),
            [
                {
                    "name": "frontend",
                    "domain": "https://dev.example.goodmorning.mx",
                },
                {
                    "name": "backend",
                    "domain": "https://api.dev.example.goodmorning.mx:8000",
                },
            ],
        )
        for compose_domain in pc.docker_compose_domains(
            "https://dev.example.goodmorning.mx"
        ):
            self.assertEqual(set(compose_domain), {"name", "domain"})

    def test_docker_compose_domains_normalize_unported_development_domain(self):
        self.assertEqual(
            pc.docker_compose_domains("dev.example.goodmorning.mx"),
            [
                {
                    "name": "frontend",
                    "domain": "https://dev.example.goodmorning.mx",
                },
                {
                    "name": "backend",
                    "domain": "https://api.dev.example.goodmorning.mx:8000",
                },
            ],
        )

    def test_internal_routing_declares_service_ports_without_public_domains(self):
        self.assertEqual(
            pc.internal_routing(),
            [
                {
                    "service": "frontend",
                    "internal_port": 80,
                    "mechanism": "coolify_compose_domain",
                },
                {
                    "service": "backend",
                    "internal_port": 8000,
                    "mechanism": "coolify_compose_domain_port_hint",
                },
            ],
        )

    def test_desired_environment_variables_are_derived_from_product_slug(self):
        self.assertEqual(
            pc.desired_environment_variables(args(product="ledger")),
            {
                "VITE_API_URL": "https://api.dev.ledger.goodmorning.mx",
                "NEXT_PUBLIC_API_URL": "https://api.dev.ledger.goodmorning.mx",
                "CORS_ORIGINS": "https://dev.ledger.goodmorning.mx",
                "APP_ENV": "dev",
                "POSTGRES_DB": "goodmorning",
                "POSTGRES_USER": "goodmorning",
            },
        )

    def test_configure_application_environment_variables_updates_existing_and_creates_missing(
        self,
    ):
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs.get("body")))
            if path == "/applications/app-1/envs" and method == "GET":
                return pc.Response(
                    200,
                    [{"uuid": "env-var-1", "key": "APP_ENV", "value": "old"}],
                    {},
                )
            return pc.Response(200, kwargs.get("body"), {})

        with mock.patch.object(pc, "request", side_effect=fake_request):
            result = pc.configure_application_environment_variables(
                "app-1", args(product="ledger")
            )

        self.assertEqual(
            result,
            pc.environment_variable_summary(
                pc.desired_environment_variables(args(product="ledger"))
            ),
        )
        self.assertNotIn("POSTGRES_PASSWORD", {item["key"] for item in result})
        written_keys = [call[2]["key"] for call in calls[1:]]
        self.assertEqual(
            written_keys, list(pc.desired_environment_variables(args(product="ledger")))
        )
        self.assertIn(
            ("PATCH", "/applications/app-1/envs", {"key": "APP_ENV", "value": "dev"}),
            calls,
        )

    def test_configure_application_environment_variables_patches_existing_name_after_get(
        self,
    ):
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs.get("body")))
            if path == "/applications/app-1/envs" and method == "GET":
                return pc.Response(
                    200,
                    {
                        "data": [
                            {
                                "uuid": "real-vite-uuid",
                                "name": "VITE_API_URL",
                                "value": "old",
                            },
                            {
                                "uuid": "real-cors-uuid",
                                "name": "CORS_ORIGINS",
                                "value": "old",
                            },
                        ]
                    },
                    {},
                )
            return pc.Response(
                200 if method == "PATCH" else 201, kwargs.get("body"), {}
            )

        with mock.patch.object(pc, "request", side_effect=fake_request):
            pc.configure_application_environment_variables(
                "app-1", args(product="ledger")
            )

        methods_by_key = {call[2]["key"]: call[0] for call in calls[1:]}
        self.assertEqual(methods_by_key["VITE_API_URL"], "PATCH")
        self.assertEqual(methods_by_key["CORS_ORIGINS"], "PATCH")
        self.assertEqual(methods_by_key["APP_ENV"], "POST")
        self.assertNotIn("POSTGRES_PASSWORD", methods_by_key)

    def test_configure_application_environment_variables_handles_post_409_by_reloading_and_patching(
        self,
    ):
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append(
                (method, path, kwargs.get("body"), kwargs.get("allowed_statuses"))
            )
            if path == "/applications/app-1/envs" and method == "GET":
                if len([call for call in calls if call[:2] == ("GET", path)]) == 1:
                    return pc.Response(200, [], {})
                return pc.Response(
                    200,
                    [
                        {
                            "uuid": "vite-created-by-concurrent-run",
                            "key": "VITE_API_URL",
                            "value": "old",
                        }
                    ],
                    {},
                )
            if path == "/applications/app-1/envs" and method == "POST":
                if kwargs["body"]["key"] == "VITE_API_URL":
                    return pc.Response(
                        409,
                        {
                            "message": "Environment variable already exists. Use PATCH request to update it."
                        },
                        {},
                    )
                return pc.Response(201, kwargs.get("body"), {})
            if path == "/applications/app-1/envs" and method == "PATCH":
                return pc.Response(200, kwargs.get("body"), {})
            raise AssertionError((method, path))

        with mock.patch.object(pc, "request", side_effect=fake_request):
            pc.configure_application_environment_variables(
                "app-1", args(product="ledger")
            )

        self.assertEqual(calls[0], ("GET", "/applications/app-1/envs", None, None))
        self.assertIn(
            (
                "PATCH",
                "/applications/app-1/envs",
                {
                    "key": "VITE_API_URL",
                    "value": "https://api.dev.ledger.goodmorning.mx",
                },
                None,
            ),
            calls,
        )
        posted_keys = [call[2]["key"] for call in calls if call[0] == "POST"]
        self.assertNotIn("POSTGRES_PASSWORD", posted_keys)

    def test_configure_application_environment_variables_refuses_duplicate_without_identifier(
        self,
    ):
        def fake_request(method, path, **kwargs):
            if path == "/applications/app-1/envs" and method == "GET":
                return pc.Response(200, [{"key": "APP_ENV", "value": "old"}], {})
            if path == "/applications/app-1/envs" and method == "POST":
                return pc.Response(201, kwargs.get("body"), {})
            raise AssertionError((method, path))

        with mock.patch.object(pc, "request", side_effect=fake_request):
            with self.assertRaisesRegex(pc.ProvisioningError, "refusing to update"):
                pc.configure_application_environment_variables(
                    "app-1", args(product="ledger")
                )

    def test_development_health_verifies_frontend_root_and_backend_health(self):
        with mock.patch.object(
            pc, "verify_url", side_effect=lambda url: {"url": url}
        ) as verify:
            result = pc.verify_development_health(
                args(domain="https://dev.example.goodmorning.mx")
            )

        self.assertEqual(
            result,
            [
                {"url": "https://dev.example.goodmorning.mx/"},
                {"url": "https://api.dev.example.goodmorning.mx/health"},
            ],
        )
        self.assertEqual(
            [call.args[0] for call in verify.call_args_list],
            [
                "https://dev.example.goodmorning.mx/",
                "https://api.dev.example.goodmorning.mx/health",
            ],
        )

    def test_verify_url_retries_without_returning_response_bodies(self):
        attempts = []

        def flaky(url):
            attempts.append(url)
            if len(attempts) == 1:
                raise pc.ProvisioningError("Health check failed with timeout error")
            return {"url": url, "status": 200, "ok": True}

        with mock.patch.object(
            pc, "verify_url_once", side_effect=flaky
        ), mock.patch.object(pc.time, "sleep") as sleep:
            result = pc.verify_url("https://dev.example.goodmorning.mx/")

        self.assertEqual(result["attempts"], 2)
        self.assertNotIn("body", result)
        sleep.assert_called_once_with(pc.HEALTH_RETRY_SLEEP_SECONDS)

    def test_classify_url_error_differentiates_dns_tls_and_timeout(self):
        self.assertEqual(pc.classify_url_error(TimeoutError("timed out")), "timeout")
        self.assertEqual(
            pc.classify_url_error(urllib.error.URLError("certificate verify failed")),
            "tls",
        )
        self.assertEqual(
            pc.classify_url_error(
                urllib.error.URLError("temporary failure in name resolution")
            ),
            "dns",
        )

    def test_github_apps_parser_accepts_current_nested_response_shape(self):
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs.get("body")))
            if path == "/projects":
                return pc.Response(
                    200,
                    {"data": [{"uuid": "project-1", "name": "factory-smoke-test"}]},
                    {},
                )
            if path == "/projects/project-1":
                return pc.Response(
                    200,
                    {
                        "data": {
                            "uuid": "project-1",
                            "name": "factory-smoke-test",
                            "environments": [{"name": "dev", "uuid": "env-dev"}],
                        }
                    },
                    {},
                )
            if path == "/servers":
                return pc.Response(
                    200, {"servers": [{"uuid": "server-1", "name": "development"}]}, {}
                )
            if path == "/github-apps":
                return pc.Response(
                    200,
                    {
                        "github_apps": [
                            {
                                "uuid": "github-app-current",
                                "name": pc.DEFAULT_COOLIFY_GITHUB_APP_NAME,
                                "html_url": "https://github.com/goodmorning-mx",
                                "api_url": "https://api.github.com",
                            }
                        ]
                    },
                    {},
                )
            if path == "/applications":
                return pc.Response(200, {"applications": []}, {})
            raise AssertionError((method, path))

        with mock.patch.object(pc, "request", side_effect=fake_request):
            result = pc.provision(args())

        self.assertEqual(result["status"], "dry_run")
        self.assertTrue(result["dry_run"])
        self.assertEqual(
            result["github_app"],
            {"uuid": "github-app-current", "name": pc.DEFAULT_COOLIFY_GITHUB_APP_NAME},
        )
        self.assertEqual(
            result["would_create"]["github_app_uuid"], "github-app-current"
        )
        self.assertIn(("GET", "/github-apps"), [(m, p) for m, p, _ in calls])
        self.assertNotIn(("GET", "/sources/github"), [(m, p) for m, p, _ in calls])
        self.assertNotIn(
            ("POST", "/applications/private-github-app"), [(m, p) for m, p, _ in calls]
        )

    def test_github_app_uuid_is_required_instead_of_numeric_id(self):
        with self.assertRaisesRegex(pc.ProvisioningError, "numeric id"):
            pc.creation_payload(
                args(),
                {"uuid": "project-1"},
                {"uuid": "env-dev", "name": "dev"},
                {"uuid": "server-1"},
                {"id": 123, "name": pc.DEFAULT_COOLIFY_GITHUB_APP_NAME},
            )

    def test_github_app_selection_requires_exact_name(self):
        with self.assertRaisesRegex(pc.ProvisioningError, "was not found"):
            pc.discover_github_app(
                [{"uuid": "github-app-7", "name": "goodmorning-mx-coolify-extra"}],
                pc.DEFAULT_COOLIFY_GITHUB_APP_NAME,
                pc.DEFAULT_REPOSITORY,
            )

    def test_project_selection_is_exact_and_rejects_duplicates(self):
        self.assertIsNone(
            pc.discover_project(
                [{"uuid": "project-old", "name": "factory-smoke-test-old"}],
                "factory-smoke-test",
            )
        )
        with self.assertRaisesRegex(pc.ProvisioningError, "duplicated"):
            pc.discover_project(
                [
                    {"uuid": "project-1", "name": "factory-smoke-test"},
                    {"uuid": "project-2", "name": "factory-smoke-test"},
                ],
                "factory-smoke-test",
            )

    def test_server_selection_prefers_exact_name_and_allows_unambiguous_fallback(self):
        servers = [
            {"uuid": "server-1", "name": "development"},
            {"uuid": "server-2", "name": "development-extra"},
        ]
        self.assertEqual(
            pc.discover_server(servers, "development")["uuid"], "server-1"
        )
        self.assertEqual(
            pc.discover_server([{"uuid": "server-3", "name": "main-server"}], "development")["uuid"],
            "server-3",
        )
        with self.assertRaisesRegex(pc.ProvisioningError, "unambiguous"):
            pc.discover_server(servers, "dev")
        with self.assertRaisesRegex(pc.ProvisioningError, "unambiguous"):
            pc.discover_server(
                [{"uuid": "builder", "name": "builder", "is_build_server": True}],
                "development",
            )

    def test_destination_identifier_is_required_before_api_calls(self):
        with self.assertRaisesRegex(pc.ProvisioningError, "destination identifier"):
            pc.validate_inputs(args(destination_uuid=None))

    def test_github_app_selection_rejects_duplicate_exact_names(self):
        with self.assertRaisesRegex(pc.ProvisioningError, "duplicated"):
            pc.discover_github_app(
                [
                    {
                        "uuid": "github-app-1",
                        "name": pc.DEFAULT_COOLIFY_GITHUB_APP_NAME,
                    },
                    {
                        "uuid": "github-app-2",
                        "name": pc.DEFAULT_COOLIFY_GITHUB_APP_NAME,
                    },
                ],
                pc.DEFAULT_COOLIFY_GITHUB_APP_NAME,
                pc.DEFAULT_REPOSITORY,
            )

    def test_github_app_selection_rejects_public_github_for_private_repository(self):
        with self.assertRaisesRegex(
            pc.ProvisioningError, "Public GitHub cannot be used"
        ):
            pc.discover_github_app(
                [{"uuid": "public-github", "name": "Public GitHub"}],
                "Public GitHub",
                pc.DEFAULT_REPOSITORY,
            )

    def test_missing_project_dry_run_plans_project_creation_and_default_environment_reuse(
        self,
    ):
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs.get("body")))
            if path == "/projects":
                return pc.Response(200, [], {})
            if path == "/projects/new-project":
                return pc.Response(
                    200,
                    {
                        "uuid": "new-project",
                        "name": pc.DEFAULT_PRODUCT,
                        "environments": [{"name": "dev", "uuid": "env-dev"}],
                    },
                    {},
                )
            if path == "/servers":
                return pc.Response(
                    200, [{"uuid": "server-1", "name": "development"}], {}
                )
            if path == "/github-apps":
                return pc.Response(
                    200,
                    {
                        "github_apps": [
                            {
                                "uuid": "github-app-7",
                                "name": pc.DEFAULT_COOLIFY_GITHUB_APP_NAME,
                                "html_url": "https://github.com",
                            }
                        ]
                    },
                    {},
                )
            if path == "/applications":
                return pc.Response(200, [], {})
            raise AssertionError((method, path))

        with mock.patch.object(pc, "request", side_effect=fake_request):
            result = pc.provision(args())

        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(
            result["project"],
            {"uuid": None, "name": pc.DEFAULT_PRODUCT, "created": False},
        )
        self.assertEqual(
            result["environment"],
            {"name": "dev", "coolify_name": "dev", "created": False},
        )
        self.assertEqual(result["would_create_project"], {"name": pc.DEFAULT_PRODUCT})
        self.assertNotIn("would_create_environment", result)
        self.assertNotIn(("POST", "/projects"), [(m, p) for m, p, _ in calls])
        self.assertNotIn(
            ("POST", "/projects/new-project/environment"), [(m, p) for m, p, _ in calls]
        )

    def test_missing_project_real_run_creates_project_and_reuses_default_environment_before_application(
        self,
    ):
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs.get("body")))
            if path == "/projects":
                if method == "GET":
                    return pc.Response(200, [], {})
                if method == "POST":
                    return pc.Response(
                        201, {"uuid": "new-project", "name": pc.DEFAULT_PRODUCT}, {}
                    )
            if path == "/projects/new-project":
                return pc.Response(
                    200,
                    {
                        "uuid": "new-project",
                        "name": pc.DEFAULT_PRODUCT,
                        "environments": [{"name": "dev", "uuid": "env-dev"}],
                    },
                    {},
                )
            if path == "/servers":
                return pc.Response(
                    200, [{"uuid": "server-1", "name": "development"}], {}
                )
            if path == "/github-apps":
                return pc.Response(
                    200,
                    {
                        "github_apps": [
                            {
                                "uuid": "github-app-7",
                                "name": pc.DEFAULT_COOLIFY_GITHUB_APP_NAME,
                                "html_url": "https://github.com",
                            }
                        ]
                    },
                    {},
                )
            if path == "/applications":
                return pc.Response(200, [], {})
            if path == "/applications/private-github-app":
                return pc.Response(
                    201, {"uuid": "app-1", "name": "factory-smoke-test-dev"}, {}
                )
            if path == "/applications/app-1":
                return pc.Response(
                    200,
                    {"uuid": "app-1", "docker_compose_raw": "services: {}"},
                    {},
                )
            if path == "/applications/app-1/envs":
                return pc.Response(
                    200 if method == "GET" else 201,
                    [] if method == "GET" else kwargs.get("body"),
                    {},
                )
            raise AssertionError((method, path))

        with mock.patch.object(
            pc, "request", side_effect=fake_request
        ), mock.patch.object(
            pc,
            "ensure_dev_environment",
            return_value={"uuid": "env-dev", "name": "dev"},
        ) as ensure, mock.patch.object(
            pc, "queue_deployment", return_value={"uuid": "deploy-1", "force": True}
        ) as queue, mock.patch.object(
            pc,
            "wait_for_deployment",
            return_value={"uuid": "deploy-1", "status": "success"},
        ) as wait, mock.patch.object(
            pc, "verify_development_health", return_value=[]
        ) as health:
            result = pc.provision(args(dry_run=False))

        self.assertEqual(result["status"], "created")
        self.assertTrue(result["domain_reconciled"])
        self.assertIn(("GET", "/applications/app-1", None), calls)
        self.assertNotIn(
            ("PATCH", "/applications/app-1"), [(m, p) for m, p, _ in calls]
        )
        self.assertEqual(
            result["project"],
            {"uuid": "new-project", "name": pc.DEFAULT_PRODUCT, "created": True},
        )
        self.assertEqual(
            result["environment"],
            {"name": "dev", "coolify_name": "dev", "created": False},
        )
        self.assertEqual(
            calls.index(("POST", "/projects", {"name": pc.DEFAULT_PRODUCT})), 4
        )
        self.assertIn(("GET", "/projects/new-project", None), calls)
        ensure.assert_called_once_with("new-project")
        queue.assert_not_called()
        wait.assert_not_called()
        health.assert_not_called()
        self.assertNotIn(
            ("POST", "/projects/new-project/environment", {"name": "dev"}),
            calls,
        )

    def test_existing_application_is_idempotent_and_not_duplicated(self):
        calls = []
        existing = {
            "uuid": "existing-app",
            "name": "factory-smoke-test-dev",
            "domains": pc.DEFAULT_DOMAIN,
            "git_repository": pc.DEFAULT_REPOSITORY,
            "git_branch": pc.DEFAULT_BRANCH,
            "docker_compose_location": pc.DEFAULT_COMPOSE,
            "environment_name": "dev",
            "github_app_uuid": "github-app-7",
            "docker_compose_raw": "services: {}",
        }
        with mock.patch.object(
            pc, "request", side_effect=self.api_side_effect(calls, existing=existing)
        ), mock.patch.object(
            pc,
            "queue_deployment",
            return_value={"uuid": "deploy-existing", "force": True},
        ) as queue, mock.patch.object(
            pc,
            "wait_for_deployment",
            return_value={"uuid": "deploy-existing", "status": "success"},
        ) as wait, mock.patch.object(
            pc, "verify_development_health", return_value=[]
        ) as health:
            result = pc.provision(args(dry_run=False))
        self.assertEqual(result["status"], "exists")
        self.assertEqual(result["application"]["uuid"], "existing-app")
        self.assertTrue(result["domain_reconciled"])
        self.assertIn(
            ("PATCH", "/applications/existing-app"), [(m, p) for m, p, _ in calls]
        )
        self.assertNotIn(
            ("POST", "/applications/private-github-app"), [(m, p) for m, p, _ in calls]
        )

    def test_hydrate_application_merges_list_and_detail_fields(self):
        with mock.patch.object(
            pc,
            "request",
            return_value=pc.Response(
                200,
                {"uuid": "app-1", "docker_compose_raw": "services: {}"},
                {},
            ),
        ):
            hydrated = pc.hydrate_application(
                {
                    "uuid": "app-1",
                    "name": "factory-smoke-test-dev",
                    "git_repository": pc.DEFAULT_REPOSITORY,
                }
            )

        self.assertEqual(hydrated["git_repository"], pc.DEFAULT_REPOSITORY)
        self.assertEqual(hydrated["docker_compose_raw"], "services: {}")

    def test_matching_source_without_github_app_is_safely_adoptable(self):
        application = {
            "uuid": "app-1",
            "name": "factory-smoke-test-dev",
            "git_repository": pc.DEFAULT_REPOSITORY,
            "git_branch": pc.DEFAULT_BRANCH,
            "docker_compose_location": pc.DEFAULT_COMPOSE,
            "environment_name": "dev",
        }
        environment = {"uuid": "env-dev", "name": "dev"}
        github_app = {
            "uuid": "github-app-7",
            "name": pc.DEFAULT_COOLIFY_GITHUB_APP_NAME,
        }

        self.assertFalse(
            pc.application_deployment_source_configured(
                application, args(), environment, github_app
            )
        )
        self.assertTrue(
            pc.application_deployment_source_adoptable(
                application, args(), environment, github_app
            )
        )
        self.assertEqual(
            pc.source_patch_payload(args(), github_app)["github_app_uuid"],
            "github-app-7",
        )

    def test_real_creation_posts_once_when_dry_run_false(self):
        calls = []
        with mock.patch.object(
            pc, "request", side_effect=self.api_side_effect(calls)
        ), mock.patch.object(pc, "queue_deployment") as queue, mock.patch.object(
            pc, "wait_for_deployment"
        ) as wait, mock.patch.object(
            pc, "verify_development_health"
        ) as health:
            result = pc.provision(args(dry_run=False))
        self.assertEqual(result["status"], "created")
        self.assertEqual(
            result["deployment"],
            {
                "queued": False,
                "reason": "instant_deploy=false; explicit deployment was not requested",
            },
        )
        queue.assert_not_called()
        wait.assert_not_called()
        health.assert_not_called()
        posts = [
            call
            for call in calls
            if call[0] == "POST" and call[1] == "/applications/private-github-app"
        ]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][2]["git_branch"], "dev")
        self.assertEqual(posts[0][2]["github_app_uuid"], "github-app-7")
        self.assertNotIn("git_source_id", posts[0][2])
        self.assertNotIn("domains", posts[0][2])
        self.assertEqual(
            posts[0][2]["docker_compose_domains"][0]["domain"],
            "https://dev.factory-smoke-test.goodmorning.mx",
        )
        self.assertEqual(
            posts[0][2]["docker_compose_domains"][1]["domain"],
            "https://api.dev.factory-smoke-test.goodmorning.mx:8000",
        )
        self.assertFalse(posts[0][2]["force_domain_override"])
        self.assertTrue(posts[0][2]["is_auto_deploy_enabled"])
        self.assertFalse(posts[0][2]["autogenerate_domain"])
        self.assertEqual(posts[0][2]["environment_uuid"], "env-dev")
        self.assertEqual(
            posts[0][2]["destination_uuid"], "bk8cw0gkswgo04o0s88sk0kg"
        )
        self.assertEqual(posts[0][2]["base_directory"], "/")
        self.assertEqual(posts[0][2]["docker_compose_location"], "/compose.yaml")
        self.assertEqual(posts[0][2]["environment_name"], "dev")
        self.assertIn(("GET", "/applications/app-1"), [(m, p) for m, p, _ in calls])
        self.assertNotIn(
            ("PATCH", "/applications/app-1"), [(m, p) for m, p, _ in calls]
        )

    def test_deploy_true_queues_once_after_domains_and_variables(self):
        calls = []

        def queue(application_uuid):
            calls.append(("QUEUE", application_uuid, None))
            return {"uuid": "deploy-1", "force": True}

        with mock.patch.dict(
            os.environ, {pc.REAL_DEPLOY_ENV: "true"}, clear=True
        ), mock.patch.object(
            pc, "request", side_effect=self.api_side_effect(calls)
        ), mock.patch.object(
            pc, "queue_deployment", side_effect=queue
        ) as queue_mock, mock.patch.object(
            pc,
            "wait_for_deployment",
            return_value={"uuid": "deploy-1", "status": "success"},
        ) as wait, mock.patch.object(
            pc,
            "verify_development_health",
            return_value=[
                {"url": "https://dev.factory-smoke-test.goodmorning.mx/", "ok": True},
                {
                    "url": "https://api.dev.factory-smoke-test.goodmorning.mx/health",
                    "ok": True,
                },
            ],
        ) as health:
            result = pc.provision(args(dry_run=False, deploy=True))

        self.assertTrue(result["deployment"]["queued"])
        queue_mock.assert_called_once_with("app-1")
        wait.assert_called_once_with("deploy-1")
        health.assert_called_once()
        queue_index = calls.index(("QUEUE", "app-1", None))
        self.assertGreater(
            calls.index(("GET", "/applications/app-1", None)),
            queue_index,
        )
        self.assertLess(
            max(
                index
                for index, call in enumerate(calls)
                if call[0] in {"POST", "PATCH"}
                and call[1] == "/applications/app-1/envs"
            ),
            queue_index,
        )

    def test_application_creation_conflict_reloads_and_reuses_verified_application(
        self,
    ):
        calls = []
        existing = {
            "uuid": "existing-app",
            "name": "factory-smoke-test-dev",
            "domains": pc.DEFAULT_DOMAIN,
            "git_repository": pc.DEFAULT_REPOSITORY,
            "git_branch": pc.DEFAULT_BRANCH,
            "docker_compose_location": pc.DEFAULT_COMPOSE,
            "environment_name": "dev",
            "github_app_uuid": "github-app-7",
        }
        application_reads = 0

        def fake_request(method, path, **kwargs):
            nonlocal application_reads
            if path == "/applications" and method == "GET":
                application_reads += 1
                calls.append((method, path, kwargs.get("body")))
                return pc.Response(
                    200, [] if application_reads == 1 else [existing], {}
                )
            if path == "/applications/private-github-app" and method == "POST":
                calls.append((method, path, kwargs.get("body")))
                return pc.Response(409, {"message": "conflict"}, {})
            return self.api_side_effect(calls, existing=existing)(
                method, path, **kwargs
            )

        with mock.patch.object(pc, "request", side_effect=fake_request):
            result = pc.provision(args(dry_run=False))

        self.assertEqual(result["status"], "exists")
        self.assertTrue(result["application_reused_after_conflict"])
        self.assertEqual(result["application"]["uuid"], "existing-app")
        self.assertIn(("GET", "/applications", None), calls)

    def test_existing_application_without_compose_raw_or_source_config_requires_manual_recreation(
        self,
    ):
        calls = []
        existing = {
            "uuid": "existing-app",
            "name": "factory-smoke-test-dev",
            "domains": pc.DEFAULT_DOMAIN,
            "docker_compose_raw": "",
        }
        with mock.patch.object(
            pc, "request", side_effect=self.api_side_effect(calls, existing=existing)
        ):
            result = pc.provision(args(dry_run=False))

        self.assertEqual(result["status"], "manual_review_required")
        self.assertTrue(result["manual_review_required"])
        self.assertIn("repository, branch", result["reason"])
        self.assertNotIn(
            ("POST", "/applications/private-github-app"), [(m, p) for m, p, _ in calls]
        )
        self.assertNotIn(
            ("PATCH", "/applications/existing-app"), [(m, p) for m, p, _ in calls]
        )

    def test_existing_application_with_empty_compose_raw_deploys_when_source_is_configured(
        self,
    ):
        calls = []
        existing = {
            "uuid": "existing-app",
            "name": "factory-smoke-test-dev",
            "domains": pc.DEFAULT_DOMAIN,
            "git_repository": pc.DEFAULT_REPOSITORY,
            "git_branch": pc.DEFAULT_BRANCH,
            "docker_compose_location": pc.DEFAULT_COMPOSE,
            "environment_name": "dev",
            "github_app_uuid": "github-app-7",
            "docker_compose_raw": "",
        }
        with mock.patch.object(
            pc, "request", side_effect=self.api_side_effect(calls, existing=existing)
        ), mock.patch.object(pc, "queue_deployment") as queue, mock.patch.object(
            pc, "wait_for_deployment"
        ) as wait, mock.patch.object(
            pc, "verify_development_health"
        ) as health:
            result = pc.provision(args(dry_run=False))

        self.assertEqual(result["status"], "exists")
        self.assertTrue(result["source_configured"])
        self.assertTrue(result["domain_reconciled"])
        self.assertIn(
            ("PATCH", "/applications/existing-app"), [(m, p) for m, p, _ in calls]
        )
        queue.assert_not_called()
        wait.assert_not_called()
        health.assert_not_called()

    def test_existing_application_with_redacted_compose_raw_deploys_when_source_is_configured(
        self,
    ):
        calls = []
        existing = {
            "uuid": "existing-app",
            "name": "factory-smoke-test-dev",
            "domains": pc.DEFAULT_DOMAIN,
            "git_repository": f"https://github.com/{pc.DEFAULT_REPOSITORY}",
            "git_branch": pc.DEFAULT_BRANCH,
            "docker_compose_location": pc.DEFAULT_COMPOSE,
            "environment_name": "dev",
            "github_app_uuid": "github-app-7",
            "docker_compose_raw": "[REDACTED]",
        }
        with mock.patch.object(
            pc, "request", side_effect=self.api_side_effect(calls, existing=existing)
        ), mock.patch.object(
            pc,
            "queue_deployment",
            return_value={"uuid": "deploy-existing", "force": True},
        ), mock.patch.object(
            pc,
            "wait_for_deployment",
            return_value={"uuid": "deploy-existing", "status": "success"},
        ), mock.patch.object(
            pc, "verify_development_health", return_value=[]
        ):
            result = pc.provision(args(dry_run=False))

        self.assertEqual(result["status"], "exists")
        self.assertTrue(result["source_configured"])
        self.assertTrue(result["domain_reconciled"])

    def test_existing_application_with_absent_compose_raw_deploys_when_source_is_configured(
        self,
    ):
        calls = []
        existing = {
            "uuid": "existing-app",
            "name": "factory-smoke-test-dev",
            "domains": pc.DEFAULT_DOMAIN,
            "git_repository": pc.DEFAULT_REPOSITORY,
            "git_branch": pc.DEFAULT_BRANCH,
            "docker_compose_location": pc.DEFAULT_COMPOSE,
            "environment_name": "dev",
            "github_app_uuid": "github-app-7",
        }
        with mock.patch.object(
            pc, "request", side_effect=self.api_side_effect(calls, existing=existing)
        ), mock.patch.object(
            pc,
            "queue_deployment",
            return_value={"uuid": "deploy-existing", "force": True},
        ), mock.patch.object(
            pc,
            "wait_for_deployment",
            return_value={"uuid": "deploy-existing", "status": "success"},
        ), mock.patch.object(
            pc, "verify_development_health", return_value=[]
        ):
            result = pc.provision(args(dry_run=False))

        self.assertEqual(result["status"], "exists")
        self.assertTrue(result["source_configured"])
        self.assertTrue(result["domain_reconciled"])

    def test_main_fails_when_provision_returns_recreate_required(self):
        out = io.StringIO()
        with mock.patch.object(
            pc, "provision", return_value={"status": "recreate_required"}
        ), redirect_stdout(out):
            self.assertEqual(pc.main([]), 2)
        self.assertEqual(json.loads(out.getvalue()), {"status": "recreate_required"})

    def test_real_run_gets_project_details_before_environment_api_when_dev_is_missing(
        self,
    ):
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs.get("body")))
            if path == "/projects":
                return pc.Response(
                    200, [{"uuid": "project-1", "name": "factory-smoke-test"}], {}
                )
            if path == "/projects/project-1":
                if calls.count(("GET", "/projects/project-1", None)) == 1:
                    return pc.Response(
                        200,
                        {
                            "uuid": "project-1",
                            "name": "factory-smoke-test",
                            "environments": [],
                        },
                        {},
                    )
                return pc.Response(
                    200,
                    {
                        "uuid": "project-1",
                        "name": "factory-smoke-test",
                        "environments": [{"name": "dev", "uuid": "env-dev"}],
                    },
                    {},
                )
            if path == "/servers":
                return pc.Response(
                    200, [{"uuid": "server-1", "name": "development"}], {}
                )
            if path == "/github-apps":
                return pc.Response(
                    200,
                    {
                        "github_apps": [
                            {
                                "uuid": "github-app-7",
                                "name": pc.DEFAULT_COOLIFY_GITHUB_APP_NAME,
                                "html_url": "https://github.com",
                            }
                        ]
                    },
                    {},
                )
            if path == "/applications":
                return pc.Response(200, [], {})
            if path == "/applications/private-github-app":
                return pc.Response(
                    201, {"uuid": "app-1", "name": "factory-smoke-test-dev"}, {}
                )
            if path == "/applications/app-1":
                return pc.Response(
                    200,
                    {"uuid": "app-1", "docker_compose_raw": "services: {}"},
                    {},
                )
            if path == "/applications/app-1/envs":
                return pc.Response(
                    200 if method == "GET" else 201,
                    [] if method == "GET" else kwargs.get("body"),
                    {},
                )
            raise AssertionError((method, path))

        with mock.patch.object(
            pc, "request", side_effect=fake_request
        ), mock.patch.object(
            pc,
            "ensure_dev_environment",
            return_value={
                "uuid": "abcdefghijklmnopqrstuvwx",
                "name": "dev",
                "created": True,
            },
        ) as ensure, mock.patch.object(
            pc, "queue_deployment", return_value={"uuid": "deploy-1", "force": True}
        ), mock.patch.object(
            pc,
            "wait_for_deployment",
            return_value={"uuid": "deploy-1", "status": "success"},
        ), mock.patch.object(
            pc, "verify_development_health", return_value=[]
        ):
            result = pc.provision(args(dry_run=False))

        self.assertEqual(result["status"], "created")
        ensure.assert_called_once_with("project-1")
        self.assertLess(
            calls.index(("GET", "/projects/project-1", None)),
            calls.index(("POST", "/applications/private-github-app", mock.ANY)),
        )
        self.assertEqual(
            [call for call in calls if call[:2] == ("GET", "/projects/project-1")],
            [
                ("GET", "/projects/project-1", None),
                ("GET", "/projects/project-1", None),
            ],
        )

    def test_dry_run_does_not_create_environment(self):
        with mock.patch.object(
            pc, "request", side_effect=self.api_side_effect([])
        ), mock.patch.object(pc, "ensure_dev_environment") as ensure:
            result = pc.provision(args(dry_run=True))
        self.assertEqual(result["status"], "dry_run")
        ensure.assert_not_called()

    def test_ensure_dev_environment_creates_dev_with_api_when_missing(self):
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append(
                (method, path, kwargs.get("body"), kwargs.get("allowed_statuses"))
            )
            if path == "/projects/project-1/environments" and method == "GET":
                return pc.Response(200, [], {})
            if path == "/projects/project-1/environments" and method == "POST":
                return pc.Response(201, {"uuid": "env-dev"}, {})
            raise AssertionError((method, path))

        with mock.patch.object(pc, "request", side_effect=fake_request):
            result = pc.ensure_dev_environment("project-1")

        self.assertEqual(result, {"uuid": "env-dev", "name": "dev", "created": True})
        self.assertEqual(
            calls,
            [
                ("GET", "/projects/project-1/environments", None, None),
                (
                    "POST",
                    "/projects/project-1/environments",
                    {"name": "dev"},
                    (201, 409, 422),
                ),
            ],
        )

    def test_ensure_dev_environment_handles_409_by_reloading_environments(self):
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs.get("body")))
            if path == "/projects/project-1/environments" and method == "GET":
                if len([c for c in calls if c[:2] == ("GET", path)]) == 1:
                    return pc.Response(200, [], {})
                return pc.Response(200, [{"uuid": "env-dev", "name": "dev"}], {})
            if path == "/projects/project-1/environments" and method == "POST":
                return pc.Response(409, {"message": "Environment already exists."}, {})
            raise AssertionError((method, path))

        with mock.patch.object(pc, "request", side_effect=fake_request):
            result = pc.ensure_dev_environment("project-1")

        self.assertEqual(result, {"uuid": "env-dev", "name": "dev", "created": False})

    def test_ensure_dev_environment_preserves_422_details(self):
        def fake_request(method, path, **kwargs):
            if path == "/projects/project-1/environments" and method == "GET":
                return pc.Response(200, [], {})
            if path == "/projects/project-1/environments" and method == "POST":
                return pc.Response(
                    422,
                    {
                        "message": "Validation failed.",
                        "errors": {"name": ["The name field is required."]},
                    },
                    {},
                )
            raise AssertionError((method, path))

        with mock.patch.object(pc, "request", side_effect=fake_request):
            with self.assertRaisesRegex(
                pc.ProvisioningError, "The name field is required"
            ):
                pc.ensure_dev_environment("project-1")

    def test_wait_for_docker_compose_raw_polls_until_loaded(self):
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path))
            if len(calls) == 1:
                return pc.Response(200, {"uuid": "app-1", "docker_compose_raw": ""}, {})
            return pc.Response(
                200, {"uuid": "app-1", "docker_compose_raw": "services: {}"}, {}
            )

        with mock.patch.object(
            pc, "request", side_effect=fake_request
        ), mock.patch.object(pc.time, "sleep") as sleep:
            pc.wait_for_docker_compose_raw("app-1")

        self.assertEqual(
            calls, [("GET", "/applications/app-1"), ("GET", "/applications/app-1")]
        )
        sleep.assert_called_once_with(pc.COMPOSE_READY_POLL_SECONDS)

    def test_refuses_non_dev_branch_before_api_calls(self):
        with mock.patch.object(pc, "request") as request:
            with self.assertRaises(pc.ProvisioningError):
                pc.provision(args(branch="main"))
        request.assert_not_called()

    def test_refuses_non_development_domain_before_api_calls(self):
        with mock.patch.object(pc, "request") as request:
            with self.assertRaises(pc.ProvisioningError):
                pc.provision(args(domain="factory-smoke-test.goodmorning.mx"))
        request.assert_not_called()

    def test_main_defaults_to_dry_run(self):
        out = io.StringIO()
        with mock.patch.object(
            pc, "provision", return_value={"status": "dry_run"}
        ) as provision, redirect_stdout(out):
            self.assertEqual(pc.main([]), 0)
        self.assertTrue(provision.call_args.args[0].dry_run)
        self.assertEqual(json.loads(out.getvalue()), {"status": "dry_run"})

    def test_token_redaction_from_errors(self):
        err = io.StringIO()
        with mock.patch.dict(
            os.environ, {"COOLIFY_API_TOKEN": "secret-token"}
        ), redirect_stderr(err):
            code = pc.main(["--product", "secret-token"])
        self.assertNotEqual(code, 0)
        self.assertNotIn("secret-token", err.getvalue())

    def test_queue_deployment_posts_force_true_and_extracts_uuid(self):
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path))
            return pc.Response(200, {"deployment_uuid": "deploy-1"}, {})

        with mock.patch.object(pc, "request", side_effect=fake_request):
            self.assertEqual(
                pc.queue_deployment("app-1"), {"uuid": "deploy-1", "force": True}
            )

        self.assertEqual(calls, [("POST", "/deploy?uuid=app-1&force=true")])

    def test_queue_deployment_extracts_uuid_from_coolify_4_1_2_deployments_list(self):
        def fake_request(method, path, **kwargs):
            self.assertEqual((method, path), ("POST", "/deploy?uuid=app-1&force=true"))
            return pc.Response(
                200,
                {
                    "message": "Deployment queued.",
                    "deployments": [
                        {
                            "application_uuid": "app-1",
                            "deployment_uuid": "deploy-real-1",
                        }
                    ],
                },
                {},
            )

        with mock.patch.object(pc, "request", side_effect=fake_request):
            self.assertEqual(
                pc.queue_deployment("app-1"),
                {"uuid": "deploy-real-1", "force": True},
            )

    def test_extract_deployment_uuid_prefers_deployment_uuid_over_application_uuid(
        self,
    ):
        self.assertEqual(
            pc.extract_deployment_uuid(
                {
                    "deployments": [
                        {
                            "uuid": "application-uuid",
                            "deployment_uuid": "deployment-uuid",
                        }
                    ]
                }
            ),
            "deployment-uuid",
        )

    def test_wait_for_deployment_polls_until_success(self):
        statuses = iter(["in_progress", "success"])

        def fake_request(method, path, **kwargs):
            self.assertEqual((method, path), ("GET", "/deployments/deploy-1"))
            return pc.Response(200, {"status": next(statuses)}, {})

        with mock.patch.object(
            pc, "request", side_effect=fake_request
        ), mock.patch.object(pc.time, "sleep") as sleep:
            result = pc.wait_for_deployment("deploy-1")

        self.assertEqual(result, {"uuid": "deploy-1", "status": "success"})
        sleep.assert_called_once_with(pc.DEPLOYMENT_POLL_SECONDS)

    def test_wait_for_deployment_raises_on_failure(self):
        with mock.patch.object(
            pc, "request", return_value=pc.Response(200, {"status": "failed"}, {})
        ):
            with self.assertRaisesRegex(pc.ProvisioningError, "failed"):
                pc.wait_for_deployment("deploy-1")

    def test_dry_run_does_not_queue_deployments_or_health_checks(self):
        with mock.patch.object(
            pc, "request", side_effect=self.api_side_effect([])
        ), mock.patch.object(pc, "queue_deployment") as queue, mock.patch.object(
            pc, "verify_development_health"
        ) as health:
            result = pc.provision(args(dry_run=True))

        self.assertEqual(result["status"], "dry_run")
        queue.assert_not_called()
        health.assert_not_called()

    def test_deployment_result_requires_explicit_environment_authorization(self):
        with mock.patch.dict(os.environ, {}, clear=True), mock.patch.object(
            pc, "queue_deployment"
        ) as queue:
            with self.assertRaisesRegex(pc.ProvisioningError, pc.REAL_DEPLOY_ENV):
                pc.deployment_result("app-1", args(dry_run=False, deploy=True))
        queue.assert_not_called()

    def test_deployment_result_queues_waits_and_returns_completed_status_when_authorized(
        self,
    ):
        with mock.patch.dict(
            os.environ, {pc.REAL_DEPLOY_ENV: "true"}, clear=True
        ), mock.patch.object(
            pc, "queue_deployment", return_value={"uuid": "deploy-1", "force": True}
        ) as queue, mock.patch.object(
            pc,
            "wait_for_deployment",
            return_value={"uuid": "deploy-1", "status": "success"},
        ) as wait:
            result = pc.deployment_result("app-1", args(dry_run=False, deploy=True))

        self.assertTrue(result["queued"])
        self.assertEqual(result["completed"], {"uuid": "deploy-1", "status": "success"})
        queue.assert_called_once_with("app-1")
        wait.assert_called_once_with("deploy-1")


class ProvisionCoolifyWorkflowTests(unittest.TestCase):
    def workflow_text(self):
        return (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "provision-coolify-development.yml"
        ).read_text(encoding="utf-8")

    def test_default_development_domain_reaches_safety_gate_variable(self):
        text = self.workflow_text()
        self.assertIn("default: dev.factory-smoke-test.goodmorning.mx", text)
        self.assertIn("coolify_github_app_name:", text)
        self.assertIn("default: goodmorning-mx-coolify", text)
        self.assertIn(
            '--coolify-github-app-name "${{ inputs.coolify_github_app_name }}"', text
        )
        self.assertIn("DEVELOPMENT_DOMAIN: ${{ inputs.domain }}", text)
        self.assertIn(
            "COOLIFY_SERVER_NAME: ${{ vars.COOLIFY_SERVER_NAME || 'development' }}",
            text,
        )
        self.assertIn(
            "COOLIFY_DESTINATION_UUID: ${{ vars.COOLIFY_DESTINATION_UUID || '",
            text,
        )
        self.assertIn('--server "${COOLIFY_SERVER_NAME}"', text)
        self.assertIn('--destination-uuid "${COOLIFY_DESTINATION_UUID}"', text)
        self.assertIn('[[ "${DEVELOPMENT_DOMAIN}" != dev.* ]]', text)
        self.assertNotIn('"${{ inputs.domain }}" != dev.*', text)

    def test_push_to_dev_reconciles_catalog_without_manual_dispatch(self):
        text = self.workflow_text()
        manifest = (
            Path(__file__).resolve().parents[1]
            / "products"
            / "factory-smoke-test.json"
        ).read_text(encoding="utf-8")

        self.assertIn("push:", text)
        self.assertIn("branches:\n      - dev", text)
        self.assertIn("products/*.json", text)
        self.assertIn("--dry-run false", text)
        self.assertIn("--deploy true", text)
        self.assertIn('"development_domain": "dev.factory-smoke-test.goodmorning.mx"', manifest)
        self.assertNotIn('"production_domain"', manifest)

    def test_workflow_fails_on_recreate_required_status(self):
        text = self.workflow_text()
        self.assertIn('payload.get("status") in {"recreate_required"', text)
        self.assertIn("Coolify provisioning returned status=recreate_required", text)

    def test_workflow_does_not_require_ssh_configuration(self):
        text = self.workflow_text()
        self.assertNotIn("COOLIFY_SSH", text)
        self.assertNotIn("GOODMORNING_FACTORY_COOLIFY_SSH_KEY", text)
        self.assertNotIn("GOODMORNING_FACTORY_COOLIFY_KNOWN_HOSTS", text)
        self.assertNotIn("rsync", text.lower())
        self.assertNotIn("scp", text.lower())


if __name__ == "__main__":
    unittest.main()
