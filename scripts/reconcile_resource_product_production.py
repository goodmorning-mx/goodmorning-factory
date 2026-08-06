#!/usr/bin/env python3
"""Catalog adapter for the audited resource-by-resource Coolify provisioner."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


PRODUCT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
TOPOLOGY = "resources-v2-production"
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
    branch = manifest.get("branch", "main")
    topology = manifest.get("topology")
    domain = manifest.get("production_domain")
    expected_domain = f"{product}.goodmorning.mx" if isinstance(product, str) else None

    if topology != TOPOLOGY:
        raise ManifestError(f"Resource manifest topology must be {TOPOLOGY!r}.")
    if not isinstance(product, str) or not PRODUCT_RE.fullmatch(product):
        raise ManifestError("product must be a lowercase DNS-safe slug.")
    if not isinstance(repository, str) or not REPOSITORY_RE.fullmatch(repository):
        raise ManifestError("repository must be an owner/repository identifier.")
    if branch != "main":
        raise ManifestError("Resource topology only supports branch 'main'.")
    if domain != expected_domain:
        raise ManifestError(f"production_domain must be {expected_domain!r}.")
    if manifest.get("backend_base_directory", "/backend") != "/backend":
        raise ManifestError("backend_base_directory must be '/backend'.")
    if manifest.get("frontend_base_directory", "/frontend") != "/frontend":
        raise ManifestError("frontend_base_directory must be '/frontend'.")
    if manifest.get("confirm_production") is not True:
        raise ManifestError("confirm_production must be true for every production reconciliation.")

    return dict(manifest)


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
        "--branch", "main",
        "--coolify-project", product,
        "--environment", "production",
        "--server", server,
        "--github-app", github_app,
        "--destination-uuid", destination_uuid,
        "--dry-run" if phase == "plan" else "--apply",
    ]

    if phase == "apply":
        argv.extend([
            "--confirm-product", product,
            "--confirm-database-start", f"{product}-postgres-prod",
            "--confirm-backend-application", f"{product}-api-prod",
            "--confirm-backend-environment", f"{product}-api-prod",
            "--confirm-frontend-application", f"{product}-frontend-prod",
            "--confirm-frontend-environment", f"{product}-frontend-prod",
        ])
    if operation in {"deploy-backend", "deploy-full"}:
        argv.append("--deploy-backend")
        if phase == "apply":
            argv.extend(["--confirm-backend-deployment", f"{product}-api-prod"])
    if operation == "deploy-full":
        argv.append("--deploy-frontend")
        if phase == "apply":
            argv.extend(["--confirm-frontend-deployment", f"{product}-frontend-prod"])
    return argv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reconcile one resources-v2 production manifest.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--phase", required=True, choices=sorted(PHASES))
    parser.add_argument("--operation", required=True, choices=sorted(OPERATIONS))
    parser.add_argument("--server", required=True)
    parser.add_argument("--github-app", required=True)
    parser.add_argument("--destination-uuid", required=True)
    return parser


def normalize_exit_code(*, operation: str, provisioner_exit_code: int) -> int:
    """Treat non-deployment partial provisioning as a successful hand-off.

    The underlying provisioner uses exit code 1 for ``status=partial`` because
    deployment gates are closed. During the explicit ``provision`` operation,
    closed deployment gates are expected: the workflow performs deployment in a
    later audited step. Preserve every other exit code unchanged.
    """

    if operation == "provision" and provisioner_exit_code == 1:
        return 0
    return provisioner_exit_code


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

    from scripts import provision_product_production as provisioner
    from scripts.production_application_conflict_recovery import (
        install_application_conflict_recovery,
    )

    install_application_conflict_recovery(provisioner)
    return normalize_exit_code(
        operation=args.operation,
        provisioner_exit_code=provisioner.main(provisioner_argv),
    )


if __name__ == "__main__":
    raise SystemExit(main())
