#!/usr/bin/env python3
"""Require newly added GoodMorning module dependencies to use approved SHAs."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

SHA_RE = re.compile(r"^[0-9a-f]{40}$")
REQ_RE = re.compile(r"^\s*([A-Za-z0-9_.-]+)(?:\[[^]]+\])?\s*(.*)$")


class ModuleInstallPinError(ValueError):
    pass


def _normalized_package(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _module_records(registry: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(registry, dict):
        raise ModuleInstallPinError("Module registry must be an object.")
    modules = registry.get("modules")
    if registry.get("schema") != 1 or not isinstance(modules, list):
        raise ModuleInstallPinError("Module registry must use schema 1.")
    result: dict[str, dict[str, Any]] = {}
    for item in modules:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise ModuleInstallPinError("Each registry module requires a name.")
        if item["name"] in result:
            raise ModuleInstallPinError(f"Duplicate module {item['name']!r} in registry.")
        result[item["name"]] = item
    return result


def _approved_commit(module: dict[str, Any], sha: str) -> bool:
    if not SHA_RE.fullmatch(sha):
        return False
    candidates = module.get("release_candidates", {})
    if not isinstance(candidates, dict):
        return False
    return any(
        isinstance(record, dict)
        and record.get("commit_sha") == sha
        and record.get("installable") is True
        and record.get("formal_release") is True
        and record.get("checks_passed") is True
        and not record.get("blockers")
        for record in candidates.values()
    )


def _requirements(contents: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in contents.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-")):
            continue
        match = REQ_RE.match(line)
        if match:
            package = _normalized_package(match.group(1))
            if package.startswith("gm-"):
                if package in result:
                    raise ModuleInstallPinError(f"Duplicate GoodMorning dependency {package!r} in requirements file.")
                result[package] = line.strip()
    return result


def _package_dependencies(contents: str) -> dict[str, str]:
    try:
        document = json.loads(contents)
    except json.JSONDecodeError as exc:
        raise ModuleInstallPinError("Invalid package.json while validating GoodMorning module pins.") from exc
    result: dict[str, str] = {}
    for section in ("dependencies", "devDependencies", "peerDependencies", "optionalDependencies"):
        values = document.get(section, {})
        if not isinstance(values, dict):
            continue
        for name, value in values.items():
            if not isinstance(name, str) or not isinstance(value, str):
                continue
            package = name.rsplit("/", 1)[-1] if name.startswith("@goodmorning/") else name
            package = _normalized_package(package)
            if package.startswith("gm-"):
                if package in result and result[package] != value:
                    raise ModuleInstallPinError(f"Conflicting GoodMorning dependency declarations for {package!r}.")
                result[package] = value
    return result


def _validate_requirement(module_name: str, value: str, module: dict[str, Any], path: str) -> None:
    match = re.fullmatch(
        rf"{re.escape(module_name)}\s*@\s*git\+https://github\.com/goodmorning-mx/{re.escape(module_name)}\.git@([0-9a-f]{{40}})",
        value,
        flags=re.IGNORECASE,
    )
    if not match or not _approved_commit(module, match.group(1)):
        raise ModuleInstallPinError(f"{path}: changed dependency {module_name!r} must use an approved full GitHub commit SHA.")


def _validate_npm_dependency(module_name: str, value: str, module: dict[str, Any], path: str) -> None:
    patterns = (
        rf"git\+https://github\.com/goodmorning-mx/{re.escape(module_name)}\.git#([0-9a-f]{{40}})",
        rf"github:goodmorning-mx/{re.escape(module_name)}#([0-9a-f]{{40}})",
        rf"https://github\.com/goodmorning-mx/{re.escape(module_name)}/archive/([0-9a-f]{{40}})\.tar\.gz",
    )
    sha = next((match.group(1) for pattern in patterns if (match := re.fullmatch(pattern, value, flags=re.IGNORECASE))), None)
    if not sha or not _approved_commit(module, sha):
        raise ModuleInstallPinError(f"{path}: changed dependency {module_name!r} must use an approved full GitHub commit SHA.")


def _base_contents(root: Path, path: Path, base_ref: str) -> str:
    relative = path.relative_to(root).as_posix()
    result = subprocess.run(
        ["git", "-C", str(root), "show", f"{base_ref}:{relative}"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout if result.returncode == 0 else ""


def validate_repository(
    root: str | Path,
    registry: dict[str, Any],
    *,
    base_ref: str = "HEAD",
    read_base: Callable[[Path], str] | None = None,
) -> list[str]:
    root_path = Path(root).resolve()
    modules = _module_records(registry)
    base_reader = read_base or (lambda path: _base_contents(root_path, path, base_ref))
    checked: list[str] = []

    dependency_files = sorted({*root_path.rglob("requirements*.txt"), *root_path.rglob("package.json")})
    for path in dependency_files:
        if any(part in {"node_modules", ".git", ".venv", "venv"} for part in path.relative_to(root_path).parts):
            continue
        relative = path.relative_to(root_path).as_posix()
        current_text = path.read_text(encoding="utf-8")
        old_text = base_reader(path)
        current = _package_dependencies(current_text) if path.name == "package.json" else _requirements(current_text)
        old = _package_dependencies(old_text) if path.name == "package.json" and old_text else _requirements(old_text)
        for package, value in current.items():
            if old.get(package) == value:
                continue  # Existing legacy references remain untouched and readable.
            module = modules.get(package)
            if not module:
                raise ModuleInstallPinError(f"{relative}: module {package!r} is not registered with Factory.")
            if path.name == "package.json":
                expected_package = module.get("frontend_package")
                if expected_package and package != _normalized_package(expected_package.rsplit("/", 1)[-1]):
                    raise ModuleInstallPinError(f"{relative}: {package!r} does not match the registered frontend package.")
                _validate_npm_dependency(package, value, module, relative)
            else:
                expected_package = module.get("backend_package")
                if expected_package and package != _normalized_package(expected_package):
                    raise ModuleInstallPinError(f"{relative}: {package!r} does not match the registered backend package.")
                _validate_requirement(package, value, module, relative)
            checked.append(f"{relative}:{package}")
    return checked


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, help="Product repository checkout to validate.")
    parser.add_argument("--registry", default="modules/registry.json")
    parser.add_argument("--base-ref", default="HEAD")
    args = parser.parse_args(argv)
    try:
        registry = json.loads(Path(args.registry).read_text(encoding="utf-8"))
        checked = validate_repository(args.root, registry, base_ref=args.base_ref)
    except (OSError, json.JSONDecodeError, ModuleInstallPinError) as exc:
        parser.error(str(exc))
    print(json.dumps({"status": "valid", "pinned_module_dependencies": checked}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
