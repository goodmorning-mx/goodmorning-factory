#!/usr/bin/env python3
"""Register a repository-first product manifest in the GoodMorning factory catalog."""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

API = "https://api.github.com"
ORG = "goodmorning-mx"
FACTORY_REPO = "goodmorning-factory"
FACTORY_BRANCH = "dev"
PRODUCT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
UUID_RE = re.compile(r"^[a-zA-Z0-9]{8,64}$")
TIMEOUT_SECONDS = 20


class CatalogError(Exception):
    pass


def token() -> str:
    value = os.environ.get("GH_TOKEN")
    if not value:
        raise CatalogError("GH_TOKEN environment variable is required.")
    return value


def redact(text: str) -> str:
    value = os.environ.get("GH_TOKEN")
    return text.replace(value, "[REDACTED]") if value else text


def request(method: str, path: str, *, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    payload = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        API + path,
        data=payload,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token()}",
            "Content-Type": "application/json",
            "User-Agent": "goodmorning-factory-catalog",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = {"message": raw}
        if exc.code == 404:
            return 404, data
        message = data.get("message") if isinstance(data, dict) else raw
        raise CatalogError(f"GitHub API {exc.code} for {method} {path}: {message}") from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise CatalogError(f"GitHub API request failed for {method} {path}: {exc}") from exc


def validate(product: str, storage_uuid: str, branch: str) -> None:
    if not PRODUCT_RE.fullmatch(product):
        raise CatalogError("product must be a lowercase DNS-safe slug of at most 63 characters.")
    if not UUID_RE.fullmatch(storage_uuid):
        raise CatalogError("backup storage UUID is invalid.")
    if branch != FACTORY_BRANCH:
        raise CatalogError("factory catalog writes are restricted to branch 'dev'.")


def build_manifest(product: str, storage_uuid: str) -> dict[str, Any]:
    return {
        "topology": "resources-v3",
        "product": product,
        "repository": f"{ORG}/{product}",
        "branch": "dev",
        "development_domain": f"dev.{product}.goodmorning.mx",
        "coolify_github_app_name": "goodmorning-mx-coolify",
        "applications": [
            {
                "name": "api", "role": "api", "base_directory": "/backend",
                "dockerfile": "/Dockerfile", "port": 8000,
                "domain": f"https://api.dev.{product}.goodmorning.mx",
                "health_path": "/health", "auto_deploy": True, "environment": [
                    {"key": "DATABASE_URL", "source": "database_url", "resource": "postgres", "driver": "psycopg"},
                ],
            },
            {
                "name": "frontend", "role": "frontend", "base_directory": "/frontend",
                "dockerfile": "/Dockerfile", "port": 80,
                "domain": f"https://dev.{product}.goodmorning.mx",
                "health_path": "/", "auto_deploy": True, "environment": [
                    {"key": "VITE_API_URL", "source": "literal", "value": f"https://api.dev.{product}.goodmorning.mx"},
                ],
            },
        ],
        "databases": [{
            "name": "postgres", "engine": "postgresql", "version": "16",
            "image": "postgres:16-alpine", "public": False, "backup": True,
        }],
        "redis": [],
        "backup": {
            "enabled": True,
            "storage_uuid": storage_uuid,
            "frequency": "0 3 * * *",
            "local_retention_count": 1,
            "local_retention_days": 1,
            "s3_retention_count": 7,
            "s3_retention_days": 7,
            "timeout_seconds": 1800,
        },
    }


def require_product_repository(product: str) -> None:
    status, repo = request("GET", f"/repos/{ORG}/{product}")
    if status != 200 or not isinstance(repo, dict):
        raise CatalogError(f"Product repository {ORG}/{product} does not exist.")
    if repo.get("private") is not True or repo.get("default_branch") != "dev":
        raise CatalogError(f"Product repository {ORG}/{product} is incompatible with the factory contract.")
    for branch in ("main", "dev"):
        branch_status, _ = request("GET", f"/repos/{ORG}/{product}/branches/{branch}")
        if branch_status != 200:
            raise CatalogError(f"Product repository {ORG}/{product} is missing branch {branch!r}.")


def register(product: str, storage_uuid: str, branch: str = FACTORY_BRANCH) -> dict[str, Any]:
    validate(product, storage_uuid, branch)
    require_product_repository(product)
    manifest = build_manifest(product, storage_uuid)
    manifest_path = f"products/{product}.json"
    encoded_path = urllib.parse.quote(manifest_path, safe="/")
    contents_path = f"/repos/{ORG}/{FACTORY_REPO}/contents/{encoded_path}?ref={branch}"
    status, existing = request("GET", contents_path)
    if status == 200:
        if not isinstance(existing, dict) or not isinstance(existing.get("content"), str):
            raise CatalogError("Existing catalog response is malformed.")
        try:
            current = json.loads(base64.b64decode(existing["content"]).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CatalogError("Existing product manifest is not valid UTF-8 JSON.") from exc
        if current != manifest:
            raise CatalogError(f"Catalog manifest {manifest_path} already exists with incompatible content.")
        return {"status": "unchanged", "product": product, "manifest_path": manifest_path, "repository": f"{ORG}/{product}"}
    if status != 404:
        raise CatalogError(f"Unable to inspect catalog manifest {manifest_path}.")

    rendered = json.dumps(manifest, indent=2, sort_keys=False) + "\n"
    create_status, created = request(
        "PUT",
        f"/repos/{ORG}/{FACTORY_REPO}/contents/{encoded_path}",
        body={
            "message": f"catalog(factory): register product {product}",
            "content": base64.b64encode(rendered.encode("utf-8")).decode("ascii"),
            "branch": branch,
        },
    )
    if create_status not in (200, 201) or not isinstance(created, dict):
        raise CatalogError(f"GitHub did not confirm catalog registration for {product}.")
    commit = created.get("commit") if isinstance(created.get("commit"), dict) else {}
    return {
        "status": "registered",
        "product": product,
        "manifest_path": manifest_path,
        "repository": f"{ORG}/{product}",
        "commit_sha": commit.get("sha"),
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description="Register a repository-first GoodMorning product manifest.")
    value.add_argument("--product", required=True)
    value.add_argument("--backup-storage-uuid", required=True)
    value.add_argument("--factory-branch", default=FACTORY_BRANCH)
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = register(args.product, args.backup_storage_uuid, args.factory_branch)
        print(json.dumps(result, sort_keys=True))
        return 0
    except CatalogError as exc:
        print(json.dumps({"status": "error", "error": redact(str(exc))}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
