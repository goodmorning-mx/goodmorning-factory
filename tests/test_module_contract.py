import unittest

from scripts.module_contract import ModuleContractError, compile_installation, compose_resources_v3, validate_product_modules
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

    def test_normalizes_managed_secret_bindings_without_values(self):
        value = modules()
        value[0].pop("env")
        value[0]["required_secrets"] = {
            "OPENAI_API_KEY": {
                "source": "GOODMORNING_MANAGED",
                "binding": "goodmorning/openai-default",
                "target": "backend",
            }
        }
        compiled = compile_installation(value)
        assert compiled["secret_bindings"] == [{
            "module": "gm-agent",
            "key": "OPENAI_API_KEY",
            "source": "GOODMORNING_MANAGED",
            "binding": "goodmorning/openai-default",
            "target": "backend",
        }]

    def test_rejects_secret_binding_values_and_duplicate_env_declarations(self):
        value = modules()
        value[0]["required_secrets"] = {"OPENAI_API_KEY": {"source": "GOODMORNING_MANAGED", "binding": "goodmorning/openai-default", "target": "backend"}}
        with self.assertRaises(ModuleContractError):
            validate_product_modules(value)
        value = modules()
        value[0].pop("env")
        value[0]["required_secrets"] = {"OPENAI_API_KEY": {"source": "GOODMORNING_MANAGED", "binding": "goodmorning/openai-default", "target": "sidecar"}}
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

    def test_module_deployment_mode_composes_shared_redis_and_gateway(self):
        value = modules()
        value[0]["deployment_modes"] = ["embedded", "gateway"]
        value[0]["default_deployment_mode"] = "embedded"
        value[0]["deployment_mode"] = "gateway"
        value[0]["resource_requirements"] = [
            {"name": "queue", "kind": "redis", "resource_name": "shared-redis", "shared": True},
            {"name": "gateway", "kind": "gateway", "resource_name": "agent-gateway", "depends_on": ["queue"], "port": 8100, "public": True},
        ]
        value[0]["bindings"] = [{"key": "REDIS_URL", "source": "redis_url", "requirement": "queue", "target": "gateway"}]
        manifest = {
            "topology": "resources-v3", "product": "demo", "repository": "goodmorning-mx/demo", "branch": "dev",
            "coolify_github_app_name": "goodmorning-mx-coolify",
            "applications": [
                {"name": "api", "role": "api", "dockerfile": "/Dockerfile", "port": 8000, "domain": "https://api.dev.demo.goodmorning.mx", "health_path": "/health", "auto_deploy": True, "environment": []},
                {"name": "frontend", "role": "frontend", "dockerfile": "/Dockerfile", "port": 80, "domain": "https://dev.demo.goodmorning.mx", "health_path": "/", "auto_deploy": True, "environment": []},
            ],
            "databases": [{"name": "postgres", "engine": "postgresql", "version": "16", "image": "postgres:16-alpine", "public": False, "backup": True}],
            "redis": [], "modules": value,
            "backup": {"enabled": True, "storage_uuid": "bk8cw0gkswgo04o0s88sk0kg", "frequency": "0 3 * * *", "local_retention_count": 1, "local_retention_days": 1, "s3_retention_count": 7, "s3_retention_days": 7, "timeout_seconds": 1800},
        }
        composed = compose_resources_v3(manifest)
        self.assertEqual([item["name"] for item in composed["redis"]], ["shared-redis"])
        gateway = next(item for item in composed["applications"] if item["name"] == "agent-gateway")
        self.assertEqual(gateway["role"], "gateway")
        self.assertEqual(composed["connections"][0]["source"], {"type": "redis", "resource": "shared-redis"})
        self.assertEqual(composed["resource_ownership"][0]["owner_module"], "gm-agent")
