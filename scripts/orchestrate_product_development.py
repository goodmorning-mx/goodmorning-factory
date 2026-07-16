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
PHASES = ("plan", "apply")
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
APPLY_TRUE_GATES = {
    "plan": frozenset(),
    "provision": PROVISION_GATES,
    "deploy-backend": BACKEND_DEPLOY_GATES,
    "deploy-full": FULL_DEPLOY_GATES,
}
ALLOWED_TRUE_GATES = APPLY_TRUE_GATES
SAFE_STATUSES = {
    "absent", "would_create", "would_start", "would_deploy", "would_verify_backend_then_deploy",
    "planned", "dry_run", "exists", "found", "healthy", "already_healthy", "not_requested",
    "present", "compatible", "verified", "passed", "finished", "running:healthy",
}
UNSAFE_STATUSES = {
    "duplicate", "incompatible", "recreate_required", "out_of_scope", "scope_unverified",
    "compatibility_unverified", "legacy_compose_migration_required", "write_outcome_unknown",
    "created_unverified", "updated_unverified", "deployment_outcome_unknown",
    "backend_deployment_outcome_unknown", "frontend_deployment_outcome_unknown", "blocked",
    "partial", "error", "unhealthy", "readiness_unverified", "timeout", "gates_incomplete",
    "ambiguous", "missing", "not_found", "unknown", "deployment_timeout", "deployment_poll_failed",
    "runtime_status_unverified", "runtime_timeout", "smoke_failed", "smoke_timeout",
}
UNSAFE_REASONS = {
    "destination_ambiguous", "destination_missing", "server_ambiguous", "github_app_ambiguous",
    "project_ambiguous", "environment_ambiguous", "identity_contradiction", "uuid_ambiguity",
    "ambiguous_backend_deployment_recovery", "ambiguous_frontend_deployment_recovery",
}
DECISION_KEYS = {
    "status", "project", "environment_resource", "postgresql", "postgresql_readiness",
    "backend_application", "backend_environment", "backend_deployment", "backend_runtime_readiness",
    "backend_smoke_check", "frontend_application", "frontend_environment", "frontend_deployment",
    "frontend_runtime_readiness", "frontend_smoke_check", "selected_server", "selected_destination",
    "selected_github_app", "github_app", "server", "destination", "resources", "actions", "legacy_compose",
}
NON_SUCCESS_EXIT_STATUSES = {"partial", "blocked", "recreate_required", "created_unverified", "error", "unhealthy", "readiness_unverified", "timeout"}
SLUG_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")


def truthy_env(name: str) -> bool:
    return os.environ.get(name) == "true"


def expected_confirmation(operation: str, product: str) -> str:
    return f"{operation}:{product}:{ENVIRONMENT}"


def validate_orchestration_inputs(args: argparse.Namespace) -> dict[str, str]:
    if args.operation not in OPERATIONS:
        raise ProvisioningError("Unsupported operation.", error_type="validation", code=2)
    if args.phase not in PHASES:
        raise ProvisioningError("Unsupported phase.", error_type="validation", code=2)
    if args.operation == "plan" and args.phase != "plan":
        raise ProvisioningError("operation=plan only supports phase=plan.", error_type="validation", code=2)
    if args.phase == "apply" and args.operation not in MUTATING_OPERATIONS:
        raise ProvisioningError("phase=apply requires a mutating target operation.", error_type="validation", code=2)
    if not SLUG_RE.fullmatch(args.product or ""):
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


def validate_operation_gates(operation: str, phase: str) -> None:
    allowed = frozenset() if phase == "plan" else APPLY_TRUE_GATES[operation]
    for gate in GATES:
        if truthy_env(gate) != (gate in allowed):
            state = "required" if gate in allowed else "not allowed"
            raise ProvisioningError(f"Gate contract violation: {gate} is {state} for {operation} phase {phase}.", error_type="safety", code=2)


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
        "--dry-run" if args.phase == "plan" else "--apply",
    ]
    if args.destination_uuid:
        argv += ["--destination-uuid", args.destination_uuid]
    if args.phase == "apply":
        argv += [
            "--confirm-product", args.product,
            "--confirm-database-start", derived["postgresql"],
            "--confirm-backend-application", derived["backend"],
            "--confirm-backend-environment", derived["backend"],
            "--confirm-frontend-application", derived["frontend"],
            "--confirm-frontend-environment", derived["frontend"],
        ]
    if args.operation in {"deploy-backend", "deploy-full"}:
        argv += ["--deploy-backend"]
        if args.phase == "apply":
            argv += ["--confirm-backend-deployment", derived["backend"]]
    if args.operation == "deploy-full":
        argv += ["--deploy-frontend"]
        if args.phase == "apply":
            argv += ["--confirm-frontend-deployment", derived["frontend"]]
    build_provisioner_parser().parse_args(argv)
    return argv


def _walk(value: Any, path: tuple[str, ...] = ()):  # yields (path, node)
    if isinstance(value, dict):
        yield path, value
        for key, item in value.items():
            yield from _walk(item, path + (str(key),))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _walk(item, path + (str(index),))


def _safe_blocker(path: tuple[str, ...], node: dict[str, Any], reason: str) -> dict[str, Any]:
    safe = {
        "path": ".".join(path) if path else "$",
        "reason": reason,
        "status": node.get("status"),
        "resource": node.get("resource") or node.get("name"),
    }
    return redact({key: value for key, value in safe.items() if value not in (None, "")})


def _is_decision_path(path: tuple[str, ...]) -> bool:
    return any(part in DECISION_KEYS for part in path)


def classify_plan_safety(result: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    blockers: list[dict[str, Any]] = []
    if result.get("selected_destination") is None:
        blockers.append({"path": "selected_destination", "reason": "destination_missing"})
    for path, node in _walk(result):
        status = str(node.get("status", "")) if "status" in node else ""
        reason = str(node.get("reason", "")) if "reason" in node else ""
        if isinstance(node.get("blockers"), list) and node.get("blockers"):
            blockers.append(_safe_blocker(path + ("blockers",), node, "blockers_present"))
        if status in UNSAFE_STATUSES:
            blockers.append(_safe_blocker(path, node, status))
        elif status and status not in SAFE_STATUSES and _is_decision_path(path):
            blockers.append(_safe_blocker(path, node, f"unknown_status:{status}"))
        if reason and (reason in UNSAFE_REASONS or "contradiction" in reason or "ambiguous" in reason or "unverified" in reason):
            blockers.append(_safe_blocker(path, node, reason))
        if node.get("write_outcome_unknown") or node.get("created_unverified") or node.get("updated_unverified"):
            blockers.append(_safe_blocker(path, node, "unknown_write_or_unverified_create"))
        if any(str(node.get(key, "")).lower() in {"ambiguous", "duplicate", "absent", "missing", "unverified"} for key in ("identity", "scope", "selection")):
            blockers.append(_safe_blocker(path, node, "identity_scope_or_selection_unverified"))
    return (not blockers, blockers)


def _result_success(exit_code: int, result: dict[str, Any]) -> bool:
    return exit_code == 0 and result.get("status") not in NON_SUCCESS_EXIT_STATUSES


def orchestration_metadata(args: argparse.Namespace, *, phase: str, plan_safe: bool | None, apply_authorized: bool, confirmation_matches: bool, exit_code: int | None = None, result: dict[str, Any] | None = None) -> dict[str, Any]:
    order = []
    if args.operation in {"deploy-backend", "deploy-full"}:
        order.append("backend")
    if args.operation == "deploy-full":
        order.append("frontend")
    if phase == "plan":
        if plan_safe is False:
            next_action = "fix_blockers"
        elif args.operation == "plan":
            next_action = "complete"
        else:
            next_action = "apply"
    else:
        next_action = "complete" if result is not None and exit_code is not None and _result_success(exit_code, result) else "inspect_failure"
    return {
        "target_operation": args.operation,
        "operation": args.operation,
        "phase": phase,
        "confirmation_required": phase == "apply",
        "confirmation_matches": confirmation_matches,
        "plan_safe": plan_safe,
        "apply_authorized": phase == "apply" and apply_authorized,
        "deployment_order": order,
        "next_action": next_action,
    }


def orchestrate(args: argparse.Namespace, *, client_factory: Callable[[], Any] | None = None) -> tuple[int, dict[str, Any]]:
    validate_orchestration_inputs(args)
    confirmation_matches = (args.confirmation == expected_confirmation(args.operation, args.product)) if args.phase == "apply" else True
    if args.phase == "apply" and not confirmation_matches:
        raise ProvisioningError("Exact confirmation is required before apply.", error_type="safety", code=2)
    validate_operation_gates(args.operation, args.phase)
    argv = build_provisioner_argv(args)
    pargs = build_provisioner_parser().parse_args(argv)
    result = provision(pargs, client_factory=client_factory)
    plan_safe = None
    if args.phase == "plan":
        plan_safe, plan_blockers = classify_plan_safety(result)
        result["plan_blockers"] = plan_blockers
    exit_code = 2 if result.get("status") in NON_SUCCESS_EXIT_STATUSES else 0
    if args.phase == "plan" and not plan_safe:
        exit_code = 2
    result["orchestration"] = orchestration_metadata(args, phase=args.phase, plan_safe=plan_safe, apply_authorized=args.phase == "apply" and confirmation_matches, confirmation_matches=confirmation_matches, exit_code=exit_code, result=result)
    return exit_code, redact(result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Controlled GoodMorning development orchestration.")
    parser.add_argument("--operation", required=True, choices=OPERATIONS)
    parser.add_argument("--phase", required=True, choices=PHASES)
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
        try:
            confirmation_matches = (args.confirmation == expected_confirmation(args.operation, args.product)) if args.phase == "apply" else True
            payload["orchestration"] = orchestration_metadata(args, phase=getattr(args, "phase", "plan"), plan_safe=False, apply_authorized=False, confirmation_matches=confirmation_matches, exit_code=exc.code, result=payload)
        except Exception:
            pass
        print(json.dumps(redact(payload), indent=2, sort_keys=True), file=sys.stderr)
        return exc.code
    print(json.dumps(result, indent=2, sort_keys=True))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
