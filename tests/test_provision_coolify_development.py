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
    )
    base.update(kw)
    return argparse.Namespace(**base)


class CoolifyDevelopmentProvisioningTests(unittest.TestCase):
    def api_side_effect(self, calls, existing=None):
        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs.get("body")))
            if path == "/projects":
                return pc.Response(200, [{"uuid": "project-1", "name": "factory-smoke-test", "environments": [{"name": "development"}]}], {})
            if path == "/servers":
                return pc.Response(200, [{"uuid": "server-1", "name": "development"}], {})
            if path == "/github-apps":
                return pc.Response(200, {"github_apps": [{"uuid": "github-app-7", "name": "GitHub App", "html_url": "https://github.com"}]}, {})
            if path == "/applications":
                return pc.Response(200, [] if existing is None else [existing], {})
            if path == "/applications/dockercompose":
                self.assertFalse(kwargs["body"]["instant_deploy"])
                return pc.Response(201, {"uuid": "app-1", "name": "factory-smoke-test-development"}, {})
            raise AssertionError((method, path))
        return fake_request

    def test_dry_run_discovers_resources_without_creating(self):
        calls = []
        with mock.patch.object(pc, "request", side_effect=self.api_side_effect(calls)):
            result = pc.provision(args())
        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["would_create"]["git_repository"], pc.DEFAULT_REPOSITORY)
        self.assertEqual(result["would_create"]["docker_compose_location"], pc.DEFAULT_COMPOSE)
        self.assertIn(("GET", "/github-apps"), [(m, p) for m, p, _ in calls])
        self.assertNotIn(("GET", "/sources/github"), [(m, p) for m, p, _ in calls])
        self.assertNotIn(("POST", "/applications/dockercompose"), [(m, p) for m, p, _ in calls])

    def test_github_apps_parser_accepts_current_nested_response_shape(self):
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs.get("body")))
            if path == "/projects":
                return pc.Response(200, {"data": [{"uuid": "project-1", "name": "factory-smoke-test", "environments": [{"name": "development"}]}]}, {})
            if path == "/servers":
                return pc.Response(200, {"servers": [{"uuid": "server-1", "name": "development"}]}, {})
            if path == "/github-apps":
                return pc.Response(
                    200,
                    {
                        "github_apps": [
                            {
                                "uuid": "github-app-current",
                                "name": "GoodMorning GitHub App",
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
        self.assertEqual(result["github_app"], {"id": "github-app-current", "name": "GoodMorning GitHub App"})
        self.assertEqual(result["would_create"]["git_source_id"], "github-app-current")
        self.assertIn(("GET", "/github-apps"), [(m, p) for m, p, _ in calls])
        self.assertNotIn(("GET", "/sources/github"), [(m, p) for m, p, _ in calls])
        self.assertNotIn(("POST", "/applications/dockercompose"), [(m, p) for m, p, _ in calls])

    def test_missing_project_dry_run_plans_project_and_environment_creation(self):
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs.get("body")))
            if path == "/projects":
                return pc.Response(200, [], {})
            if path == "/servers":
                return pc.Response(200, [{"uuid": "server-1", "name": "development"}], {})
            if path == "/github-apps":
                return pc.Response(200, {"github_apps": [{"uuid": "github-app-7", "name": "GitHub App", "html_url": "https://github.com"}]}, {})
            if path == "/applications":
                return pc.Response(200, [], {})
            raise AssertionError((method, path))

        with mock.patch.object(pc, "request", side_effect=fake_request):
            result = pc.provision(args())

        self.assertEqual(result["status"], "dry_run")
        self.assertEqual(result["project"], {"uuid": None, "name": pc.DEFAULT_PRODUCT, "created": False})
        self.assertEqual(result["environment"], {"name": "development", "created": True})
        self.assertEqual(result["would_create_project"], {"name": pc.DEFAULT_PRODUCT})
        self.assertEqual(result["would_create_environment"], {"name": "development"})
        self.assertNotIn(("POST", "/projects"), [(m, p) for m, p, _ in calls])
        self.assertNotIn(("POST", "/projects/new-project/environment"), [(m, p) for m, p, _ in calls])

    def test_missing_project_real_run_creates_project_and_environment_before_application(self):
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs.get("body")))
            if path == "/projects":
                if method == "GET":
                    return pc.Response(200, [], {})
                if method == "POST":
                    return pc.Response(201, {"uuid": "new-project", "name": pc.DEFAULT_PRODUCT}, {})
            if path == "/servers":
                return pc.Response(200, [{"uuid": "server-1", "name": "development"}], {})
            if path == "/github-apps":
                return pc.Response(200, {"github_apps": [{"uuid": "github-app-7", "name": "GitHub App", "html_url": "https://github.com"}]}, {})
            if path == "/applications":
                return pc.Response(200, [], {})
            if path == "/projects/new-project/environment":
                return pc.Response(201, {"uuid": "env-1", "name": "development"}, {})
            if path == "/applications/dockercompose":
                return pc.Response(201, {"uuid": "app-1", "name": "factory-smoke-test-development"}, {})
            raise AssertionError((method, path))

        with mock.patch.object(pc, "request", side_effect=fake_request):
            result = pc.provision(args(dry_run=False))

        self.assertEqual(result["status"], "created")
        self.assertEqual(result["project"], {"uuid": "new-project", "name": pc.DEFAULT_PRODUCT, "created": True})
        self.assertEqual(result["environment"], {"name": "development", "created": True})
        self.assertEqual(calls.index(("POST", "/projects", {"name": pc.DEFAULT_PRODUCT})), 4)
        self.assertLess(
            calls.index(("POST", "/projects/new-project/environment", {"name": "development"})),
            calls.index(("POST", "/applications/dockercompose", mock.ANY)),
        )

    def test_existing_application_is_idempotent_and_not_duplicated(self):
        calls = []
        existing = {"uuid": "existing-app", "name": "factory-smoke-test", "domains": pc.DEFAULT_DOMAIN}
        with mock.patch.object(pc, "request", side_effect=self.api_side_effect(calls, existing=existing)):
            result = pc.provision(args(dry_run=False))
        self.assertEqual(result["status"], "exists")
        self.assertEqual(result["application"]["uuid"], "existing-app")
        self.assertNotIn(("POST", "/applications/dockercompose"), [(m, p) for m, p, _ in calls])

    def test_real_creation_posts_once_when_dry_run_false(self):
        calls = []
        with mock.patch.object(pc, "request", side_effect=self.api_side_effect(calls)):
            result = pc.provision(args(dry_run=False))
        self.assertEqual(result["status"], "created")
        posts = [call for call in calls if call[0] == "POST" and call[1] == "/applications/dockercompose"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][2]["git_branch"], "dev")
        self.assertEqual(posts[0][2]["domains"], pc.DEFAULT_DOMAIN)

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
        self.assertIn("DEVELOPMENT_DOMAIN: ${{ inputs.domain }}", text)
        self.assertIn('[[ "${DEVELOPMENT_DOMAIN}" != dev.* ]]', text)
        self.assertNotIn('"${{ inputs.domain }}" != dev.*', text)


if __name__ == "__main__":
    unittest.main()
