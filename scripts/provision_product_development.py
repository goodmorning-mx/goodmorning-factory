#!/usr/bin/env python3
"""Plan or safely apply GoodMorning Coolify development primitives.

Dry-run is GET-only and produces the complete three-resource plan. Apply can
create only the Coolify Project, exact ``dev`` Environment, and private native
PostgreSQL record after explicit gates. Backend Dockerfile Applications and their runtime environment variables can be created/reconciled only after Stage 5 gates. Deployments and lifecycle actions remain forbidden.
"""

from __future__ import annotations

import argparse
import json
import math
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
WRITE_METHODS = {"POST", "PATCH"}
POST_PROJECT_CREATE = "/projects"
POST_POSTGRES_CREATE = "/databases/postgresql"
POST_BACKEND_APPLICATION_CREATE = "/applications/private-github-app"
BACKEND_ENV_KEYS = {"DATABASE_URL", "APP_ENV", "CORS_ORIGINS"}
APPROVED_BACKEND_APPLICATION_FIELDS = {"name", "project_uuid", "environment_uuid", "environment_name", "server_uuid", "destination_uuid", "github_app_uuid", "git_repository", "git_branch", "build_pack", "base_directory", "dockerfile_location", "ports_exposes", "domains", "instant_deploy", "is_auto_deploy_enabled", "autogenerate_domain", "health_check_enabled", "health_check_path", "health_check_port", "health_check_method", "health_check_scheme", "health_check_return_code"}
APPROVED_BACKEND_ENV_FIELDS = {"key", "value", "is_runtime", "is_buildtime", "is_preview", "is_literal", "is_multiline", "is_shown_once"}
ENV_PAYLOAD_FIELDS = APPROVED_BACKEND_ENV_FIELDS
ALLOWED_POST_KINDS = {"project_create", "environment_create", "postgresql_create", "postgresql_start", "backend_application_create", "backend_env_create"}
ALLOWED_PATCH_KINDS = {"backend_env_update"}
FORBIDDEN_ENDPOINT_RE = re.compile(r"/(deploy|deployments|restart|stop)(?:/|$)", re.I)
NON_SUCCESS_EXIT_STATUSES = {"partial", "blocked", "recreate_required", "created_unverified", "error", "unhealthy", "readiness_unverified", "timeout"}


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
        write_outcome_unknown: bool = False,
        request_attempted: bool = False,
    ):
        super().__init__(redact(message))
        self.message = redact(message)
        self.error_type = error_type
        self.code = code
        self.http_status = http_status
        self.method = method
        self.path = path
        self.write_performed = write_performed
        self.write_outcome_unknown = write_outcome_unknown
        self.request_attempted = request_attempted

    def to_dict(self) -> dict[str, Any]:
        error = {"type": self.error_type, "message": self.message, "request_attempted": self.request_attempted, "write_performed": self.write_performed, "write_outcome_unknown": self.write_outcome_unknown}
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
    allow_database_start: bool = False
    confirm_database_start: str | None = None
    allow_backend_application_writes: bool = False
    confirm_backend_application: str | None = None
    allow_backend_env_writes: bool = False
    confirm_backend_environment: str | None = None

    @property
    def is_apply(self) -> bool:
        return self.name == "apply"

    @property
    def database_start_enabled(self) -> bool:
        return (
            self.is_apply
            and self.allow_resource_writes
            and self.confirm_product == self.product
            and self.allow_database_start
            and self.confirm_database_start == f"{self.product}-postgres-dev"
        )

    @property
    def backend_application_name(self) -> str:
        return f"{self.product}-api-dev"

    @property
    def backend_application_writes_enabled(self) -> bool:
        return (self.is_apply and self.allow_resource_writes and self.confirm_product == self.product and self.allow_backend_application_writes and self.confirm_backend_application == self.backend_application_name)

    @property
    def backend_env_writes_enabled(self) -> bool:
        return self.backend_application_writes_enabled and self.allow_backend_env_writes and self.confirm_backend_environment == self.backend_application_name

    def gates(self) -> dict[str, Any]:
        return {
            "apply_requested": self.is_apply,
            "resource_write_env": self.allow_resource_writes,
            "confirm_product_matches": self.confirm_product == self.product if self.confirm_product is not None else False,
            "allowed_post_kinds": sorted(ALLOWED_POST_KINDS),
            "database_start_env": self.allow_database_start,
            "confirm_database_start_matches": self.confirm_database_start == f"{self.product}-postgres-dev" if self.confirm_database_start is not None else False,
            "database_start_enabled": self.database_start_enabled,
            "always_forbidden_methods": ["PUT", "DELETE"],
            "conditionally_allowed_methods": ["PATCH"],
            "allowed_patch_operations": ["backend_env_update"] if self.backend_env_writes_enabled else [],
            "patch_scope": "backend_application_envs_bound_uuid" if self.backend_env_writes_enabled else None,
            "deployments_forbidden": True,
            "backend_application_env": self.allow_backend_application_writes,
            "confirm_backend_application_matches": self.confirm_backend_application == self.backend_application_name if self.confirm_backend_application is not None else False,
            "backend_application_writes_enabled": self.backend_application_writes_enabled,
            "backend_env_env": self.allow_backend_env_writes,
            "confirm_backend_environment_matches": self.confirm_backend_environment == self.backend_application_name if self.confirm_backend_environment is not None else False,
            "backend_env_writes_enabled": self.backend_env_writes_enabled,
            "backend_application_creation_enabled": self.backend_application_writes_enabled,
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
    request_attempted: bool = False
    write_performed: bool = False
    write_outcome_unknown: bool = False
    verified: bool = False
    recovered_after_conflict: bool = False
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        result = {
            "resource": self.resource,
            "action": self.action,
            "status": self.status,
            "request_attempted": self.request_attempted,
            "write_performed": self.write_performed,
            "write_outcome_unknown": self.write_outcome_unknown,
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
    text = re.sub(r"postgresql\+psycopg://[^\s'\"]+", "postgresql+psycopg://[REDACTED]", text, flags=re.I)
    text = re.sub(r"postgres(?:ql)?://[^\s'\"]+", "postgresql://[REDACTED]", text, flags=re.I)
    text = re.sub(r"DATABASE_URL\s*[=:]\s*[^\s,&}]+", "DATABASE_URL=[REDACTED]", text, flags=re.I)
    text = re.sub(r"(internal_db_url|postgres_password|password|token|secret|authorization)([=:]\s*)([^\s,&}]+)", r"\1\2[REDACTED]", text, flags=re.I)
    return text


def sanitize_for_output(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: sanitize_for_output(item) if str(key) in {"internal_db_url_available", "database_url_driver_ready"} else ("[REDACTED]" if SECRET_KEY_RE.search(str(key)) else sanitize_for_output(item))
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
        self.expected_backend_application_uuid: str | None = None
        self.backend_application_bindings: dict[str, Any] = {}


    def bind_backend_application_uuid(self, uuid: Any, bindings: dict[str, Any] | None = None) -> None:
        text = str(uuid or "")
        if not text:
            raise ProvisioningError("Cannot bind an empty Backend Application UUID.", error_type="safety")
        if self.expected_backend_application_uuid is not None and self.expected_backend_application_uuid != text:
            raise ProvisioningError("Refusing to change the verified Backend Application UUID binding.", error_type="safety")
        self.expected_backend_application_uuid = text
        self.backend_application_bindings = dict(bindings or {})

    def _backend_env_path_matches_binding(self, path: str) -> bool:
        if self.expected_backend_application_uuid is None:
            return False
        encoded = urllib.parse.quote(self.expected_backend_application_uuid, safe="")
        return path == f"/applications/{encoded}/envs"

    def _validate_backend_application_payload(self, json_body: dict[str, Any] | None) -> None:
        if not isinstance(json_body, dict):
            raise ProvisioningError("Backend Application creation requires an approved JSON object payload.", error_type="safety")
        if set(json_body) != APPROVED_BACKEND_APPLICATION_FIELDS:
            raise ProvisioningError("Backend Application payload fields do not exactly match the approved contract.", error_type="safety")
        expected_name = self.mode.backend_application_name
        expected_domain = f"https://api.dev.{self.mode.product}.goodmorning.mx"
        checks = {
            "name": expected_name,
            "git_branch": "dev",
            "build_pack": "dockerfile",
            "base_directory": "/backend",
            "dockerfile_location": "/Dockerfile",
            "ports_exposes": "8000",
            "domains": expected_domain,
            "instant_deploy": False,
            "is_auto_deploy_enabled": False,
            "autogenerate_domain": False,
            "health_check_enabled": True,
            "health_check_path": "/health",
            "health_check_port": "8000",
            "health_check_method": "GET",
            "health_check_scheme": "http",
            "health_check_return_code": 200,
        }
        for key, expected in checks.items():
            if json_body.get(key) != expected:
                raise ProvisioningError(f"Backend Application payload field {key} does not match the approved Stage 5 contract.", error_type="safety")
        if ":8000" in str(json_body.get("domains") or ""):
            raise ProvisioningError("Backend Application public domain must not include the internal port.", error_type="safety")
        for key in ("project_uuid", "environment_uuid", "server_uuid", "destination_uuid", "github_app_uuid"):
            expected = self.backend_application_bindings.get(key)
            if expected not in (None, "") and str(json_body.get(key)) != str(expected):
                raise ProvisioningError(f"Backend Application payload binding {key} does not match the selected binding.", error_type="safety")
        if json_body.get("environment_name") != "dev":
            raise ProvisioningError("Backend Application payload must target environment_name=dev.", error_type="safety")

    def _validate_backend_env_payload(self, json_body: dict[str, Any] | None) -> None:
        if not isinstance(json_body, dict):
            raise ProvisioningError("Backend env write requires an approved JSON object payload.", error_type="safety")
        if set(json_body) - APPROVED_BACKEND_ENV_FIELDS:
            raise ProvisioningError("Backend env payload contains fields outside the approved contract.", error_type="safety")
        required = {"key", "value", "is_runtime", "is_buildtime", "is_preview", "is_literal", "is_multiline"}
        if not required <= set(json_body):
            raise ProvisioningError("Backend env payload is missing required approved fields.", error_type="safety")
        if json_body.get("key") not in BACKEND_ENV_KEYS:
            raise ProvisioningError("Backend env payload key is not allowlisted.", error_type="safety")
        expected_flags = {"is_runtime": True, "is_buildtime": False, "is_preview": False, "is_literal": True, "is_multiline": False}
        for key, expected in expected_flags.items():
            if json_body.get(key) is not expected:
                raise ProvisioningError(f"Backend env payload flag {key} does not match the approved contract.", error_type="safety")

    @classmethod
    def from_env(cls, mode: ExecutionMode | None = None) -> "CoolifyClient":
        return cls(os.environ.get("COOLIFY_BASE_URL", ""), os.environ.get("COOLIFY_API_TOKEN", ""), mode=mode)

    def _check_request(self, method: str, path: str, *, post_kind: str | None = None, json_body: dict[str, Any] | None = None) -> None:
        method = method.upper()
        if not path.startswith("/"):
            raise ProvisioningError("Coolify API path must start with '/'.")
        if FORBIDDEN_ENDPOINT_RE.search(path) or ("/start" in path and not (method == "POST" and post_kind == "postgresql_start" and re.fullmatch(r"/databases/[^/]+/start", path))):
            raise ProvisioningError(f"Lifecycle/deployment endpoint is forbidden in this stage: {path}", error_type="safety", method=method, path=path)
        if method in {"PUT", "DELETE"}:
            raise ProvisioningError(f"Refusing non-read-only Coolify method {method}. Method {method} is always forbidden in this stage.", error_type="safety", method=method, path=path)
        if method == "PATCH":
            if post_kind != "backend_env_update" or not getattr(self.mode, "backend_env_writes_enabled", False):
                raise ProvisioningError("Refusing non-read-only Coolify method PATCH. PATCH is allowed only for verified Backend Application env variables after env gates.", error_type="safety", method=method, path=path)
            if not self._backend_env_path_matches_binding(path):
                raise ProvisioningError("PATCH endpoint is limited to env vars for the verified Backend Application UUID binding.", error_type="safety", method=method, path=path)
            self._validate_backend_env_payload(json_body)
        if method == "POST":
            self.mode.assert_write_gates()
            if path.startswith("/applications") and post_kind not in {"backend_application_create", "backend_env_create"}:
                raise ProvisioningError("Only Backend Application creation/env writes are allowlisted for applications in Stage 5.", error_type="safety", method=method, path=path)
            if post_kind == "backend_application_create" and not getattr(self.mode, "backend_application_writes_enabled", False):
                raise ProvisioningError("Backend Application creation requires COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES=true and exact --confirm-backend-application.", error_type="safety", method=method, path=path)
            if post_kind == "backend_env_create" and not getattr(self.mode, "backend_env_writes_enabled", False):
                raise ProvisioningError("Backend env writes require COOLIFY_ALLOW_BACKEND_ENV_WRITES=true and exact --confirm-backend-environment.", error_type="safety", method=method, path=path)
            if post_kind not in ALLOWED_POST_KINDS:
                raise ProvisioningError("POST requires a stage allowlisted operation kind.", error_type="safety", method=method, path=path)
            if post_kind == "project_create" and path != POST_PROJECT_CREATE:
                raise ProvisioningError("Project creation POST endpoint is not allowlisted.", error_type="safety", method=method, path=path)
            if post_kind == "environment_create" and not re.fullmatch(r"/projects/[^/]+/environments", path):
                raise ProvisioningError("Environment creation POST endpoint is not allowlisted.", error_type="safety", method=method, path=path)
            if post_kind == "postgresql_create" and path != POST_POSTGRES_CREATE:
                raise ProvisioningError("PostgreSQL creation POST endpoint is not allowlisted.", error_type="safety", method=method, path=path)
            if post_kind == "backend_application_create":
                if path != POST_BACKEND_APPLICATION_CREATE:
                    raise ProvisioningError("Backend Application creation POST endpoint is not allowlisted.", error_type="safety", method=method, path=path)
                self._validate_backend_application_payload(json_body)
            if post_kind == "backend_env_create":
                if not self._backend_env_path_matches_binding(path):
                    raise ProvisioningError("Backend env create endpoint is limited to the verified Backend Application UUID binding.", error_type="safety", method=method, path=path)
                self._validate_backend_env_payload(json_body)
            if post_kind == "postgresql_start":
                if not self.mode.database_start_enabled:
                    raise ProvisioningError("PostgreSQL start requires COOLIFY_ALLOW_DATABASE_START=true and exact --confirm-database-start before POST.", error_type="safety", method=method, path=path)
                if not re.fullmatch(r"/databases/[^/]+/start", path):
                    raise ProvisioningError("PostgreSQL start POST endpoint must include the exact selected database UUID.", error_type="safety", method=method, path=path)
        elif method not in READ_ONLY_METHODS and method != "PATCH":
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
        self._check_request(method, path, post_kind=post_kind, json_body=json_body)
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
                        write_performed=False,
                        write_outcome_unknown=method in WRITE_METHODS,
                        request_attempted=True,
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
                write_outcome_unknown=(method in WRITE_METHODS and exc.code >= 500),
                request_attempted=True,
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProvisioningError(
                f"Coolify connection failure for {method} {path}: {exc}",
                error_type="network",
                code=2,
                method=method,
                path=path,
                write_performed=False,
                write_outcome_unknown=(method in WRITE_METHODS),
                request_attempted=True,
            ) from exc
        except json.JSONDecodeError as exc:
            raise ProvisioningError(
                f"Malformed JSON response for {method} {path}: {exc}",
                error_type="coolify_api",
                code=2,
                method=method,
                path=path,
                write_performed=False,
                write_outcome_unknown=(method in WRITE_METHODS),
                request_attempted=True,
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

    def start_postgresql(self, uuid: str) -> Response:
        path = f"/databases/{urllib.parse.quote(str(uuid), safe='')}/start"
        return self.request("POST", path, allowed_statuses={200, 201, 202}, post_kind="postgresql_start")

    def create_backend_application(self, payload: dict[str, Any]) -> Response:
        return self.request("POST", POST_BACKEND_APPLICATION_CREATE, json_body=payload, allowed_statuses={200, 201, 202}, post_kind="backend_application_create")

    def create_backend_env(self, uuid: str, payload: dict[str, Any]) -> Response:
        return self.request("POST", f"/applications/{urllib.parse.quote(str(uuid), safe='')}/envs", json_body=payload, allowed_statuses={200, 201, 202}, post_kind="backend_env_create")

    def update_backend_env(self, uuid: str, payload: dict[str, Any]) -> Response:
        return self.request("PATCH", f"/applications/{urllib.parse.quote(str(uuid), safe='')}/envs", json_body=payload, allowed_statuses={200, 201, 202}, post_kind="backend_env_update")


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
    destination = select_destination(server, args, blockers)
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
                    "write_contract": "Stage 3 apply may create this native private PostgreSQL record with instant_deploy=false after all write gates pass.",
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


def validate_readiness_timing_args(args: argparse.Namespace) -> None:
    for name in ("postgres_readiness_interval", "postgres_readiness_timeout"):
        value = getattr(args, name, None)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
            raise ProvisioningError(f"--{name.replace('_', '-')} must be a finite number greater than zero.", error_type="validation")


def mode_from_args(args: argparse.Namespace) -> ExecutionMode:
    return ExecutionMode(
        "apply" if getattr(args, "apply", False) else "dry_run",
        args.product,
        getattr(args, "confirm_product", None),
        os.environ.get("COOLIFY_ALLOW_RESOURCE_WRITES") == "true",
        os.environ.get("COOLIFY_ALLOW_DATABASE_START") == "true",
        getattr(args, "confirm_database_start", None),
        os.environ.get("COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES") == "true",
        getattr(args, "confirm_backend_application", None),
        os.environ.get("COOLIFY_ALLOW_BACKEND_ENV_WRITES") == "true",
        getattr(args, "confirm_backend_environment", None),
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
    actions.append(ActionRecord("project", "attempt_create", "attempted", request_attempted=False).to_dict())
    try:
        response = client.create_project(project_create_payload(args.coolify_project))
    except ProvisioningError as exc:
        if exc.write_outcome_unknown:
            actions.append(ActionRecord("project", "create", "outcome_unknown", request_attempted=True, write_outcome_unknown=True).to_dict())
            raise
        if exc.http_status in {409, 422}:
            verified = verified_project_from_list(client, args.coolify_project)
            if verified:
                verified["status"] = "exists"
                verified["recovered_after_conflict"] = True
                actions.append(ActionRecord("project", "recover_after_conflict", "exists", request_attempted=True, verified=True, recovered_after_conflict=True).to_dict())
                return verified
        raise
    verified = project_from_create_response(response.data, args.coolify_project)
    if not verified or response.status == 202:
        discovered = verified_project_from_list(client, args.coolify_project)
        if discovered:
            verified = {**discovered, "status": "created"}
    if not verified:
        actions.append(ActionRecord("project", "create", "created_unverified", request_attempted=True, write_performed=True).to_dict())
        raise ProvisioningError("Project create write completed but exact project verification failed.", error_type="verification", code=2, method="POST", path=POST_PROJECT_CREATE, write_performed=True)
    actions.append(ActionRecord("project", "create", "created", request_attempted=True, write_performed=True, verified=True).to_dict())
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
    actions.append(ActionRecord("environment", "attempt_create", "attempted", request_attempted=False).to_dict())
    try:
        response = client.create_environment(str(project_uuid), environment_create_payload())
    except ProvisioningError as exc:
        if exc.write_outcome_unknown:
            actions.append(ActionRecord("environment", "create", "outcome_unknown", request_attempted=True, write_outcome_unknown=True).to_dict())
            raise
        if exc.http_status in {409, 422}:
            verified = verified_environment_from_list(client, str(project_uuid))
            if verified:
                verified["status"] = "exists"
                verified["recovered_after_conflict"] = True
                actions.append(ActionRecord("environment", "recover_after_conflict", "exists", request_attempted=True, verified=True, recovered_after_conflict=True).to_dict())
                return verified
        raise
    verified = environment_from_create_response(response.data, str(project_uuid))
    if not verified or response.status == 202:
        discovered = verified_environment_from_list(client, str(project_uuid))
        if discovered:
            verified = {**discovered, "status": "created"}
    if not verified:
        actions.append(ActionRecord("environment", "create", "created_unverified", request_attempted=True, write_performed=True).to_dict())
        raise ProvisioningError("Environment create write completed but exact dev verification failed.", error_type="verification", code=2, method="POST", path=f"/projects/{project_uuid}/environments", write_performed=True)
    actions.append(ActionRecord("environment", "create", "created", request_attempted=True, write_performed=True, verified=True).to_dict())
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
    actions.append(ActionRecord("postgresql", "attempt_create", "attempted", request_attempted=False).to_dict())
    try:
        response = client.create_postgresql(postgresql_create_payload(args, str(project["uuid"]), str(server["uuid"]), password))
    except ProvisioningError as exc:
        if exc.write_outcome_unknown:
            actions.append(ActionRecord("postgresql", "create", "outcome_unknown", request_attempted=True, write_outcome_unknown=True).to_dict())
            raise
        if exc.http_status in {409, 422}:
            matches = exact_postgres_matches(client, name)
            if len(matches) == 1 and field(matches[0], "uuid", "id"):
                detail = hydrate_database_by_uuid(client, field(matches[0], "uuid", "id"), matches[0])
                result = postgres_classification_from_detail(detail, context)
                if result["status"] == "exists":
                    result["recovered_after_conflict"] = True
                    actions.append(ActionRecord("postgresql", "recover_after_conflict", "exists", request_attempted=True, verified=True, recovered_after_conflict=True).to_dict())
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
        actions.append(ActionRecord("postgresql", "create", "created_unverified", request_attempted=True, write_performed=True).to_dict())
        return {"status": "created_unverified", "name": name, "write_performed": True, "internal_db_url_available": False, "credentials_configured": True}
    try:
        detail = hydrate_database_by_uuid(client, uuid, item if isinstance(item, dict) else {"uuid": uuid, "name": name})
    except ProvisioningError as exc:
        actions.append(ActionRecord("postgresql", "create", "created_unverified", request_attempted=True, write_performed=True).to_dict())
        return {"status": "created_unverified", "name": name, "uuid": uuid, "write_performed": True, "verification_error": exc.message, "internal_db_url_available": False, "credentials_configured": True}
    result = postgres_classification_from_detail(detail, context)
    if result["status"] == "exists":
        result["status"] = "created"
        result["credentials_configured"] = True
        actions.append(ActionRecord("postgresql", "create", "created", request_attempted=True, write_performed=True, verified=True).to_dict())
        return result
    actions.append(ActionRecord("postgresql", "create", "created_unverified", request_attempted=True, write_performed=True).to_dict())
    return {**result, "status": "created_unverified" if result["status"] in {"compatibility_unverified", "scope_unverified"} else result["status"], "write_performed": True, "credentials_configured": True}



READINESS_STATUS_MAP = {
    "queued": "starting",
    "starting": "starting",
    "running:healthy": "healthy",
    "healthy": "healthy",
    "exited": "stopped",
    "stopped": "stopped",
    "running:unhealthy": "unhealthy",
    "exited:unhealthy": "unhealthy",
    "unhealthy": "unhealthy",
    "failed": "unhealthy",
}
READINESS_FAILURE_STATUSES = {"blocked", "readiness_unverified", "unhealthy", "timeout", "partial"}


def classify_database_readiness_status(value: Any) -> str:
    if value in (None, ""):
        return "readiness_unverified"
    text = str(value).strip().lower()
    return READINESS_STATUS_MAP.get(text, "readiness_unverified")


def database_raw_status(item: dict[str, Any]) -> Any:
    return nested_field(item, "status", "human_status", "resource.status")


def prepare_psycopg_database_url(value: str) -> str:
    error = ProvisioningError("DATABASE_URL has no supported PostgreSQL connection shape.", error_type="validation")
    if not isinstance(value, str) or not value or value != value.strip():
        raise error
    if value.startswith("postgresql://"):
        rest = value[len("postgresql://"):]
    elif value.startswith("postgres://"):
        rest = value[len("postgres://"):]
    else:
        raise error
    try:
        parsed = urllib.parse.urlsplit(value)
        hostname = parsed.hostname
    except ValueError as exc:
        raise error from exc
    if parsed.fragment or not parsed.scheme or not parsed.netloc or not hostname or parsed.path in ("", "/"):
        raise error
    return "postgresql+psycopg://" + rest


def readiness_result(status: str, uuid: Any = None, **updates: Any) -> dict[str, Any]:
    result = {
        "status": status,
        "uuid": uuid,
        "start_requested": False,
        "request_attempted": False,
        "write_performed": False,
        "write_outcome_unknown": False,
        "poll_attempts": 0,
        "internal_db_url_available": False,
        "database_url_driver_ready": False,
    }
    result.update(updates)
    return result


def verify_internal_db_url_flags(item: dict[str, Any]) -> dict[str, bool]:
    url = nested_field(item, "internal_db_url")
    available = isinstance(url, str) and bool(url.strip())
    ready = False
    if available:
        try:
            prepare_psycopg_database_url(url)
            ready = True
        except ProvisioningError:
            ready = False
    return {"internal_db_url_available": available, "database_url_driver_ready": ready}


def postgresql_start_preconditions(args: argparse.Namespace, postgres: dict[str, Any]) -> list[dict[str, Any]]:
    blockers = []
    if postgres.get("name") != f"{args.product}-postgres-dev" or not postgres.get("uuid"):
        blockers.append({"reason": "identity_unverified"})
    if postgres.get("status") not in {"exists", "created"}:
        blockers.append({"reason": f"postgresql_status_{postgres.get('status')}"})
    if postgres.get("write_outcome_unknown"):
        blockers.append({"reason": "prior_write_outcome_unknown"})
    return blockers


def poll_postgresql_readiness(client: CoolifyClient, uuid: str, *, timeout: float, interval: float, sleeper: Callable[[float], None] | None = None) -> dict[str, Any]:
    sleeper = sleeper or (lambda seconds: None if seconds <= 0 else __import__("time").sleep(seconds))
    attempts = 0
    max_attempts = max(1, int(timeout // interval) + 1 if interval > 0 else 1)
    last_item: dict[str, Any] = {}
    for attempt in range(max_attempts):
        attempts += 1
        try:
            data = client.get(f"/databases/{uuid}")
        except ProvisioningError:
            return readiness_result("readiness_unverified", uuid, poll_attempts=attempts)
        item = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
        if not isinstance(item, dict):
            return readiness_result("readiness_unverified", uuid, poll_attempts=attempts)
        last_item = item
        classified = classify_database_readiness_status(database_raw_status(item))
        if classified == "healthy":
            return readiness_result("healthy", uuid, poll_attempts=attempts, **verify_internal_db_url_flags(item))
        if classified == "unhealthy":
            return readiness_result("unhealthy", uuid, poll_attempts=attempts, **verify_internal_db_url_flags(item))
        if classified == "readiness_unverified":
            return readiness_result("readiness_unverified", uuid, poll_attempts=attempts, **verify_internal_db_url_flags(item))
        if attempt < max_attempts - 1:
            sleeper(interval)
    return readiness_result("timeout", uuid, poll_attempts=attempts, **verify_internal_db_url_flags(last_item))


def ensure_postgresql_ready(client: CoolifyClient, args: argparse.Namespace, postgres: dict[str, Any], mode: ExecutionMode, actions: list[dict[str, Any]], *, sleeper: Callable[[float], None] | None = None) -> dict[str, Any]:
    uuid = postgres.get("uuid")
    blockers = postgresql_start_preconditions(args, postgres)
    if blockers:
        return readiness_result("blocked", uuid)
    try:
        detail = hydrate_database_by_uuid(client, uuid, postgres)
    except ProvisioningError:
        if mode.is_apply:
            return readiness_result("readiness_unverified", uuid)
        detail = postgres
    state = classify_database_readiness_status(database_raw_status(detail))
    flags = verify_internal_db_url_flags(detail)
    if state == "healthy":
        return readiness_result("already_healthy", uuid, **flags)
    if state == "unhealthy":
        return readiness_result("unhealthy", uuid, **flags)
    if state == "readiness_unverified":
        return readiness_result("readiness_unverified", uuid, **flags)
    if state == "starting":
        return poll_postgresql_readiness(client, str(uuid), timeout=getattr(args, "postgres_readiness_timeout", 300.0), interval=getattr(args, "postgres_readiness_interval", 5.0), sleeper=sleeper)
    if state == "stopped":
        if not mode.is_apply:
            return readiness_result("would_start", uuid, **flags)
        if not mode.database_start_enabled:
            return readiness_result("blocked", uuid, **flags)
        try:
            client.start_postgresql(str(uuid))
        except ProvisioningError as exc:
            if exc.write_outcome_unknown:
                actions.append(ActionRecord("postgresql", "start", "outcome_unknown", request_attempted=True, write_outcome_unknown=True).to_dict())
                return readiness_result("partial", uuid, start_requested=True, request_attempted=True, write_outcome_unknown=True, **flags)
            if exc.http_status in {409, 422}:
                rediscovered = hydrate_database_by_uuid(client, uuid, postgres)
                recovered_state = classify_database_readiness_status(database_raw_status(rediscovered))
                if recovered_state in {"starting", "healthy"}:
                    return poll_postgresql_readiness(client, str(uuid), timeout=getattr(args, "postgres_readiness_timeout", 300.0), interval=getattr(args, "postgres_readiness_interval", 5.0), sleeper=sleeper) if recovered_state == "starting" else readiness_result("healthy", uuid, poll_attempts=1, **verify_internal_db_url_flags(rediscovered))
                return readiness_result("blocked", uuid, start_requested=True, request_attempted=True, **flags)
            raise
        actions.append(ActionRecord("postgresql", "start", "start_accepted", request_attempted=True, write_performed=True, verified=False).to_dict())
        polled = poll_postgresql_readiness(client, str(uuid), timeout=getattr(args, "postgres_readiness_timeout", 300.0), interval=getattr(args, "postgres_readiness_interval", 5.0), sleeper=sleeper)
        if polled.get("status") == "healthy":
            actions.append(ActionRecord("postgresql", "readiness", "healthy", request_attempted=False, write_performed=False, verified=True).to_dict())
        return {**polled, "start_requested": True, "request_attempted": True, "write_performed": True}
    return readiness_result("readiness_unverified", uuid, **flags)


def backend_application_name(product: str) -> str:
    return f"{product}-api-dev"


def api_domain(product: str) -> str:
    return f"https://api.dev.{product}.goodmorning.mx"


def frontend_domain(product: str) -> str:
    return f"https://dev.{product}.goodmorning.mx"


def select_destination(server: dict[str, Any], args: argparse.Namespace, blockers: list[Any]) -> dict[str, Any] | None:
    destinations = item_list(server.get("destinations") if isinstance(server, dict) else [])
    selected = getattr(args, "destination_uuid", None)
    if not destinations:
        blockers.append({"resource": "destination", "status": "blocked", "reason": "no_destination_for_selected_server"})
        return None
    if selected:
        matches = [d for d in destinations if str(field(d, "uuid", "id") or "") == str(selected)]
        if len(matches) != 1:
            blockers.append({"resource": "destination", "status": "blocked", "reason": "destination_not_on_selected_server"})
            return None
        return summary(matches[0], "name", "uuid", "id", "network")
    if len(destinations) > 1:
        blockers.append({"resource": "destination", "status": "blocked", "reason": "multiple_destinations_require_destination_uuid"})
        return None
    return summary(destinations[0], "name", "uuid", "id", "network")


def backend_create_payload(args: argparse.Namespace, bindings: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": backend_application_name(args.product),
        "project_uuid": bindings.get("project_uuid"),
        "environment_uuid": bindings.get("environment_uuid"),
        "environment_name": "dev",
        "server_uuid": bindings.get("server_uuid"),
        "destination_uuid": bindings.get("destination_uuid"),
        "github_app_uuid": bindings.get("github_app_uuid"),
        "git_repository": normalize_repository(args.repository),
        "git_branch": "dev",
        "build_pack": "dockerfile",
        "base_directory": "/backend",
        "dockerfile_location": "/Dockerfile",
        "ports_exposes": "8000",
        "domains": api_domain(args.product),
        "instant_deploy": False,
        "is_auto_deploy_enabled": False,
        "autogenerate_domain": False,
        "health_check_enabled": True,
        "health_check_path": "/health",
        "health_check_port": "8000",
        "health_check_method": "GET",
        "health_check_scheme": "http",
        "health_check_return_code": 200,
    }


def backend_payload_preview(payload: dict[str, Any]) -> dict[str, Any]:
    return redact({k: v for k, v in payload.items() if k != "value"})


def backend_application_differences(item: dict[str, Any], args: argparse.Namespace, context: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    expected = backend_create_payload(args, {**context, "destination_uuid": context.get("destination_uuid")})
    paths = {
        "name": ("name",), "git_repository": ("git_repository", "repository"), "git_branch": ("git_branch", "branch"),
        "build_pack": ("build_pack", "buildpack"), "base_directory": ("base_directory",), "dockerfile_location": ("dockerfile_location", "dockerfile"),
        "ports_exposes": ("ports_exposes",), "domains": ("domains", "domain"), "github_app_uuid": ("github_app_uuid", "source.uuid", "github_app.uuid"),
        "destination_uuid": ("destination_uuid", "destination.uuid", "destination.id"),
    }
    diffs=[]; unver=[]
    for key, ps in paths.items():
        actual = nested_field(item, *ps); exp = expected.get(key)
        if actual in (None, ""):
            unver.append({"field": key, "expected": exp})
        elif key == "domains" and ":8000" in normalize_domain(actual):
            diffs.append({"field": key, "expected": exp, "actual": "contains_port"})
        elif not normalized_equal(key, actual, exp):
            diffs.append({"field": key, "expected": exp, "actual": actual})
    for key in ("instant_deploy", "is_auto_deploy_enabled", "autogenerate_domain"):
        actual = nested_field(item, key)
        if actual in (None, ""):
            continue
        if bool_value(actual) is not False:
            diffs.append({"field": key, "expected": False, "actual": actual})
    hc = {"health_check_enabled": True, "health_check_path": "/health", "health_check_port": "8000", "health_check_method": "GET", "health_check_scheme": "http", "health_check_return_code": 200}
    for key, exp in hc.items():
        actual = nested_field(item, key)
        if actual in (None, ""):
            unver.append({"field": key, "expected": exp})
        elif str(actual).lower() != str(exp).lower():
            diffs.append({"field": key, "expected": exp, "actual": actual})
    return diffs, unver


def classify_backend_application(matches: list[dict[str, Any]], args: argparse.Namespace, context: dict[str, Any]) -> dict[str, Any]:
    name = backend_application_name(args.product)
    if not matches: return {"name": name, "status": "absent"}
    scoped=[]; reports=[]
    for item in matches:
        scope, details = resource_scope(item, context)
        reports.append({"resource": operational_summary(item), "scope": scope, "scope_details": details})
        if scope == "in_scope": scoped.append(item)
    if not scoped:
        return {"name": name, "status": "out_of_scope" if all(r["scope"]=="out_of_scope" for r in reports) else "scope_unverified", "matches": reports}
    if len(scoped)>1: return {"name": name, "status": "duplicate", "matches": [operational_summary(i) for i in scoped]}
    item=scoped[0]
    diffs, unver = backend_application_differences(item,args,context)
    if diffs: return {"name": name, "status": "incompatible", "uuid": field(item,"uuid","id"), "differences": diffs, "matches": reports}
    if unver: return {"name": name, "status": "compatibility_unverified", "uuid": field(item,"uuid","id"), "unverified_fields": unver, "matches": reports}
    return {"name": name, "status": "exists", "uuid": field(item,"uuid","id"), "matches": reports}


def exact_backend_matches(client: CoolifyClient, args: argparse.Namespace) -> list[dict[str, Any]]:
    name=backend_application_name(args.product)
    return [i for i in item_list(client.get("/applications")) if str(field(i,"name") or "") == name]


def hydrate_application_by_uuid(client: CoolifyClient, uuid: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    detail=client.get(f"/applications/{uuid}")
    item=detail.get("data") if isinstance(detail,dict) and isinstance(detail.get("data"),dict) else detail
    if not isinstance(item,dict): raise ProvisioningError("Application detail endpoint returned an unsupported shape.", error_type="coolify_api", code=2)
    return {**fallback, **item, "_discovery_source":"applications", "_detail_hydrated": True}


def postgres_driver_url(client: CoolifyClient, postgres: dict[str, Any]) -> str | None:
    uuid=postgres.get("uuid")
    if not uuid: return None
    detail=hydrate_database_by_uuid(client, uuid, postgres)
    raw=nested_field(detail,"internal_db_url")
    if not isinstance(raw,str): return None
    return prepare_psycopg_database_url(raw)


def postgres_backend_precondition(client: CoolifyClient, args: argparse.Namespace, postgres: dict[str, Any], readiness: dict[str, Any]) -> tuple[bool, list[dict[str, Any]], str | None]:
    blockers=[]; url=None
    if postgres.get("name") != f"{args.product}-postgres-dev" or postgres.get("status") not in {"exists","created"}:
        blockers.append({"resource":"postgresql","reason":"identity_or_compatibility_unverified"})
    if readiness.get("status") not in {"healthy","already_healthy"}:
        blockers.append({"resource":"postgresql","reason":"not_healthy"})
    if not readiness.get("internal_db_url_available"):
        blockers.append({"resource":"postgresql","reason":"internal_db_url_unavailable"})
    if not readiness.get("database_url_driver_ready"):
        blockers.append({"resource":"postgresql","reason":"database_url_driver_not_ready"})
    if not blockers:
        try: url=postgres_driver_url(client, postgres)
        except ProvisioningError: blockers.append({"resource":"postgresql","reason":"database_url_driver_not_ready"})
    return (not blockers), blockers, url


def ensure_backend_application(client: CoolifyClient, args: argparse.Namespace, context: dict[str, Any], mode: ExecutionMode, actions: list[dict[str, Any]], precondition_ok: bool) -> dict[str, Any]:
    if hasattr(client, "backend_application_bindings"):
        client.backend_application_bindings = dict(context)
    matches=[]
    for m in exact_backend_matches(client,args):
        uuid=field(m,"uuid","id")
        if uuid:
            try: m=hydrate_application_by_uuid(client,uuid,m)
            except ProvisioningError: pass
        matches.append(m)
    classified=classify_backend_application(matches,args,context)
    st=classified["status"]
    if st == "exists":
        if hasattr(client, "bind_backend_application_uuid"):
            client.bind_backend_application_uuid(classified.get("uuid"), context)
        actions.append(ActionRecord("backend_application","reuse","exists",verified=True).to_dict()); return classified
    if st in {"duplicate","incompatible","compatibility_unverified","scope_unverified","out_of_scope"}:
        return {**classified, "status": "recreate_required" if st=="incompatible" else st}
    if not precondition_ok:
        return {"name": backend_application_name(args.product), "status":"blocked"}
    if not mode.is_apply:
        actions.append(ActionRecord("backend_application","plan_create","would_create").to_dict())
        return {"name": backend_application_name(args.product), "status":"would_create", "create_payload_preview": backend_payload_preview(backend_create_payload(args,context))}
    if not mode.backend_application_writes_enabled:
        return {"name": backend_application_name(args.product), "status":"blocked", "reason":"backend_application_gates_incomplete"}
    payload=backend_create_payload(args,context); actions.append(ActionRecord("backend_application","attempt_create","attempted").to_dict())
    try:
        resp=client.create_backend_application(payload)
    except ProvisioningError as exc:
        if exc.write_outcome_unknown:
            actions.append(ActionRecord("backend_application","create","write_outcome_unknown",request_attempted=True,write_outcome_unknown=True).to_dict())
            return {"name":backend_application_name(args.product),"status":"write_outcome_unknown","request_attempted":True,"write_outcome_unknown":True}
        if exc.http_status in {409,422}:
            rematches=[hydrate_application_by_uuid(client, field(m,"uuid","id"), m) if field(m,"uuid","id") else m for m in exact_backend_matches(client,args)]
            recovered=classify_backend_application(rematches,args,context)
            if recovered.get("status")=="exists":
                if hasattr(client, "bind_backend_application_uuid"):
                    client.bind_backend_application_uuid(recovered.get("uuid"), context)
                actions.append(ActionRecord("backend_application","recover_after_conflict","exists",request_attempted=True,verified=True,recovered_after_conflict=True).to_dict())
                return {**recovered,"recovered_after_conflict":True}
        raise
    item=resp.data.get("data") if isinstance(resp.data,dict) and isinstance(resp.data.get("data"),dict) else resp.data
    uuid=field(item,"uuid","id") if isinstance(item,dict) else None
    if not uuid:
        actions.append(ActionRecord("backend_application","create","created_unverified",request_attempted=True,write_performed=True).to_dict())
        return {"name":backend_application_name(args.product),"status":"created_unverified","write_performed":True}
    try: detail=hydrate_application_by_uuid(client,uuid,item)
    except ProvisioningError:
        actions.append(ActionRecord("backend_application","create","created_unverified",request_attempted=True,write_performed=True).to_dict()); return {"name":backend_application_name(args.product),"uuid":uuid,"status":"created_unverified","write_performed":True}
    verified=classify_backend_application([detail],args,context)
    if verified.get("status")=="exists":
        if hasattr(client, "bind_backend_application_uuid"):
            client.bind_backend_application_uuid(verified.get("uuid"), context)
        actions.append(ActionRecord("backend_application","create","created",request_attempted=True,write_performed=True,verified=True).to_dict())
        return {**verified,"status":"created"}
    return {**verified,"status":"created_unverified","write_performed":True}


def desired_backend_env(product: str, database_url: str | None) -> list[dict[str, Any]]:
    base={"is_runtime":True,"is_buildtime":False,"is_preview":False,"is_literal":True,"is_multiline":False}
    return [
        {"key":"DATABASE_URL","value":database_url,"secret":True, **base},
        {"key":"APP_ENV","value":"dev","secret":False, **base},
        {"key":"CORS_ORIGINS","value":frontend_domain(product),"secret":False, **base},
    ]


def env_payload(desired: dict[str, Any]) -> dict[str, Any]:
    payload={k:desired[k] for k in ("key","value","is_runtime","is_buildtime","is_preview","is_literal","is_multiline")}
    if desired.get("secret"): payload["is_shown_once"]=True
    return payload


def env_value(item: dict[str, Any]) -> tuple[bool, Any]:
    for k in ("value","real_value"):
        if k in item and item[k] not in (None, "********", "*****"):
            return True,item[k]
    return False,None


def env_flags_compatible(item: dict[str, Any], desired: dict[str, Any]) -> bool:
    for key in ("is_runtime", "is_buildtime", "is_preview", "is_literal", "is_multiline"):
        if bool_value(item.get(key)) != desired[key]:
            return False
    return True


def locate_env_variable(envs: list[dict[str, Any]], desired: dict[str, Any]) -> dict[str, Any]:
    key = desired["key"]
    matches = [item for item in envs if item.get("key") == key and bool_value(item.get("is_preview")) is False]
    row = {"key": key, "desired_flags": {k: desired[k] for k in ("is_runtime", "is_buildtime", "is_preview", "is_literal", "is_multiline")}, "driver_ready": bool(desired.get("value")) if key == "DATABASE_URL" else None, "value_available": False, "value_verified": False}
    if len(matches) > 1:
        return {**row, "status": "duplicate", "compatible": False}
    if not matches:
        return {**row, "status": "absent", "compatible": False}
    found = matches[0]
    available, value = env_value(found)
    row["value_available"] = available
    flags_ok = env_flags_compatible(found, desired)
    if key == "DATABASE_URL" and not available:
        return {**row, "status": "secret_value_unverified", "compatible": "unverified"}
    value_ok = available and value == desired.get("value")
    row["value_verified"] = value_ok
    if flags_ok and value_ok:
        return {**row, "status": "exists", "compatible": True}
    return {**row, "status": "incompatible", "compatible": False}


def rediscover_envs_after_write(client: CoolifyClient, uuid: str, desired: dict[str, Any], operation: str) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    try:
        envs = item_list(client.get(f"/applications/{urllib.parse.quote(str(uuid), safe='')}/envs"))
    except ProvisioningError:
        suffix = "created_unverified" if operation == "create" else "updated_unverified"
        return None, {"key": desired["key"], "status": suffix, "verified": False, "value_available": False, "value_verified": False}
    row = locate_env_variable(envs, desired)
    if row["status"] == "exists":
        return envs, {**row, "status": "created" if operation == "create" else "patched", "verified": True}
    if row["status"] == "secret_value_unverified":
        return envs, {**row, "verified": False}
    if row["status"] == "duplicate":
        return envs, {**row, "verified": False}
    suffix = "created_unverified" if operation == "create" else "updated_unverified"
    return envs, {**row, "status": suffix, "verified": False}


def env_write_action(resource_key: str, operation: str, status: str, *, request_attempted: bool, write_performed: bool = False, write_outcome_unknown: bool = False, verified: bool = False, recovered_after_conflict: bool = False) -> dict[str, Any]:
    return ActionRecord(
        "backend_environment",
        f"{operation}_{resource_key}",
        status,
        request_attempted=request_attempted,
        write_performed=write_performed,
        write_outcome_unknown=write_outcome_unknown,
        verified=verified,
        recovered_after_conflict=recovered_after_conflict,
        details={"key": resource_key, "operation": operation},
    ).to_dict()


def reconcile_env_conflict(client: CoolifyClient, uuid: str, desired: dict[str, Any], operation: str) -> tuple[list[dict[str, Any]] | None, dict[str, Any], dict[str, Any]]:
    envs, row = rediscover_envs_after_write(client, uuid, desired, operation)
    if row.get("status") in {"created", "patched"}:
        row["recovered_after_conflict"] = True
        action = env_write_action(desired["key"], operation, row["status"], request_attempted=True, write_performed=False, verified=True, recovered_after_conflict=True)
        return envs, row, action
    action = env_write_action(desired["key"], operation, row.get("status", "blocked"), request_attempted=True, write_performed=False, verified=False)
    return envs, row, action


def reconcile_backend_environment(client: CoolifyClient, args: argparse.Namespace, backend: dict[str, Any], db_url: str | None, mode: ExecutionMode, actions: list[dict[str, Any]]) -> dict[str, Any]:
    uuid=backend.get("uuid")
    results=[]; blockers=[]
    if backend.get("status") not in {"exists","created"} or not uuid:
        return {"status":"blocked","variables":[],"blockers":[{"reason":"backend_application_not_verified"}]}
    envs=item_list(client.get(f"/applications/{urllib.parse.quote(str(uuid), safe='')}/envs"))
    for desired in desired_backend_env(args.product, db_url):
        key=desired["key"]
        row=locate_env_variable(envs, desired)
        if row["status"] == "duplicate":
            blockers.append({"resource":"backend_environment","key":key,"reason":"duplicate"}); results.append(row); break
        if row["status"] == "exists":
            results.append(row); continue
        if row["status"] == "secret_value_unverified":
            blockers.append({"resource":"backend_environment","key":key,"reason":"secret_value_unverified"}); results.append(row); break
        operation = "create" if row["status"] == "absent" else "update"
        row["action"] = "would_create" if operation == "create" and not mode.is_apply else ("would_patch" if not mode.is_apply else operation)
        if not mode.is_apply:
            results.append(row); continue
        if not mode.backend_env_writes_enabled:
            row["status"]="blocked"; blockers.append({"resource":"backend_environment","key":key,"reason":"backend_env_gates_incomplete"}); results.append(row); break
        try:
            if operation == "create":
                client.create_backend_env(str(uuid), env_payload(desired))
            else:
                client.update_backend_env(str(uuid), env_payload(desired))
        except ProvisioningError as exc:
            if exc.write_outcome_unknown:
                row.update({"status":"write_outcome_unknown", "write_outcome_unknown": True})
                actions.append(env_write_action(key, operation, "write_outcome_unknown", request_attempted=True, write_outcome_unknown=True))
                blockers.append({"resource":"backend_environment","key":key,"reason":"write_outcome_unknown"}); results.append(row); break
            if exc.http_status in {409, 422}:
                envs, row, action = reconcile_env_conflict(client, str(uuid), desired, operation)
                actions.append(action); results.append(row)
                if row.get("status") not in {"created", "patched"}:
                    blockers.append({"resource":"backend_environment","key":key,"reason":row.get("status")}); break
                continue
            raise
        envs, verified_row = rediscover_envs_after_write(client, str(uuid), desired, operation)
        results.append(verified_row)
        actions.append(env_write_action(key, operation, verified_row["status"], request_attempted=True, write_performed=True, verified=bool(verified_row.get("verified"))))
        if verified_row.get("status") not in {"created", "patched"}:
            blockers.append({"resource":"backend_environment","key":key,"reason":verified_row.get("status")}); break
    if not mode.is_apply:
        status="planned"
    elif blockers:
        if any(r.get("status") == "secret_value_unverified" for r in results):
            status="secret_value_unverified"
        elif any(r.get("status") == "duplicate" for r in results):
            status="partial"
        elif any(r.get("status") in {"created_unverified", "updated_unverified", "write_outcome_unknown"} for r in results):
            status="partial"
        else:
            status="blocked"
    else:
        status="exists"
    return {"status":status,"variables":results,"blockers":blockers}

def global_status(mode: ExecutionMode, resources: list[dict[str, Any]], blockers: list[Any], actions: list[dict[str, Any]], readiness: dict[str, Any] | None = None) -> str:
    statuses = {resource.get("status") for resource in resources}
    write_performed = any(action.get("write_performed") for action in actions) or any(resource.get("write_performed") for resource in resources)
    write_outcome_unknown = any(action.get("write_outcome_unknown") for action in actions) or any(resource.get("write_outcome_unknown") for resource in resources)
    if not mode.is_apply:
        return "dry_run"
    if mode.is_apply and readiness and (readiness.get("write_outcome_unknown") or readiness.get("status") in READINESS_FAILURE_STATUSES):
        return "partial" if readiness.get("write_outcome_unknown") else readiness.get("status")
    if "incompatible" in statuses:
        return "recreate_required"
    if write_outcome_unknown or "created_unverified" in statuses or (write_performed and blockers):
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
    destination = select_destination(server, args, blockers)
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
        postgresql_readiness = ensure_postgresql_ready(client, args, postgres, mode, actions)
    except ProvisioningError as exc:
        if exc.write_performed or exc.write_outcome_unknown or any(action.get("write_performed") or action.get("write_outcome_unknown") for action in actions):
            blockers.append({"message": exc.message, "type": exc.error_type, "write_performed": exc.write_performed, "write_outcome_unknown": exc.write_outcome_unknown})
            project = locals().get("project", {"status": "blocked"})
            environment = locals().get("environment", {"status": "blocked"})
            postgres = locals().get("postgres", {"status": "blocked"})
            postgresql_readiness = locals().get("postgresql_readiness", readiness_result("blocked", postgres.get("uuid")))
        else:
            raise
    postgresql_readiness = locals().get("postgresql_readiness", readiness_result("blocked", postgres.get("uuid")))
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
        "destination_uuid": (destination or {}).get("uuid") or (destination or {}).get("id"),
    }
    discovery = {
        "project": {"name": args.coolify_project, "uuid": project.get("uuid")},
        "environment": {"name": "dev", "uuid": environment.get("uuid")},
        "server": server_summary,
        "github_app": github_app_summary,
        "bindings": context,
        "existing_resources": discover_existing_resources(client, args.product, context, args),
    }
    precondition_ok, pg_blockers, database_url = postgres_backend_precondition(client, args, postgres, postgresql_readiness)
    blockers.extend(pg_blockers if mode.is_apply else [])
    if destination is None:
        precondition_ok = False
    backend = ensure_backend_application(client, args, context, mode, actions, precondition_ok)
    if backend.get("status") in {"duplicate", "compatibility_unverified", "scope_unverified", "recreate_required", "blocked", "created_unverified", "write_outcome_unknown"}:
        blockers.append({"resource": "backend_application", "status": backend.get("status")})
    backend_env = reconcile_backend_environment(client, args, backend, database_url, mode, actions) if backend.get("status") in {"exists", "created"} and precondition_ok else {"status": "blocked" if mode.is_apply else "planned", "variables": [], "blockers": []}
    blockers.extend(backend_env.get("blockers", []))
    plan = build_plan(args, discovery)
    plan["mode"] = mode.name
    allowed_posts = [] if not mode.is_apply else sorted({"project_create", "environment_create", "postgresql_create"} | ({"postgresql_start"} if mode.database_start_enabled else set()) | ({"backend_application_create"} if mode.backend_application_writes_enabled else set()) | ({"backend_env_create"} if mode.backend_env_writes_enabled else set()))
    plan["safety"] = {
        "writes_enabled": mode.is_apply and mode.allow_resource_writes and mode.confirm_product == mode.product,
        "allowed_post_operations": allowed_posts,
        "allowed_patch_operations": ["backend_env_update"] if mode.is_apply and mode.backend_env_writes_enabled else [],
        "database_start_enabled": mode.database_start_enabled,
        "forbidden_methods": ["PUT", "DELETE"],
        "deploy_actions_enabled": False,
        "application_creation_enabled": mode.backend_application_writes_enabled,
    }
    for resource in plan["resources"]:
        if resource["name"] == f"{args.product}-postgres-dev":
            resource.update({"status": postgres.get("status"), "provisioning": postgres})
        elif resource["name"] == backend_application_name(args.product):
            resource.update({"status": backend.get("status"), "provisioning": backend})
            resource["create_payload_preview"] = backend_payload_preview(backend_create_payload(args, context))
        else:
            resource["status"] = "planned_later"
    status = global_status(mode, [project, environment, postgres, backend, backend_env], blockers, actions, postgresql_readiness)
    return redact({
        **plan,
        "status": status,
        "mode": mode.name,
        "safety_gates": mode.gates(),
        "selected_server": server_summary,
        "selected_destination": destination,
        "project": project,
        "environment_resource": environment,
        "postgresql": postgres,
        "postgresql_readiness": postgresql_readiness,
        "backend_application": backend,
        "backend_environment": backend_env,
        "actions": actions,
        "warnings": warnings,
        "blockers": blockers,
        "contract": {
            "confirmed_endpoints": ["POST /api/v1/projects", "POST /api/v1/projects/{project_uuid}/environments", "POST /api/v1/databases/postgresql", "GET /api/v1/databases/{uuid}", "POST /api/v1/databases/{uuid}/start", "POST /api/v1/applications/private-github-app", "GET /api/v1/applications/{uuid}", "GET /api/v1/applications/{uuid}/envs", "POST /api/v1/applications/{uuid}/envs", "PATCH /api/v1/applications/{uuid}/envs"],
            "postgresql_payload_preview": redact(postgresql_create_payload(args, "${project.uuid}", "${server.uuid}", "${generated_secret}")),
            "postgresql_payload_source": "Coolify OpenAPI/API reference for create PostgreSQL database; version is represented by the image field in this isolated builder pending any upstream contract rename.",
            "backend_application_payload_preview": backend_payload_preview(backend_create_payload(args, context)),
            "backend_env_keys": sorted(BACKEND_ENV_KEYS),
            "upstream_sources": ["coollabsio/coolify openapi.yaml v4.x", "coollabsio/coolify ApplicationsController.php v4.x", "coollabsio/coolify-cli v4.x", "coolify.io official docs", "goodmorning-mx/goodmorning-mvp-template backend/app/config.py dev"],
        },
    })


def provision(args: argparse.Namespace, *, client_factory: Callable[[], CoolifyClient] | None = None) -> dict[str, Any]:
    validate_args(args)
    validate_readiness_timing_args(args)
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
    parser.add_argument("--confirm-database-start")
    parser.add_argument("--confirm-backend-application")
    parser.add_argument("--confirm-backend-environment")
    parser.add_argument("--destination-uuid")
    parser.add_argument("--postgres-readiness-interval", type=float, default=5.0)
    parser.add_argument("--postgres-readiness-timeout", type=float, default=300.0)
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
