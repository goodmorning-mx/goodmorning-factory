#!/usr/bin/env python3
"""Catalog adapter for the audited resource-by-resource Coolify provisioner."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from scripts.module_contract import ModuleContractError, compile_installation, validate_product_modules
except ModuleNotFoundError:  # Direct execution: python scripts/reconcile_resource_product.py
    from module_contract import ModuleContractError, compile_installation, validate_product_modules


PRODUCT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
TOPOLOGY = "resources-v2"
OPERATIONS = {"plan", "provision", "deploy-backend", "deploy-full"}
PHASES = {"plan", "apply"}


class ManifestError(ValueError):
    pass


def load_manifest(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ManifestError(f"Unable to read resource manifest {source.name!r}.") from exc
    if not isinstance(value, dict):
        raise ManifestError("Resource manifest must be a JSON object.")
    return validate_manifest(value)


def validate_manifest(manifest: dict[str, Any]) -> dict[str, Any]:
    product = manifest.get("product")
    repository = manifest.get("repository")
    branch = manifest.get("branch", "dev")
    topology = manifest.get("topology")
    domain = manifest.get("development_domain")
    expected_domain = f"dev.{product}.goodmorning.mx" if isinstance(product, str) else None

    if topology != TOPOLOGY:
        raise ManifestError(f"Resource manifest topology must be {TOPOLOGY!r}.")
    if not isinstance(product, str) or not PRODUCT_RE.fullmatch(product):
        raise ManifestError("product must be a lowercase DNS-safe slug.")
    if not isinstance(repository, str) or not REPOSITORY_RE.fullmatch(repository):
        raise ManifestError("repository must be an owner/repository identifier.")
    if branch != "dev":
        raise ManifestError("Resource topology only supports branch 'dev'.")
    if domain != expected_domain:
        raise ManifestError(f"development_domain must be {expected_domain!r}.")
    if manifest.get("backend_base_directory", "/backend") != "/backend":
        raise ManifestError("backend_base_directory must be '/backend'.")
    if manifest.get("frontend_base_directory", "/frontend") != "/frontend":
        raise ManifestError("frontend_base_directory must be '/frontend'.")

    try:
        modules = validate_product_modules(manifest.get("modules", []))
    except ModuleContractError as exc:
        raise ManifestError(f"Invalid reusable modules: {exc}") from exc

    normalized = dict(manifest)
    normalized["modules"] = modules
    return normalized


def build_provisioner_argv(
    manifest: dict[str, Any],
    *,
    phase: str,
    operation: str,
    server: str,
    github_app: str,
    destination_uuid: str,
) -> list[str]:
    manifest = validate_manifest(manifest)
    if phase not in PHASES:
        raise ManifestError(f"Unsupported phase {phase!r}.")
    if operation not in OPERATIONS:
        raise ManifestError(f"Unsupported operation {operation!r}.")
    if phase == "apply" and operation == "plan":
        raise ManifestError("operation='plan' cannot be applied.")

    product = manifest["product"]
    argv = [
        "--product", product,
        "--repository", manifest["repository"],
        "--branch", "dev",
        "--coolify-project", product,
        "--environment", "dev",
        "--server", server,
        "--github-app", github_app,
        "--destination-uuid", destination_uuid,
        "--dry-run" if phase == "plan" else "--apply",
        "--required-secrets-json", json.dumps(compile_installation(manifest["modules"])["secret_bindings"], separators=(",", ":")),
    ]

    if phase == "apply":
        argv.extend([
            "--confirm-product", product,
            "--confirm-database-start", f"{product}-postgres-dev",
            "--confirm-backend-application", f"{product}-api-dev",
            "--confirm-backend-environment", f"{product}-api-dev",
            "--confirm-frontend-application", f"{product}-frontend-dev",
            "--confirm-frontend-environment", f"{product}-frontend-dev",
        ])
    if operation in {"deploy-backend", "deploy-full"}:
        argv.append("--deploy-backend")
        if phase == "apply":
            argv.extend(["--confirm-backend-deployment", f"{product}-api-dev"])
    if operation == "deploy-full":
        argv.append("--deploy-frontend")
        if phase == "apply":
            argv.extend(["--confirm-frontend-deployment", f"{product}-frontend-dev"])
    return argv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reconcile one resources-v2 product manifest.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--phase", required=True, choices=sorted(PHASES))
    parser.add_argument("--operation", required=True, choices=sorted(OPERATIONS))
    parser.add_argument("--server", required=True)
    parser.add_argument("--github-app", required=True)
    parser.add_argument("--destination-uuid", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        provisioner_argv = build_provisioner_argv(
            manifest,
            phase=args.phase,
            operation=args.operation,
            server=args.server,
            github_app=args.github_app,
            destination_uuid=args.destination_uuid,
        )
    except ManifestError as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 2

    if __package__ in (None, ""):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

    from scripts.provision_product_development import main as provision_main

    return provision_main(provisioner_argv)


if __name__ == "__main__":
    raise SystemExit(main())
