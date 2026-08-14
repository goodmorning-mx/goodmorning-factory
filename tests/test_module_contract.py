import unittest

from scripts.module_contract import ModuleContractError, compile_installation, validate_product_modules
from scripts import reconcile_resource_product as resources_contract


def modules():
    return [
        {
            "name": "gm-agent",
            "version": "0.1.0",
            "kind": "full-stack",
            "backend_package": "gm-agent",
            "frontend_package": "@goodmorning/gm-agent",
            "python_requires": ["gm-mcp>=0.1.0", "gm-billing>=0.1.0"],
            "node_requires": ["react>=18"],
            "env": [{"key": "OPENAI_API_KEY", "required": True, "secret": True}],
            "dependencies": ["gm-mcp", "gm-billing"],
            "migrations": {"mode": "alembic", "path": "migrations", "minimum_schema": 1},
            "compatibility": {"python": ">=3.12", "node": ">=20", "factory_api": "^1.0", "products": ["*"]},
            "entrypoints": {"backend": "gm_agent:install", "frontend": "@goodmorning/gm-agent"},
        },
        {
            "name": "gm-billing",
            "version": "0.1.0",
            "kind": "full-stack",
            "backend_package": "gm-billing",
            "frontend_package": "@goodmorning/gm-billing",
            "dependencies": [],
            "migrations": {"mode": "sql", "path": "migrations"},
        },
        {
            "name": "gm-mcp",
            "version": "0.1.0",
            "kind": "backend",
            "backend_package": "gm-mcp",
            "dependencies": [],
        },
    ]


class ModuleContractTests(unittest.TestCase):
    def test_compiles_packages_and_migrations(self):
        result = compile_installation(modules())
        self.assertEqual(["gm-agent", "gm-billing", "gm-mcp"], [m["name"] for m in result["modules"]])
        self.assertEqual(result["python_packages"], ["gm-agent", "gm-billing", "gm-mcp"])
        self.assertEqual(result["migration_order"], ["gm-agent", "gm-billing"])

    def test_rejects_unknown_dependency(self):
        value = modules()[:1]
        with self.assertRaises(ModuleContractError):
            validate_product_modules(value)

    def test_rejects_secret_defaults_and_unknown_fields(self):
        value = modules()
        value[2]["env"] = [{"key": "TOKEN", "secret": True, "default": "bad"}]
        with self.assertRaises(ModuleContractError):
            validate_product_modules(value)

    def test_resources_manifest_accepts_and_normalizes_modules(self):
        manifest = {
            "topology": "resources-v2",
            "product": "demo",
            "repository": "goodmorning-mx/demo",
            "branch": "dev",
            "development_domain": "dev.demo.goodmorning.mx",
            "modules": modules(),
        }
        result = resources_contract.validate_manifest(manifest)
        self.assertEqual([m["name"] for m in result["modules"]], ["gm-agent", "gm-billing", "gm-mcp"])
        value = modules()
        value[2]["unexpected"] = True
        with self.assertRaises(ModuleContractError):
            validate_product_modules(value)
