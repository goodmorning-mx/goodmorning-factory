#!/usr/bin/env python3
"""Clone a GoodMorning product repository and derive an isolated DEV manifest.

This command deliberately only copies Git source.  It never copies a database,
volume, uploaded file, secret value, Coolify UUID, or production domain.
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from scripts.advanced_product_contract import ContractError, validate_manifest

API = "https://api.github.com"
ORG = "goodmorning-mx"
FACTORY_DOMAIN = "goodmorning.mx"
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
RESERVED = {"goodmorning-factory", "goodmorning-mvp-template"}
DATA_PATTERNS = ("*.sql", "*.dump", "*.sqlite", "*.sqlite3", "uploads/*", "data/*")
TEXT_SUFFIXES = {
    ".css", ".env", ".html", ".ini", ".js", ".json", ".md", ".py", ".rst",
    ".sh", ".sql", ".toml", ".ts", ".tsx", ".txt", ".vue", ".xml", ".yaml", ".yml",
}
SECRET_ENV_BY_BINDING = {
    "goodmorning/openai-default": "GOODMORNING_MANAGED_OPENAI_API_KEY",
    "goodmorning/stripe-secret-key": "GOODMORNING_MANAGED_STRIPE_SECRET_KEY",
    "goodmorning/stripe-webhook-secret": "GOODMORNING_MANAGED_STRIPE_WEBHOOK_SECRET",
}


class CloneError(RuntimeError):
    pass


def validate_slug(value: str) -> None:
    if not value or len(value) > 63 or value in RESERVED or not NAME_RE.fullmatch(value):
        raise CloneError("Product slug must be lowercase letters, numbers, and hyphens; max 63 characters; not reserved.")


def token() -> str:
    value = os.environ.get("GH_TOKEN")
    if not value:
        raise CloneError("GH_TOKEN is required and must not be passed as a command-line argument.")
    return value


def github(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    raw = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        API + path, data=raw, method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token()}",
            "Content-Type": "application/json",
            "User-Agent": "goodmorning-factory-product-cloner",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = response.read().decode()
            return json.loads(payload) if payload else None
    except urllib.error.HTTPError as exc:
        message = exc.read().decode(errors="replace")
        raise CloneError(f"GitHub API {exc.code} for {method} {path}: {message[:300]}") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise CloneError(f"GitHub connection failed for {method} {path}: {exc}") from exc


def require_source(source: str) -> dict[str, Any]:
    repo = github("GET", f"/repos/{ORG}/{source}")
    if not isinstance(repo, dict) or repo.get("private") is not True or repo.get("default_branch") != "dev":
        raise CloneError("Source repository must be private and use dev as its default branch.")
    for branch in ("main", "dev"):
        github("GET", f"/repos/{ORG}/{source}/branches/{branch}")
    return repo


def require_destination_absent(destination: str) -> None:
    try:
        github("GET", f"/repos/{ORG}/{destination}")
    except CloneError as exc:
        if "GitHub API 404" in str(exc):
            return
        raise
    raise CloneError(f"Destination repository {ORG}/{destination} already exists.")


def load_source_manifest(source: str, path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
        validate_manifest(manifest)
    except (OSError, json.JSONDecodeError, ContractError) as exc:
        raise CloneError(f"Invalid source manifest {path}: {exc}") from exc
    if manifest.get("product") != source or manifest.get("repository") != f"{ORG}/{source}":
        raise CloneError("Source manifest identity does not match source product.")
    if manifest.get("legacy") or manifest.get("migration"):
        raise CloneError("Cloning a legacy or migration manifest is refused; use a clean resources-v3 product.")
    if any(
        item.get("source") == "legacy_env"
        for application in manifest.get("applications", [])
        for item in application.get("environment", [])
    ):
        raise CloneError("Cloning a product with legacy environment bindings is refused.")
    return manifest


def required_secret_bindings(manifest: dict[str, Any]) -> set[str]:
    bindings: set[str] = set()
    for module in manifest.get("modules", []):
        for item in (module.get("required_secrets") or {}).values():
            if isinstance(item, dict) and item.get("binding"):
                bindings.add(item["binding"])
    for application in manifest.get("applications", []):
        for item in application.get("environment", []):
            if item.get("source") == "managed_secret" and item.get("binding"):
                bindings.add(item["binding"])
    missing = [binding for binding in sorted(bindings) if not os.environ.get(SECRET_ENV_BY_BINDING.get(binding, ""))]
    if missing:
        raise CloneError("Required managed secret binding(s) are unavailable: " + ", ".join(missing))
    return bindings


def coolify_preflight(destination: str, manifest: dict[str, Any]) -> None:
    """Reject collisions before creating the destination repository."""
    base_url = os.environ.get("COOLIFY_BASE_URL", "")
    coolify_token = os.environ.get("COOLIFY_API_TOKEN", "")
    if not base_url or not coolify_token:
        raise CloneError("COOLIFY_BASE_URL and COOLIFY_API_TOKEN are required for destination preflight.")
    try:
        from scripts.reconcile_advanced_product import Coolify, _items, _coolify_project_name

        client = Coolify(base_url, coolify_token, apply=False)
        project_name = _coolify_project_name(destination)
        projects = _items(client.get("/projects"))
        if any(str(item.get("name")) == project_name for item in projects):
            raise CloneError(f"Coolify project {project_name!r} already exists.")
        expected_names = {
            f"{destination}-{item['name']}-dev"
            for kind in ("applications", "databases", "redis")
            for item in manifest.get(kind, [])
        }
        expected_names.update(f"{destination}-{item['name']}-dev" for item in manifest.get("applications", []))
        for endpoint in ("/applications", "/databases", "/redis"):
            for item in _items(client.get(endpoint)):
                if str(item.get("name") or "") in expected_names:
                    raise CloneError(f"Coolify resource {item['name']!r} already exists.")
                serialized = json.dumps(item, sort_keys=True)
                for domain in (f"dev.{destination}.{FACTORY_DOMAIN}", f"api.dev.{destination}.{FACTORY_DOMAIN}"):
                    if domain in serialized:
                        raise CloneError(f"Coolify domain {domain!r} is already in use.")
    except CloneError:
        raise
    except Exception as exc:
        raise CloneError(f"Coolify destination preflight failed safely: {exc}") from exc


def derive_manifest(source: str, destination: str, source_manifest: dict[str, Any], storage_uuid: str) -> dict[str, Any]:
    if not storage_uuid or not re.fullmatch(r"[a-z0-9]{20,64}", storage_uuid):
        raise CloneError("A valid destination backup storage UUID is required from Factory configuration.")
    manifest = copy.deepcopy(source_manifest)

    def replace(value: Any) -> Any:
        if isinstance(value, str):
            marker = "__GOODMORNING_CLONE_DESTINATION__"
            return value.replace(source, marker).replace(marker, destination)
        if isinstance(value, list):
            return [replace(item) for item in value]
        if isinstance(value, dict):
            return {key: replace(item) for key, item in value.items()}
        return value

    manifest = replace(manifest)
    manifest["product"] = destination
    manifest["repository"] = f"{ORG}/{destination}"
    manifest["branch"] = "dev"
    manifest["development_domain"] = f"dev.{destination}.{FACTORY_DOMAIN}"
    manifest["backup"]["storage_uuid"] = storage_uuid
    manifest.pop("legacy", None)
    manifest.pop("migration", None)
    validate_manifest(manifest)
    return manifest


def run(command: list[str], cwd: Path | None = None) -> None:
    environment = os.environ.copy()
    environment["GIT_TERMINAL_PROMPT"] = "0"
    token_value = token()
    header = f"AUTHORIZATION: bearer {token_value}"
    command = ["git", "-c", f"http.extraHeader={header}", *command]
    result = subprocess.run(command, cwd=cwd, env=environment, text=True, capture_output=True)
    if result.returncode:
        raise CloneError(f"Git command failed: {' '.join(command[:3])} ({result.stderr[-300:]})")


def reject_data_paths(repo: Path) -> None:
    tracked = subprocess.check_output(["git", "ls-tree", "-r", "--name-only", "dev"], cwd=repo, text=True).splitlines()
    for path in tracked:
        if any(Path(path).match(pattern) for pattern in DATA_PATTERNS):
            raise CloneError(f"Source repository contains a data path and cannot be cloned: {path}")


def rewrite_source_identity(repo: Path, source: str, destination: str) -> None:
    for branch in ("main", "dev"):
        run(["checkout", branch], cwd=repo)
        changed = False
        for path in repo.rglob("*"):
            if not path.is_file() or path.name == ".git" or path.suffix.lower() not in TEXT_SUFFIXES:
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            marker = "__GOODMORNING_CLONE_DESTINATION__"
            updated = content.replace(source, marker).replace(marker, destination)
            if updated != content:
                path.write_text(updated, encoding="utf-8")
                changed = True
        if changed:
            run(["add", "-A"], cwd=repo)
            run(["-c", "user.name=GoodMorning Factory", "-c", "user.email=factory@goodmorning.mx", "commit", "-m", f"factory: derive product identity for {destination}"], cwd=repo)
        run(["push", "origin", branch], cwd=repo)


def clone(source: str, destination: str, source_manifest_path: Path, storage_uuid: str, description: str) -> dict[str, Any]:
    validate_slug(source)
    validate_slug(destination)
    if source == destination:
        raise CloneError("Source and destination products must be different.")
    require_source(source)
    require_destination_absent(destination)
    manifest = load_source_manifest(source, source_manifest_path)
    bindings = required_secret_bindings(manifest)
    derived = derive_manifest(source, destination, manifest, storage_uuid)
    coolify_preflight(destination, derived)

    with tempfile.TemporaryDirectory(prefix=f"gm-clone-{destination}-") as temporary:
        workspace = Path(temporary) / "repo"
        source_url = f"https://github.com/{ORG}/{source}.git"
        run(["clone", "--branch", "dev", source_url, str(workspace)])
        run(["branch", "main", "origin/main"], cwd=workspace)
        reject_data_paths(workspace)
        created = github("POST", f"/orgs/{ORG}/repos", {"name": destination, "description": description, "private": True, "has_issues": True})
        if not isinstance(created, dict):
            raise CloneError("GitHub did not confirm destination repository creation.")
        github("PATCH", f"/repos/{ORG}/{destination}", {"default_branch": "dev"})
        run(["remote", "set-url", "origin", f"https://github.com/{ORG}/{destination}.git"], cwd=workspace)
        run(["push", "origin", "main", "dev"], cwd=workspace)
        rewrite_source_identity(workspace, source, destination)
        github("PUT", f"/repos/{ORG}/{destination}/topics", {"names": ["goodmorning", "goodmorning-product", "managed-by-goodmorning-factory"]})

    return {
        "status": "created",
        "source": source,
        "destination": destination,
        "repository": f"{ORG}/{destination}",
        "manifest": derived,
        "managed_secret_bindings": sorted(bindings),
        "data_copied": False,
    }


def parser() -> argparse.ArgumentParser:
    value = argparse.ArgumentParser(description=__doc__)
    value.add_argument("--source-product", required=True)
    value.add_argument("--destination-slug", required=True)
    value.add_argument("--source-manifest", required=True, type=Path)
    value.add_argument("--backup-storage-uuid", default=os.environ.get("COOLIFY_BACKUP_STORAGE_UUID", ""))
    value.add_argument("--description", default="GoodMorning product clone")
    return value


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        print(json.dumps(clone(args.source_product, args.destination_slug, args.source_manifest, args.backup_storage_uuid, args.description), sort_keys=True))
        return 0
    except CloneError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
