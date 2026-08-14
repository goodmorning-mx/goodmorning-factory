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
    from scripts.module_contract import ModuleContractError, validate_product_modules
except ModuleNotFoundError:  # Direct execution: python scripts/reconcile_resource_product_production.py
    from module_contract import ModuleContractError, validate_product_modules


PRODUCT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
TOPOLOGY = "resources-v2-production"
OPERATIONS = {"plan", "provision", "deploy-backend", "deploy-full"}
PHASES = {"plan", "apply"}
LEGACY_FRONTEND_MIGRATION_KEYS = {"uuid", "confirmation", "expected"}
LEGACY_FRONTEND_EXPECTED_KEYS = {
    "name",
    "git_repository",
    "git_branch",
    "build_pack",
    "base_directory",
    "dockerfile_location",
    "ports_exposes",
    "domains",
}
FRONTEND_SOURCE_VERIFICATION_KEYS = {
    "uuid",
    "confirmation",
    "unverified_field",
    "required_gates",
}
FRONTEND_SOURCE_VERIFICATION_GATES = ["deployment", "runtime", "smoke"]
FRONTEND_SOURCE_REPLACEMENT_KEYS = {
    "legacy_uuid",
    "confirmation",
    "candidate_name",
    "quarantine_name",
    "required_gates",
}
FRONTEND_SOURCE_REPLACEMENT_GATES = [
    "deployment",
    "runtime",
    "domain_swap",
    "smoke",
]
COOLIFY_UUID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{5,63}$")


class ManifestError(ValueError):
    pass


def validate_legacy_frontend_migration(manifest: dict[str, Any]) -> None:
    """Validate an exact, one-time adoption contract for a legacy frontend.

    This contract is optional. When present, it authorizes migration of one
    observed Coolify Application UUID only if every recorded legacy field still
    matches. Any drift fails closed before a PATCH is attempted.
    """

    migration = manifest.get("legacy_frontend_migration")
    if migration is None:
        return
    if not isinstance(migration, dict) or set(migration) != LEGACY_FRONTEND_MIGRATION_KEYS:
        raise ManifestError(
            "legacy_frontend_migration must contain exactly uuid, confirmation, and expected."
        )

    product = manifest.get("product")
    identifier = migration.get("uuid")
    if not isinstance(identifier, str) or not COOLIFY_UUID_RE.fullmatch(identifier):
        raise ManifestError("legacy_frontend_migration.uuid must be one exact Coolify UUID.")
    if migration.get("confirmation") != f"migrate:{product}-frontend-prod":
        raise ManifestError(
            "legacy_frontend_migration.confirmation must match the production frontend exactly."
        )

    expected = migration.get("expected")
    if not isinstance(expected, dict) or set(expected) != LEGACY_FRONTEND_EXPECTED_KEYS:
        raise ManifestError(
            "legacy_frontend_migration.expected does not match the approved legacy identity contract."
        )
    if not isinstance(expected.get("name"), str) or not expected["name"].strip():
        raise ManifestError("legacy frontend name must be non-empty.")
    if not isinstance(expected.get("git_repository"), str) or not REPOSITORY_RE.fullmatch(
        expected["git_repository"]
    ):
        raise ManifestError("legacy frontend repository must be an owner/repository identifier.")
    if expected.get("git_branch") != "main":
        raise ManifestError("legacy frontend branch must be main.")
    if expected.get("build_pack") != "dockerfile":
        raise ManifestError("legacy frontend build_pack must be dockerfile.")
    if not isinstance(expected.get("base_directory"), str) or not expected["base_directory"].startswith("/"):
        raise ManifestError("legacy frontend base_directory must be an absolute repository path.")
    if expected.get("dockerfile_location") != "/Dockerfile":
        raise ManifestError("legacy frontend Dockerfile location must be /Dockerfile.")
    if str(expected.get("ports_exposes")) != "80":
        raise ManifestError("legacy frontend must expose port 80.")
    if expected.get("domains") != f"https://{manifest.get('production_domain')}":
        raise ManifestError("legacy frontend domain must equal the production domain exactly.")


def validate_frontend_source_verification(manifest: dict[str, Any]) -> None:
    """Validate the narrow fallback for an API-unobservable source binding.

    Coolify versions that cannot update or expose a migrated application's
    GitHub App binding may use this contract for one exact UUID. It permits
    structural reconciliation to continue, but production source verification
    still requires a successful deployment, healthy runtime, and public smoke
    check in the deploy-full phase.
    """

    contract = manifest.get("frontend_source_verification")
    if contract is None:
        return
    if not isinstance(contract, dict) or set(contract) != FRONTEND_SOURCE_VERIFICATION_KEYS:
        raise ManifestError(
            "frontend_source_verification must contain exactly uuid, confirmation, "
            "unverified_field, and required_gates."
        )

    product = manifest.get("product")
    identifier = contract.get("uuid")
    if not isinstance(identifier, str) or not COOLIFY_UUID_RE.fullmatch(identifier):
        raise ManifestError("frontend_source_verification.uuid must be one exact Coolify UUID.")
    if contract.get("confirmation") != f"verify-source-by-deployment:{product}-frontend-prod":
        raise ManifestError(
            "frontend_source_verification.confirmation must match the production frontend exactly."
        )
    if contract.get("unverified_field") != "github_app_uuid":
        raise ManifestError(
            "frontend_source_verification may waive only an API-unobservable github_app_uuid."
        )
    if contract.get("required_gates") != FRONTEND_SOURCE_VERIFICATION_GATES:
        raise ManifestError(
            "frontend_source_verification requires deployment, runtime, and smoke gates in order."
        )
    migration = manifest.get("legacy_frontend_migration")
    if isinstance(migration, dict) and str(migration.get("uuid") or "") != identifier:
        raise ManifestError(
            "frontend source verification and legacy migration must bind the same Coolify UUID."
        )


def validate_frontend_source_replacement(manifest: dict[str, Any]) -> None:
    """Validate a staged, non-destructive replacement of one legacy frontend."""

    contract = manifest.get("frontend_source_replacement")
    if contract is None:
        return
    if manifest.get("frontend_source_verification") is not None:
        raise ManifestError(
            "frontend_source_replacement and frontend_source_verification are mutually exclusive."
        )
    if not isinstance(contract, dict) or set(contract) != FRONTEND_SOURCE_REPLACEMENT_KEYS:
        raise ManifestError(
            "frontend_source_replacement must contain exactly legacy_uuid, confirmation, "
            "candidate_name, quarantine_name, and required_gates."
        )

    product = manifest.get("product")
    canonical_name = f"{product}-frontend-prod"
    legacy_uuid = contract.get("legacy_uuid")
    if not isinstance(legacy_uuid, str) or not COOLIFY_UUID_RE.fullmatch(legacy_uuid):
        raise ManifestError(
            "frontend_source_replacement.legacy_uuid must be one exact Coolify UUID."
        )
    if contract.get("confirmation") != f"replace-source:{canonical_name}":
        raise ManifestError(
            "frontend_source_replacement.confirmation must match the production frontend exactly."
        )
    if contract.get("candidate_name") != f"{canonical_name}-candidate":
        raise ManifestError(
            "frontend_source_replacement.candidate_name must be the exact isolated candidate name."
        )
    if contract.get("quarantine_name") != f"{canonical_name}-legacy":
        raise ManifestError(
            "frontend_source_replacement.quarantine_name must be the exact retained legacy name."
        )
    if contract.get("required_gates") != FRONTEND_SOURCE_REPLACEMENT_GATES:
        raise ManifestError(
            "frontend_source_replacement requires deployment, runtime, domain_swap, and smoke gates."
        )


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

    try:
        modules = validate_product_modules(manifest.get("modules", []))
    except ModuleContractError as exc:
        raise ManifestError(f"Invalid reusable modules: {exc}") from exc

    validate_legacy_frontend_migration(manifest)
    validate_frontend_source_verification(manifest)
    validate_frontend_source_replacement(manifest)
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


def run_provisioner(provisioner: Any, provisioner_argv: list[str]) -> int:
    """Run the legacy provisioner and convert SystemExit into a numeric code.

    The provisioner is also a CLI and may raise ``SystemExit`` instead of
    returning its final code. The adapter must observe that code so it can apply
    the operation-specific hand-off policy without swallowing unexpected errors.
    """

    try:
        result = provisioner.main(provisioner_argv)
    except SystemExit as exc:
        code = exc.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        return 1
    return int(result or 0)


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

    install_application_conflict_recovery(
        provisioner,
        legacy_frontend_migration=manifest.get("legacy_frontend_migration"),
        frontend_source_verification=manifest.get("frontend_source_verification"),
        frontend_source_replacement=manifest.get("frontend_source_replacement"),
    )
    # Preserve every provisioner exit code. Non-zero means reconciliation found
    # a real blocker and must never be normalized into a deploy hand-off.
    return run_provisioner(provisioner, provisioner_argv)


if __name__ == "__main__":
    raise SystemExit(main())
