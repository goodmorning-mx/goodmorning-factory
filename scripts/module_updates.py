#!/usr/bin/env python3
"""Discover and safely apply registered GoodMorning module updates.

The planner is deterministic and read-only.  The apply command only edits a
consumer repository after the caller has explicitly selected a module,
product, and target release.  It never copies module source code.
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable

SEMVER_RE = re.compile(r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")
SAFE_SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
POLICIES = {"auto", "manual", "blocked"}


class ModuleUpdateError(ValueError):
    pass


def version_tuple(value: str) -> tuple[int, int, int, str]:
    match = SEMVER_RE.fullmatch(value.strip())
    if not match:
        raise ModuleUpdateError(f"Invalid semantic version: {value!r}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3)), match.group(4) or ""


def normalize_version(value: str) -> str:
    return ".".join(str(x) for x in version_tuple(value)[:3]) + (("-" + version_tuple(value)[3]) if version_tuple(value)[3] else "")


def classify_update(installed: str, available: str) -> str:
    old, new = version_tuple(installed), version_tuple(available)
    if new <= old:
        return "none"
    if new[0] != old[0]:
        return "major"
    if new[1] != old[1]:
        return "minor"
    return "patch"


def load_registry(path: str | Path) -> dict[str, dict[str, Any]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or raw.get("schema") != 1 or not isinstance(raw.get("modules"), list):
        raise ModuleUpdateError("Module registry must use schema 1.")
    result: dict[str, dict[str, Any]] = {}
    for item in raw["modules"]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str) or not isinstance(item.get("repository"), str):
            raise ModuleUpdateError("Each registry module requires name and repository.")
        if item["name"] in result:
            raise ModuleUpdateError(f"Duplicate registered module {item['name']!r}.")
        policy = item.get("default_policy", {})
        if not isinstance(policy, dict) or set(policy) != {"patch", "minor", "major"} or any(v not in POLICIES for v in policy.values()):
            raise ModuleUpdateError(f"Invalid default policy for {item['name']!r}.")
        result[item["name"]] = dict(item)
    return result


def load_products(products_dir: str | Path) -> list[dict[str, Any]]:
    result = []
    for path in sorted(Path(products_dir).glob("*.json")):
        raw = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("product"), str) and SAFE_SLUG.fullmatch(raw["product"]):
            raw["_path"] = str(path)
            result.append(raw)
    return result


def _tags(repo: str, opener: Callable[[str], Any] | None = None) -> list[str]:
    url = f"https://api.github.com/repos/{urllib.parse.quote(repo, safe='/')}/tags?per_page=100"
    request = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "goodmorning-factory-module-updater"})
    try:
        with (opener or urllib.request.urlopen)(request, timeout=20) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise ModuleUpdateError(f"Unable to discover releases for {repo!r}: {exc}") from exc
    if not isinstance(data, list):
        raise ModuleUpdateError(f"Release discovery for {repo!r} returned an invalid response.")
    return [item["name"] for item in data if isinstance(item, dict) and isinstance(item.get("name"), str) and SEMVER_RE.fullmatch(item["name"])]


def latest_release(module: dict[str, Any], opener: Callable[[str], Any] | None = None) -> str:
    tags = _tags(module["repository"], opener)
    if not tags:
        raise ModuleUpdateError(f"No semantic release tags found for {module['name']!r}.")
    return normalize_version(max(tags, key=version_tuple))


def product_policy(product: dict[str, Any], module: str, default: dict[str, str]) -> dict[str, str]:
    raw = product.get("module_update_policy", {})
    if not isinstance(raw, dict):
        raise ModuleUpdateError(f"Product {product['product']!r} has invalid module_update_policy.")
    chosen = dict(default)
    if isinstance(raw.get("default"), dict):
        chosen.update(raw["default"])
    overrides = raw.get("modules", {})
    if isinstance(overrides, dict) and isinstance(overrides.get(module), dict):
        chosen.update(overrides[module])
    if set(chosen) != {"patch", "minor", "major"} or any(value not in POLICIES for value in chosen.values()):
        raise ModuleUpdateError(f"Product {product['product']!r} has invalid policy for {module!r}.")
    return chosen


def compatibility_blockers(product: dict[str, Any], module: dict[str, Any], target: str) -> list[str]:
    blockers: list[str] = []
    declared = next((m for m in product.get("modules", []) if isinstance(m, dict) and m.get("name") == module["name"]), None)
    if not isinstance(declared, dict):
        return ["module_not_declared"]
    compatibility = declared.get("compatibility", {})
    for key in ("python_requires", "node_requires"):
        if module.get(key) and compatibility.get("python" if key == "python_requires" else "node"):
            # The product declaration is the compatibility contract; absence is
            # safe, contradiction is blocked and never silently rewritten.
            if str(module[key]) not in str(compatibility.get("python" if key == "python_requires" else "node")):
                blockers.append(f"{key}_compatibility_unverified")
    migrations = module.get("migrations", {})
    if isinstance(migrations, dict) and migrations.get("mode", "none") != "none" and not migrations.get("automation_command"):
        blockers.append("migration_requires_explicit_command")
    return blockers


def plan_updates(products_dir: str | Path, registry_path: str | Path, releases: dict[str, str] | None = None, opener: Callable[[str], Any] | None = None) -> dict[str, Any]:
    registry = load_registry(registry_path)
    plans: list[dict[str, Any]] = []
    for product in load_products(products_dir):
        for installed in product.get("modules", []):
            if not isinstance(installed, dict) or installed.get("name") not in registry:
                continue
            name = installed["name"]
            module = registry[name]
            available = normalize_version(releases[name]) if releases and name in releases else latest_release(module, opener)
            change = classify_update(str(installed.get("version", "")), available)
            if change == "none":
                continue
            policy = product_policy(product, name, module["default_policy"])
            blockers = compatibility_blockers(product, module, available)
            action = "blocked" if blockers or policy[change] == "blocked" else policy[change]
            plans.append({"product": product["product"], "module": name, "installed": installed.get("version"), "available": available, "change": change, "policy": policy[change], "action": action, "blockers": blockers})
    return {"schema": 1, "plans": plans, "consumer_count": len({item["product"] for item in plans})}


def _replace_ref(value: str, repository: str, target: str) -> str:
    pattern = rf"(github:|git\+https://github\.com/){re.escape(repository)}(?:\.git)?(?:#|@)v?[^\"'\s]+"
    return re.sub(pattern, lambda match: match.group(0)[:match.group(0).rfind("#")] + "#v" + target if "#" in match.group(0) else match.group(0)[:match.group(0).rfind("@")] + "@v" + target, value)


def apply_update(product_root: str | Path, product: dict[str, Any], module: dict[str, Any], target: str) -> list[str]:
    target = normalize_version(target)
    installed = next((item for item in product.get("modules", []) if isinstance(item, dict) and item.get("name") == module["name"]), None)
    if not installed:
        raise ModuleUpdateError(f"Product does not declare {module['name']!r}.")
    change = classify_update(str(installed.get("version")), target)
    if change == "none":
        return []
    policy = product_policy(product, module["name"], module["default_policy"])
    blockers = compatibility_blockers(product, module, target)
    if blockers or policy[change] != "auto":
        raise ModuleUpdateError(f"Update blocked or requires review: {', '.join(blockers or [policy[change]])}.")
    root = Path(product_root)
    changed: list[str] = []
    installed["version"] = target
    repository = module["repository"]
    reference_found = False
    for path in [root / "backend" / "requirements.txt", root / "requirements.txt"]:
        if path.exists():
            text = path.read_text(encoding="utf-8")
            updated = _replace_ref(text, repository, target)
            backend_package = module.get("backend_package")
            reference_found = reference_found or (isinstance(backend_package, str) and backend_package in text and (f"#v{target}" in text or f"@v{target}" in text))
            if updated != text:
                path.write_text(updated, encoding="utf-8")
                changed.append(str(path))
    package_path = root / "frontend" / "package.json"
    if package_path.exists():
        data = json.loads(package_path.read_text(encoding="utf-8"))
        touched = False
        for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
            for key, value in list(data.get(section, {}).items()):
                if key in {module.get("frontend_package"), module["name"]} and isinstance(value, str):
                    replacement = _replace_ref(value, repository, target)
                    reference_found = reference_found or (f"#v{target}" in value or f"@v{target}" in value)
                    if replacement != value:
                        data[section][key] = replacement; touched = True
        if touched:
            package_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
            changed.append(str(package_path))
    if not changed and not reference_found:
        raise ModuleUpdateError(f"No backend/frontend dependency reference found for {module['name']!r}.")
    return changed


def update_factory_manifest(product: dict[str, Any], module_name: str, target: str) -> str:
    """Persist the installed version in Factory's product catalog."""
    target = normalize_version(target)
    item = next((value for value in product.get("modules", []) if isinstance(value, dict) and value.get("name") == module_name), None)
    if not item:
        raise ModuleUpdateError(f"Product does not declare {module_name!r}.")
    item["version"] = target
    path = Path(product["_path"])
    path.write_text(json.dumps({k: v for k, v in product.items() if not k.startswith("_")}, indent=2) + "\n", encoding="utf-8")
    return str(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--products-dir", default="products")
    parser.add_argument("--registry", default="modules/registry.json")
    parser.add_argument("--releases", help="JSON object for deterministic/offline release resolution")
    parser.add_argument("--module")
    parser.add_argument("--product")
    parser.add_argument("--target")
    parser.add_argument("--product-root")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    releases = json.loads(args.releases) if args.releases else None
    result = plan_updates(args.products_dir, args.registry, releases=releases)
    if args.module and args.product and args.target and args.apply:
        registry = load_registry(args.registry)
        product = next((p for p in load_products(args.products_dir) if p["product"] == args.product), None)
        if not product:
            raise ModuleUpdateError(f"Unknown product {args.product!r}.")
        plan = next((p for p in result["plans"] if p["product"] == args.product and p["module"] == args.module and p["available"] == normalize_version(args.target)), None)
        if not plan or plan["action"] != "auto":
            raise ModuleUpdateError("Selected update is not eligible for automatic application.")
        changed = apply_update(args.product_root or ".", product, registry[args.module], args.target)
        result["applied"] = {"product": args.product, "module": args.module, "target": normalize_version(args.target), "changed": changed, "status": "updated"}
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ModuleUpdateError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}))
        raise SystemExit(2)
