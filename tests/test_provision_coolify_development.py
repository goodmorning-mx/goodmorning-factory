import argparse
import io
import json
import os
from pathlib import Path
import unittest
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
        coolify_github_app_name=pc.DEFAULT_COOLIFY_GITHUB_APP_NAME,
    )
    base.update(kw)
    return argparse.Namespace(**base)


class CoolifyDevelopmentProvisioningTests(unittest.TestCase):
    def api_side_effect(self, calls, existing=None):
        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs.get("body")))
            if path == "/projects":
                return pc.Response(200, [{"uuid": "project-1", "name": "factory-smoke-test"}], {})
            if path == "/projects/project-1":
                return pc.Response(200, {"uuid": "project-1", "name": "factory-smoke-test", "environments": [{"name": "dev", "uuid": "env-dev"}]}, {})
            if path == "/projects/new-project":
                return pc.Response(200, {"uuid": "new-project", "name": pc.DEFAULT_PRODUCT, "environments": [{"name": "dev", "uuid": "env-dev"}]}, {})
            if path == "/servers":
                return pc.Response(200, [{"uuid": "server-1", "name": "development"}], {})
            if path == "/github-apps":
                return pc.Response(200, {"github_apps": [{"uuid": "github-app-7", "name": pc.DEFAULT_COOLIFY_GITHUB_APP_NAME, "html_url": "https://github.com"}]}, {})
            if path == "/applications":
                return pc.Response(200, [] if existing is None else [existing], {})
            if path == "/applications/private-github-app":
                self.assertFalse(kwargs["body"]["instant_deploy"])
                return pc.Response(201, {"uuid": "app-1", "name": "factory-smoke-test-development"}, {})
            if path == "/applications/app-1" or path == "/applications/existing-app":
                self.assertEqual(method, "PATCH")
                self.assertEqual(
                    kwargs["body"],
                    {
                        "docker_compose_domains": [
                            {"name": "frontend", "domain": "https://dev.factory-smoke-test.goodmorning.mx"},
                            {"name": "backend", "domain": "https://api.dev.factory-smoke-test.goodmorning.mx"},
                        ],
                        "force_domain_override": False,
                    },
                )
                return pc.Response(200, {"uuid": path.rsplit("/", 1)[-1]}, {})
            raise AssertionError((method, path))
        return fake_request

    def test_dry_run_discovers_resources_without_creating(self):
        calls = []
        with mock.patch.object(pc, "request", side_effect=self.api_side_effect(calls)):
            result = pc.provision(args())
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["would_create"]["git_repository"], pc.DEFAULT_REPOSITORY)
        self.assertEqual(result["would_create"]["docker_compose_location"], pc.DEFAULT_COMPOSE)
        self.assertEqual(
            result["would_patch"],
            {
                "docker_compose_domains": [
                    {"name": "frontend", "domain": "https://dev.factory-smoke-test.goodmorning.mx"},
                    {"name": "backend", "domain": "https://api.dev.factory-smoke-test.goodmorning.mx"},
                ],
                "force_domain_override": False,
            },
        )
        self.assertIn(("GET", "/github-apps"), [(m, p) for m, p, _ in calls])
        self.assertNotIn(("GET", "/sources/github"), [(m, p) for m, p, _ in calls])
        self.assertNotIn(("POST", "/applications/private-github-app"), [(m, p) for m, p, _ in calls])
        self.assertNotIn(("PATCH", "/applications/app-1"), [(m, p) for m, p, _ in calls])

    def test_docker_compose_domains_are_derived_from_development_domain(self):
        self.assertEqual(
            pc.docker_compose_domains("https://dev.example.goodmorning.mx"),
            [
                {"name": "frontend", "domain": "https://dev.example.goodmorning.mx"},
                {"name": "backend", "domain": "https://api.dev.example.goodmorning.mx"},
            ],
        )

    def test_github_apps_parser_accepts_current_nested_response_shape(self):
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs.get("body")))
            if path == "/projects":
                return pc.Response(200, {"data": [{"uuid": "project-1", "name": "factory-smoke-test"}]}, {})
            if path == "/projects/project-1":
                return pc.Response(200, {"data": {"uuid": "project-1", "name": "factory-smoke-test", "environments": [{"name": "dev", "uuid": "env-dev"}]}}, {})
            if path == "/servers":
                return pc.Response(200, {"servers": [{"uuid": "server-1", "name": "development"}]}, {})
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
        self.assertEqual(result["github_app"], {"uuid": "github-app-current", "name": pc.DEFAULT_COOLIFY_GITHUB_APP_NAME})
        self.assertEqual(result["would_create"]["github_app_uuid"], "github-app-current")
        self.assertIn(("GET", "/github-apps"), [(m, p) for m, p, _ in calls])
        self.assertNotIn(("GET", "/sources/github"), [(m, p) for m, p, _ in calls])
        self.assertNotIn(("POST", "/applications/private-github-app"), [(m, p) for m, p, _ in calls])


    def test_github_app_uuid_is_required_instead_of_numeric_id(self):
        with self.assertRaisesRegex(pc.ProvisioningError, "numeric id"):
            pc.creation_payload(
                args(),
                {"uuid": "project-1"},
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

    def test_github_app_selection_rejects_duplicate_exact_names(self):
        with self.assertRaisesRegex(pc.ProvisioningError, "duplicated"):
            pc.discover_github_app(
                [
                    {"uuid": "github-app-1", "name": pc.DEFAULT_COOLIFY_GITHUB_APP_NAME},
                    {"uuid": "github-app-2", "name": pc.DEFAULT_COOLIFY_GITHUB_APP_NAME},
                ],
                pc.DEFAULT_COOLIFY_GITHUB_APP_NAME,
                pc.DEFAULT_REPOSITORY,
            )

    def test_github_app_selection_rejects_public_github_for_private_repository(self):
        with self.assertRaisesRegex(pc.ProvisioningError, "Public GitHub cannot be used"):
            pc.discover_github_app(
                [{"uuid": "public-github", "name": "Public GitHub"}],
                "Public GitHub",
                pc.DEFAULT_REPOSITORY,
            )

    def test_missing_project_dry_run_plans_project_creation_and_default_environment_reuse(self):
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs.get("body")))
            if path == "/projects":
                return pc.Response(200, [], {})
            if path == "/projects/new-project":
                return pc.Response(200, {"uuid": "new-project", "name": pc.DEFAULT_PRODUCT, "environments": [{"name": "dev", "uuid": "env-dev"}]}, {})
            if path == "/servers":
                return pc.Response(200, [{"uuid": "server-1", "name": "development"}], {})
            if path == "/github-apps":
                return pc.Response(200, {"github_apps": [{"uuid": "github-app-7", "name": pc.DEFAULT_COOLIFY_GITHUB_APP_NAME, "html_url": "https://github.com"}]}, {})
            if path == "/applications":
                return pc.Response(200, [], {})
            raise AssertionError((method, path))

        with mock.patch.object(pc, "request", side_effect=fake_request):
            result = pc.provision(args())

        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["project"], {"uuid": None, "name": pc.DEFAULT_PRODUCT, "created": False})
        self.assertEqual(result["environment"], {"name": "development", "coolify_name": "dev", "created": False})
        self.assertEqual(result["would_create_project"], {"name": pc.DEFAULT_PRODUCT})
        self.assertNotIn("would_create_environment", result)
        self.assertNotIn(("POST", "/projects"), [(m, p) for m, p, _ in calls])
        self.assertNotIn(("POST", "/projects/new-project/environment"), [(m, p) for m, p, _ in calls])

    def test_missing_project_real_run_creates_project_and_reuses_default_environment_before_application(self):
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs.get("body")))
            if path == "/projects":
                if method == "GET":
                    return pc.Response(200, [], {})
                if method == "POST":
                    return pc.Response(201, {"uuid": "new-project", "name": pc.DEFAULT_PRODUCT}, {})
            if path == "/projects/new-project":
                return pc.Response(200, {"uuid": "new-project", "name": pc.DEFAULT_PRODUCT, "environments": [{"name": "dev", "uuid": "env-dev"}]}, {})
            if path == "/servers":
                return pc.Response(200, [{"uuid": "server-1", "name": "development"}], {})
            if path == "/github-apps":
                return pc.Response(200, {"github_apps": [{"uuid": "github-app-7", "name": pc.DEFAULT_COOLIFY_GITHUB_APP_NAME, "html_url": "https://github.com"}]}, {})
            if path == "/applications":
                return pc.Response(200, [], {})
            if path == "/applications/private-github-app":
                return pc.Response(201, {"uuid": "app-1", "name": "factory-smoke-test-development"}, {})
            if path == "/applications/app-1":
                self.assertEqual(method, "PATCH")
                return pc.Response(200, {"uuid": "app-1"}, {})
            raise AssertionError((method, path))

        with mock.patch.object(pc, "request", side_effect=fake_request), mock.patch.object(pc, "ensure_dev_environment", return_value={"uuid": "env-dev", "name": "dev"}) as ensure:
            result = pc.provision(args(dry_run=False))

        self.assertEqual(result["status"], "created")
        self.assertEqual(result["project"], {"uuid": "new-project", "name": pc.DEFAULT_PRODUCT, "created": True})
        self.assertEqual(result["environment"], {"name": "development", "coolify_name": "dev", "created": False})
        self.assertEqual(calls.index(("POST", "/projects", {"name": pc.DEFAULT_PRODUCT})), 4)
        self.assertIn(("GET", "/projects/new-project", None), calls)
        ensure.assert_called_once_with("new-project")
        self.assertNotIn(("POST", "/projects/new-project/environment", {"name": "development"}), calls)

    def test_existing_application_is_idempotent_and_not_duplicated(self):
        calls = []
        existing = {"uuid": "existing-app", "name": "factory-smoke-test", "domains": pc.DEFAULT_DOMAIN}
        with mock.patch.object(pc, "request", side_effect=self.api_side_effect(calls, existing=existing)), mock.patch.object(pc, "ensure_dev_environment", return_value={"uuid": "env-dev", "name": "dev"}) as ensure:
            result = pc.provision(args(dry_run=False))
        self.assertEqual(result["status"], "exists")
        self.assertEqual(result["application"]["uuid"], "existing-app")
        self.assertTrue(result["domain_reconciled"])
        self.assertIn(("PATCH", "/applications/existing-app"), [(m, p) for m, p, _ in calls])
        self.assertNotIn(("POST", "/applications/private-github-app"), [(m, p) for m, p, _ in calls])
        ensure.assert_called_once_with("project-1")

    def test_real_creation_posts_once_when_dry_run_false(self):
        calls = []
        with mock.patch.object(pc, "request", side_effect=self.api_side_effect(calls)), mock.patch.object(pc, "ensure_dev_environment", return_value={"uuid": "env-dev", "name": "dev"}) as ensure:
            result = pc.provision(args(dry_run=False))
        self.assertEqual(result["status"], "created")
        ensure.assert_called_once_with("project-1")
        posts = [call for call in calls if call[0] == "POST" and call[1] == "/applications/private-github-app"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][2]["git_branch"], "dev")
        self.assertEqual(posts[0][2]["github_app_uuid"], "github-app-7")
        self.assertNotIn("git_source_id", posts[0][2])
        self.assertNotIn("domains", posts[0][2])
        self.assertEqual(posts[0][2]["base_directory"], "/")
        self.assertEqual(posts[0][2]["docker_compose_location"], "/compose.yaml")
        self.assertEqual(posts[0][2]["environment_name"], "dev")
        patches = [call for call in calls if call[0] == "PATCH" and call[1] == "/applications/app-1"]
        self.assertEqual(len(patches), 1)
        self.assertFalse(patches[0][2]["force_domain_override"])
        self.assertEqual(patches[0][2]["docker_compose_domains"][1]["domain"], "https://api.dev.factory-smoke-test.goodmorning.mx")


    def test_dry_run_does_not_execute_ssh_helper(self):
        with mock.patch.object(pc, "request", side_effect=self.api_side_effect([])), mock.patch.object(pc, "ensure_dev_environment") as ensure:
            result = pc.provision(args(dry_run=True))
        self.assertEqual(result["status"], "dry_run")
        ensure.assert_not_called()

    def test_ensure_dev_environment_uses_fixed_ssh_command_and_validates_json(self):
        completed = mock.Mock(returncode=0, stdout=json.dumps({"status": "ok", "environment": {"uuid": "env-dev", "name": "dev"}}))
        with mock.patch.dict(
            os.environ,
            {
                "COOLIFY_SSH_HOST": "coolify.example",
                "COOLIFY_SSH_USER": "provisioner",
                "COOLIFY_SSH_KEY_PATH": "/tmp/key",
                "COOLIFY_SSH_KNOWN_HOSTS_PATH": "/tmp/known_hosts",
            },
        ), mock.patch.object(pc.subprocess, "run", return_value=completed) as run:
            result = pc.ensure_dev_environment("project-1")
        self.assertEqual(result, {"uuid": "env-dev", "name": "dev"})
        run.assert_called_once_with(
            [
                "ssh",
                "-i",
                "/tmp/key",
                "-o",
                "UserKnownHostsFile=/tmp/known_hosts",
                "-o",
                "StrictHostKeyChecking=yes",
                "-o",
                "BatchMode=yes",
                "provisioner@coolify.example",
                "ensure-dev-env",
                "project-1",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=pc.TIMEOUT_SECONDS,
        )

    def test_ensure_dev_environment_rejects_non_dev_json(self):
        with self.assertRaisesRegex(pc.ProvisioningError, "dev environment"):
            pc.validate_ensure_dev_env_response({"status": "ok", "environment": {"uuid": "env-prod", "name": "production"}})

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
        with mock.patch.object(pc, "provision", return_value={"status": "dry_run"}) as provision, redirect_stdout(out):
            self.assertEqual(pc.main([]), 0)
        self.assertTrue(provision.call_args.args[0].dry_run)
        self.assertEqual(json.loads(out.getvalue()), {"status": "dry_run"})

    def test_token_redaction_from_errors(self):
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"COOLIFY_API_TOKEN": "secret-token"}), redirect_stderr(err):
            code = pc.main(["--product", "secret-token"])
        self.assertNotEqual(code, 0)
        self.assertNotIn("secret-token", err.getvalue())


class ProvisionCoolifyWorkflowTests(unittest.TestCase):
    def workflow_text(self):
        return (Path(__file__).resolve().parents[1] / ".github" / "workflows" / "provision-coolify-development.yml").read_text(encoding="utf-8")

    def test_default_development_domain_reaches_safety_gate_variable(self):
        text = self.workflow_text()
        self.assertIn("default: dev.factory-smoke-test.goodmorning.mx", text)
        self.assertIn("coolify_github_app_name:", text)
        self.assertIn("default: goodmorning-mx-coolify", text)
        self.assertIn("--coolify-github-app-name \"${{ inputs.coolify_github_app_name }}\"", text)
        self.assertIn("DEVELOPMENT_DOMAIN: ${{ inputs.domain }}", text)
        self.assertIn('[[ "${DEVELOPMENT_DOMAIN}" != dev.* ]]', text)
        self.assertNotIn('"${{ inputs.domain }}" != dev.*', text)


if __name__ == "__main__":
    unittest.main()
