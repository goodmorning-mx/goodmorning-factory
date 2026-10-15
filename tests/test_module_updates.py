import json
import tempfile
import unittest
from pathlib import Path

from scripts.module_updates import (
    ModuleUpdateError,
    apply_update,
    classify_update,
    load_products,
    plan_updates,
    update_factory_manifest,
)


REGISTRY = {
    "schema": 1,
    "modules": [{
        "name": "gm-pwa",
        "repository": "goodmorning-mx/gm-pwa",
        "frontend_package": "@goodmorning/gm-pwa",
        "node_requires": ">=20",
        "default_policy": {"patch": "auto", "minor": "manual", "major": "blocked"},
        "release_candidates": {
            "0.1.3": {
                "version": "0.1.3", "tag": "v0.1.3", "commit_sha": "b" * 40,
                "formal_release": True, "checks_passed": True, "installable": True, "blockers": [],
            }
        },
    }],
}


def product(path: Path, policy=None):
    return {
        "product": "demo",
        "repository": "goodmorning-mx/demo",
        "modules": [{
            "name": "gm-pwa", "version": "0.1.2", "kind": "frontend",
            "frontend_package": "@goodmorning/gm-pwa",
            "compatibility": {"node": ">=20"},
        }],
        "module_update_policy": policy or {"default": {"patch": "auto", "minor": "manual", "major": "blocked"}},
        "_path": str(path),
    }


class ModuleUpdateTests(unittest.TestCase):
    def test_semver_classification(self):
        self.assertEqual(classify_update("0.1.2", "0.1.3"), "patch")
        self.assertEqual(classify_update("0.1.2", "0.2.0"), "minor")
        self.assertEqual(classify_update("0.1.2", "1.0.0"), "major")
        self.assertEqual(classify_update("0.1.2", "0.1.2"), "none")

    def test_plan_finds_consumer_and_policy_action(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            products = root / "products"
            products.mkdir()
            value = product(products / "demo.json")
            (products / "demo.json").write_text(json.dumps(value), encoding="utf-8")
            registry = root / "registry.json"
            registry.write_text(json.dumps(REGISTRY), encoding="utf-8")
            result = plan_updates(products, registry, releases={"gm-pwa": "0.1.3"})
            self.assertEqual(result["consumer_count"], 1)
            self.assertEqual(result["plans"][0]["action"], "auto")
            value["module_update_policy"]["default"]["patch"] = "manual"
            (products / "demo.json").write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(plan_updates(products, registry, releases={"gm-pwa": "0.1.3"})["plans"][0]["action"], "manual")

    def test_major_and_incompatible_updates_are_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); products = root / "products"; products.mkdir()
            value = product(products / "demo.json")
            (products / "demo.json").write_text(json.dumps(value), encoding="utf-8")
            registry = root / "registry.json"; registry.write_text(json.dumps(REGISTRY), encoding="utf-8")
            major = plan_updates(products, registry, releases={"gm-pwa": "1.0.0"})["plans"][0]
            self.assertEqual(major["action"], "blocked")
            value["modules"][0]["compatibility"]["node"] = ">=18"
            (products / "demo.json").write_text(json.dumps(value), encoding="utf-8")
            incompatible = plan_updates(products, registry, releases={"gm-pwa": "0.1.3"})["plans"][0]
            self.assertIn("node_requires_compatibility_unverified", incompatible["blockers"])

    def test_apply_updates_backend_and_frontend_refs_without_copying_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); manifest = root / "products" / "demo.json"; manifest.parent.mkdir()
            value = product(manifest)
            manifest.write_text(json.dumps(value), encoding="utf-8")
            consumer = root / "consumer"; (consumer / "frontend").mkdir(parents=True); (consumer / "backend").mkdir()
            (consumer / "backend" / "requirements.txt").write_text("git+https://github.com/goodmorning-mx/gm-pwa.git@v0.1.2\n", encoding="utf-8")
            (consumer / "frontend" / "package.json").write_text(json.dumps({"dependencies": {"@goodmorning/gm-pwa": "github:goodmorning-mx/gm-pwa#v0.1.2"}}), encoding="utf-8")
            registry_module = REGISTRY["modules"][0]
            changed = apply_update(consumer, value, registry_module, "0.1.3")
            self.assertEqual(len(changed), 2)
            self.assertIn("#" + "b" * 40, (consumer / "frontend" / "package.json").read_text())
            self.assertIn("@" + "b" * 40, (consumer / "backend" / "requirements.txt").read_text())
            self.assertFalse((consumer / "gm-pwa").exists())
            update_factory_manifest(value, "gm-pwa", "0.1.3", REGISTRY["modules"][0])
            self.assertIn('"version": "0.1.3"', manifest.read_text())
            self.assertIn('"source_commit": "' + "b" * 40 + '"', manifest.read_text())

    def test_module_without_reviewed_installable_release_is_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); products = root / "products"; products.mkdir()
            value = product(products / "demo.json")
            (products / "demo.json").write_text(json.dumps(value), encoding="utf-8")
            registry_value = json.loads(json.dumps(REGISTRY))
            registry_value["modules"][0]["release_candidates"]["0.1.3"]["installable"] = False
            registry = root / "registry.json"; registry.write_text(json.dumps(registry_value), encoding="utf-8")
            planned = plan_updates(products, registry, releases={"gm-pwa": "0.1.3"})["plans"][0]
            self.assertEqual(planned["action"], "blocked")
            self.assertIn("release_not_installable", planned["blockers"])
            self.assertIsNone(planned["available_source_commit"])

    def test_apply_refuses_manual_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); manifest = root / "demo.json"
            value = product(manifest, {"default": {"patch": "manual", "minor": "manual", "major": "blocked"}})
            manifest.write_text(json.dumps(value), encoding="utf-8")
            consumer = root / "consumer"; (consumer / "frontend").mkdir(parents=True)
            (consumer / "frontend" / "package.json").write_text(json.dumps({"dependencies": {"@goodmorning/gm-pwa": "github:goodmorning-mx/gm-pwa#v0.1.2"}}), encoding="utf-8")
            with self.assertRaises(ModuleUpdateError):
                apply_update(consumer, value, REGISTRY["modules"][0], "0.1.3")

    def test_additive_infrastructure_change_requires_v3_reconciliation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); products = root / "products"; products.mkdir()
            value = product(products / "demo.json")
            value["modules"][0]["deployment_modes"] = ["embedded", "gateway"]
            value["modules"][0]["default_deployment_mode"] = "embedded"
            value["modules"][0]["deployment_mode"] = "gateway"
            (products / "demo.json").write_text(json.dumps(value), encoding="utf-8")
            registry_value = json.loads(json.dumps(REGISTRY))
            registry_value["modules"][0]["deployment_modes"] = ["embedded", "gateway"]
            registry_value["modules"][0]["default_deployment_mode"] = "gateway"
            registry_value["modules"][0]["resource_requirements"] = [{"name": "gateway", "kind": "gateway", "resource_name": "pwa-gateway", "deployment_modes": ["gateway"]}]
            registry = root / "registry.json"; registry.write_text(json.dumps(registry_value), encoding="utf-8")
            item = plan_updates(products, registry, releases={"gm-pwa": "0.1.3"})["plans"][0]
            self.assertEqual(item["action"], "auto")
            self.assertTrue(item["infrastructure"]["requires_reconcile"])
            self.assertEqual(item["infrastructure"]["added_resources"], ["gateway"])

    def test_infrastructure_removal_blocks_auto_update(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); products = root / "products"; products.mkdir()
            value = product(products / "demo.json")
            value["modules"][0]["deployment_modes"] = ["embedded", "gateway"]
            value["modules"][0]["default_deployment_mode"] = "embedded"
            value["modules"][0]["deployment_mode"] = "gateway"
            value["modules"][0]["resource_requirements"] = [{"name": "gateway", "kind": "gateway", "resource_name": "pwa-gateway", "deployment_modes": ["gateway"]}]
            (products / "demo.json").write_text(json.dumps(value), encoding="utf-8")
            registry_value = json.loads(json.dumps(REGISTRY))
            registry = root / "registry.json"; registry.write_text(json.dumps(registry_value), encoding="utf-8")
            item = plan_updates(products, registry, releases={"gm-pwa": "0.1.3"})["plans"][0]
            self.assertEqual(item["action"], "blocked")
            self.assertIn("infrastructure_resource_removed", item["blockers"])

    def test_update_preserves_consumer_selected_gateway_mode(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); products = root / "products"; products.mkdir()
            value = product(products / "demo.json")
            value["modules"][0]["deployment_modes"] = ["embedded", "gateway"]
            value["modules"][0]["default_deployment_mode"] = "embedded"
            value["modules"][0]["deployment_mode"] = "gateway"
            value["modules"][0]["resource_requirements"] = [{
                "name": "gateway", "kind": "gateway", "resource_name": "pwa-gateway",
                "deployment_modes": ["gateway"],
            }]
            (products / "demo.json").write_text(json.dumps(value), encoding="utf-8")
            registry_value = json.loads(json.dumps(REGISTRY))
            registry_value["modules"][0]["deployment_modes"] = ["embedded", "gateway"]
            registry_value["modules"][0]["default_deployment_mode"] = "embedded"
            registry_value["modules"][0]["resource_requirements"] = [{
                "name": "gateway", "kind": "gateway", "resource_name": "pwa-gateway",
                "deployment_modes": ["gateway"],
            }]
            registry = root / "registry.json"; registry.write_text(json.dumps(registry_value), encoding="utf-8")
            item = plan_updates(products, registry, releases={"gm-pwa": "0.1.3"})["plans"][0]
            self.assertEqual(item["infrastructure"]["mode"], {"from": "gateway", "to": "gateway"})
            self.assertNotIn("infrastructure_default_deployment_mode_changed", item["blockers"])
            self.assertNotIn("infrastructure_resource_removed", item["blockers"])

    def test_explicit_registry_mode_change_is_detected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); products = root / "products"; products.mkdir()
            value = product(products / "demo.json")
            value["modules"][0]["deployment_mode"] = "gateway"
            (products / "demo.json").write_text(json.dumps(value), encoding="utf-8")
            registry_value = json.loads(json.dumps(REGISTRY))
            registry_value["modules"][0]["deployment_modes"] = ["embedded", "gateway"]
            registry_value["modules"][0]["deployment_mode"] = "embedded"
            registry = root / "registry.json"; registry.write_text(json.dumps(registry_value), encoding="utf-8")
            item = plan_updates(products, registry, releases={"gm-pwa": "0.1.3"})["plans"][0]
            self.assertEqual(item["infrastructure"]["mode"], {"from": "gateway", "to": "embedded"})
            self.assertIn("infrastructure_default_deployment_mode_changed", item["blockers"])


if __name__ == "__main__":
    unittest.main()
