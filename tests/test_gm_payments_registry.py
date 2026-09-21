import json
from pathlib import Path


def test_gm_payments_is_registered_as_reusable_backend_only_module():
    root = Path(__file__).parents[1]
    registry = json.loads((root / "modules/registry.json").read_text())
    entry = next(item for item in registry["modules"] if item["name"] == "gm-payments")
    assert entry["repository"] == "goodmorning-mx/gm-payments"
    assert entry["backend_package"] == "gm-payments"
    assert "frontend_package" not in entry
    assert entry["deployment_modes"] == ["embedded", "service"]
    assert entry["migrations"] == {"mode": "sql", "path": "migrations", "minimum_schema": 1}
    assert set(entry["required_secrets"]) == {"STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET"}
    assert entry["default_policy"] == {"patch": "auto", "minor": "manual", "major": "blocked"}


def test_gm_payments_is_not_added_to_existing_product_manifests():
    root = Path(__file__).parents[1]
    for path in (root / "products").glob("*.json"):
        value = json.loads(path.read_text())
        names = {module.get("name") for module in value.get("modules", [])}
        if path.name == "cv.json":
            assert "gm-payments" in names
        else:
            assert "gm-payments" not in names, path
