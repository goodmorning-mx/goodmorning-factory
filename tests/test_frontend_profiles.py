import json
import unittest
from pathlib import Path

from scripts.advanced_product_contract import ContractError, validate_manifest
from scripts.module_contract import compose_resources_v3


BACKUP = {
    "enabled": True,
    "storage_uuid": "c1rrntk8wv4cf3tl3wqviwp3",
    "frequency": "0 3 * * *",
    "local_retention_count": 1,
    "local_retention_days": 1,
    "s3_retention_count": 7,
    "s3_retention_days": 7,
    "timeout_seconds": 1800,
}


def base_manifest() -> dict:
    return {
        "topology": "resources-v3",
        "product": "profile-test",
        "repository": "goodmorning-mx/profile-test",
        "branch": "dev",
        "development_domain": "dev.profile-test.goodmorning.mx",
        "coolify_github_app_name": "goodmorning-mx-coolify",
        "applications": [
            {
                "name": "api", "role": "api", "base_directory": "/backend", "dockerfile": "/Dockerfile",
                "port": 8000, "domain": "https://api.dev.profile-test.goodmorning.mx", "health_path": "/health",
                "auto_deploy": True, "environment": [],
            }
        ],
        "databases": [{"name": "postgres", "engine": "postgresql", "version": "16", "image": "postgres:16-alpine", "public": False, "backup": True}],
        "redis": [],
        "backup": BACKUP,
    }


class FrontendProfileTests(unittest.TestCase):
    def test_existing_manifests_default_to_react_without_catalog_edits(self):
        root = Path(__file__).parents[1]
        for path in (root / "products").glob("*.json"):
            raw = json.loads(path.read_text())
            if raw.get("topology") not in {"resources-v3", "resources-v3-production"}:
                continue
            normalized = validate_manifest(raw)
            self.assertEqual(normalized["frontend_profile"], raw.get("frontend_profile", "react"), path)


    def test_frontend_profiles_are_valid(self):
        for profile in ("react", "next", "hybrid"):
            with self.subTest(profile=profile):
                value = base_manifest()
                value["frontend_profile"] = profile
                if profile == "react":
                    value["applications"].append({
                        "name": "frontend", "role": "frontend", "base_directory": "/frontend", "dockerfile": "/Dockerfile",
                        "port": 80, "domain": "https://dev.profile-test.goodmorning.mx", "health_path": "/", "auto_deploy": True, "environment": [],
                    })
                elif profile == "next":
                    value["applications"].append({
                        "name": "seo", "role": "frontend", "base_directory": "/seo", "dockerfile": "/Dockerfile",
                        "port": 3000, "domain": "https://dev.profile-test.goodmorning.mx", "health_path": "/health", "auto_deploy": True, "environment": [],
                    })
                else:
                    value["applications"].extend([
                        {"name": "frontend", "role": "frontend", "base_directory": "/frontend", "dockerfile": "/Dockerfile", "port": 80, "domain": None, "health_path": "/", "auto_deploy": True, "environment": []},
                        {"name": "seo", "role": "frontend", "base_directory": "/seo", "dockerfile": "/Dockerfile", "port": 3000, "domain": None, "health_path": "/health", "auto_deploy": True, "environment": []},
                        {"name": "frontend-gateway", "role": "gateway", "base_directory": "/gateway", "dockerfile": "/Dockerfile", "port": 80, "domain": "https://dev.profile-test.goodmorning.mx", "health_path": "/health", "auto_deploy": True, "environment": [], "dependencies": [
                            {"type": "application", "name": "frontend"}, {"type": "application", "name": "seo"}, {"type": "application", "name": "api"},
                        ]},
                    ])
                    value["connections"] = [
                        {"target": "frontend-gateway", "key": "REACT_UPSTREAM", "source": {"type": "application", "resource": "frontend"}},
                        {"target": "frontend-gateway", "key": "NEXT_UPSTREAM", "source": {"type": "application", "resource": "seo"}},
                        {"target": "frontend-gateway", "key": "API_UPSTREAM", "source": {"type": "application", "resource": "api"}},
                    ]
                    value["routing"] = {
                        "mode": "hybrid", "host": "https://dev.profile-test.goodmorning.mx",
                        "gateway": {"required": True, "public_application": "frontend-gateway", "routes": [
                            {"path": "/", "application": "seo"}, {"path": "/seo", "application": "seo"},
                            {"path": "/app", "application": "frontend"}, {"path": "/api", "application": "api"},
                        ]},
                    }
                self.assertEqual(validate_manifest(value)["frontend_profile"], profile)


    def test_profile_falls_back_to_react(self):
        value = base_manifest()
        value["applications"].append({
            "name": "frontend", "role": "frontend", "base_directory": "/frontend", "dockerfile": "/Dockerfile",
            "port": 80, "domain": "https://dev.profile-test.goodmorning.mx", "health_path": "/", "auto_deploy": True, "environment": [],
        })
        self.assertEqual(validate_manifest(value)["frontend_profile"], "react")


    def test_invalid_profile_is_rejected(self):
        value = base_manifest()
        value["frontend_profile"] = "astro"
        with self.assertRaises(ContractError):
            validate_manifest(value)


    def test_next_profile_rejects_noncanonical_domain(self):
        value = base_manifest()
        value["frontend_profile"] = "next"
        value["applications"].append({
            "name": "seo", "role": "frontend", "base_directory": "/seo", "dockerfile": "/Dockerfile",
            "port": 3000, "domain": "https://app.profile-test.goodmorning.mx", "health_path": "/health", "auto_deploy": True, "environment": [],
        })
        with self.assertRaises(ContractError):
            validate_manifest(value)


    def test_resources_v3_composes_next_and_hybrid_surfaces(self):
        value = base_manifest()
        value["modules"] = []
        value["frontend_profile"] = "next"
        next_result = compose_resources_v3(value)
        self.assertEqual([(item["name"], item["base_directory"], item["port"]) for item in next_result["applications"]], [("api", "/backend", 8000), ("seo", "/seo", 3000)])
        self.assertEqual(next_result["applications"][1]["environment"], [{"key": "NEXT_PUBLIC_API_URL", "source": "literal", "value": "https://api.dev.profile-test.goodmorning.mx"}])
        self.assertEqual(next_result["routing"]["application"], "seo")

        value["frontend_profile"] = "react"
        react_result = compose_resources_v3(value)
        self.assertEqual({item["name"] for item in react_result["applications"]}, {"api", "frontend"})
        self.assertEqual(react_result["applications"][1]["port"], 80)

        value["frontend_profile"] = "hybrid"
        hybrid_result = compose_resources_v3(value)
        self.assertEqual({item["name"] for item in hybrid_result["applications"]}, {"api", "frontend", "seo", "frontend-gateway"})
        self.assertTrue(hybrid_result["routing"]["gateway"]["required"])
        self.assertEqual(hybrid_result["routing"]["gateway"]["public_application"], "frontend-gateway")
        self.assertEqual(hybrid_result["routing"]["gateway"]["routes"][0], {"path": "/", "application": "seo"})
        self.assertEqual({item["key"] for item in hybrid_result["connections"]}, {"REACT_UPSTREAM", "NEXT_UPSTREAM", "API_UPSTREAM"})
