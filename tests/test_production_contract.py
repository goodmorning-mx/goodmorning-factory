import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts import provision_product_production as production
from scripts import reconcile_database_backups as backups
from scripts import reconcile_resource_product_production as adapter


class ProductionContractTests(unittest.TestCase):
    def manifest(self):
        return {
            "topology": "resources-v2-production",
            "product": "ledger",
            "repository": "goodmorning-mx/ledger",
            "branch": "main",
            "production_domain": "ledger.goodmorning.mx",
            "backend_base_directory": "/backend",
            "frontend_base_directory": "/frontend",
            "confirm_production": True,
            "backup": {
                "enabled": True,
                "storage_uuid": "c1rrntk8wv4cf3tl3wqviwp3",
                "frequency": "0 2 * * *",
                "local_retention_count": 2,
                "local_retention_days": 2,
                "s3_retention_count": 30,
                "s3_retention_days": 30,
            },
        }

    def test_production_application_contract(self):
        args = SimpleNamespace(product="ledger", repository="goodmorning-mx/ledger")
        context = {
            "project_uuid": "project-1",
            "environment_uuid": "environment-1",
            "server_uuid": "server-1",
            "destination_uuid": "destination-1",
            "github_app_uuid": "github-app-1",
            "git_repository": "goodmorning-mx/ledger",
        }
        backend = production.backend_create_payload(args, context)
        frontend = production.frontend_create_payload(args, context)
        self.assertEqual(backend["name"], "ledger-api-prod")
        self.assertEqual(backend["git_branch"], "main")
        self.assertEqual(backend["environment_name"], "production")
        self.assertEqual(backend["domains"], "https://api.ledger.goodmorning.mx")
        self.assertFalse(backend["is_auto_deploy_enabled"])
        self.assertEqual(frontend["name"], "ledger-frontend-prod")
        self.assertEqual(frontend["domains"], "https://ledger.goodmorning.mx")
        self.assertFalse(frontend["is_auto_deploy_enabled"])
        self.assertNotIn(":8000", backend["domains"])
        self.assertNotIn(":80", frontend["domains"])

    def test_production_manifest_builds_exact_confirmations(self):
        argv = adapter.build_provisioner_argv(
            self.manifest(),
            phase="apply",
            operation="deploy-full",
            server="localhost",
            github_app="goodmorning-mx-coolify",
            destination_uuid="destination-1",
        )
        joined = " ".join(argv)
        self.assertIn("--branch main", joined)
        self.assertIn("--environment production", joined)
        self.assertIn("--confirm-database-start ledger-postgres-prod", joined)
        self.assertIn("--confirm-backend-deployment ledger-api-prod", joined)
        self.assertIn("--confirm-frontend-deployment ledger-frontend-prod", joined)

    def test_production_backup_selects_isolated_database(self):
        manifest = self.manifest()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            loaded = backups.load_manifest(path)
        self.assertEqual(loaded["_backup_environment"], "production")
        self.assertEqual(loaded["_backup_database_name"], "ledger-postgres-prod")

    def test_production_contract_rejects_development_inputs(self):
        manifest = self.manifest()
        manifest["branch"] = "dev"
        with self.assertRaises(adapter.ManifestError):
            adapter.validate_manifest(manifest)
        manifest = self.manifest()
        manifest["production_domain"] = "dev.ledger.goodmorning.mx"
        with self.assertRaises(adapter.ManifestError):
            adapter.validate_manifest(manifest)


if __name__ == "__main__":
    unittest.main()
