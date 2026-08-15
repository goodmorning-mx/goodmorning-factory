#!/usr/bin/env python3
"""Validate and compile the GoodMorning resources-v3 advanced product contract.

This module is intentionally side-effect free.  The reconciler imports it before
performing any Coolify reads or writes, and CI uses it to reject unsafe manifests.
"""
from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path
from typing import Any

TOPOLOGY = "resources-v3"
PRODUCTION_TOPOLOGY = "resources-v3-production"
PRODUCT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
ENV_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
SAFE_PATH_RE = re.compile(r"^/[A-Za-z0-9._/-]+$")
ALLOWED_ROLES = {"api", "frontend", "mobile", "worker", "gateway"}
ALLOWED_DATABASE_ENGINES = {"postgresql", "timescaledb"}
ALLOWED_ENV_SOURCES = {
    "literal",
    "database_url",
    "redis_url",
    "service_url",
    "managed_secret",
    "legacy_env",
    "generated_secret",
}
SENSITIVE_KEY_RE = re.compile(r"(PASSWORD|SECRET|TOKEN|KEY|CREDENTIAL)", re.I)


class ContractError(ValueError):
    pass


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ContractError(message)


def _clean_https_domain(value: Any, *, product: str, production: bool) -> str:
    _require(isinstance(value, str), "Public application domain must be a string.")
    parsed = urllib.parse.urlparse(value)
    _require(
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.hostname.endswith(".goodmorning.mx")
        and parsed.port is None
        and parsed.path in ("", "/")
        and not parsed.query
        and not parsed.fragment,
        "Public application domains must be clean HTTPS goodmorning.mx URLs without ports or paths.",
    )
    if production:
        _require(
            (
                parsed.hostname == f"{product}.goodmorning.mx"
                or parsed.hostname.endswith(f".{product}.goodmorning.mx")
            )
            and parsed.hostname != f"dev.{product}.goodmorning.mx"
            and not parsed.hostname.endswith(f".dev.{product}.goodmorning.mx"),
            "Public application domain is outside the product production namespace.",
        )
    else:
        _require(
            parsed.hostname == f"dev.{product}.goodmorning.mx"
            or parsed.hostname == f"api.dev.{product}.goodmorning.mx"
            or parsed.hostname.endswith(f".dev.{product}.goodmorning.mx"),
            "Public application domain is outside the product development namespace.",
        )
    return value.rstrip("/")


def _validate_environment(item: Any, *, database_names: set[str], redis_names: set[str], application_names: set[str]) -> dict[str, Any]:
    _require(isinstance(item, dict), "Application environment entries must be objects.")
    _require(
        set(item)
        <= {"key", "source", "value", "resource", "legacy_key", "driver", "binding"},
        "Unknown application environment field.",
    )
    key = item.get("key")
    source = item.get("source")
    _require(isinstance(key, str) and ENV_KEY_RE.fullmatch(key) is not None, "Invalid environment variable key.")
    _require(source in ALLOWED_ENV_SOURCES, f"Unsupported environment source for {key!r}.")
    if source == "literal":
        _require(
            set(item) <= {"key", "source", "value"},
            f"Literal environment variable {key!r} contains unrelated fields.",
        )
        value = item.get("value")
        _require(isinstance(value, str) and len(value) <= 2048, f"Literal value for {key!r} is invalid.")
        _require(not SENSITIVE_KEY_RE.search(key), f"Sensitive variable {key!r} cannot be stored as a literal.")
        return {"key": key, "source": source, "value": value}
    if source == "database_url":
        _require(
            set(item) <= {"key", "source", "resource", "driver"},
            f"Database URL variable {key!r} contains unrelated fields.",
        )
        resource = item.get("resource")
        _require(resource in database_names, f"Environment variable {key!r} references an unknown database.")
        driver = item.get("driver")
        _require(driver in {None, "psycopg"}, f"Unsupported database driver for {key!r}.")
        result = {"key": key, "source": source, "resource": resource}
        if driver is not None:
            result["driver"] = driver
        return result
    if source == "redis_url":
        _require(
            set(item) <= {"key", "source", "resource"},
            f"Redis URL variable {key!r} contains unrelated fields.",
        )
        resource = item.get("resource")
        _require(resource in redis_names, f"Environment variable {key!r} references an unknown Redis resource.")
        return {"key": key, "source": source, "resource": resource}
    if source == "service_url":
        _require(
            set(item) <= {"key", "source", "resource"},
            f"Service URL variable {key!r} contains unrelated fields.",
        )
        resource = item.get("resource")
        _require(resource in application_names, f"Service URL variable {key!r} references an unknown application resource.")
        return {"key": key, "source": source, "resource": resource}
    if source == "managed_secret":
        _require(
            set(item) <= {"key", "source", "binding"},
            f"Managed secret {key!r} contains unrelated fields.",
        )
        binding = item.get("binding")
        _require(
            isinstance(binding, str)
            and (
                re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", binding)
                or re.fullmatch(r"[a-z0-9][a-z0-9/-]{1,127}", binding)
            ),
            f"Managed secret binding for {key!r} is invalid.",
        )
        _require(SENSITIVE_KEY_RE.search(key) is not None, f"Managed secret target {key!r} is not sensitive.")
        return {"key": key, "source": source, "binding": binding}
    if source == "generated_secret":
        _require(
            set(item) <= {"key", "source"},
            f"Generated secret {key!r} contains unrelated fields.",
        )
        _require(
            SENSITIVE_KEY_RE.search(key) is not None,
            f"Generated values are restricted to sensitive variables; {key!r} is not sensitive.",
        )
        return {"key": key, "source": source}
    _require(
        set(item) <= {"key", "source", "legacy_key"},
        f"Legacy environment variable {key!r} contains unrelated fields.",
    )
    legacy_key = item.get("legacy_key", key)
    _require(isinstance(legacy_key, str) and ENV_KEY_RE.fullmatch(legacy_key) is not None, "Invalid legacy environment key.")
    return {"key": key, "source": source, "legacy_key": legacy_key}


def validate_manifest(raw: Any) -> dict[str, Any]:
    _require(isinstance(raw, dict), "Advanced product manifest must be a JSON object.")
    allowed = {
        "topology", "product", "repository", "branch", "development_domain", "coolify_github_app_name",
        "legacy", "migration", "connections", "applications", "databases", "redis", "backup",
        "confirm_production", "promotion_policy",
    }
    _require(set(raw) <= allowed, "Advanced product manifest contains unknown top-level fields.")
    topology = raw.get("topology")
    _require(
        topology in {TOPOLOGY, PRODUCTION_TOPOLOGY},
        "Advanced product topology is not supported.",
    )
    production = topology == PRODUCTION_TOPOLOGY
    product = raw.get("product")
    repository = raw.get("repository")
    _require(isinstance(product, str) and PRODUCT_RE.fullmatch(product) is not None, "Invalid product slug.")
    _require(isinstance(repository, str) and REPOSITORY_RE.fullmatch(repository) is not None, "Invalid repository.")
    migration_input = raw.get("migration")
    legacy_repository_adoption = (
        topology in {TOPOLOGY, PRODUCTION_TOPOLOGY}
        and isinstance(migration_input, dict)
        and migration_input.get("from_topology") == "resources-v2"
    )
    _require(
        repository == f"goodmorning-mx/{product}" or legacy_repository_adoption,
        "Repository must be the product repository in goodmorning-mx unless adopting a v2 source in place.",
    )
    expected_branch = "main" if production else "dev"
    _require(
        raw.get("branch") == expected_branch,
        f"Advanced {expected_branch} products may only reconcile branch {expected_branch!r}.",
    )
    if production:
        _require(raw.get("confirm_production") is True, "Advanced production requires confirm_production=true.")
        _require(raw.get("promotion_policy") == "auto-on-main", "Advanced production requires promotion_policy='auto-on-main'.")
    else:
        _require("confirm_production" not in raw and "promotion_policy" not in raw, "Production authorization fields are forbidden in development manifests.")
        if raw.get("development_domain") is not None:
            _require(raw.get("development_domain") == f"dev.{product}.goodmorning.mx", "development_domain must match the product DEV namespace.")
    github_app = raw.get("coolify_github_app_name")
    _require(isinstance(github_app, str) and github_app == "goodmorning-mx-coolify", "Unexpected Coolify GitHub App.")

    legacy_raw = raw.get("legacy")
    legacy: dict[str, Any] | None = None
    environment_keys: list[str] = []
    if legacy_raw is not None:
        _require(isinstance(legacy_raw, dict), "Legacy rollback descriptor must be an object.")
        _require(set(legacy_raw) <= {"application_uuid", "retain", "environment_keys"}, "Unknown legacy descriptor field.")
        _require(isinstance(legacy_raw.get("application_uuid"), str) and legacy_raw["application_uuid"], "Legacy application UUID is required.")
        _require(legacy_raw.get("retain") is True, "Legacy resource retention must be enabled during migration.")
        environment_keys = legacy_raw.get("environment_keys", [])
        _require(
            isinstance(environment_keys, list)
            and len(environment_keys) == len(set(environment_keys))
            and all(isinstance(key, str) and ENV_KEY_RE.fullmatch(key) for key in environment_keys),
            "Legacy environment key allowlist is invalid.",
        )
        legacy = {**legacy_raw, "environment_keys": environment_keys}
    if production:
        _require(legacy is None, "Advanced production cannot import a legacy application environment.")

    migration_raw = raw.get("migration")
    migration: dict[str, Any] | None = None
    if migration_raw is not None:
        _require(topology in {TOPOLOGY, PRODUCTION_TOPOLOGY}, "Migration descriptors are only valid for resources-v3 adoption.")
        _require(isinstance(migration_raw, dict), "Migration descriptor must be an object.")
        _require(set(migration_raw) <= {"from_topology", "mode", "retain", "resource_names", "resource_uuids", "preserve"}, "Unknown migration descriptor field.")
        expected_source_topology = "resources-v2-production" if production else "resources-v2"
        _require(migration_raw.get("from_topology") == expected_source_topology, "Migration source topology does not match the target environment.")
        _require(migration_raw.get("mode") == "adopt-in-place" and migration_raw.get("retain") is True, "Migration must retain existing resources and use adopt-in-place mode.")
        names = migration_raw.get("resource_names")
        _require(isinstance(names, dict), "Migration resource_names must be an object.")
        _require(set(names) == {"applications", "databases", "redis"}, "Migration resource_names must enumerate all resource kinds.")
        for kind in names:
            _require(isinstance(names[kind], list) and all(isinstance(name, str) and PRODUCT_RE.fullmatch(name) for name in names[kind]), "Migration resource names are invalid.")
        uuids = migration_raw.get("resource_uuids", {})
        _require(isinstance(uuids, dict), "Migration resource_uuids must be an object when present.")
        preserve = migration_raw.get("preserve", [])
        _require(
            isinstance(preserve, list)
            and len(preserve) == len(set(preserve))
            and set(preserve) <= {"resources", "uuids", "databases", "volumes", "domains", "secrets", "configuration"},
            "Migration preserve fields are invalid.",
        )
        migration = dict(migration_raw)

    databases_raw = raw.get("databases")
    _require(isinstance(databases_raw, list) and databases_raw, "At least one database is required.")
    databases: list[dict[str, Any]] = []
    database_names: set[str] = set()
    for item in databases_raw:
        _require(isinstance(item, dict), "Database entries must be objects.")
        _require(set(item) <= {"name", "engine", "version", "image", "public", "backup"}, "Unknown database field.")
        name = item.get("name")
        engine = item.get("engine")
        _require(isinstance(name, str) and PRODUCT_RE.fullmatch(name), "Invalid database name.")
        _require(name not in database_names, "Duplicate database name.")
        _require(engine in ALLOWED_DATABASE_ENGINES, "Unsupported database engine.")
        _require(item.get("version") == "16", "Advanced databases currently require PostgreSQL 16.")
        image = item.get("image")
        expected_image = "timescale/timescaledb:latest-pg16" if engine == "timescaledb" else "postgres:16-alpine"
        _require(image == expected_image, "Database image does not match the selected engine.")
        _require(item.get("public") is False, "Advanced databases must remain private.")
        _require(item.get("backup") is True, "Persistent advanced databases require external backups.")
        database_names.add(name)
        databases.append(dict(item))

    redis_raw = raw.get("redis", [])
    _require(isinstance(redis_raw, list), "Redis resources must be a list.")
    redis_resources: list[dict[str, Any]] = []
    redis_names: set[str] = set()
    for item in redis_raw:
        _require(isinstance(item, dict), "Redis entries must be objects.")
        _require(set(item) <= {"name", "version", "image", "public", "persistent"}, "Unknown Redis field.")
        name = item.get("name")
        _require(isinstance(name, str) and PRODUCT_RE.fullmatch(name), "Invalid Redis name.")
        _require(name not in redis_names, "Duplicate Redis name.")
        _require(item.get("version") == "7.4" and item.get("image") == "redis:7.4-alpine", "Redis must use the reviewed 7.4 Alpine image.")
        _require(item.get("public") is False, "Redis must remain private.")
        _require(item.get("persistent") is True, "Advanced Redis must enable persistence.")
        redis_names.add(name)
        redis_resources.append(dict(item))

    applications_raw = raw.get("applications")
    _require(isinstance(applications_raw, list) and applications_raw, "At least one application is required.")
    applications: list[dict[str, Any]] = []
    declared_application_names = {item.get("name") for item in applications_raw if isinstance(item, dict)}
    names: set[str] = set()
    public_domains: set[str] = set()
    mobile_count = 0
    api_count = 0
    for item in applications_raw:
        _require(isinstance(item, dict), "Application entries must be objects.")
        allowed_app = {
            "name", "role", "dockerfile", "port", "domain", "health_path",
            "environment", "persistent_volume", "auto_deploy", "base_directory",
            "dependencies",
        }
        _require(set(item) <= allowed_app, "Unknown application field.")
        name = item.get("name")
        role = item.get("role")
        dockerfile = item.get("dockerfile")
        base_directory = item.get("base_directory", "/")
        port = item.get("port")
        _require(isinstance(name, str) and PRODUCT_RE.fullmatch(name), "Invalid application name.")
        _require(name not in names, "Duplicate application name.")
        _require(role in ALLOWED_ROLES, "Unsupported application role.")
        _require(isinstance(dockerfile, str) and SAFE_PATH_RE.fullmatch(dockerfile) and ".." not in dockerfile, "Invalid Dockerfile path.")
        _require(
            isinstance(base_directory, str)
            and (base_directory == "/" or SAFE_PATH_RE.fullmatch(base_directory))
            and ".." not in base_directory,
            "Invalid application base directory.",
        )
        _require(isinstance(port, int) and 1 <= port <= 65535, "Invalid application port.")
        _require(item.get("auto_deploy") is True, "Advanced applications must enable native auto-deploy.")
        health_path = item.get("health_path")
        _require(isinstance(health_path, str) and health_path.startswith("/") and "?" not in health_path, "Invalid health path.")
        domain = item.get("domain")
        if role == "worker":
            _require(domain is None and port == 8000, "Workers must remain private and use the reviewed placeholder port.")
        else:
            domain = _clean_https_domain(domain, product=product, production=production)
            _require(domain not in public_domains, "Duplicate public domain.")
            public_domains.add(domain)
        if role == "mobile":
            mobile_count += 1
        if role == "api":
            api_count += 1
        volume = item.get("persistent_volume")
        if volume is not None:
            _require(role == "api" and isinstance(volume, dict), "Only the API may declare the reviewed job volume.")
            _require(set(volume) == {"name", "mount_path"}, "Persistent volume fields must be exact.")
            _require(volume["name"] == f"{product}-backend-data" and volume["mount_path"] == "/app/data", "Unexpected persistent volume.")
        environment_raw = item.get("environment", [])
        _require(isinstance(environment_raw, list), "Application environment must be a list.")
        environment = [
            _validate_environment(entry, database_names=database_names, redis_names=redis_names, application_names=declared_application_names)
            for entry in environment_raw
        ]
        keys = [entry["key"] for entry in environment]
        _require(len(keys) == len(set(keys)), f"Application {name!r} has duplicate environment keys.")
        dependencies = item.get("dependencies", [])
        _require(isinstance(dependencies, list), f"Application {name!r} dependencies must be a list.")
        names.add(name)
        applications.append(
            {
                **item,
                "base_directory": base_directory,
                "domain": domain,
                "environment": environment,
                "dependencies": dependencies,
            }
        )

    _require(api_count == 1, "Advanced product must declare exactly one API.")
    _require(mobile_count <= 1, "Advanced product may declare at most one mobile frontend.")

    # Dependencies are explicit resource references. Strings are accepted as a
    # concise form and normalized to typed references in the canonical state.
    resource_kinds = {
        **{name: "application" for name in names},
        **{name: "database" for name in database_names},
        **{name: "redis" for name in redis_names},
    }
    _require(
        len(resource_kinds) == len(names) + len(database_names) + len(redis_names),
        "Application, database, and Redis names must be globally unique within a product.",
    )
    application_map = {app["name"]: app for app in applications}
    for app in applications:
        normalized_dependencies: list[dict[str, str]] = []
        seen_dependencies: set[tuple[str, str]] = set()
        for dependency in app["dependencies"]:
            if isinstance(dependency, str):
                dependency = {"name": dependency}
            _require(isinstance(dependency, dict), f"Application {app['name']!r} dependency must be a resource reference.")
            _require(set(dependency) <= {"type", "name"} and isinstance(dependency.get("name"), str), f"Application {app['name']!r} dependency is invalid.")
            dependency_name = dependency["name"]
            dependency_type = dependency.get("type") or resource_kinds.get(dependency_name)
            _require(dependency_type in {"application", "database", "redis"} and resource_kinds.get(dependency_name) == dependency_type, f"Application {app['name']!r} dependency references an undeclared resource.")
            _require(dependency_name != app["name"], f"Application {app['name']!r} cannot depend on itself.")
            pair = (dependency_type, dependency_name)
            _require(pair not in seen_dependencies, f"Application {app['name']!r} has duplicate dependencies.")
            seen_dependencies.add(pair)
            normalized_dependencies.append({"type": dependency_type, "name": dependency_name})
        app["dependencies"] = normalized_dependencies

    # A dependency cycle would make reconciliation order undefined.
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(name: str) -> None:
        if name in visiting:
            raise ContractError("Application dependencies cannot contain cycles.")
        if name in visited:
            return
        visiting.add(name)
        for dependency in application_map[name]["dependencies"]:
            if dependency["type"] == "application":
                visit(dependency["name"])
        visiting.remove(name)
        visited.add(name)
    for app in applications:
        visit(app["name"])

    connections_raw = raw.get("connections", [])
    _require(isinstance(connections_raw, list), "Connections must be a list.")
    connections: list[dict[str, Any]] = []
    connection_keys: set[tuple[str, str]] = set()
    apps_by_name = {app["name"]: app for app in applications}
    for item in connections_raw:
        _require(isinstance(item, dict) and set(item) == {"target", "key", "source"}, "Connection fields must be exactly target, key and source.")
        target, key, source = item["target"], item["key"], item["source"]
        _require(isinstance(target, str) and target in apps_by_name, "Connection target must name a declared application.")
        _require(isinstance(key, str) and ENV_KEY_RE.fullmatch(key), "Connection key is invalid.")
        _require(isinstance(source, dict) and set(source) <= {"type", "resource", "driver"}, "Connection source fields are invalid.")
        source_type, resource = source.get("type"), source.get("resource")
        valid = (source_type == "database" and resource in database_names) or (source_type == "redis" and resource in redis_names) or (source_type == "application" and resource in apps_by_name)
        _require(valid, "Connection source does not reference a declared resource.")
        pair = (target, key)
        _require(pair not in connection_keys, "Duplicate application connection.")
        connection_keys.add(pair)
        _require(key not in {entry["key"] for entry in apps_by_name[target]["environment"]}, f"Connection {target!r}/{key!r} duplicates an environment entry.")
        if source_type == "application":
            _require(apps_by_name[resource].get("domain") is not None, "Service URL connections require a source application with a public domain.")
        connections.append({"target": target, "key": key, "source": dict(source)})
        apps_by_name[target]["environment"].append({
            "key": key,
            "source": {"database": "database_url", "redis": "redis_url", "application": "service_url"}[source_type],
            "resource": resource,
            **({"driver": source["driver"]} if source.get("driver") is not None else {}),
        })
    declared_legacy_keys = {
        entry["legacy_key"]
        for app in applications
        for entry in app["environment"]
        if entry["source"] == "legacy_env"
    }
    _require(
        declared_legacy_keys <= set(environment_keys),
        "Application requests a legacy secret outside the allowlist.",
    )
    _require(
        not declared_legacy_keys or legacy is not None,
        "legacy_env sources require a legacy rollback descriptor.",
    )

    backup = raw.get("backup")
    _require(isinstance(backup, dict) and backup.get("enabled") is True, "Advanced database backups must be enabled.")
    _require(
        set(backup)
        == {
            "enabled", "storage_uuid", "frequency", "local_retention_count",
            "local_retention_days", "s3_retention_count", "s3_retention_days",
            "timeout_seconds",
        },
        "Advanced backup fields must match the reviewed retention contract.",
    )
    _require(
        isinstance(backup.get("storage_uuid"), str)
        and re.fullmatch(r"^[a-z0-9]{20,64}$", backup["storage_uuid"]),
        "Invalid backup storage UUID.",
    )
    _require(
        isinstance(backup.get("frequency"), str) and len(backup["frequency"].split()) == 5,
        "Backup frequency must be a five-field cron expression.",
    )
    for field in (
        "local_retention_count", "local_retention_days", "s3_retention_count",
        "s3_retention_days", "timeout_seconds",
    ):
        _require(isinstance(backup.get(field), int) and backup[field] > 0, f"Invalid backup field {field!r}.")

    return {
        "topology": topology,
        "product": product,
        "repository": repository,
        "branch": expected_branch,
        **({"development_domain": raw["development_domain"]} if raw.get("development_domain") is not None else {}),
        "coolify_github_app_name": github_app,
        "legacy": legacy,
        "migration": migration,
        "connections": connections,
        "applications": applications,
        "databases": databases,
        "redis": redis_resources,
        "backup": dict(backup),
        **(
            {"confirm_production": True, "promotion_policy": "auto-on-main"}
            if production
            else {}
        ),
    }


def load_manifest(path: str | Path) -> dict[str, Any]:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(f"Unable to read advanced product manifest: {exc}") from exc
    return validate_manifest(raw)


def resource_names(manifest: dict[str, Any]) -> dict[str, list[str]]:
    product = manifest["product"]
    suffix = "prod" if manifest["topology"] == PRODUCTION_TOPOLOGY else "dev"
    return {
        "applications": [f"{product}-{item['name']}-{suffix}" for item in manifest["applications"]],
        "databases": [f"{product}-{item['name']}-{suffix}" for item in manifest["databases"]],
        "redis": [f"{product}-{item['name']}-{suffix}" for item in manifest["redis"]],
    }
