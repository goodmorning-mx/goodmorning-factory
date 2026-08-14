#!/usr/bin/env python3
"""Validate and normalize reusable GoodMorning module declarations.

The factory stores module metadata in product manifests. It never imports a
module implementation: product repositories install the declared packages and
the runtime composes them through the generated configuration.
"""
from __future__ import annotations

import re
from typing import Any

MODULE_RE = re.compile(r"^[a-z][a-z0-9-]{1,62}$")
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
PACKAGE_RE = re.compile(r"^@?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)?$")
ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
ALLOWED_KINDS = {"backend", "frontend", "full-stack"}
ALLOWED_MIGRATION_MODES = {"none", "alembic", "sql"}
ALLOWED_SECRET_SOURCES = {"GOODMORNING_MANAGED", "CUSTOMER_MANAGED"}
ALLOWED_SECRET_TARGETS = {"backend", "frontend"}
ALLOWED_PWA_UPDATE_MODES = {"prompt"}


class ModuleContractError(ValueError):
    """Raised when a product module declaration is unsafe or incomplete."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ModuleContractError(message)


def _string(value: Any, message: str) -> str:
    _require(isinstance(value, str) and bool(value.strip()), message)
    return value.strip()


def _package(value: Any, field: str) -> str:
    value = _string(value, f"{field} is required.")
    _require(PACKAGE_RE.fullmatch(value) is not None, f"Invalid {field}.")
    return value


def _required_secrets(raw: Any, module_name: str) -> dict[str, dict[str, str]]:
    _require(isinstance(raw, dict), f"required_secrets for {module_name!r} must be an object.")
    normalized: dict[str, dict[str, str]] = {}
    for key, item in raw.items():
        _require(isinstance(key, str) and ENV_KEY_RE.fullmatch(key) is not None, f"Invalid required secret key {key!r}.")
        _require(isinstance(item, dict) and set(item) <= {"source", "binding", "target"}, f"Invalid required secret binding for {key!r}.")
        source = _string(item.get("source"), f"Secret source for {key!r} is required.")
        _require(source in ALLOWED_SECRET_SOURCES, f"Unsupported secret source {source!r} for {key!r}.")
        binding = _string(item.get("binding"), f"Secret binding for {key!r} is required.")
        target = _string(item.get("target"), f"Secret target for {key!r} is required.")
        _require(target in ALLOWED_SECRET_TARGETS, f"Unsupported secret target {target!r} for {key!r}.")
        normalized[key] = {"source": source, "binding": binding, "target": target}
    return normalized


def _version(value: Any, field: str = "version") -> str:
    value = _string(value, f"{field} is required.")
    _require(VERSION_RE.fullmatch(value) is not None, f"Invalid {field}; use semantic versioning.")
    return value


def validate_module(raw: Any) -> dict[str, Any]:
    _require(isinstance(raw, dict), "Module declarations must be objects.")
    allowed = {
        "name", "version", "kind", "backend_package", "frontend_package",
        "python_requires", "node_requires", "env", "dependencies", "migrations",
        "compatibility", "entrypoints", "required_secrets", "pwa",
    }
    _require(set(raw) <= allowed, "Module declaration contains unknown fields.")

    name = _string(raw.get("name"), "Module name is required.")
    _require(MODULE_RE.fullmatch(name) is not None, "Invalid module name.")
    version = _version(raw.get("version"))
    kind = raw.get("kind")
    _require(kind in ALLOWED_KINDS, "Module kind must be backend, frontend, or full-stack.")

    backend = raw.get("backend_package")
    frontend = raw.get("frontend_package")
    if kind in {"backend", "full-stack"}:
        backend = _package(backend, "backend_package")
    else:
        _require(backend is None, "Frontend-only modules cannot declare backend_package.")
    if kind in {"frontend", "full-stack"}:
        frontend = _package(frontend, "frontend_package")
    else:
        _require(frontend is None, "Backend-only modules cannot declare frontend_package.")

    python_requires = raw.get("python_requires", [])
    node_requires = raw.get("node_requires", [])
    _require(isinstance(python_requires, list) and all(isinstance(v, str) and v for v in python_requires), "python_requires must be a list of strings.")
    _require(isinstance(node_requires, list) and all(isinstance(v, str) and v for v in node_requires), "node_requires must be a list of strings.")

    env_raw = raw.get("env", [])
    _require(isinstance(env_raw, list), "Module env must be a list.")
    env: list[dict[str, Any]] = []
    seen_env: set[str] = set()
    for item in env_raw:
        _require(isinstance(item, dict) and set(item) <= {"key", "required", "default", "secret"}, "Invalid module environment declaration.")
        key = _string(item.get("key"), "Module environment key is required.")
        _require(ENV_KEY_RE.fullmatch(key) is not None, f"Invalid module environment key {key!r}.")
        _require(key not in seen_env, f"Duplicate module environment key {key!r}.")
        required = item.get("required", True)
        _require(isinstance(required, bool), f"Module environment required flag for {key!r} must be boolean.")
        secret = item.get("secret", False)
        _require(isinstance(secret, bool), f"Module environment secret flag for {key!r} must be boolean.")
        _require(not (secret and "default" in item), f"Secret module environment {key!r} cannot have a default.")
        env_item: dict[str, Any] = {"key": key, "required": required, "secret": secret}
        if "default" in item:
            _require(isinstance(item["default"], str), f"Default for module environment {key!r} must be a string.")
            env_item["default"] = item["default"]
        env.append(env_item)
        seen_env.add(key)

    required_secrets = _required_secrets(raw.get("required_secrets", {}), name)
    _require(not (set(required_secrets) & seen_env), "A required secret must not also be declared as a module environment variable.")

    dependencies = raw.get("dependencies", [])
    _require(isinstance(dependencies, list) and all(isinstance(v, str) and v for v in dependencies), "Module dependencies must be a list of module names.")
    _require(name not in dependencies and len(dependencies) == len(set(dependencies)), "Module dependencies must be unique and cannot be self-referential.")

    migration_raw = raw.get("migrations", {})
    _require(isinstance(migration_raw, dict) and set(migration_raw) <= {"mode", "path", "minimum_schema"}, "Invalid module migrations declaration.")
    migration_mode = migration_raw.get("mode", "none")
    _require(migration_mode in ALLOWED_MIGRATION_MODES, "Unsupported module migration mode.")
    migration: dict[str, Any] = {"mode": migration_mode}
    if migration_mode != "none":
        path = _string(migration_raw.get("path"), "Migration path is required when migrations are enabled.")
        _require(path.startswith("/") is False and ".." not in path, "Migration path must stay inside the installed module.")
        migration["path"] = path
        minimum_schema = migration_raw.get("minimum_schema", 1)
        _require(isinstance(minimum_schema, int) and minimum_schema >= 1, "minimum_schema must be a positive integer.")
        migration["minimum_schema"] = minimum_schema

    compatibility = raw.get("compatibility", {})
    _require(isinstance(compatibility, dict) and set(compatibility) <= {"python", "node", "factory_api", "products"}, "Invalid module compatibility declaration.")
    normalized_compatibility: dict[str, Any] = {}
    for key in ("python", "node", "factory_api"):
        if key in compatibility:
            normalized_compatibility[key] = _string(compatibility[key], f"compatibility.{key} is required.")
    products = compatibility.get("products", ["*"])
    _require(isinstance(products, list) and products and all(isinstance(v, str) and v for v in products), "compatibility.products must be a non-empty list of strings.")
    normalized_compatibility["products"] = products

    entrypoints = raw.get("entrypoints", {})
    _require(isinstance(entrypoints, dict) and set(entrypoints) <= {"backend", "frontend"}, "Invalid module entrypoints declaration.")

    pwa_raw = raw.get("pwa")
    pwa: dict[str, Any] = {}
    if pwa_raw is not None:
        _require(kind in {"frontend", "full-stack"}, "PWA requirements apply only to frontend modules.")
        _require(isinstance(pwa_raw, dict) and set(pwa_raw) <= {"manifest_required", "service_worker_required", "update_mode", "api_auth_cache"}, "Invalid PWA requirements declaration.")
        for field in ("manifest_required", "service_worker_required"):
            _require(isinstance(pwa_raw.get(field), bool) and pwa_raw[field], f"pwa.{field} must be true.")
        update_mode = _string(pwa_raw.get("update_mode"), "pwa.update_mode is required.")
        _require(update_mode in ALLOWED_PWA_UPDATE_MODES, "PWA updates must use prompt mode.")
        api_auth_cache = _string(pwa_raw.get("api_auth_cache"), "pwa.api_auth_cache is required.")
        _require(api_auth_cache == "network-only", "PWA API/auth cache policy must be network-only.")
        pwa = {"manifest_required": True, "service_worker_required": True, "update_mode": update_mode, "api_auth_cache": api_auth_cache}

    return {
        "name": name,
        "version": version,
        "kind": kind,
        "backend_package": backend,
        "frontend_package": frontend,
        "python_requires": list(python_requires),
        "node_requires": list(node_requires),
        "env": env,
        "required_secrets": required_secrets,
        "dependencies": list(dependencies),
        "migrations": migration,
        "compatibility": normalized_compatibility,
        "entrypoints": dict(entrypoints),
        "pwa": pwa,
    }


def validate_product_modules(raw: Any) -> list[dict[str, Any]]:
    """Validate a product's module list and return it in deterministic order."""
    _require(isinstance(raw, list), "Product modules must be a list.")
    modules = [validate_module(item) for item in raw]
    names = [module["name"] for module in modules]
    _require(len(names) == len(set(names)), "Product modules must have unique names.")
    known = set(names)
    for module in modules:
        _require(set(module["dependencies"]) <= known, f"Module dependencies for {module['name']!r} reference an undeclared module.")
    return sorted(modules, key=lambda module: module["name"])


def compile_installation(raw: Any) -> dict[str, Any]:
    """Compile declarations into package and runtime configuration inputs."""
    modules = validate_product_modules(raw)
    python_packages = [m["backend_package"] for m in modules if m["backend_package"]]
    node_packages = [m["frontend_package"] for m in modules if m["frontend_package"]]
    env = [entry for module in modules for entry in module["env"]]
    secret_bindings = [
        {"module": module["name"], "key": key, **binding}
        for module in modules
        for key, binding in module["required_secrets"].items()
    ]
    return {
        "modules": modules,
        "python_packages": python_packages,
        "node_packages": node_packages,
        "environment": env,
        "secret_bindings": secret_bindings,
        "migration_order": [m["name"] for m in modules if m["migrations"]["mode"] != "none"],
    }
