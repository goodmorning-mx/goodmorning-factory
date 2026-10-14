#!/usr/bin/env python3
"""Register the automatic production manifest for a Factory product."""

from __future__ import annotations

import argparse
import base64
import json
import sys
import urllib.parse
from typing import Any

from scripts import register_product_manifest as catalog


def build_manifest(product: str, storage_uuid: str, frontend_profile: str = "react") -> dict[str, Any]:
    catalog.validate(product, storage_uuid, catalog.FACTORY_BRANCH, frontend_profile)
    applications = [{
        "name": "api", "role": "api", "base_directory": "/backend",
        "dockerfile": "/Dockerfile", "port": 8000,
        "domain": f"https://api.{product}.goodmorning.mx",
        "health_path": "/health", "auto_deploy": False, "environment": [
            {"key": "DATABASE_URL", "source": "database_url", "resource": "postgres", "driver": "psycopg"},
        ],
    }]
    if frontend_profile in {"react", "hybrid"}:
        applications.append({
            "name": "frontend", "role": "frontend", "base_directory": "/frontend",
            "dockerfile": "/Dockerfile", "port": 80,
            "domain": f"https://{product}.goodmorning.mx" if frontend_profile == "react" else None,
            "health_path": "/", "auto_deploy": False, "environment": [
                {"key": "VITE_API_URL", "source": "literal", "value": f"https://api.{product}.goodmorning.mx" if frontend_profile == "react" else "/api"},
                *([{"key": "VITE_BASE_PATH", "source": "literal", "value": "/app/"}] if frontend_profile == "hybrid" else []),
            ],
        })
    if frontend_profile in {"next", "hybrid"}:
        applications.append({
            "name": "seo", "role": "frontend", "base_directory": "/seo",
            "dockerfile": "/Dockerfile", "port": 3000,
            "domain": f"https://{product}.goodmorning.mx" if frontend_profile == "next" else None,
            "health_path": "/health", "auto_deploy": False, "environment": [
                {"key": "NEXT_PUBLIC_API_URL", "source": "literal", "value": f"https://api.{product}.goodmorning.mx" if frontend_profile == "next" else "/api"},
            ],
        })
    manifest = {
        "frontend_profile": frontend_profile,
        "topology": "resources-v3-production",
        "product": product,
        "repository": f"{catalog.ORG}/{product}",
        "branch": "main",
        "coolify_github_app_name": "goodmorning-mx-coolify",
        "applications": applications,
        "databases": [{
            "name": "postgres", "engine": "postgresql", "version": "16",
            "image": "postgres:16-alpine", "public": False, "backup": True,
        }],
        "redis": [],
        "confirm_production": True,
        "promotion_policy": "auto-on-main",
        "backup": {
            "enabled": True,
            "storage_uuid": storage_uuid,
            "frequency": "0 2 * * *",
            "local_retention_count": 2,
            "local_retention_days": 2,
            "s3_retention_count": 30,
            "s3_retention_days": 30,
            "timeout_seconds": 1800,
        },
    }
    if frontend_profile == "next":
        manifest["routing"] = {"mode": "next", "host": f"https://{product}.goodmorning.mx", "application": "seo"}
    elif frontend_profile == "hybrid":
        manifest["applications"].append({
            "name": "frontend-gateway", "role": "gateway", "base_directory": "/gateway",
            "dockerfile": "/Dockerfile", "port": 80,
            "domain": f"https://{product}.goodmorning.mx",
            "health_path": "/health", "auto_deploy": False, "environment": [],
            "dependencies": [
                {"type": "application", "name": "frontend"},
                {"type": "application", "name": "seo"},
                {"type": "application", "name": "api"},
            ],
        })
        manifest["connections"] = [
            {"target": "frontend-gateway", "key": "REACT_UPSTREAM", "source": {"type": "application", "resource": "frontend"}},
            {"target": "frontend-gateway", "key": "NEXT_UPSTREAM", "source": {"type": "application", "resource": "seo"}},
            {"target": "frontend-gateway", "key": "API_UPSTREAM", "source": {"type": "application", "resource": "api"}},
        ]
        manifest["routing"] = {
            "mode": "hybrid", "host": f"https://{product}.goodmorning.mx",
            "gateway": {"required": True, "public_application": "frontend-gateway", "routes": [
                {"path": "/", "application": "seo"},
                {"path": "/seo", "application": "seo"},
                {"path": "/app", "application": "frontend"},
                {"path": "/api", "application": "api"},
            ]},
        }
    return manifest


def register(
    product: str,
    storage_uuid: str,
    branch: str = catalog.FACTORY_BRANCH,
    frontend_profile: str = "react",
) -> dict[str, Any]:
    catalog.validate(product, storage_uuid, branch, frontend_profile)
    catalog.require_product_repository(product)
    manifest = build_manifest(product, storage_uuid, frontend_profile)
    manifest_path = f"production/{product}.json"
    encoded_path = urllib.parse.quote(manifest_path, safe="/")
    contents_path = (
        f"/repos/{catalog.ORG}/{catalog.FACTORY_REPO}/contents/"
        f"{encoded_path}?ref={branch}"
    )
    status, existing = catalog.request("GET", contents_path)
    if status == 200:
        if not isinstance(existing, dict) or not isinstance(existing.get("content"), str):
            raise catalog.CatalogError("Existing production catalog response is malformed.")
        try:
            current = json.loads(
                base64.b64decode(existing["content"]).decode("utf-8")
            )
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise catalog.CatalogError(
                "Existing production manifest is not valid UTF-8 JSON."
            ) from exc
        if current != manifest:
            raise catalog.CatalogError(
                f"Production manifest {manifest_path} already exists with incompatible content."
            )
        return {
            "status": "unchanged",
            "product": product,
            "manifest_path": manifest_path,
            "repository": f"{catalog.ORG}/{product}",
            "promotion_policy": "auto-on-main",
        }
    if status != 404:
        raise catalog.CatalogError(
            f"Unable to inspect production manifest {manifest_path}."
        )

    rendered = json.dumps(manifest, indent=2, sort_keys=False) + "\n"
    create_status, created = catalog.request(
        "PUT",
        f"/repos/{catalog.ORG}/{catalog.FACTORY_REPO}/contents/{encoded_path}",
        body={
            "message": f"catalog(factory): register production product {product}",
            "content": base64.b64encode(rendered.encode("utf-8")).decode("ascii"),
            "branch": branch,
        },
    )
    if create_status not in (200, 201) or not isinstance(created, dict):
        raise catalog.CatalogError(
            f"GitHub did not confirm production catalog registration for {product}."
        )
    commit = created.get("commit") if isinstance(created.get("commit"), dict) else {}
    return {
        "status": "registered",
        "product": product,
        "manifest_path": manifest_path,
        "repository": f"{catalog.ORG}/{product}",
        "promotion_policy": "auto-on-main",
        "commit_sha": commit.get("sha"),
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(
        description="Register an automatic GoodMorning production manifest."
    )
    value.add_argument("--product", required=True)
    value.add_argument("--backup-storage-uuid", required=True)
    value.add_argument("--factory-branch", default=catalog.FACTORY_BRANCH)
    value.add_argument("--frontend-profile", choices=sorted(catalog.FRONTEND_PROFILES), default="react")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = register(
            args.product,
            args.backup_storage_uuid,
            args.factory_branch,
            args.frontend_profile,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except catalog.CatalogError as exc:
        print(
            json.dumps(
                {"status": "error", "error": catalog.redact(str(exc))},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
