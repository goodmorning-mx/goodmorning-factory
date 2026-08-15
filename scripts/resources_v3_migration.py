#!/usr/bin/env python3
"""Normalize legacy resources-v2 manifests into the resources-v3 contract.

The conversion is declarative and non-destructive.  The v3 reconciler adopts
resources by their existing names and therefore preserves Coolify UUIDs,
databases, volumes, domains, secrets, and application configuration whenever
those resources are compatible.  It never performs a delete or replacement.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from scripts.advanced_product_contract import ContractError, validate_manifest


def migrate_v2_manifest(raw: dict[str, Any], *, resource_uuids: dict[str, Any] | None = None) -> dict[str, Any]:
    if raw.get("topology") != "resources-v2":
        raise ContractError("Only a resources-v2 manifest can be migrated by this adapter.")
    product = raw.get("product")
    repository = raw.get("repository")
    if not isinstance(product, str) or not isinstance(repository, str):
        raise ContractError("resources-v2 migration requires product and repository.")
    domain = raw.get("development_domain") or f"dev.{product}.goodmorning.mx"
    backup = raw.get("backup")
    if not isinstance(backup, dict):
        raise ContractError("resources-v2 migration requires its reviewed backup contract.")
    app_names = {"api": f"{product}-api-dev", "frontend": f"{product}-frontend-dev"}
    uuid_map = resource_uuids or {}
    migration: dict[str, Any] = {
        "from_topology": "resources-v2",
        "mode": "adopt-in-place",
        "retain": True,
        "resource_names": {
            "applications": list(app_names.values()),
            "databases": [f"{product}-postgres-dev"],
            "redis": [],
        },
    }
    if uuid_map:
        migration["resource_uuids"] = dict(uuid_map)
    return validate_manifest({
        "topology": "resources-v3",
        "product": product,
        "repository": repository,
        "branch": "dev",
        "coolify_github_app_name": raw.get("coolify_github_app_name", "goodmorning-mx-coolify"),
        "migration": migration,
        "applications": [
            {
                "name": "api", "role": "api", "base_directory": raw.get("backend_base_directory", "/backend"),
                "dockerfile": "/Dockerfile", "port": 8000,
                "domain": f"https://api.{domain}", "health_path": "/health", "auto_deploy": True,
                "environment": [],
            },
            {
                "name": "frontend", "role": "frontend", "base_directory": raw.get("frontend_base_directory", "/frontend"),
                "dockerfile": "/Dockerfile", "port": 80,
                "domain": f"https://{domain}", "health_path": "/", "auto_deploy": True,
                "environment": [],
            },
        ],
        "databases": [{
            "name": "postgres", "engine": "postgresql", "version": "16",
            "image": "postgres:16-alpine", "public": False, "backup": True,
        }],
        "redis": [],
        "backup": backup,
    })


def migrate_file(source: str | Path, destination: str | Path) -> dict[str, Any]:
    raw = json.loads(Path(source).read_text(encoding="utf-8"))
    converted = migrate_v2_manifest(raw)
    Path(destination).write_text(json.dumps(converted, indent=2) + "\n", encoding="utf-8")
    return converted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Convert one resources-v2 manifest to resources-v3.")
    parser.add_argument("source")
    parser.add_argument("destination")
    args = parser.parse_args(argv)
    try:
        result = migrate_file(args.source, args.destination)
    except (OSError, json.JSONDecodeError, ContractError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 2
    print(json.dumps({"status": "migrated", "topology": result["topology"], "product": result["product"], "migration": result["migration"]}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
