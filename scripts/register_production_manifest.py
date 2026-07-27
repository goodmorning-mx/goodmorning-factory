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


def build_manifest(product: str, storage_uuid: str) -> dict[str, Any]:
    return {
        "topology": "resources-v2-production",
        "product": product,
        "repository": f"{catalog.ORG}/{product}",
        "branch": "main",
        "production_domain": f"{product}.goodmorning.mx",
        "coolify_github_app_name": "goodmorning-mx-coolify",
        "backend_base_directory": "/backend",
        "frontend_base_directory": "/frontend",
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


def register(
    product: str,
    storage_uuid: str,
    branch: str = catalog.FACTORY_BRANCH,
) -> dict[str, Any]:
    catalog.validate(product, storage_uuid, branch)
    catalog.require_product_repository(product)
    manifest = build_manifest(product, storage_uuid)
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
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = register(
            args.product,
            args.backup_storage_uuid,
            args.factory_branch,
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
