#!/usr/bin/env python3
"""Plan or safely apply GoodMorning Coolify development primitives.

Dry-run is GET-only and produces the complete three-resource plan. Apply can
create only the Coolify Project, exact ``dev`` Environment, and private native
PostgreSQL record after explicit gates. Applications, deployments, and lifecycle
actions remain forbidden in this stage.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import secrets
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
SECRET_KEY_RE = re.compile(r"(PASSWORD|SECRET|TOKEN|KEY|PRIVATE|CREDENTIAL|DATABASE_URL|AUTHORIZATION|INTERNAL_DB_URL)", re.I)
PRODUCT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
READ_ONLY_METHODS = {"GET"}
POST_PROJECT_CREATE = "/projects"
POST_POSTGRES_CREATE = "/databases/postgresql"
ALLOWED_POST_KINDS = {"project_create", "environment_create", "postgresql_create"}
FORBIDDEN_ENDPOINT_RE = re.compile(r"/(deploy|deployments|start|restart|stop)(?:/|$)", re.I)
NON_SUCCESS_EXIT_STATUSES = {"partial", "blocked", "recreate_required", "created_unverified", "error"}


class ProvisioningError(Exception):
    """Structured, redacted provisioning failure."""

    def __init__(
        self,
        message: str,
        *,
        error_type: str = "validation",
        code: int = 1,
        http_status: int | None = None,
        method: str | None = None,
        path: str | None = None,
        write_performed: bool = False,
    ):
        super().__init__(redact(message))
        self.message = redact(message)
        self.error_type = error_type
        self.code = code
        self.http_status = http_status
        self.method = method
        self.path = path
        self.write_performed = write_performed

    def to_dict(self) -> dict[str, Any]:
        error = {"type": self.error_type, "message": self.message, "write_performed": self.write_performed}
        if self.http_status is not None:
            error["http_status"] = self.http_status
        if self.method:
            error["method"] = self.method
        if self.path:
            error["path"] = self.path
        return {"status": "error", "error": error}


@dataclass(frozen=True)
class Response:
    status: int
    data: Any
    headers: dict[str, str]


@dataclass(frozen=True)
class ExecutionMode:
    name: str
    product: str
    confirm_product: str | None
    allow_resource_writes: bool

    @property
    def is_apply(self) -> bool:
        return self.name == "apply"

    def gates(self) -> dict[str, Any]:
        return {
            "apply_requested": self.is_apply,
            "resource_write_env": self.allow_resource_writes,
            "confirm_product_matches": self.confirm_product == self.product if self.confirm_product is not None else False,
            "allowed_post_kinds": sorted(ALLOWED_POST_KINDS),
            "forbidden_methods": ["PATCH", "PUT", "DELETE"],
            "deployments_forbidden": True,
            "application_creation_forbidden": True,
        }

    def assert_write_gates(self) -> None:
        if not self.is_apply:
            raise ProvisioningError("Refusing non-read-only Coolify method POST. Dry-run mode rejects all POST requests.", error_type="safety")
        if not self.allow_resource_writes:
            raise ProvisioningError("Apply requires COOLIFY_ALLOW_RESOURCE_WRITES=true before any POST.", error_type="safety")
        if self.confirm_product != self.product:
            raise ProvisioningError("Apply requires --confirm-product to match --product exactly before any POST.", error_type="safety")


@dataclass
class ActionRecord:
    resource: str
    action: str
    status: str
    write_performed: bool = False
    verified: bool = False
    recovered_after_conflict: bool = False
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "resource": self.resource,
            "action": self.action,
            "status": self.status,
            "write_performed": self.write_performed,
            "verified": self.verified,
        }
        if self.recovered_after_conflict:
            result["recovered_after_conflict"] = True
        if self.details:
            result["details"] = redact(self.details)
        return result


def sanitize_string(text: str) -> str:
    for key in SENSITIVE_ENV:
        secret = os.environ.get(key)
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", text, flags=re.I)
    text = re.sub(r"(https?://)([^:/\s]+):([^@/\s]+)@", r"\1[REDACTED]@", text)
    text = re.sub(r"postgres(?:ql)?://[^\s'\"]+", "postgresql://[REDACTED]", text, flags=re.I)
    text = re.sub(r"DATABASE_URL\s*[=:]\s*[^\s,&}]+", "DATABASE_URL=[REDACTED]", text, flags=re.I)
    text = re.sub(r"(internal_db_url|postgres_password|password|token|secret|authorization)([=:]\s*)([^\s,&}]+)", r"\1\2[REDACTED]", text, flags=re.I)
    return text


def sanitize_for_output(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]" if SECRET_KEY_RE.search(str(key)) else sanitize_for_output(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [sanitize_for_output(item) for item in value]
    if isinstance(value, str):
        return sanitize_string(value)
    return value


def redact(value: Any) -> Any:
    """Redact secrets from nested values and strings."""

    return sanitize_for_output(value)


def safe_http_body(raw: str, *, limit: int = 1000) -> str:
    try:
        text = json.dumps(sanitize_for_output(json.loads(raw)), sort_keys=True)
    except json.JSONDecodeError:
        text = sanitize_string(raw)
    return text[:limit] + ("...[truncated]" if len(text) > limit else "")


class CoolifyClient:
    """Small reusable HTTP client with stage-aware write safety."""

    def __init__(self, base_url: str, api_token: str, *, timeout: int = TIMEOUT_SECONDS, mode: ExecutionMode | None = None):
        self.base_url = validate_base_url(base_url).rstrip("/")
        if not api_token:
            raise ProvisioningError("COOLIFY_API_TOKEN is required.")
        self.api_token = api_token
        self.timeout = timeout
        self.mode = mode or ExecutionMode("dry_run", "", None, False)

    @classmethod
    def from_env(cls, mode: ExecutionMode | None = None) -> "CoolifyClient":
        return cls(os.environ.get("COOLIFY_BASE_URL", ""), os.environ.get("COOLIFY_API_TOKEN", ""), mode=mode)

    def _check_request(self, method: str, path: str, *, post_kind: str | None = None) -> None:
        method = method.upper()
        if not path.startswith("/"):
            raise ProvisioningError("Coolify API path must start with '/'.")
        if FORBIDDEN_ENDPOINT_RE.search(path):
            raise ProvisioningError(f"Lifecycle/deployment endpoint is forbidden in this stage: {path}", error_type="safety", method=method, path=path)
        if method in {"PATCH", "PUT", "DELETE"}:
            raise ProvisioningError(f"Refusing non-read-only Coolify method {method}. Method {method} is always forbidden in this stage.", error_type="safety", method=method, path=path)
        if method == "POST":
            self.mode.assert_write_gates()
            if path.startswith("/applications"):
                raise ProvisioningError("Application creation is forbidden in this stage.", error_type="safety", method=method, path=path)
            if post_kind not in ALLOWED_POST_KINDS:
                raise ProvisioningError("POST requires a stage-3 allowlisted operation kind.", error_type="safety", method=method, path=path)
            if post_kind == "project_create" and path != POST_PROJECT_CREATE:
                raise ProvisioningError("Project creation POST endpoint is not allowlisted.", error_type="safety", method=method, path=path)
            if post_kind == "environment_create" and not re.fullmatch(r"/projects/[^/]+/environments", path):
                raise ProvisioningError("Environment creation POST endpoint is not allowlisted.", error_type="safety", method=method, path=path)
            if post_kind == "postgresql_create" and path != POST_POSTGRES_CREATE:
                raise ProvisioningError("PostgreSQL creation POST endpoint is not allowlisted.", error_type="safety", method=method, path=path)
        elif method not in READ_ONLY_METHODS:
            raise ProvisioningError(f"Refusing non-read-only Coolify method {method}.", error_type="safety", method=method, path=path)

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        allowed_statuses: set[int] | None = None,
        post_kind: str | None = None,
    ) -> Response:
        method = method.upper()
        self._check_request(method, path, post_kind=post_kind)
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_token}",
            "User-Agent": "goodmorning-factory-product-development-provisioner",
        }
        body = None
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(json_body).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/api/v1{path}",
            method=method,
            headers=headers,
            data=body,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                data = json.loads(raw) if raw else None
                if allowed_statuses is not None and response.status not in allowed_statuses:
                    raise ProvisioningError(
                        f"Unexpected Coolify status {response.status} for {method} {path}.",
                        error_type="coolify_api",
                        code=2,
                        http_status=response.status,
                        method=method,
                        path=path,
                        write_performed=method == "POST",
                    )
                return Response(response.status, data, dict(response.headers))
        except urllib.error.HTTPError as exc:
            body = safe_http_body(exc.read().decode("utf-8", errors="replace"))
            raise ProvisioningError(
                f"Coolify API {exc.code} for {method} {path}: {body}",
                error_type="coolify_api",
                code=2,
                http_status=exc.code,
                method=method,
                path=path,
                write_performed=False,
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProvisioningError(
                f"Coolify connection failure for {method} {path}: {exc}",
                error_type="network",
                code=2,
                method=method,
                path=path,
                write_performed=False,
            ) from exc
        except json.JSONDecodeError as exc:
            raise ProvisioningError(
                f"Malformed JSON response for {method} {path}: {exc}",
                error_type="coolify_api",
                code=2,
                method=method,
                path=path,
                write_performed=method == "POST",
            ) from exc

    def get(self, path: str) -> Any:
        return self.request("GET", path).data

    def create_project(self, payload: dict[str, Any]) -> Response:
        return self.request("POST", POST_PROJECT_CREATE, json_body=payload, allowed_statuses={200, 201, 202}, post_kind="project_create")

    def create_environment(self, project_uuid: str, payload: dict[str, Any]) -> Response:
        path = f"/projects/{urllib.parse.quote(str(project_uuid), safe='')}/environments"
        return self.request("POST", path, json_body=payload, allowed_statuses={200, 201, 202}, post_kind="environment_create")

    def create_postgresql(self, payload: dict[str, Any]) -> Response:
        return self.request("POST", POST_POSTGRES_CREATE, json_body=payload, allowed_statuses={200, 201, 202}, post_kind="postgresql_create")


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
    dry_run = bool(getattr(args, "dry_run", False))
    apply = bool(getattr(args, "apply", False))
    if dry_run == apply:
        if apply:
            raise ProvisioningError("Choose exactly one of --dry-run or --apply.", error_type="safety")
        raise ProvisioningError("Only --dry-run is supported unless --apply is explicitly selected.", error_type="safety")


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


def candidate_identifier(item: dict[str, Any]) -> Any:
    return field(item, "uuid", "id")


def is_expected_or_related_candidate(item: dict[str, Any], product: str, repository: str) -> bool:
    name = str(field(item, "name") or "")
    if name in expected_resource_specs(product):
        return True
    if name == f"{product}-dev":
        return True
    if is_compose_application(item) and (name.startswith(f"{product}-") or nested_field(item, "git_repository", "repository") == repository):
        return True
    return False


def hydrate_candidate(client: CoolifyClient, item: dict[str, Any], source_kind: str) -> tuple[dict[str, Any], dict[str, str] | None]:
    identifier = candidate_identifier(item)
    if not identifier:
        return item, {"endpoint": source_kind, "warning": f"Candidate {field(item, 'name') or '[unnamed]'} has no uuid/id for detail hydration."}
    singular = "applications" if source_kind == "applications" else "databases"
    path = f"/{singular}/{identifier}"
    try:
        detail = client.get(path)
    except ProvisioningError as exc:
        return item, {"endpoint": path, "warning": exc.message}
    detail_item = detail.get("data") if isinstance(detail, dict) and isinstance(detail.get("data"), dict) else detail
    if not isinstance(detail_item, dict):
        return item, {"endpoint": path, "warning": "Detail endpoint returned an unsupported shape."}
    merged = {**item, **detail_item, "_discovery_source": source_kind, "_detail_hydrated": True}
    return merged, None


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


def normalize_repository(value: Any) -> str:
    text = str(value or "").strip().removesuffix(".git")
    prefix = "https://github.com/"
    if text.startswith(prefix):
        text = text[len(prefix):]
    return text.strip("/")


def normalize_domain(value: Any) -> str:
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    return str(value or "").rstrip("/")


def normalize_port(value: Any) -> str:
    if isinstance(value, list) and len(value) == 1:
        value = value[0]
    return str(value or "")


def normalized_equal(key: str, actual: Any, expected: Any) -> bool:
    if key == "git_repository":
        return normalize_repository(actual) == normalize_repository(expected)
    if key == "domains":
        return normalize_domain(actual) == normalize_domain(expected)
    if key == "ports_exposes":
        return normalize_port(actual) == normalize_port(expected)
    return str(actual) == str(expected)


def bool_value(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.lower()
        if lowered in {"false", "0", "no"}:
            return False
        if lowered in {"true", "1", "yes"}:
            return True
    return None



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
        elif not normalized_equal(key, actual, expected_value):
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
    if expected["kind"] == "database":
        differences, unverified = postgres_differences(item)
        if differences:
            return {"name": name, "status": "incompatible", "matches": reports, "differences": differences}
        if unverified:
            return {"name": name, "status": "compatibility_unverified", "matches": reports, "unverified_fields": unverified}
    if expected["kind"] == "application":
        if is_compose_application(item):
            return {"name": name, "status": "legacy_compose_migration_required", "matches": reports}
        differences, unverified = application_differences(item, expected, context)
        if differences:
            return {"name": name, "status": "incompatible", "matches": reports, "differences": differences}
        if unverified:
            return {"name": name, "status": "compatibility_unverified", "matches": reports, "unverified_fields": unverified}
    return {"name": name, "status": "found", "matches": reports}


def legacy_compose_report(name: str, item: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    scope_status, scope_details = resource_scope(item, context)
    if scope_status == "in_scope":
        status = "legacy_compose_migration_required"
    else:
        status = scope_status
    return {"name": name, "status": status, "resource": operational_summary(item), "scope_details": scope_details}


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
            if is_expected_or_related_candidate(item, product, args.repository):
                item, warning = hydrate_candidate(client, item, source_kind)
                if warning:
                    warnings.append(warning)
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
        "legacy_compose": [legacy_compose_report(legacy_name, item, context) for item in legacy_matches],
        "defensive_compose_related": [legacy_compose_report(str(field(item, "name") or ""), item, context) for item in defensive_compose],
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


def mode_from_args(args: argparse.Namespace) -> ExecutionMode:
    return ExecutionMode(
        "apply" if getattr(args, "apply", False) else "dry_run",
        args.product,
        getattr(args, "confirm_product", None),
        os.environ.get("COOLIFY_ALLOW_RESOURCE_WRITES") == "true",
    )


POSTGRES_ENGINE_VALUES = {"postgres", "postgresql", "standalone-postgresql"}


def normalize_postgres_engine(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().lower().replace("_", "-")
    return text if text in POSTGRES_ENGINE_VALUES else None

def is_postgres_engine(value: Any) -> bool | None:
    normalized = normalize_postgres_engine(value)
    if normalized is None and value in (None, ""):
        return None
    return normalized is not None


def postgres_major_version(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return str(value)
    text = str(value).strip().lower()
    if text.isdigit():
        return text
    match = re.search(r"(?:^|[:/])postgres(?:ql)?:(\d+)(?:-alpine)?$", text)
    if match:
        return match.group(1)
    match = re.fullmatch(r"postgres(?:ql)?:(\d+)(?:-alpine)?", text)
    if match:
        return match.group(1)
    return None


def postgres_version_field(item: dict[str, Any]) -> Any:
    return nested_field(item, "postgres_version", "version", "image", "image_tag")


def postgres_differences(item: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    differences = []
    unverified = []
    engine = nested_field(item, "type", "kind", "database_type", "resource_type")
    engine_ok = is_postgres_engine(engine)
    if engine_ok is None:
        unverified.append({"field": "type", "expected": "postgresql"})
    elif not engine_ok:
        differences.append({"field": "type", "expected": "postgresql", "actual": engine})
    raw_version = postgres_version_field(item)
    major = postgres_major_version(raw_version)
    if major is None:
        if raw_version in (None, ""):
            unverified.append({"field": "version", "expected": POSTGRES_VERSION})
        else:
            differences.append({"field": "version", "expected": POSTGRES_VERSION, "actual": raw_version})
    elif major != POSTGRES_VERSION:
        differences.append({"field": "version", "expected": POSTGRES_VERSION, "actual": raw_version})
    is_public = nested_field(item, "is_public", "public")
    public_value = bool_value(is_public)
    if public_value is None:
        unverified.append({"field": "is_public", "expected": False})
    elif public_value is not False:
        differences.append({"field": "is_public", "expected": False, "actual": is_public})
    public_port = nested_field(item, "public_port", "ports_mappings")
    if public_port not in (None, "", [], False):
        differences.append({"field": "public_port", "expected": None, "actual": "configured"})
    return differences, unverified


def deterministic_sql_names(product: str) -> dict[str, str]:
    safe = re.sub(r"[^a-z0-9_]", "_", product.replace("-", "_"))
    return {"postgres_user": f"{safe}_dev_user", "postgres_db": f"{safe}_dev"}


def generate_postgres_password() -> str:
    return secrets.token_urlsafe(36)


def project_create_payload(name: str) -> dict[str, Any]:
    return {"name": name}


def environment_create_payload() -> dict[str, Any]:
    return {"name": "dev"}


def postgresql_create_payload(args: argparse.Namespace, project_uuid: str, server_uuid: str, password: str) -> dict[str, Any]:
    names = deterministic_sql_names(args.product)
    return {
        "server_uuid": server_uuid,
        "project_uuid": project_uuid,
        "environment_name": "dev",
        "name": f"{args.product}-postgres-dev",
        "description": "GoodMorning development PostgreSQL 16 resource managed by factory provisioning stage 3.",
        "image": "postgres:16-alpine",
        "postgres_user": names["postgres_user"],
        "postgres_password": password,
        "postgres_db": names["postgres_db"],
        "is_public": False,
        "instant_deploy": False,
    }


def exact_matches(items: list[dict[str, Any]], expected: str) -> list[dict[str, Any]]:
    return [item for item in items if str(item.get("name") or "") == expected]


def verified_project_from_list(client: CoolifyClient, name: str) -> dict[str, Any] | None:
    matches = exact_matches(item_list(client.get("/projects")), name)
    if len(matches) != 1:
        return None
    identifier = field(matches[0], "uuid", "id")
    if not identifier:
        return None
    return {"status": "exists", "name": name, "uuid": identifier}


def project_from_create_response(data: Any, expected_name: str) -> dict[str, Any] | None:
    item = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
    if not isinstance(item, dict):
        return None
    if field(item, "name") != expected_name:
        return None
    identifier = field(item, "uuid", "id")
    if not identifier:
        return None
    return {"status": "created", "name": expected_name, "uuid": identifier}


def project_environments(client: CoolifyClient, project_uuid: str) -> list[dict[str, Any]]:
    detailed_project = client.get(f"/projects/{project_uuid}")
    project_details = detailed_project.get("data") if isinstance(detailed_project, dict) and isinstance(detailed_project.get("data"), dict) else detailed_project
    environments = item_list(project_details.get("environments") if isinstance(project_details, dict) else [])
    if not environments:
        environments = item_list(client.get(f"/projects/{project_uuid}/environments"))
    return environments


def verified_environment_from_list(client: CoolifyClient, project_uuid: str) -> dict[str, Any] | None:
    matches = exact_matches(project_environments(client, project_uuid), "dev")
    if len(matches) != 1:
        return None
    identifier = field(matches[0], "uuid", "id")
    if not identifier:
        return None
    return {"status": "exists", "name": "dev", "uuid": identifier, "project_uuid": project_uuid}


def environment_from_create_response(data: Any, project_uuid: str) -> dict[str, Any] | None:
    item = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
    if not isinstance(item, dict):
        return None
    if field(item, "name") != "dev":
        return None
    identifier = field(item, "uuid", "id")
    if not identifier:
        return None
    return {"status": "created", "name": "dev", "uuid": identifier, "project_uuid": project_uuid}


def ensure_project(client: CoolifyClient, args: argparse.Namespace, mode: ExecutionMode, actions: list[dict[str, Any]]) -> dict[str, Any]:
    matches = exact_matches(item_list(client.get("/projects")), args.coolify_project)
    if len(matches) > 1:
        raise ProvisioningError(f"Found multiple Coolify project records named exactly '{args.coolify_project}'.", error_type="discovery")
    if len(matches) == 1:
        identifier = field(matches[0], "uuid", "id")
        if not identifier:
            raise ProvisioningError("Selected Coolify project is missing an identifier.", error_type="discovery")
        actions.append(ActionRecord("project", "reuse", "exists", verified=True).to_dict())
        return {"status": "exists", "name": args.coolify_project, "uuid": identifier}
    if not mode.is_apply:
        actions.append(ActionRecord("project", "plan_create", "would_create").to_dict())
        return {"status": "would_create", "name": args.coolify_project, "uuid": "${project.uuid}"}
    actions.append(ActionRecord("project", "attempt_create", "attempted").to_dict())
    try:
        response = client.create_project(project_create_payload(args.coolify_project))
    except ProvisioningError as exc:
        if exc.http_status in {409, 422}:
            verified = verified_project_from_list(client, args.coolify_project)
            if verified:
                verified["status"] = "exists"
                verified["recovered_after_conflict"] = True
                actions.append(ActionRecord("project", "recover_after_conflict", "exists", verified=True, recovered_after_conflict=True).to_dict())
                return verified
        raise
    verified = project_from_create_response(response.data, args.coolify_project)
    if not verified or response.status == 202:
        discovered = verified_project_from_list(client, args.coolify_project)
        if discovered:
            verified = {**discovered, "status": "created"}
    if not verified:
        actions.append(ActionRecord("project", "create", "created_unverified", write_performed=True).to_dict())
        raise ProvisioningError("Project create write completed but exact project verification failed.", error_type="verification", code=2, method="POST", path=POST_PROJECT_CREATE, write_performed=True)
    actions.append(ActionRecord("project", "create", "created", write_performed=True, verified=True).to_dict())
    return verified


def ensure_environment(client: CoolifyClient, project: dict[str, Any], mode: ExecutionMode, actions: list[dict[str, Any]]) -> dict[str, Any]:
    project_uuid = project["uuid"]
    if str(project_uuid).startswith("${"):
        actions.append(ActionRecord("environment", "plan_create", "would_create").to_dict())
        return {"status": "would_create", "name": "dev", "uuid": "${environment.uuid}", "project_uuid": project_uuid}
    verified = verified_environment_from_list(client, str(project_uuid))
    if verified:
        actions.append(ActionRecord("environment", "reuse", "exists", verified=True).to_dict())
        return verified
    if not mode.is_apply:
        actions.append(ActionRecord("environment", "plan_create", "would_create").to_dict())
        return {"status": "would_create", "name": "dev", "uuid": "${environment.uuid}", "project_uuid": project_uuid}
    actions.append(ActionRecord("environment", "attempt_create", "attempted").to_dict())
    try:
        response = client.create_environment(str(project_uuid), environment_create_payload())
    except ProvisioningError as exc:
        if exc.http_status in {409, 422}:
            verified = verified_environment_from_list(client, str(project_uuid))
            if verified:
                verified["status"] = "exists"
                verified["recovered_after_conflict"] = True
                actions.append(ActionRecord("environment", "recover_after_conflict", "exists", verified=True, recovered_after_conflict=True).to_dict())
                return verified
        raise
    verified = environment_from_create_response(response.data, str(project_uuid))
    if not verified or response.status == 202:
        discovered = verified_environment_from_list(client, str(project_uuid))
        if discovered:
            verified = {**discovered, "status": "created"}
    if not verified:
        actions.append(ActionRecord("environment", "create", "created_unverified", write_performed=True).to_dict())
        raise ProvisioningError("Environment create write completed but exact dev verification failed.", error_type="verification", code=2, method="POST", path=f"/projects/{project_uuid}/environments", write_performed=True)
    actions.append(ActionRecord("environment", "create", "created", write_performed=True, verified=True).to_dict())
    return verified


def postgres_classification_from_detail(item: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    name = context["name"]
    scope_status, scope_details = resource_scope(item, context)
    if scope_status != "in_scope":
        return {"status": scope_status, "name": name, "uuid": field(item, "uuid", "id"), "scope_details": scope_details}
    differences, unverified = postgres_differences(item)
    base = {
        "name": name,
        "uuid": field(item, "uuid", "id"),
        "internal_db_url_available": bool(nested_field(item, "internal_db_url")),
        "credentials_configured": bool(nested_field(item, "postgres_user", "postgres_db")),
        "scope_details": scope_details,
    }
    if differences:
        return {**base, "status": "incompatible", "differences": differences}
    if unverified:
        return {**base, "status": "compatibility_unverified", "unverified_fields": unverified}
    return {**base, "status": "exists"}


def hydrate_database_by_uuid(client: CoolifyClient, uuid: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    detail = client.get(f"/databases/{uuid}")
    detail_item = detail.get("data") if isinstance(detail, dict) and isinstance(detail.get("data"), dict) else detail
    if not isinstance(detail_item, dict):
        raise ProvisioningError("PostgreSQL detail endpoint returned an unsupported shape.", error_type="coolify_api", code=2)
    return {**fallback, **detail_item, "_discovery_source": "databases", "_detail_hydrated": True}


def exact_postgres_matches(client: CoolifyClient, name: str) -> list[dict[str, Any]]:
    return [item for item in item_list(client.get("/databases")) if str(field(item, "name") or "") == name]


def ensure_postgresql(client: CoolifyClient, args: argparse.Namespace, project: dict[str, Any], environment: dict[str, Any], server: dict[str, Any], mode: ExecutionMode, actions: list[dict[str, Any]]) -> dict[str, Any]:
    name = f"{args.product}-postgres-dev"
    context = {"name": name, "project_uuid": project["uuid"], "environment_uuid": environment["uuid"], "server_uuid": server["uuid"]}
    matches = exact_postgres_matches(client, name)
    if len(matches) > 1:
        raise ProvisioningError(f"Found multiple PostgreSQL resources named exactly '{name}'.", error_type="discovery")
    if len(matches) == 1:
        uuid = field(matches[0], "uuid", "id")
        if not uuid:
            return {"status": "compatibility_unverified", "name": name, "unverified_fields": [{"field": "uuid", "expected": "present"}]}
        try:
            detail = hydrate_database_by_uuid(client, uuid, matches[0])
        except ProvisioningError:
            if mode.is_apply:
                raise
            detail = matches[0]
        result = postgres_classification_from_detail(detail, context)
        if result["status"] == "exists":
            actions.append(ActionRecord("postgresql", "reuse", "exists", verified=True).to_dict())
        return result
    if not mode.is_apply:
        actions.append(ActionRecord("postgresql", "plan_create", "would_create").to_dict())
        return {"status": "would_create", "name": name, "uuid": "${postgres.uuid}", "secret_requirements": ["Generate postgres_password in-memory only during apply creation."], "internal_db_url_available": False, "credentials_configured": False}
    password = generate_postgres_password()
    actions.append(ActionRecord("postgresql", "attempt_create", "attempted").to_dict())
    try:
        response = client.create_postgresql(postgresql_create_payload(args, str(project["uuid"]), str(server["uuid"]), password))
    except ProvisioningError as exc:
        if exc.http_status in {409, 422}:
            matches = exact_postgres_matches(client, name)
            if len(matches) == 1 and field(matches[0], "uuid", "id"):
                detail = hydrate_database_by_uuid(client, field(matches[0], "uuid", "id"), matches[0])
                result = postgres_classification_from_detail(detail, context)
                if result["status"] == "exists":
                    result["recovered_after_conflict"] = True
                    actions.append(ActionRecord("postgresql", "recover_after_conflict", "exists", verified=True, recovered_after_conflict=True).to_dict())
                    return result
        raise
    item = response.data.get("data") if isinstance(response.data, dict) and isinstance(response.data.get("data"), dict) else response.data
    uuid = field(item, "uuid", "id") if isinstance(item, dict) else None
    if not uuid or response.status == 202:
        matches = exact_postgres_matches(client, name)
        if len(matches) == 1:
            uuid = field(matches[0], "uuid", "id")
            item = matches[0]
    if not uuid:
        actions.append(ActionRecord("postgresql", "create", "created_unverified", write_performed=True).to_dict())
        return {"status": "created_unverified", "name": name, "write_performed": True, "internal_db_url_available": False, "credentials_configured": True}
    try:
        detail = hydrate_database_by_uuid(client, uuid, item if isinstance(item, dict) else {"uuid": uuid, "name": name})
    except ProvisioningError as exc:
        actions.append(ActionRecord("postgresql", "create", "created_unverified", write_performed=True).to_dict())
        return {"status": "created_unverified", "name": name, "uuid": uuid, "write_performed": True, "verification_error": exc.message, "internal_db_url_available": False, "credentials_configured": True}
    result = postgres_classification_from_detail(detail, context)
    if result["status"] == "exists":
        result["status"] = "created"
        result["credentials_configured"] = True
        actions.append(ActionRecord("postgresql", "create", "created", write_performed=True, verified=True).to_dict())
        return result
    actions.append(ActionRecord("postgresql", "create", "created_unverified", write_performed=True).to_dict())
    return {**result, "status": "created_unverified" if result["status"] in {"compatibility_unverified", "scope_unverified"} else result["status"], "write_performed": True, "credentials_configured": True}


def global_status(mode: ExecutionMode, resources: list[dict[str, Any]], blockers: list[Any], actions: list[dict[str, Any]]) -> str:
    statuses = {resource.get("status") for resource in resources}
    write_performed = any(action.get("write_performed") for action in actions) or any(resource.get("write_performed") for resource in resources)
    if "incompatible" in statuses:
        return "recreate_required"
    if "created_unverified" in statuses or (write_performed and blockers):
        return "partial"
    if blockers:
        return "blocked"
    if not mode.is_apply:
        return "dry_run"
    if statuses <= {"exists"}:
        return "exists"
    if "created" in statuses and statuses <= {"created", "exists"}:
        return "created"
    return "ready"


def build_stage3_result(args: argparse.Namespace, client: CoolifyClient, mode: ExecutionMode) -> dict[str, Any]:
    actions: list[dict[str, Any]] = []
    warnings: list[Any] = []
    blockers: list[Any] = []
    server = select_unique_by_name(item_list(client.get("/servers")), args.server, "server")
    github_app = select_unique_by_name(item_list(client.get("/github-apps")), args.github_app, "GitHub App")
    server_summary = summary(server, "name", "uuid", "id")
    github_app_summary = summary(github_app, "name", "uuid", "id")
    server_uuid = server_summary.get("uuid") or server_summary.get("id")
    if not server_uuid:
        raise ProvisioningError("Selected Coolify server is missing an identifier.", error_type="discovery")
    try:
        project = ensure_project(client, args, mode, actions)
        environment = ensure_environment(client, project, mode, actions)
        postgres = ensure_postgresql(client, args, project, environment, {"uuid": server_uuid, "name": args.server}, mode, actions)
    except ProvisioningError as exc:
        if exc.write_performed or any(action.get("write_performed") for action in actions):
            blockers.append({"message": exc.message, "type": exc.error_type, "write_performed": True})
            project = locals().get("project", {"status": "blocked"})
            environment = locals().get("environment", {"status": "blocked"})
            postgres = locals().get("postgres", {"status": "blocked"})
        else:
            raise
    if mode.is_apply and postgres.get("status") in {"compatibility_unverified", "scope_unverified", "out_of_scope"}:
        blockers.append({"message": f"PostgreSQL status requires manual review: {postgres['status']}", "write_performed": False})
    if postgres.get("status") == "incompatible":
        blockers.append({"message": "Existing PostgreSQL contradicts desired state; manual recreation required.", "write_performed": False})
    context = {
        "project_uuid": project.get("uuid"),
        "environment_uuid": environment.get("uuid"),
        "environment_name": "dev",
        "server_uuid": server_uuid,
        "github_app_uuid": github_app_summary.get("uuid") or github_app_summary.get("id"),
    }
    discovery = {
        "project": {"name": args.coolify_project, "uuid": project.get("uuid")},
        "environment": {"name": "dev", "uuid": environment.get("uuid")},
        "server": server_summary,
        "github_app": github_app_summary,
        "bindings": context,
        "existing_resources": discover_existing_resources(client, args.product, context, args),
    }
    plan = build_plan(args, discovery)
    for resource in plan["resources"]:
        if resource["name"] == f"{args.product}-postgres-dev":
            resource.update({"status": postgres.get("status"), "provisioning": postgres})
        else:
            resource["status"] = "planned_later"
    status = global_status(mode, [project, environment, postgres], blockers, actions)
    return redact({
        **plan,
        "status": status,
        "mode": mode.name,
        "safety_gates": mode.gates(),
        "selected_server": server_summary,
        "project": project,
        "environment_resource": environment,
        "postgresql": postgres,
        "actions": actions,
        "warnings": warnings,
        "blockers": blockers,
        "contract": {
            "confirmed_endpoints": ["POST /api/v1/projects", "POST /api/v1/projects/{project_uuid}/environments", "POST /api/v1/databases/postgresql", "GET /api/v1/databases/{uuid}"],
            "postgresql_payload_preview": redact(postgresql_create_payload(args, "${project.uuid}", "${server.uuid}", "${generated_secret}")),
            "postgresql_payload_source": "Coolify OpenAPI/API reference for create PostgreSQL database; version is represented by the image field in this isolated builder pending any upstream contract rename.",
        },
    })


def provision(args: argparse.Namespace, *, client_factory: Callable[[], CoolifyClient] | None = None) -> dict[str, Any]:
    validate_args(args)
    mode = mode_from_args(args)
    if mode.is_apply:
        mode.assert_write_gates()
    client = client_factory() if client_factory else CoolifyClient.from_env(mode)
    if hasattr(client, "mode"):
        client.mode = mode
    return build_stage3_result(args, client, mode)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build or apply a safe Coolify development resource plan.")
    parser.add_argument("--product", required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--branch", required=True, choices=[ALLOWED_BRANCH])
    parser.add_argument("--coolify-project", required=True)
    parser.add_argument("--environment", required=True, choices=[ALLOWED_ENVIRONMENT])
    parser.add_argument("--server", required=True)
    parser.add_argument("--github-app", required=True, help="Exact Coolify GitHub App name")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true")
    mode.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-product")
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
    return 2 if result.get("status") in NON_SUCCESS_EXIT_STATUSES else 0


if __name__ == "__main__":
    raise SystemExit(main())
