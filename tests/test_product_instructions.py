import base64
import json
import unittest
from unittest import mock

from scripts.product_instructions import (
    CUSTOM_END,
    CUSTOM_START,
    InstructionsError,
    reconcile_remote,
    render_instructions,
)
from scripts.register_product_manifest import build_manifest


class ProductInstructionsTests(unittest.TestCase):
    def test_render_contains_operational_context_without_manifest_secrets(self):
        manifest = build_manifest("acme", "private-storage-uuid")
        manifest["modules"] = [{
            "name": "gm-agent",
            "version": "1.2.3",
            "kind": "full-stack",
            "deployment_modes": ["embedded", "gateway"],
            "default_deployment_mode": "embedded",
            "deployment_mode": "gateway",
            "bindings": [{"key": "OPENAI_API_KEY", "source": "managed_secret", "binding": "GOODMORNING_MANAGED_OPENAI_API_KEY", "target": "backend"}],
        }]
        rendered = render_instructions(manifest)
        self.assertIn("Resources V3", rendered)
        self.assertIn("gm-agent", rendered)
        self.assertIn("managed_secret", rendered)
        self.assertNotIn("private-storage-uuid", rendered)
        self.assertIn("GOODMORNING_MANAGED_OPENAI_API_KEY", rendered)  # binding names are safe metadata; values are never rendered
        self.assertIn(CUSTOM_START, rendered)
        self.assertIn(CUSTOM_END, rendered)

    def test_render_preserves_product_custom_section_and_replaces_managed_section(self):
        manifest = build_manifest("acme", "storageuuid123")
        existing = render_instructions(manifest).replace(
            "Agrega aquí instrucciones específicas del producto. Esta sección no es administrada por Factory.",
            "Deploy the trading worker only after its market-data contract is reviewed.",
        )
        updated = dict(manifest, development_domain="dev.acme.goodmorning.mx")
        rendered = render_instructions(updated, existing)
        self.assertIn("Deploy the trading worker", rendered)
        self.assertIn("GOODMORNING FACTORY: MANAGED START", rendered)
        self.assertEqual(rendered.count("GOODMORNING FACTORY: MANAGED START"), 1)

    def test_legacy_existing_file_is_preserved_as_custom_content(self):
        rendered = render_instructions(build_manifest("acme", "storageuuid123"), "# Product notes\n\nKeep the seed data contract.")
        self.assertIn("# Product notes", rendered)
        self.assertIn("Keep the seed data contract.", rendered)
        self.assertIn(CUSTOM_START, rendered)

    def test_reconcile_remote_creates_and_is_idempotent(self):
        manifest = build_manifest("acme", "storageuuid123")
        calls = []
        stored = {}

        def request(method, path, **kwargs):
            calls.append((method, path, kwargs.get("body")))
            if method == "GET":
                return (200, stored) if stored else (404, {"message": "Not Found"})
            body = kwargs["body"]
            stored.update({"content": body["content"], "sha": "sha-1"})
            return 201, {"commit": {"sha": "commit-1"}}

        first = reconcile_remote("acme", manifest, request)
        self.assertEqual(first["status"], "updated")
        self.assertEqual(reconcile_remote("acme", manifest, request)["status"], "unchanged")
        self.assertEqual([call[0] for call in calls], ["GET", "PUT", "GET"])

    def test_reconcile_remote_rejects_mismatched_product(self):
        with self.assertRaises(InstructionsError):
            reconcile_remote("other", build_manifest("acme", "storageuuid123"), mock.Mock())

    def test_reconcile_remote_uses_manifest_repository_for_legacy_aliases(self):
        manifest = build_manifest("alejandro", "storageuuid123")
        manifest["repository"] = "goodmorning-mx/factory-smoke-test"
        calls = []

        def request(method, path, **kwargs):
            calls.append((method, path))
            return (404, {}) if method == "GET" else (201, {"commit": {"sha": "commit-1"}})

        reconcile_remote("alejandro", manifest, request)
        self.assertTrue(calls[0][1].startswith("/repos/goodmorning-mx/factory-smoke-test/contents/"))


if __name__ == "__main__":
    unittest.main()
