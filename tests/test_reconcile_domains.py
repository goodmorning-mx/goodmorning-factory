import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import advanced_product_contract as contract
from scripts import reconcile_domains as domains
from scripts import reconcile_advanced_product as advanced
try:
    from test_advanced_product_contract import production_manifest
except ModuleNotFoundError:
    from tests.test_advanced_product_contract import production_manifest


def bound_manifest():
    value = production_manifest()
    value["domain_bindings"] = [{
        "apex": "example.org",
        "aliases": ["www.example.org"],
        "application": "mobile",
        "environment": "production",
        "desired_state": "active",
    }]
    return contract.validate_manifest(value)


class CoolifyDouble:
    def __init__(self, *, apply=False, domains="https://mobile.helloworldv2.goodmorning.mx", extra_apps=None):
        self.apply = apply
        self.application_domains = domains
        self.extra_apps = extra_apps or []
        self.patches = []

    def get(self, path):
        if path == "/servers":
            return [{"uuid": "server-1", "name": "localhost"}]
        if path == "/projects":
            return [{"uuid": "project-1", "name": "helloworldv2"}]
        if path == "/projects/project-1":
            return {"environments": [{"uuid": "prod-env-1", "name": "production"}]}
        if path == "/github-apps":
            return [{"uuid": "github-1", "name": "goodmorning-mx-coolify"}]
        if path == "/applications":
            target = {
                "uuid": "frontend-1",
                "name": "helloworldv2-mobile-prod",
                "project_uuid": "project-1",
                "environment_uuid": "prod-env-1",
                "domains": self.application_domains,
            }
            return [target, *self.extra_apps]
        if path == "/applications/frontend-1":
            return {
                "uuid": "frontend-1",
                "name": "helloworldv2-mobile-prod",
                "project_uuid": "project-1",
                "environment_uuid": "prod-env-1",
                "git_branch": "main",
                "domains": self.application_domains,
            }
        raise AssertionError(f"Unexpected GET path: {path}")

    def patch(self, path, body):
        self.patches.append((path, body))
        self.application_domains = body["domains"]
        return {"uuid": "frontend-1"}


class ReconcileDomainsTests(unittest.TestCase):
    def test_production_workflow_uses_established_destination_fallback(self):
        workflow = (
            Path(__file__).resolve().parents[1]
            / ".github"
            / "workflows"
            / "reconcile-production-domains.yml"
        ).read_text(encoding="utf-8")

        self.assertIn("vars.COOLIFY_RESOURCES_DESTINATION_UUID || 'g61slcctgnbvhhc8si0h9tf0'", workflow)

    def test_example_manifest_has_reviewable_production_domain_binding(self):
        root = Path(__file__).parents[1]
        manifest = contract.load_manifest(root / "production" / "inbrainwebpage.json")
        self.assertEqual(manifest["domain_bindings"], [{
            "apex": "inbrainmedical.com",
            "aliases": ["www.inbrainmedical.com"],
            "application": "frontend",
            "environment": "production",
            "desired_state": "active",
        }])
        frontend = next(item for item in manifest["applications"] if item["name"] == "frontend")
        self.assertEqual(frontend["custom_domains"], ["https://inbrainmedical.com", "https://www.inbrainmedical.com"])

    def test_contract_rejects_invalid_or_unsafe_bindings(self):
        bad_cases = [
            {"apex": "EXAMPLE.org", "aliases": [], "application": "mobile", "environment": "production", "desired_state": "active"},
            {"apex": "example.org", "aliases": ["example.org"], "application": "mobile", "environment": "production", "desired_state": "active"},
            {"apex": "example.org", "aliases": [], "application": "missing", "environment": "production", "desired_state": "active"},
            {"apex": "example.org", "aliases": [], "application": "mobile", "environment": "dev", "desired_state": "active"},
            {"apex": "example.org", "aliases": [], "application": "mobile", "environment": "production", "desired_state": "deleted"},
        ]
        for binding in bad_cases:
            with self.subTest(binding=binding), self.assertRaises(contract.ContractError):
                value = production_manifest()
                value["domain_bindings"] = [binding]
                contract.validate_manifest(value)

    def test_plan_is_default_read_only_and_reports_domain_update(self):
        client = CoolifyDouble()
        result = domains.reconcile_domains(
            bound_manifest(), client=client, server_name="localhost", destination_uuid="destination-1",
            confirm_product=None, confirm_environment=None,
        )
        self.assertEqual(result["status"], "planned")
        self.assertEqual(result["state"], "pending")
        self.assertEqual(result["actions"][0]["action"], "would_update")
        self.assertEqual(client.patches, [])

    def test_apply_updates_only_domains_then_verifies_and_is_idempotent(self):
        client = CoolifyDouble(apply=True)
        with patch.dict(os.environ, {"COOLIFY_ALLOW_ADVANCED_PRODUCTION_WRITES": "true"}, clear=True):
            result = domains.reconcile_domains(
                bound_manifest(), client=client, server_name="localhost", destination_uuid="destination-1",
                confirm_product="helloworldv2", confirm_environment="production", confirmation="domain:helloworldv2:production",
            )
            second = domains.reconcile_domains(
                bound_manifest(), client=client, server_name="localhost", destination_uuid="destination-1",
                confirm_product="helloworldv2", confirm_environment="production", confirmation="domain:helloworldv2:production",
            )
        self.assertEqual(result["state"], "ssl_pending")
        self.assertEqual(client.patches, [(
            "/applications/frontend-1",
            {"domains": "https://mobile.helloworldv2.goodmorning.mx,https://example.org,https://www.example.org"},
        )])
        self.assertEqual(second["actions"][0]["action"], "unchanged")

    def test_conflict_fails_without_force_override_or_write(self):
        conflicting = {"uuid": "other-1", "name": "other-app", "domains": "https://www.example.org"}
        client = CoolifyDouble(extra_apps=[conflicting])
        with self.assertRaisesRegex(advanced.ReconcileError, "already assigned"):
            domains.reconcile_domains(
                bound_manifest(), client=client, server_name="localhost", destination_uuid="destination-1",
                confirm_product=None, confirm_environment=None,
            )
        self.assertEqual(client.patches, [])

    def test_apply_requires_exact_production_gate_and_confirmation(self):
        client = CoolifyDouble(apply=True)
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(advanced.ReconcileError, "COOLIFY_ALLOW_ADVANCED_PRODUCTION_WRITES"):
            domains.reconcile_domains(
                bound_manifest(), client=client, server_name="localhost", destination_uuid="destination-1",
                confirm_product="helloworldv2", confirm_environment="production", confirmation="domain:helloworldv2:production",
            )

    def test_dns_https_and_api_health_checks_return_redacted_structured_status(self):
        manifest = bound_manifest()
        dns_hosts = []
        https_urls = []
        result = domains.validate_public_endpoints(
            manifest,
            attempts=1,
            dns_check=lambda host: dns_hosts.append(host) or True,
            https_check=lambda url: https_urls.append(url) or (True, 200),
            sleeper=lambda _: self.fail("no retry expected"),
        )
        self.assertEqual(result["status"], "healthy")
        self.assertTrue(all(item["dns"] == "dns_configured" for item in result["checks"]))
        self.assertEqual(set(dns_hosts), {"example.org", "www.example.org", "api.helloworldv2.goodmorning.mx"})
        self.assertIn("https://api.helloworldv2.goodmorning.mx/health", https_urls)
        self.assertTrue(all(item["http_status_200"] for item in result["checks"]))
        self.assertNotIn("secret", json.dumps(result).lower())

    def test_validation_retries_with_bounded_exponential_backoff(self):
        manifest = bound_manifest()
        waits = []
        calls = {"count": 0}

        def https_check(_url):
            calls["count"] += 1
            ready = calls["count"] > 1
            return ready, 200 if ready else None

        result = domains.validate_public_endpoints(
            manifest, attempts=2, retry_base_seconds=0.5, sleeper=waits.append,
            dns_check=lambda _host: True, https_check=https_check,
        )
        self.assertEqual(result["status"], "healthy")
        self.assertIn(0.5, waits)

    def test_validation_distinguishes_dns_pending_ssl_pending_and_health_error(self):
        manifest = bound_manifest()
        pending = domains.validate_public_endpoints(
            manifest, attempts=1, dns_check=lambda _host: False,
            https_check=lambda _url: (False, None),
        )
        ssl_pending = domains.validate_public_endpoints(
            manifest, attempts=1, dns_check=lambda _host: True,
            https_check=lambda _url: (False, None),
        )
        unhealthy = domains.validate_public_endpoints(
            manifest, attempts=1, dns_check=lambda _host: True,
            https_check=lambda _url: (True, 503),
        )
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(ssl_pending["status"], "ssl_pending")
        self.assertEqual(unhealthy["status"], "error")

    def test_cli_errors_and_reports_do_not_echo_runtime_secrets(self):
        fake = CoolifyDouble()
        output = io.StringIO()
        source = production_manifest()
        source["domain_bindings"] = [{
            "apex": "example.org", "aliases": ["www.example.org"],
            "application": "mobile", "environment": "production", "desired_state": "active",
        }]
        with tempfile.TemporaryDirectory() as directory:
            manifest_path = Path(directory) / "domains.json"
            manifest_path.write_text(json.dumps(source), encoding="utf-8")
            with patch.object(domains.advanced, "Coolify", return_value=fake), \
                 patch.dict(os.environ, {"COOLIFY_API_TOKEN": "sensitive-runtime-token"}, clear=True), \
                 contextlib.redirect_stdout(output):
                code = domains.main([
                    "--manifest", str(manifest_path),
                    "--destination-uuid", "destination-1",
                ])
        self.assertEqual(code, 0)
        self.assertNotIn("sensitive-runtime-token", output.getvalue())


if __name__ == "__main__":
    unittest.main()
