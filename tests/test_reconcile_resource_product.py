import json
import tempfile
import unittest
from pathlib import Path

from scripts import reconcile_resource_product as resource


def manifest(**updates):
    value = {
        "topology": "resources-v2",
        "product": "goodmorningmockup",
        "repository": "goodmorning-mx/factory-smoke-test",
        "branch": "dev",
        "development_domain": "dev.goodmorningmockup.goodmorning.mx",
    }
    value.update(updates)
    return value


class ResourceManifestTests(unittest.TestCase):
    def test_manifest_accepts_repository_independent_from_product_slug(self):
        self.assertEqual(
            resource.validate_manifest(manifest())["repository"],
            "goodmorning-mx/factory-smoke-test",
        )

    def test_manifest_rejects_noncanonical_domain(self):
        with self.assertRaises(resource.ManifestError):
            resource.validate_manifest(manifest(development_domain="temporary.example.com"))

    def test_manifest_rejects_compose_topology(self):
        with self.assertRaises(resource.ManifestError):
            resource.validate_manifest(manifest(topology="compose-v1"))

    def test_load_manifest_reads_valid_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "product.json")
            path.write_text(json.dumps(manifest()), encoding="utf-8")
            self.assertEqual(resource.load_manifest(path)["product"], "goodmorningmockup")

    def test_apply_full_builds_all_exact_confirmations(self):
        argv = resource.build_provisioner_argv(
            manifest(),
            phase="apply",
            operation="deploy-full",
            server="localhost",
            github_app="goodmorning-mx-coolify",
            destination_uuid="bk8cw0gkswgo04o0s88sk0kg",
        )
        self.assertIn("--apply", argv)
        self.assertIn("--deploy-backend", argv)
        self.assertIn("--deploy-frontend", argv)
        self.assertIn("goodmorning-mx/factory-smoke-test", argv)
        self.assertIn("goodmorningmockup-postgres-dev", argv)
        self.assertIn("goodmorningmockup-api-dev", argv)
        self.assertIn("goodmorningmockup-frontend-dev", argv)

    def test_plan_never_contains_write_confirmations(self):
        argv = resource.build_provisioner_argv(
            manifest(),
            phase="plan",
            operation="deploy-full",
            server="localhost",
            github_app="goodmorning-mx-coolify",
            destination_uuid="destination",
        )
        self.assertIn("--dry-run", argv)
        self.assertNotIn("--confirm-product", argv)


if __name__ == "__main__":
    unittest.main()
