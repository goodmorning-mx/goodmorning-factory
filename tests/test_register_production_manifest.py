import base64
import json
import unittest
from unittest import mock

import scripts.register_production_manifest as production


class RegisterProductionManifestTests(unittest.TestCase):
    def test_manifest_is_automatic_main_release_contract(self):
        manifest = production.build_manifest("acme", "storageuuid123")
        self.assertEqual(manifest["topology"], "resources-v2-production")
        self.assertEqual(manifest["repository"], "goodmorning-mx/acme")
        self.assertEqual(manifest["branch"], "main")
        self.assertEqual(manifest["production_domain"], "acme.goodmorning.mx")
        self.assertEqual(manifest["promotion_policy"], "auto-on-main")
        self.assertTrue(manifest["confirm_production"])
        self.assertTrue(manifest["backup"]["enabled"])

    def test_registers_production_manifest_after_repository_verification(self):
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs.get("body")))
            if method == "GET":
                return 404, {"message": "Not Found"}
            if method == "PUT":
                decoded = json.loads(
                    base64.b64decode(kwargs["body"]["content"]).decode("utf-8")
                )
                self.assertEqual(decoded["branch"], "main")
                self.assertEqual(decoded["promotion_policy"], "auto-on-main")
                return 201, {"commit": {"sha": "prodcommit"}}
            raise AssertionError((method, path))

        with (
            mock.patch.object(production.catalog, "require_product_repository"),
            mock.patch.object(production.catalog, "request", side_effect=fake_request),
        ):
            result = production.register("acme", "storageuuid123")

        self.assertEqual(result["status"], "registered")
        self.assertEqual(result["manifest_path"], "production/acme.json")
        self.assertEqual(result["commit_sha"], "prodcommit")
        self.assertTrue(any(method == "PUT" for method, _, _ in calls))

    def test_existing_exact_manifest_is_idempotent(self):
        manifest = production.build_manifest("acme", "storageuuid123")
        encoded = base64.b64encode(json.dumps(manifest).encode("utf-8")).decode("ascii")

        with (
            mock.patch.object(production.catalog, "require_product_repository"),
            mock.patch.object(
                production.catalog,
                "request",
                return_value=(200, {"content": encoded}),
            ),
        ):
            result = production.register("acme", "storageuuid123")

        self.assertEqual(result["status"], "unchanged")

    def test_existing_incompatible_production_manifest_fails_closed(self):
        manifest = production.build_manifest("acme", "storageuuid123")
        manifest["repository"] = "goodmorning-mx/other"
        encoded = base64.b64encode(json.dumps(manifest).encode("utf-8")).decode("ascii")

        with (
            mock.patch.object(production.catalog, "require_product_repository"),
            mock.patch.object(
                production.catalog,
                "request",
                return_value=(200, {"content": encoded}),
            ),
        ):
            with self.assertRaises(production.catalog.CatalogError):
                production.register("acme", "storageuuid123")


if __name__ == "__main__":
    unittest.main()
