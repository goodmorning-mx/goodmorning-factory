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
            self.assertIn("v0.1.3", (consumer / "frontend" / "package.json").read_text())
            self.assertIn("v0.1.3", (consumer / "backend" / "requirements.txt").read_text())
            self.assertFalse((consumer / "gm-pwa").exists())
            update_factory_manifest(value, "gm-pwa", "0.1.3")
            self.assertIn('"version": "0.1.3"', manifest.read_text())

    def test_apply_refuses_manual_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); manifest = root / "demo.json"
            value = product(manifest, {"default": {"patch": "manual", "minor": "manual", "major": "blocked"}})
            manifest.write_text(json.dumps(value), encoding="utf-8")
            consumer = root / "consumer"; (consumer / "frontend").mkdir(parents=True)
            (consumer / "frontend" / "package.json").write_text(json.dumps({"dependencies": {"@goodmorning/gm-pwa": "github:goodmorning-mx/gm-pwa#v0.1.2"}}), encoding="utf-8")
            with self.assertRaises(ModuleUpdateError):
                apply_update(consumer, value, REGISTRY["modules"][0], "0.1.3")


if __name__ == "__main__":
    unittest.main()
