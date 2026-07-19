import base64
import json
import unittest
from unittest import mock

import scripts.register_product_manifest as catalog


class RegisterProductManifestTests(unittest.TestCase):
    def test_manifest_targets_dedicated_product_repository(self):
        manifest = catalog.build_manifest("acme", "storageuuid123")
        self.assertEqual(manifest["repository"], "goodmorning-mx/acme")
        self.assertEqual(manifest["development_domain"], "dev.acme.goodmorning.mx")
        self.assertEqual(manifest["branch"], "dev")
        self.assertTrue(manifest["backup"]["enabled"])

    def test_rejects_invalid_product_storage_or_branch(self):
        for product, storage, branch in (
            ("Bad", "storageuuid123", "dev"),
            ("good", "bad!", "dev"),
            ("good", "storageuuid123", "main"),
        ):
            with self.assertRaises(catalog.CatalogError):
                catalog.validate(product, storage, branch)

    def test_registers_new_manifest_after_repository_verification(self):
        calls = []

        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs.get("body")))
            if path == "/repos/goodmorning-mx/acme":
                return 200, {"private": True, "default_branch": "dev"}
            if path.endswith("/branches/main") or path.endswith("/branches/dev"):
                return 200, {"name": path.rsplit("/", 1)[-1]}
            if method == "GET" and "/contents/products/acme.json" in path:
                return 404, {"message": "Not Found"}
            if method == "PUT" and "/contents/products/acme.json" in path:
                body = kwargs["body"]
                decoded = json.loads(base64.b64decode(body["content"]).decode("utf-8"))
                self.assertEqual(decoded["repository"], "goodmorning-mx/acme")
                self.assertEqual(body["branch"], "dev")
                return 201, {"commit": {"sha": "commit123"}}
            raise AssertionError((method, path))

        with mock.patch.object(catalog, "request", side_effect=fake_request):
            result = catalog.register("acme", "storageuuid123")
        self.assertEqual(result["status"], "registered")
        self.assertEqual(result["commit_sha"], "commit123")
        self.assertTrue(any(method == "PUT" for method, _, _ in calls))

    def test_exact_existing_manifest_is_idempotent(self):
        manifest = catalog.build_manifest("acme", "storageuuid123")
        encoded = base64.b64encode(json.dumps(manifest).encode("utf-8")).decode("ascii")

        def fake_request(method, path, **kwargs):
            if path == "/repos/goodmorning-mx/acme":
                return 200, {"private": True, "default_branch": "dev"}
            if path.endswith("/branches/main") or path.endswith("/branches/dev"):
                return 200, {}
            if "/contents/products/acme.json" in path:
                return 200, {"content": encoded}
            raise AssertionError((method, path))

        with mock.patch.object(catalog, "request", side_effect=fake_request):
            result = catalog.register("acme", "storageuuid123")
        self.assertEqual(result["status"], "unchanged")

    def test_incompatible_existing_manifest_fails_closed(self):
        incompatible = catalog.build_manifest("acme", "storageuuid123")
        incompatible["repository"] = "goodmorning-mx/shared"
        encoded = base64.b64encode(json.dumps(incompatible).encode("utf-8")).decode("ascii")

        def fake_request(method, path, **kwargs):
            if path == "/repos/goodmorning-mx/acme":
                return 200, {"private": True, "default_branch": "dev"}
            if path.endswith("/branches/main") or path.endswith("/branches/dev"):
                return 200, {}
            if "/contents/products/acme.json" in path:
                return 200, {"content": encoded}
            raise AssertionError((method, path))

        with mock.patch.object(catalog, "request", side_effect=fake_request):
            with self.assertRaises(catalog.CatalogError):
                catalog.register("acme", "storageuuid123")


if __name__ == "__main__":
    unittest.main()
