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
ALLOWED_DEPLOYMENT_MODES = {"embedded", "service", "worker", "gateway"}
ALLOWED_REQUIREMENT_KINDS = {"postgresql", "timescaledb", "redis", "service", "worker", "gateway"}
ALLOWED_BINDING_SOURCES = {"database_url", "redis_url", "service_url", "generated_secret", "managed_secret"}


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


def _deployment_contract(raw: Any, module_name: str) -> tuple[list[str], str, str | None]:
    modes = raw.get("deployment_modes", ["embedded"])
    _require(
        isinstance(modes, list) and modes and len(modes) == len(set(modes))
        and all(mode in ALLOWED_DEPLOYMENT_MODES for mode in modes),
        f"deployment_modes for {module_name!r} are invalid.",
    )
    default = raw.get("default_deployment_mode", modes[0])
    _require(default in modes, f"default_deployment_mode for {module_name!r} must be declared in deployment_modes.")
    selected = raw.get("deployment_mode")
    _require(selected is None or selected in modes, f"deployment_mode for {module_name!r} must be declared in deployment_modes.")
    return list(modes), default, selected


def _resource_requirements(raw: Any, module_name: str) -> list[dict[str, Any]]:
    requirements_raw = raw.get("resource_requirements", [])
    _require(isinstance(requirements_raw, list), f"resource_requirements for {module_name!r} must be a list.")
    result: list[dict[str, Any]] = []
    names: set[str] = set()
    for item in requirements_raw:
        _require(isinstance(item, dict), f"Resource requirements for {module_name!r} must be objects.")
        allowed = {
            "name", "kind", "resource_name", "shared", "optional", "deployment_modes",
            "depends_on", "role", "base_directory", "dockerfile", "port", "public", "health_path",
        }
        _require(set(item) <= allowed, f"Unknown resource requirement field for {module_name!r}.")
        name = _string(item.get("name"), f"Resource requirement name for {module_name!r} is required.")
        _require(re.fullmatch(r"[a-z][a-z0-9-]{0,62}", name) is not None, f"Invalid resource requirement name {name!r}.")
        _require(name not in names, f"Duplicate resource requirement {name!r} for {module_name!r}.")
        kind = item.get("kind")
        _require(kind in ALLOWED_REQUIREMENT_KINDS, f"Unsupported resource requirement kind {kind!r}.")
        resource_name = item.get("resource_name", name)
        _require(isinstance(resource_name, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,62}", resource_name), f"Invalid resource_name for {module_name!r}/{name!r}.")
        modes = item.get("deployment_modes", [])
        _require(isinstance(modes, list) and len(modes) == len(set(modes)) and all(mode in ALLOWED_DEPLOYMENT_MODES for mode in modes), f"Invalid deployment_modes for resource {module_name!r}/{name!r}.")
        depends_on = item.get("depends_on", [])
        _require(isinstance(depends_on, list) and len(depends_on) == len(set(depends_on)) and all(isinstance(value, str) and value for value in depends_on), f"Invalid depends_on for resource {module_name!r}/{name!r}.")
        normalized: dict[str, Any] = {
            "name": name, "kind": kind, "resource_name": resource_name,
            "shared": item.get("shared", False), "optional": item.get("optional", False),
            "deployment_modes": list(modes), "depends_on": list(depends_on),
        }
        _require(isinstance(normalized["shared"], bool) and isinstance(normalized["optional"], bool), f"Resource sharing flags for {module_name!r}/{name!r} must be boolean.")
        if kind in {"service", "worker", "gateway"}:
            role = item.get("role", "gateway" if kind in {"service", "gateway"} else "worker")
            _require(role in {"gateway", "worker"}, f"Module resource {module_name!r}/{name!r} must use gateway or worker role.")
            base_directory = item.get("base_directory", "/")
            dockerfile = item.get("dockerfile", "/Dockerfile")
            port = item.get("port", 8000)
            health_path = item.get("health_path", "/health")
            _require(isinstance(base_directory, str) and base_directory.startswith("/") and ".." not in base_directory, f"Invalid base_directory for {module_name!r}/{name!r}.")
            _require(isinstance(dockerfile, str) and dockerfile.startswith("/") and ".." not in dockerfile, f"Invalid dockerfile for {module_name!r}/{name!r}.")
            _require(isinstance(port, int) and 1 <= port <= 65535, f"Invalid port for {module_name!r}/{name!r}.")
            _require(isinstance(health_path, str) and health_path.startswith("/"), f"Invalid health_path for {module_name!r}/{name!r}.")
            public = item.get("public", kind in {"service", "gateway"})
            _require(isinstance(public, bool), f"public for {module_name!r}/{name!r} must be boolean.")
            normalized.update({"role": role, "base_directory": base_directory, "dockerfile": dockerfile, "port": port, "public": public, "health_path": health_path})
        else:
            _require(set(item) <= {"name", "kind", "resource_name", "shared", "optional", "deployment_modes", "depends_on"}, f"Data resource {module_name!r}/{name!r} contains application-only fields.")
        result.append(normalized)
        names.add(name)
    for requirement in result:
        _require(set(requirement["depends_on"]) <= names, f"Resource dependency for {module_name!r}/{requirement['name']!r} references an undeclared requirement.")
    by_name = {item["name"]: item for item in result}
    ordered: list[dict[str, Any]] = []
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(name: str) -> None:
        _require(name not in visiting, f"Resource dependencies cannot contain cycles for {module_name!r}.")
        if name in visited:
            return
        visiting.add(name)
        for dependency in by_name[name]["depends_on"]:
            visit(dependency)
        visiting.remove(name)
        visited.add(name)
        ordered.append(by_name[name])
    for item in result:
        visit(item["name"])
    return ordered


def _bindings(raw: Any, module_name: str) -> list[dict[str, Any]]:
    bindings_raw = raw.get("bindings", [])
    _require(isinstance(bindings_raw, list), f"bindings for {module_name!r} must be a list.")
    result: list[dict[str, Any]] = []
    keys: set[tuple[str, str]] = set()
    requirement_names = {item["name"] for item in _resource_requirements(raw, module_name)}
    for item in bindings_raw:
        _require(isinstance(item, dict) and set(item) <= {"key", "source", "requirement", "target", "binding", "driver"}, f"Invalid module binding for {module_name!r}.")
        key = item.get("key")
        source = item.get("source")
        target = item.get("target", "backend")
        _require(isinstance(key, str) and ENV_KEY_RE.fullmatch(key), f"Invalid module binding key for {module_name!r}.")
        _require(source in ALLOWED_BINDING_SOURCES, f"Unsupported module binding source {source!r}.")
        _require(target in {"backend", "frontend"} | requirement_names, f"Invalid module binding target {target!r} for {module_name!r}.")
        _require(target != "frontend" or source not in {"generated_secret", "managed_secret"}, "Secrets must never be bound to frontend resources.")
        requirement = item.get("requirement")
        if source in {"database_url", "redis_url", "service_url"}:
            _require(requirement in requirement_names, f"Module binding {key!r} references an unknown requirement.")
        else:
            _require(requirement is None, f"Module binding {key!r} cannot reference a resource for {source!r}.")
        if source in {"generated_secret", "managed_secret"}:
            if source == "managed_secret":
                binding = item.get("binding")
                _require(isinstance(binding, str) and binding, f"Managed secret binding for {key!r} is required.")
            _require("driver" not in item, f"Secret binding {key!r} cannot declare a driver.")
        elif source != "database_url":
            _require("driver" not in item, f"Only database bindings may declare a driver.")
        pair = (target, key)
        _require(pair not in keys, f"Duplicate module binding {target}/{key}.")
        keys.add(pair)
        normalized = {"key": key, "source": source, "target": target}
        if requirement is not None:
            normalized["requirement"] = requirement
        if item.get("binding") is not None:
            normalized["binding"] = item["binding"]
        if item.get("driver") is not None:
            _require(item["driver"] == "psycopg", f"Unsupported module binding driver for {key!r}.")
            normalized["driver"] = item["driver"]
        result.append(normalized)
    return result


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
        "deployment_modes", "default_deployment_mode", "deployment_mode",
        "resource_requirements", "bindings",
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

    deployment_modes, default_deployment_mode, deployment_mode = _deployment_contract(raw, name)
    resource_requirements = _resource_requirements(raw, name)
    bindings = _bindings(raw, name)

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
        "deployment_modes": deployment_modes,
        "default_deployment_mode": default_deployment_mode,
        "deployment_mode": deployment_mode or default_deployment_mode,
        "resource_requirements": resource_requirements,
        "bindings": bindings,
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


def module_dependency_order(modules: list[dict[str, Any]]) -> list[str]:
    """Return module names in dependency-first order without changing catalog order."""
    by_name = {module["name"]: module for module in modules}
    visiting: set[str] = set()
    visited: set[str] = set()
    order: list[str] = []

    def visit(name: str) -> None:
        if name in visiting:
            raise ModuleContractError("Module dependencies cannot contain cycles.")
        if name in visited:
            return
        visiting.add(name)
        for dependency in by_name[name]["dependencies"]:
            visit(dependency)
        visiting.remove(name)
        visited.add(name)
        order.append(name)

    for module in modules:
        visit(module["name"])
    return order


def compose_resources_v3(manifest: dict[str, Any]) -> dict[str, Any]:
    """Compile module requirements into one Resources V3 desired-state manifest.

    The function is pure: it only returns a new manifest and never talks to
    Coolify. Existing resources are referenced by stable names and are never
    duplicated when a module marks a requirement as shared.
    """
    _require(isinstance(manifest, dict), "Product manifest must be an object.")
    modules = validate_product_modules(manifest.get("modules", []))
    result = {key: value for key, value in manifest.items()}
    result["modules"] = modules
    result["module_dependency_order"] = module_dependency_order(modules)
    if manifest.get("topology") not in {"resources-v3", "resources-v3-production"}:
        return result

    applications = [dict(item) for item in manifest.get("applications", []) if isinstance(item, dict)]
    databases = [dict(item) for item in manifest.get("databases", []) if isinstance(item, dict)]
    redis = [dict(item) for item in manifest.get("redis", []) if isinstance(item, dict)]
    connections = [dict(item) for item in manifest.get("connections", []) if isinstance(item, dict)]
    ownership = [dict(item) for item in manifest.get("resource_ownership", []) if isinstance(item, dict)]
    app_by_name = {item.get("name"): item for item in applications}
    db_names = {item.get("name") for item in databases}
    redis_names = {item.get("name") for item in redis}
    module_by_name = {module["name"]: module for module in modules}
    requirement_resources: dict[tuple[str, str], tuple[str, str]] = {}
    module_requirement_resources: dict[tuple[str, str], str] = {}
    existing_by_key: dict[tuple[str, str], str] = {}
    for item in ownership:
        if isinstance(item.get("owner_module"), str) and isinstance(item.get("requirement"), str) and isinstance(item.get("kind"), str) and isinstance(item.get("resource"), str):
            existing_by_key[(item["owner_module"], item["requirement"])] = item["resource"]

    def target_app(target: str) -> str:
        if target in app_by_name:
            return target
        if target == "backend":
            candidates = [item["name"] for item in applications if item.get("role") == "api"]
        else:
            candidates = [item["name"] for item in applications if item.get("role") == "frontend"]
        _require(len(candidates) == 1, f"Module binding target {target!r} requires exactly one matching base application.")
        return candidates[0]

    def add_env(target: str, entry: dict[str, Any]) -> None:
        app_name = target_app(target)
        environment = app_by_name[app_name].setdefault("environment", [])
        existing = next((item for item in environment if item.get("key") == entry["key"]), None)
        if existing is not None:
            _require(existing == entry, f"Module binding {app_name}/{entry['key']} conflicts with an existing environment key.")
            return
        environment.append(entry)

    for module_name in result["module_dependency_order"]:
        module = module_by_name[module_name]
        selected_mode = module["deployment_mode"]
        for requirement in module["resource_requirements"]:
            active_modes = requirement["deployment_modes"]
            if active_modes and selected_mode not in active_modes:
                continue
            local_name = requirement["name"]
            kind = requirement["kind"]
            resource_name = existing_by_key.get((module_name, local_name), requirement["resource_name"])
            resource_key = (kind, resource_name)
            if resource_key in requirement_resources:
                resource_name = requirement_resources[resource_key][0]
            elif kind in {"postgresql", "timescaledb"}:
                _require(resource_name not in db_names or requirement["shared"], f"Module resource {resource_name!r} conflicts with an existing database.")
                if resource_name not in db_names:
                    databases.append({
                        "name": resource_name, "engine": kind, "version": "16",
                        "image": "timescale/timescaledb:latest-pg16" if kind == "timescaledb" else "postgres:16-alpine",
                        "public": False, "backup": True,
                    })
                    db_names.add(resource_name)
            elif kind == "redis":
                _require(resource_name not in redis_names or requirement["shared"], f"Module resource {resource_name!r} conflicts with an existing Redis resource.")
                if resource_name not in redis_names:
                    redis.append({"name": resource_name, "version": "7.4", "image": "redis:7.4-alpine", "public": False, "persistent": True})
                    redis_names.add(resource_name)
            else:
                _require(resource_name not in app_by_name, f"Module application resource {resource_name!r} conflicts with an existing application.")
                if resource_name not in app_by_name:
                    public = requirement["public"]
                    product = manifest["product"]
                    production = manifest["topology"] == "resources-v3-production"
                    host = f"{resource_name}.{product}.goodmorning.mx" if production else f"{resource_name}.dev.{product}.goodmorning.mx"
                    application = {
                        "name": resource_name, "role": requirement["role"],
                        "base_directory": requirement["base_directory"], "dockerfile": requirement["dockerfile"],
                        "port": requirement["port"], "domain": f"https://{host}" if public else None,
                        "health_path": requirement["health_path"], "auto_deploy": True, "environment": [],
                        "dependencies": [],
                    }
                    for dependency in requirement["depends_on"]:
                        dependency_resource = module_requirement_resources.get((module_name, dependency))
                        if dependency_resource is not None:
                            dependency_kind = next(
                                item["kind"] for item in module["resource_requirements"] if item["name"] == dependency
                            )
                            dependency_type = "application" if dependency_kind in {"service", "worker", "gateway"} else dependency_kind
                            application["dependencies"].append({"type": dependency_type, "name": dependency_resource})
                    applications.append(application)
                    app_by_name[resource_name] = application
            requirement_resources[resource_key] = (resource_name, kind)
            module_requirement_resources[(module_name, local_name)] = resource_name
            ownership.append({
                "resource": resource_name, "kind": kind, "owner_module": module_name,
                "requirement": local_name, "shared": requirement["shared"], "consumers": [module_name],
            })

        for binding in module["bindings"]:
            target = binding["target"]
            source = binding["source"]
            entry: dict[str, Any] = {"key": binding["key"], "source": source}
            if binding.get("requirement") is not None:
                resource_name = module_requirement_resources.get((module_name, binding["requirement"]))
                _require(resource_name is not None, f"Module binding requirement {binding['requirement']!r} is not active for {module_name!r}.")
                if source in {"database_url", "redis_url"}:
                    entry["resource"] = resource_name
                else:
                    entry["resource"] = resource_name
            if binding.get("driver") is not None:
                entry["driver"] = binding["driver"]
            if binding.get("binding") is not None:
                entry["binding"] = binding["binding"]
            if source == "service_url":
                connections.append({"target": target_app(target), "key": binding["key"], "source": {"type": "application", "resource": entry["resource"]}})
            elif source == "database_url":
                connections.append({"target": target_app(target), "key": binding["key"], "source": {"type": "database", "resource": entry["resource"], **({"driver": binding["driver"]} if binding.get("driver") else {})}})
            elif source == "redis_url":
                connections.append({"target": target_app(target), "key": binding["key"], "source": {"type": "redis", "resource": entry["resource"]}})
            else:
                add_env(target, entry)

        # Preserve the established required_secrets contract while compiling
        # it into the same V3 runtime binding model.
        for key, secret in module["required_secrets"].items():
            if secret["source"] == "GOODMORNING_MANAGED":
                add_env(secret["target"], {"key": key, "source": "managed_secret", "binding": secret["binding"]})

    result["applications"] = applications
    result["databases"] = databases
    result["redis"] = redis
    result["connections"] = connections
    result["resource_ownership"] = ownership
    return result


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
        "dependency_order": module_dependency_order(modules),
    }
