#!/usr/bin/env python3
"""Plan a GoodMorning Coolify development environment using separate resources.

This provisioner is intentionally read-only. It validates inputs, discovers existing
Coolify records, builds the desired resource plan, and prints a dry-run JSON plan.
It must not create, patch, delete, start, or deploy any Coolify resource.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable

ALLOWED_BRANCH = "dev"
ALLOWED_ENVIRONMENT = "dev"
POSTGRES_VERSION = "16"
TIMEOUT_SECONDS = 30
SENSITIVE_ENV = ("COOLIFY_API_TOKEN",)
SECRET_KEY_RE = re.compile(r"(PASSWORD|SECRET|TOKEN|KEY|PRIVATE|CREDENTIAL|DATABASE_URL)", re.I)
PRODUCT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
READ_ONLY_METHODS = {"GET"}


class ProvisioningError(Exception):
    """Structured, redacted provisioning failure."""

    def __init__(self, message: str, *, error_type: str = "validation", code: int = 1):
        super().__init__(message)
        self.message = redact(message)
        self.error_type = error_type
        self.code = code

    def to_dict(self) -> dict[str, Any]:
        return {"status": "error", "error": {"type": self.error_type, "message": self.message}}


@dataclass(frozen=True)
class Response:
    status: int
    data: Any
    headers: dict[str, str]


def redact(value: Any) -> Any:
    """Redact secrets from nested values and strings."""

    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if SECRET_KEY_RE.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        text = value
        for key in SENSITIVE_ENV:
            secret = os.environ.get(key)
            if secret:
                text = text.replace(secret, "[REDACTED]")
        return text
    return value


class CoolifyClient:
    """Small reusable HTTP client restricted to read-only Coolify API calls."""

    def __init__(self, base_url: str, api_token: str, *, timeout: int = TIMEOUT_SECONDS):
        self.base_url = validate_base_url(base_url).rstrip("/")
        if not api_token:
            raise ProvisioningError("COOLIFY_API_TOKEN is required.")
        self.api_token = api_token
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "CoolifyClient":
        return cls(os.environ.get("COOLIFY_BASE_URL", ""), os.environ.get("COOLIFY_API_TOKEN", ""))

    def request(self, method: str, path: str) -> Response:
        method = method.upper()
        if method not in READ_ONLY_METHODS:
            raise ProvisioningError(
                f"Refusing non-read-only Coolify method {method}.",
                error_type="safety",
            )
        if not path.startswith("/"):
            raise ProvisioningError("Coolify API path must start with '/'.")
        request = urllib.request.Request(
            f"{self.base_url}/api/v1{path}",
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.api_token}",
                "User-Agent": "goodmorning-factory-product-development-planner",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                data = json.loads(raw) if raw else None
                return Response(response.status, data, dict(response.headers))
        except urllib.error.HTTPError as exc:
            body = safe_http_body(exc.read().decode("utf-8", errors="replace"))
            raise ProvisioningError(
                f"Coolify API {exc.code} for {method} {path}: {body}",
                error_type="coolify_api",
                code=2,
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProvisioningError(
                f"Coolify connection failure for {method} {path}: {exc}",
                error_type="network",
                code=2,
            ) from exc
        except json.JSONDecodeError as exc:
            raise ProvisioningError(
                f"Malformed JSON response for {method} {path}: {exc}",
                error_type="coolify_api",
                code=2,
            ) from exc

    def get(self, path: str) -> Any:
        return self.request("GET", path).data




def redact_non_json_body(text: str, *, limit: int = 1000) -> str:
    text = redact(text)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", text, flags=re.I)
    text = re.sub(r"(https?://)([^:/\s]+):([^@/\s]+)@", r"\1[REDACTED]@", text)
    text = re.sub(r"postgres(?:ql)?://[^\s'\"]+", "postgresql://[REDACTED]", text, flags=re.I)
    text = re.sub(r"(password|token|secret)([=:]\s*)([^\s,&}]+)", r"\1\2[REDACTED]", text, flags=re.I)
    return text[:limit] + ("...[truncated]" if len(text) > limit else "")


def safe_http_body(raw: str) -> str:
    try:
        return json.dumps(redact(json.loads(raw)), sort_keys=True)[:1000]
    except json.JSONDecodeError:
        return redact_non_json_body(raw)


def validate_base_url(value: str) -> str:
    value = value.strip()
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise ProvisioningError("COOLIFY_BASE_URL must be an https URL without credentials, query, fragment, or extra path.")
    if parsed.username or parsed.password:
        raise ProvisioningError("COOLIFY_BASE_URL must not include embedded credentials.")
    if parsed.query or parsed.fragment:
        raise ProvisioningError("COOLIFY_BASE_URL must not include query strings or fragments.")
    if parsed.path not in ("", "/"):
        raise ProvisioningError("COOLIFY_BASE_URL must not include an additional path.")
    return f"{parsed.scheme}://{parsed.netloc}"


def validate_args(args: argparse.Namespace) -> None:
    if not PRODUCT_RE.fullmatch(args.product):
        raise ProvisioningError("product must be a lowercase DNS-safe slug.")
    if not REPOSITORY_RE.fullmatch(args.repository):
        raise ProvisioningError("repository must use owner/name format.")
    if args.branch != ALLOWED_BRANCH:
        raise ProvisioningError("Only branch 'dev' is supported by this stage.")
    if args.environment != ALLOWED_ENVIRONMENT:
        raise ProvisioningError("Only environment 'dev' is supported by this stage.")
    if not args.coolify_project.strip():
        raise ProvisioningError("Coolify project is required.")
    if not args.server.strip():
        raise ProvisioningError("server is required.")
    if not args.github_app.strip():
        raise ProvisioningError("exact GitHub App name is required.")
    if not args.dry_run:
        raise ProvisioningError("Only --dry-run is supported; writes are disabled.", error_type="safety")


def item_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("data", "items", "projects", "environments", "servers", "github_apps", "githubApps", "applications", "databases"):
            if isinstance(data.get(key), list):
                return [item for item in data[key] if isinstance(item, dict)]
    return []


def field(item: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = item.get(name)
        if value not in (None, ""):
            return value
    return None


def select_unique_by_name(items: list[dict[str, Any]], expected: str, kind: str) -> dict[str, Any]:
    matches = [item for item in items if str(item.get("name") or "") == expected]
    if not matches:
        raise ProvisioningError(f"Could not find Coolify {kind} named exactly '{expected}'.", error_type="discovery")
    if len(matches) > 1:
        raise ProvisioningError(f"Found multiple Coolify {kind} records named exactly '{expected}'.", error_type="discovery")
    return matches[0]


def discover(client: CoolifyClient, args: argparse.Namespace) -> dict[str, Any]:
    projects = item_list(client.get("/projects"))
    project = select_unique_by_name(projects, args.coolify_project, "project")
    project_id = field(project, "uuid", "id")
    if not project_id:
        raise ProvisioningError("Selected Coolify project is missing an identifier.", error_type="discovery")

    detailed_project = client.get(f"/projects/{project_id}")
    project_details = detailed_project.get("data") if isinstance(detailed_project, dict) and isinstance(detailed_project.get("data"), dict) else detailed_project
    environments = item_list(project_details.get("environments") if isinstance(project_details, dict) else [])
    if not environments:
        environments = item_list(client.get(f"/projects/{project_id}/environments"))
    environment = select_unique_by_name(environments, args.environment, "environment")

    server = select_unique_by_name(item_list(client.get("/servers")), args.server, "server")
    github_app = select_unique_by_name(item_list(client.get("/github-apps")), args.github_app, "GitHub App")

    project_summary = summary(project, "name", "uuid", "id")
    environment_summary = summary(environment, "name", "uuid", "id")
    server_summary = summary(server, "name", "uuid", "id")
    github_app_summary = summary(github_app, "name", "uuid", "id")
    context = {
        "project_uuid": project_summary.get("uuid") or project_summary.get("id"),
        "environment_uuid": environment_summary.get("uuid") or environment_summary.get("id"),
        "environment_name": args.environment,
        "server_uuid": server_summary.get("uuid") or server_summary.get("id"),
        "github_app_uuid": github_app_summary.get("uuid") or github_app_summary.get("id"),
    }
    existing = discover_existing_resources(client, args.product, context, args)
    return {
        "project": project_summary,
        "environment": environment_summary,
        "server": server_summary,
        "github_app": github_app_summary,
        "bindings": context,
        "existing_resources": existing,
    }


def expected_resource_specs(product: str, args: argparse.Namespace | None = None) -> dict[str, dict[str, Any]]:
    repository = args.repository if args else None
    github_app_name = args.github_app if args else None
    return {
        f"{product}-postgres-dev": {"kind": "database", "type": "postgresql"},
        f"{product}-api-dev": {
            "kind": "application",
            "git_repository": repository,
            "git_branch": "dev",
            "build_pack": "dockerfile",
            "base_directory": "/backend",
            "dockerfile_location": "/Dockerfile",
            "ports_exposes": "8000",
            "domains": f"https://api.dev.{product}.goodmorning.mx",
            "github_app_name": github_app_name,
        },
        f"{product}-frontend-dev": {
            "kind": "application",
            "git_repository": repository,
            "git_branch": "dev",
            "build_pack": "dockerfile",
            "base_directory": "/frontend",
            "dockerfile_location": "/Dockerfile",
            "ports_exposes": "80",
            "domains": f"https://dev.{product}.goodmorning.mx",
            "github_app_name": github_app_name,
        },
    }


def nested_field(item: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = item
        for part in path.split("."):
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(part)
        if current not in (None, ""):
            if isinstance(current, dict):
                for key in ("uuid", "id", "name"):
                    if current.get(key) not in (None, ""):
                        return current[key]
            return current
    return None


def resource_scope(item: dict[str, Any], context: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    checks = [
        ("project_uuid", context.get("project_uuid"), nested_field(item, "project_uuid", "project.uuid", "project.id")),
        ("environment_uuid", context.get("environment_uuid"), nested_field(item, "environment_uuid", "environment.uuid", "environment.id")),
        ("server_uuid", context.get("server_uuid"), nested_field(item, "server_uuid", "server.uuid", "server.id", "destination.server.uuid", "destination.server.id")),
    ]
    details = []
    unknown = False
    for name, expected, actual in checks:
        if actual in (None, ""):
            unknown = True
            details.append({"field": name, "status": "unverified"})
        elif expected and str(actual) != str(expected):
            details.append({"field": name, "status": "mismatch", "expected": expected, "actual": actual})
        else:
            details.append({"field": name, "status": "matched"})
    if any(detail["status"] == "mismatch" for detail in details):
        return "out_of_scope", details
    if unknown:
        return "scope_unverified", details
    return "in_scope", details


def resource_kind(item: dict[str, Any]) -> str:
    source_kind = str(item.get("_discovery_source") or "")
    raw = str(field(item, "kind", "type", "resource_type") or "").lower()
    if source_kind == "databases" or "postgres" in raw:
        return "database"
    return "application"


def is_compose_application(item: dict[str, Any]) -> bool:
    raw = " ".join(str(field(item, name) or "") for name in ("build_pack", "buildpack", "type", "docker_compose_location", "docker_compose_raw"))
    return "compose" in raw.lower()


def operational_summary(item: dict[str, Any]) -> dict[str, Any]:
    return summary(
        item,
        "name",
        "uuid",
        "id",
        "type",
        "kind",
        "build_pack",
        "buildpack",
        "git_repository",
        "git_branch",
        "base_directory",
        "dockerfile_location",
        "ports_exposes",
        "domains",
        "project_uuid",
        "environment_uuid",
        "server_uuid",
    )


def application_differences(item: dict[str, Any], expected: dict[str, Any], context: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    field_paths = {
        "git_repository": ("git_repository", "repository", "source.git_repository"),
        "git_branch": ("git_branch", "branch", "source.git_branch"),
        "build_pack": ("build_pack", "buildpack"),
        "base_directory": ("base_directory",),
        "dockerfile_location": ("dockerfile_location", "dockerfile"),
        "ports_exposes": ("ports_exposes",),
        "domains": ("domains", "domain"),
    }
    differences = []
    unverified = []
    for key, paths in field_paths.items():
        expected_value = expected.get(key)
        actual = nested_field(item, *paths)
        if actual in (None, ""):
            unverified.append({"field": key, "expected": expected_value})
        elif str(actual) != str(expected_value):
            differences.append({"field": key, "expected": expected_value, "actual": actual})
    source_uuid = nested_field(item, "github_app_uuid", "source_uuid", "source.uuid", "github_app.uuid")
    if source_uuid in (None, ""):
        unverified.append({"field": "github_app_uuid", "expected": context.get("github_app_uuid")})
    elif context.get("github_app_uuid") and str(source_uuid) != str(context["github_app_uuid"]):
        differences.append({"field": "github_app_uuid", "expected": context.get("github_app_uuid"), "actual": source_uuid})
    return differences, unverified


def classify_expected_resource(name: str, expected: dict[str, Any], matches: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    if not matches:
        return {"name": name, "status": "absent"}

    scoped = []
    reports = []
    for item in matches:
        scope_status, scope_details = resource_scope(item, context)
        report = {"resource": operational_summary(item), "scope": scope_status, "scope_details": scope_details}
        if scope_status == "out_of_scope":
            reports.append({**report, "status": "out_of_scope"})
        elif scope_status == "scope_unverified":
            reports.append({**report, "status": "scope_unverified"})
        else:
            scoped.append(item)
            reports.append(report)

    if not scoped:
        status = "out_of_scope" if all(report.get("status") == "out_of_scope" for report in reports) else "scope_unverified"
        return {"name": name, "status": status, "matches": reports}
    if len(scoped) > 1:
        return {"name": name, "status": "duplicate", "matches": [operational_summary(item) for item in scoped]}

    item = scoped[0]
    if resource_kind(item) != expected["kind"]:
        return {"name": name, "status": "incompatible", "matches": reports, "differences": [{"field": "kind", "expected": expected["kind"], "actual": resource_kind(item)}]}
    if expected["kind"] == "application":
        if is_compose_application(item):
            return {"name": name, "status": "legacy_compose_migration_required", "matches": reports}
        differences, unverified = application_differences(item, expected, context)
        if differences:
            return {"name": name, "status": "incompatible", "matches": reports, "differences": differences}
        if unverified:
            return {"name": name, "status": "compatibility_unverified", "matches": reports, "unverified_fields": unverified}
    return {"name": name, "status": "found", "matches": reports}


def discover_existing_resources(client: CoolifyClient, product: str, context: dict[str, Any], args: argparse.Namespace) -> dict[str, Any]:
    """Report expected resource identities using only GET discovery calls.

    The environment endpoint fallback in discover() is only a transport/API-shape
    fallback: the selected environment must still be named exactly ``dev``.
    """

    collected: list[dict[str, Any]] = []
    warnings: list[dict[str, str]] = []
    for path, source_kind in (("/applications", "applications"), ("/databases", "databases")):
        try:
            resources = item_list(client.get(path))
        except ProvisioningError as exc:
            warnings.append({"endpoint": path, "warning": exc.message})
            continue
        for item in resources:
            item = dict(item)
            item["_discovery_source"] = source_kind
            collected.append(item)

    expected = expected_resource_specs(product, args)
    classifications = []
    for name, spec in expected.items():
        matches = [item for item in collected if str(field(item, "name") or "") == name]
        classifications.append(classify_expected_resource(name, spec, matches, context))

    legacy_name = f"{product}-dev"
    legacy_matches = [item for item in collected if str(field(item, "name") or "") == legacy_name and is_compose_application(item)]
    defensive_compose = [
        item for item in collected
        if item not in legacy_matches and is_compose_application(item)
        and (str(field(item, "name") or "").startswith(f"{product}-") or nested_field(item, "git_repository", "repository") == args.repository)
    ]

    unexpected_same_product_prefix = [
        operational_summary(item)
        for item in collected
        if str(field(item, "name") or "").startswith(f"{product}-")
        and str(field(item, "name") or "") not in expected
        and str(field(item, "name") or "") != legacy_name
    ]

    return {
        "classification": classifications,
        "legacy_compose": [
            {"name": legacy_name, "status": "legacy_compose_migration_required", "resource": operational_summary(item)}
            for item in legacy_matches
        ],
        "defensive_compose_related": [operational_summary(item) for item in defensive_compose],
        "unexpected_same_product_prefix": unexpected_same_product_prefix,
        "warnings": warnings,
    }


def summary(item: dict[str, Any], *fields: str) -> dict[str, Any]:
    result = {name: field(item, name) for name in fields if field(item, name) is not None}
    return redact(result)


def application_desired_state(
    *,
    name: str,
    repository: str,
    github_app_name: str,
    base_directory: str,
    ports_exposes: str,
    domains: str,
) -> dict[str, Any]:
    return {
        "name": name,
        "kind": "application",
        "source": {
            "git_repository": repository,
            "repository_access": "private_github_app",
            "github_app_name": github_app_name,
            "git_branch": "dev",
        },
        "build": {
            "build_pack": "dockerfile",
            "base_directory": base_directory,
            "dockerfile_location": "/Dockerfile",
            "ports_exposes": ports_exposes,
        },
        "routing": {"domains": domains},
        "deployment_policy": {
            "instant_deploy": False,
            "auto_deploy_enabled": False,
        },
    }


def application_create_payload_preview(desired_state: dict[str, Any], bindings: dict[str, Any]) -> dict[str, Any]:
    """Preview only documented Coolify Application creation payload fields."""

    source = desired_state["source"]
    build = desired_state["build"]
    return {
        "name": desired_state["name"],
        "git_repository": source["git_repository"],
        "git_branch": source["git_branch"],
        "build_pack": build["build_pack"],
        "base_directory": build["base_directory"],
        "dockerfile_location": build["dockerfile_location"],
        "ports_exposes": build["ports_exposes"],
        "domains": desired_state["routing"]["domains"],
        "instant_deploy": desired_state["deployment_policy"]["instant_deploy"],
        "project_uuid": bindings.get("project_uuid"),
        "server_uuid": bindings.get("server_uuid"),
        "environment_name": "dev",
        "github_app_uuid": bindings.get("github_app_uuid"),
    }


def build_plan(args: argparse.Namespace, discovery: dict[str, Any]) -> dict[str, Any]:
    bindings = discovery.get("bindings", {}) if isinstance(discovery, dict) else {}
    api_domain = f"https://api.dev.{args.product}.goodmorning.mx"
    frontend_domain = f"https://dev.{args.product}.goodmorning.mx"
    postgres_desired_state = {
        "name": f"{args.product}-postgres-dev",
        "kind": "database",
        "type": "postgresql",
        "version": POSTGRES_VERSION,
        "is_public": False,
        "domain": None,
    }
    backend_desired_state = application_desired_state(
        name=f"{args.product}-api-dev",
        repository=args.repository,
        github_app_name=args.github_app,
        base_directory="/backend",
        ports_exposes="8000",
        domains=api_domain,
    )
    frontend_desired_state = application_desired_state(
        name=f"{args.product}-frontend-dev",
        repository=args.repository,
        github_app_name=args.github_app,
        base_directory="/frontend",
        ports_exposes="80",
        domains=frontend_domain,
    )
    return {
        "status": "dry_run",
        "mode": "read_only_plan",
        "safety": {"writes_enabled": False, "forbidden_methods": ["POST", "PATCH", "PUT", "DELETE"], "deploy_actions_enabled": False},
        "inputs": redact(vars(args)),
        "discovery": discovery,
        "resources": [
            {
                "name": postgres_desired_state["name"],
                "kind": "database",
                "desired_state": postgres_desired_state,
                "create_payload_preview": {
                    "project_uuid": bindings.get("project_uuid"),
                    "server_uuid": bindings.get("server_uuid"),
                    "environment_name": "dev",
                    "name": postgres_desired_state["name"],
                    "postgres_version": POSTGRES_VERSION,
                    "is_public": False,
                    "instant_deploy": False,
                    "pending_contract_decisions": ["Confirm exact Coolify database creation field names before enabling writes."],
                },
                "secret_requirements": ["POSTGRES_PASSWORD or Coolify generated database password"],
            },
            {
                "name": backend_desired_state["name"],
                "kind": "application",
                "desired_state": backend_desired_state,
                "create_payload_preview": application_create_payload_preview(backend_desired_state, bindings),
                "secret_requirements": [],
            },
            {
                "name": frontend_desired_state["name"],
                "kind": "application",
                "desired_state": frontend_desired_state,
                "create_payload_preview": application_create_payload_preview(frontend_desired_state, bindings),
                "secret_requirements": [],
            },
        ],
    }


def provision(args: argparse.Namespace, *, client_factory: Callable[[], CoolifyClient] | None = None) -> dict[str, Any]:
    validate_args(args)
    client = client_factory() if client_factory else CoolifyClient.from_env()
    return build_plan(args, discover(client, args))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build a read-only Coolify development resource plan.")
    parser.add_argument("--product", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--branch", required=True, choices=[ALLOWED_BRANCH])
    parser.add_argument("--coolify-project", required=True)
    parser.add_argument("--environment", required=True, choices=[ALLOWED_ENVIRONMENT])
    parser.add_argument("--server", required=True)
    parser.add_argument("--github-app", required=True, help="Exact Coolify GitHub App name")
    parser.add_argument("--dry-run", action="store_true", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        result = provision(args)
    except ProvisioningError as exc:
        print(json.dumps(exc.to_dict(), indent=2, sort_keys=True), file=sys.stderr)
        return exc.code
    print(json.dumps(redact(result), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
