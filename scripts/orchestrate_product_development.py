#!/usr/bin/env python3
"""Stage 9 controlled development orchestration for GoodMorning products."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Callable

try:
    from provision_product_development import ProvisioningError, build_parser as build_provisioner_parser, provision, redact
except ModuleNotFoundError:  # pragma: no cover - package import path for tests
    from scripts.provision_product_development import ProvisioningError, build_parser as build_provisioner_parser, provision, redact

OPERATIONS = ("plan", "provision", "deploy-backend", "deploy-full")
MUTATING_OPERATIONS = {"provision", "deploy-backend", "deploy-full"}
ENVIRONMENT = "dev"
OWNER = "goodmorning-mx"
BRANCH = "dev"
GATES = (
    "COOLIFY_ALLOW_RESOURCE_WRITES",
    "COOLIFY_ALLOW_DATABASE_START",
    "COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES",
    "COOLIFY_ALLOW_BACKEND_ENV_WRITES",
    "COOLIFY_ALLOW_FRONTEND_APPLICATION_WRITES",
    "COOLIFY_ALLOW_FRONTEND_ENV_WRITES",
    "COOLIFY_ALLOW_DEPLOYMENTS",
    "COOLIFY_ALLOW_BACKEND_DEPLOYMENT",
    "COOLIFY_ALLOW_FRONTEND_DEPLOYMENT",
)
PROVISION_GATES = frozenset(GATES[:6])
BACKEND_DEPLOY_GATES = PROVISION_GATES | {"COOLIFY_ALLOW_DEPLOYMENTS", "COOLIFY_ALLOW_BACKEND_DEPLOYMENT"}
FULL_DEPLOY_GATES = BACKEND_DEPLOY_GATES | {"COOLIFY_ALLOW_FRONTEND_DEPLOYMENT"}
ALLOWED_TRUE_GATES = {
    "plan": frozenset(),
    "provision": PROVISION_GATES,
    "deploy-backend": BACKEND_DEPLOY_GATES,
    "deploy-full": FULL_DEPLOY_GATES,
}
UNSAFE_STATUSES = {
    "duplicate", "incompatible", "recreate_required", "out_of_scope", "scope_unverified",
    "compatibility_unverified", "legacy_compose_migration_required", "write_outcome_unknown",
    "created_unverified", "updated_unverified", "deployment_outcome_unknown",
    "backend_deployment_outcome_unknown", "frontend_deployment_outcome_unknown",
}
UNSAFE_REASONS = UNSAFE_STATUSES | {
    "destination_ambiguous", "destination_missing", "server_ambiguous", "github_app_ambiguous",
    "project_ambiguous", "environment_ambiguous", "identity_contradiction",
    "uuid_ambiguity", "ambiguous_backend_deployment_recovery", "ambiguous_frontend_deployment_recovery",
}
SAFE_STATUSES = {"absent", "would_create", "would_start", "would_deploy", "would_verify_backend_then_deploy", "planned", "dry_run", "exists", "found", "healthy", "not_requested"}


def truthy_env(name: str) -> bool:
    return os.environ.get(name) == "true"


def expected_confirmation(operation: str, product: str) -> str:
    return f"{operation}:{product}:{ENVIRONMENT}"


def validate_orchestration_inputs(args: argparse.Namespace) -> dict[str, str]:
    if args.operation not in OPERATIONS:
        raise ProvisioningError("Unsupported operation.", error_type="validation", code=2)
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", args.product or ""):
        raise ProvisioningError("--product must be a lowercase DNS-safe slug.", error_type="validation", code=2)
    return {
        "product": args.product,
        "repository": f"{OWNER}/{args.product}",
        "branch": BRANCH,
        "coolify_project": args.product,
        "environment": ENVIRONMENT,
        "backend": f"{args.product}-api-dev",
        "frontend": f"{args.product}-frontend-dev",
        "postgresql": f"{args.product}-postgres-dev",
        "api_domain": f"https://api.dev.{args.product}.goodmorning.mx",
        "frontend_domain": f"https://dev.{args.product}.goodmorning.mx",
    }


def validate_operation_gates(operation: str) -> None:
    allowed = ALLOWED_TRUE_GATES[operation]
    for gate in GATES:
        if truthy_env(gate) != (gate in allowed):
            state = "required" if gate in allowed else "not allowed"
            raise ProvisioningError(f"Gate contract violation: {gate} is {state} for {operation}.", error_type="safety", code=2)


def build_provisioner_argv(args: argparse.Namespace) -> list[str]:
    derived = validate_orchestration_inputs(args)
    argv = [
        "--product", args.product,
        "--repository", derived["repository"],
        "--branch", BRANCH,
        "--coolify-project", args.product,
        "--environment", ENVIRONMENT,
        "--server", args.server,
        "--github-app", args.github_app,
        "--dry-run" if args.operation == "plan" else "--apply",
    ]
    if args.destination_uuid:
        argv += ["--destination-uuid", args.destination_uuid]
    if args.operation in MUTATING_OPERATIONS:
        argv += [
            "--confirm-product", args.product,
            "--confirm-database-start", derived["postgresql"],
            "--confirm-backend-application", derived["backend"],
            "--confirm-backend-environment", derived["backend"],
            "--confirm-frontend-application", derived["frontend"],
            "--confirm-frontend-environment", derived["frontend"],
        ]
    if args.operation in {"deploy-backend", "deploy-full"}:
        argv += ["--deploy-backend", "--confirm-backend-deployment", derived["backend"]]
    if args.operation == "deploy-full":
        argv += ["--deploy-frontend", "--confirm-frontend-deployment", derived["frontend"]]
    build_provisioner_parser().parse_args(argv)
    return argv


def _walk(value: Any):
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _walk(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk(item)


def classify_plan_safety(result: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    for node in _walk(result):
        status = str(node.get("status", ""))
        reason = str(node.get("reason", ""))
        if status in UNSAFE_STATUSES or reason in UNSAFE_REASONS or node.get("write_outcome_unknown") or node.get("created_unverified"):
            blockers.append({"status": status or None, "reason": reason or None, "resource": node.get("resource") or node.get("name")})
    for key in ("selected_destination", "selected_server"):
        node = result.get(key)
        if isinstance(node, dict) and str(node.get("status", "")) in {"absent", "ambiguous", "duplicate"}:
            blockers.append({"resource": key, "status": node.get("status")})
    return (not blockers, blockers)


def orchestration_metadata(args: argparse.Namespace, *, phase: str, plan_safe: bool | None, apply_authorized: bool, confirmation_matches: bool) -> dict[str, Any]:
    order = []
    if args.operation in {"deploy-backend", "deploy-full"}: order.append("backend")
    if args.operation == "deploy-full": order.append("frontend")
    next_action = "apply" if phase == "plan" and plan_safe and args.operation != "plan" else ("complete" if plan_safe is not False else "fix_blockers")
    return {
        "operation": args.operation,
        "phase": phase,
        "confirmation_required": args.operation in MUTATING_OPERATIONS,
        "confirmation_matches": confirmation_matches,
        "plan_safe": plan_safe,
        "apply_authorized": apply_authorized,
        "deployment_order": order,
        "next_action": next_action,
    }


def orchestrate(args: argparse.Namespace, *, client_factory: Callable[[], Any] | None = None) -> tuple[int, dict[str, Any]]:
    validate_orchestration_inputs(args)
    confirmation_matches = (args.confirmation == expected_confirmation(args.operation, args.product)) if args.operation in MUTATING_OPERATIONS else True
    if args.operation in MUTATING_OPERATIONS and not confirmation_matches:
        raise ProvisioningError("Exact confirmation is required before mutating operations.", error_type="safety", code=2)
    validate_operation_gates(args.operation)
    argv = build_provisioner_argv(args)
    pargs = build_provisioner_parser().parse_args(argv)
    result = provision(pargs, client_factory=client_factory)
    plan_safe = None
    phase = "apply" if args.operation in MUTATING_OPERATIONS else "plan"
    if args.operation == "plan":
        plan_safe, plan_blockers = classify_plan_safety(result)
        result["plan_blockers"] = plan_blockers
    exit_code = 2 if result.get("status") in {"partial", "blocked", "recreate_required", "created_unverified", "error", "unhealthy", "readiness_unverified", "timeout"} else 0
    if args.operation == "plan" and not plan_safe:
        exit_code = 2
    result["orchestration"] = orchestration_metadata(args, phase=phase, plan_safe=plan_safe, apply_authorized=args.operation in MUTATING_OPERATIONS and confirmation_matches, confirmation_matches=confirmation_matches)
    return exit_code, redact(result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Controlled GoodMorning development orchestration.")
    parser.add_argument("--operation", required=True, choices=OPERATIONS)
    parser.add_argument("--product", required=True)
    parser.add_argument("--server", required=True)
    parser.add_argument("--github-app", required=True)
    parser.add_argument("--destination-uuid")
    parser.add_argument("--confirmation")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        code, result = orchestrate(args)
    except ProvisioningError as exc:
        payload = exc.to_dict()
        payload["orchestration"] = orchestration_metadata(args, phase="preflight", plan_safe=False, apply_authorized=False, confirmation_matches=(args.confirmation == expected_confirmation(args.operation, args.product)) if args.operation in MUTATING_OPERATIONS else True)
        print(json.dumps(redact(payload), indent=2, sort_keys=True), file=sys.stderr)
        return exc.code
    print(json.dumps(result, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
