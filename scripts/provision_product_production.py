#!/usr/bin/env python3
"""Plan or safely apply GoodMorning Coolify production primitives.

Dry-run is GET-only and produces the complete three-resource plan. Apply can
create only the Coolify Project, exact ``production`` Environment, and private native
PostgreSQL record after explicit gates. Backend Dockerfile Applications and their runtime environment variables can be created/reconciled only after Stage 5 gates. Stage 7 permits only the Backend POST /deploy path after explicit deployment gates and preflight; Frontend deployment, Application start/restart/stop, deployment cancel, rollback, and all non-allowlisted deployment actions remain forbidden.
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
from html.parser import HTMLParser
from dataclasses import dataclass
from typing import Any, Callable

ALLOWED_BRANCH = "main"
ALLOWED_ENVIRONMENT = "production"
POSTGRES_VERSION = "16"
FACTORY_POSTGRES_DESCRIPTION = "GoodMorning production PostgreSQL 16 resource managed by factory provisioning stage 3."
POSTGRES_SCOPE_FALLBACK_SOURCE = "factory_managed_identity_and_explicit_selected_scope"
POSTGRES_SCOPE_FALLBACK_REASON = "public_api_omits_project_and_environment_scope"
TIMEOUT_SECONDS = 30
SENSITIVE_ENV = ("COOLIFY_API_TOKEN",)
SECRET_KEY_RE = re.compile(r"(PASSWORD|SECRET|TOKEN|KEY|PRIVATE|CREDENTIAL|DATABASE_URL|AUTHORIZATION|INTERNAL_DB_URL)", re.I)
PRODUCT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
COOLIFY_IDENTIFIER_MAX_LENGTH = 64
COOLIFY_IDENTIFIER_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
READ_ONLY_METHODS = {"GET"}
WRITE_METHODS = {"POST", "PATCH"}
POST_PROJECT_CREATE = "/projects"
POST_POSTGRES_CREATE = "/databases/postgresql"
POST_BACKEND_APPLICATION_CREATE = "/applications/private-github-app"
POST_FRONTEND_APPLICATION_CREATE = "/applications/private-github-app"
FRONTEND_ENV_KEYS = {"VITE_API_URL"}
BACKEND_ENV_KEYS = {"DATABASE_URL", "APP_ENV", "CORS_ORIGINS"}
APPROVED_BACKEND_APPLICATION_FIELDS = {"name", "project_uuid", "environment_uuid", "environment_name", "server_uuid", "destination_uuid", "github_app_uuid", "git_repository", "git_branch", "build_pack", "base_directory", "dockerfile_location", "ports_exposes", "domains", "instant_deploy", "is_auto_deploy_enabled", "autogenerate_domain", "health_check_enabled", "health_check_path", "health_check_port", "health_check_method", "health_check_scheme", "health_check_return_code"}
APPROVED_BACKEND_ENV_FIELDS = {"key", "value", "is_runtime", "is_buildtime", "is_preview", "is_literal", "is_multiline", "is_shown_once"}
APPROVED_FRONTEND_APPLICATION_FIELDS = APPROVED_BACKEND_APPLICATION_FIELDS
APPROVED_FRONTEND_ENV_FIELDS = {"key", "value", "is_runtime", "is_buildtime", "is_preview", "is_literal", "is_multiline"}
ENV_PAYLOAD_FIELDS = APPROVED_BACKEND_ENV_FIELDS
ALLOWED_POST_KINDS = {"project_create", "environment_create", "postgresql_create", "postgresql_start", "backend_application_create", "backend_env_create", "frontend_application_create", "frontend_env_create", "backend_deploy", "frontend_deploy"}
ALLOWED_PATCH_KINDS = {"backend_env_update", "frontend_env_update"}
FORBIDDEN_ENDPOINT_RE = re.compile(r"/(restart|stop|rollback)(?:/|$)|/applications/[^/]+/(?:start|restart|stop|rollback)(?:/|$)|/deployments/[^/]+/cancel(?:/|$)", re.I)
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
    allow_frontend_application_writes: bool = False
    confirm_frontend_application: str | None = None
    allow_frontend_env_writes: bool = False
    confirm_frontend_environment: str | None = None
    deploy_backend: bool = False
    deploy_frontend: bool = False
    allow_deployments: bool = False
    allow_backend_deployment: bool = False
    confirm_backend_deployment: str | None = None
    allow_frontend_deployment: bool = False
    confirm_frontend_deployment: str | None = None

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
            and self.confirm_database_start == f"{self.product}-postgres-prod"
        )

    @property
    def backend_application_name(self) -> str:
        return f"{self.product}-api-prod"

    @property
    def backend_application_writes_enabled(self) -> bool:
        return (self.is_apply and self.allow_resource_writes and self.confirm_product == self.product and self.allow_backend_application_writes and self.confirm_backend_application == self.backend_application_name)

    @property
    def backend_env_writes_enabled(self) -> bool:
        return self.backend_application_writes_enabled and self.allow_backend_env_writes and self.confirm_backend_environment == self.backend_application_name

    @property
    def backend_deployment_enabled(self) -> bool:
        return (self.is_apply and self.deploy_backend and self.allow_resource_writes and self.confirm_product == self.product and self.allow_deployments and self.allow_backend_deployment and self.confirm_backend_deployment == self.backend_application_name)

    @property
    def frontend_application_name(self) -> str:
        return f"{self.product}-frontend-prod"

    @property
    def frontend_deployment_enabled(self) -> bool:
        return (self.is_apply and self.deploy_frontend and self.allow_resource_writes and self.confirm_product == self.product and self.allow_deployments and self.allow_frontend_deployment and self.confirm_frontend_deployment == self.frontend_application_name)

    @property
    def frontend_application_writes_enabled(self) -> bool:
        return self.is_apply and self.allow_resource_writes and self.confirm_product == self.product and self.allow_frontend_application_writes and self.confirm_frontend_application == self.frontend_application_name

    @property
    def frontend_env_writes_enabled(self) -> bool:
        return self.frontend_application_writes_enabled and self.allow_frontend_env_writes and self.confirm_frontend_environment == self.frontend_application_name

    def gates(self) -> dict[str, Any]:
        return {
            "apply_requested": self.is_apply,
            "resource_write_env": self.allow_resource_writes,
            "confirm_product_matches": self.confirm_product == self.product if self.confirm_product is not None else False,
            "allowed_post_kinds": sorted(ALLOWED_POST_KINDS),
            "database_start_env": self.allow_database_start,
            "confirm_database_start_matches": self.confirm_database_start == f"{self.product}-postgres-prod" if self.confirm_database_start is not None else False,
            "database_start_enabled": self.database_start_enabled,
            "always_forbidden_methods": ["PUT", "DELETE"],
            "conditionally_allowed_methods": ["PATCH"],
            "allowed_patch_operations": ["backend_env_update"] if self.backend_env_writes_enabled else [],
            "patch_scope": "backend_application_envs_bound_uuid" if self.backend_env_writes_enabled else None,
            "non_allowlisted_deployments_forbidden": True,
            "deploy_frontend_requested": self.deploy_frontend,
            "frontend_deployment_env": self.allow_frontend_deployment,
            "confirm_frontend_deployment": self.confirm_frontend_deployment,
            "confirm_frontend_deployment_matches": self.confirm_frontend_deployment == self.frontend_application_name if self.confirm_frontend_deployment is not None else False,
            "frontend_deployment_enabled": self.frontend_deployment_enabled,
            "deployment_cancel_forbidden": True,
            "application_lifecycle_forbidden": True,
            "rollback_forbidden": True,
            "backend_application_env": self.allow_backend_application_writes,
            "confirm_backend_application_matches": self.confirm_backend_application == self.backend_application_name if self.confirm_backend_application is not None else False,
            "backend_application_writes_enabled": self.backend_application_writes_enabled,
            "backend_env_env": self.allow_backend_env_writes,
            "confirm_backend_environment_matches": self.confirm_backend_environment == self.backend_application_name if self.confirm_backend_environment is not None else False,
            "backend_env_writes_enabled": self.backend_env_writes_enabled,
            "backend_application_creation_enabled": self.backend_application_writes_enabled,
            "frontend_application_env": self.allow_frontend_application_writes,
            "confirm_frontend_application_matches": self.confirm_frontend_application == self.frontend_application_name if self.confirm_frontend_application is not None else False,
            "frontend_application_writes_enabled": self.frontend_application_writes_enabled,
            "frontend_env_env": self.allow_frontend_env_writes,
            "confirm_frontend_environment_matches": self.confirm_frontend_environment == self.frontend_application_name if self.confirm_frontend_environment is not None else False,
            "frontend_env_writes_enabled": self.frontend_env_writes_enabled,
            "frontend_application_creation_enabled": self.frontend_application_writes_enabled,
            "deploy_backend_requested": self.deploy_backend,
            "deployments_env": self.allow_deployments,
            "backend_deployment_env": self.allow_backend_deployment,
            "confirm_backend_deployment_matches": self.confirm_backend_deployment == self.backend_application_name if self.confirm_backend_deployment is not None else False,
            "backend_deployment_enabled": self.backend_deployment_enabled,
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
            key: item if str(key) in {"observed_variable_name_counts", "observed_variable_names"} else (sanitize_for_output(item) if str(key) in {"database_credentials_available", "internal_db_url_available", "database_url_driver_ready", "internal_db_url_source"} else ("[REDACTED]" if SECRET_KEY_RE.search(str(key)) else sanitize_for_output(item)))
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
        self.expected_backend_env_contract: dict[str, dict[str, Any]] | None = None
        self.expected_frontend_application_uuid: str | None = None
        self.frontend_application_bindings: dict[str, Any] = {}
        self.expected_frontend_env_contract: dict[str, dict[str, Any]] | None = None
        self.backend_deployment_preconditions_ok = False
        self.frontend_deployment_preconditions_ok = False


    def bind_backend_application_bindings(self, bindings: dict[str, Any]) -> None:
        sanitized = dict(bindings or {})
        if self.backend_application_bindings and self.backend_application_bindings != sanitized:
            raise ProvisioningError("Refusing to change selected Backend Application bindings during this execution.", error_type="safety")
        self.backend_application_bindings = sanitized

    def bind_backend_env_contract(self, desired_env: list[dict[str, Any]]) -> None:
        contract = {item["key"]: env_payload(item) for item in desired_env}
        if set(contract) != BACKEND_ENV_KEYS:
            raise ProvisioningError("Backend env desired contract must include exactly the approved keys.", error_type="safety")
        if self.expected_backend_env_contract is not None and self.expected_backend_env_contract != contract:
            raise ProvisioningError("Refusing to change the bound Backend env desired contract during this execution.", error_type="safety")
        self.expected_backend_env_contract = contract

    def bind_backend_application_uuid(self, uuid: Any, bindings: dict[str, Any] | None = None) -> None:
        text = str(uuid or "")
        if not text:
            raise ProvisioningError("Cannot bind an empty Backend Application UUID.", error_type="safety")
        if self.expected_backend_application_uuid is not None and self.expected_backend_application_uuid != text:
            raise ProvisioningError("Refusing to change the verified Backend Application UUID binding.", error_type="safety")
        self.expected_backend_application_uuid = text
        self.bind_backend_application_bindings(dict(bindings or {}))


    def bind_frontend_application_bindings(self, bindings: dict[str, Any]) -> None:
        sanitized = dict(bindings or {})
        if self.frontend_application_bindings and self.frontend_application_bindings != sanitized:
            raise ProvisioningError("Refusing to change selected Frontend Application bindings during this execution.", error_type="safety")
        self.frontend_application_bindings = sanitized

    def bind_frontend_env_contract(self, desired_env: list[dict[str, Any]]) -> None:
        contract = {item["key"]: frontend_env_payload(item) for item in desired_env}
        if set(contract) != FRONTEND_ENV_KEYS:
            raise ProvisioningError("Frontend env desired contract must include exactly VITE_API_URL.", error_type="safety")
        if self.expected_frontend_env_contract is not None and self.expected_frontend_env_contract != contract:
            raise ProvisioningError("Refusing to change the bound Frontend env desired contract during this execution.", error_type="safety")
        self.expected_frontend_env_contract = contract

    def bind_frontend_application_uuid(self, uuid: Any, bindings: dict[str, Any] | None = None) -> None:
        text = str(uuid or "")
        if not text:
            raise ProvisioningError("Cannot bind an empty Frontend Application UUID.", error_type="safety")
        if self.expected_backend_application_uuid is not None and self.expected_backend_application_uuid == text:
            raise ProvisioningError("Frontend Application UUID must not equal the Backend Application UUID binding.", error_type="safety")
        if self.expected_frontend_application_uuid is not None and self.expected_frontend_application_uuid != text:
            raise ProvisioningError("Refusing to change the verified Frontend Application UUID binding.", error_type="safety")
        self.expected_frontend_application_uuid = text
        self.bind_frontend_application_bindings(dict(bindings or {}))

    def _frontend_env_path_matches_binding(self, path: str) -> bool:
        if self.expected_frontend_application_uuid is None:
            return False
        encoded = urllib.parse.quote(self.expected_frontend_application_uuid, safe="")
        return path == f"/applications/{encoded}/envs"

    def bind_backend_deployment_preconditions(self, ok: bool) -> None:
        self.backend_deployment_preconditions_ok = bool(ok)

    def bind_frontend_deployment_preconditions(self, ok: bool) -> None:
        self.frontend_deployment_preconditions_ok = bool(ok)

    def _validate_backend_deploy_payload(self, json_body: dict[str, Any] | None) -> None:
        if not isinstance(json_body, dict):
            raise ProvisioningError("Backend deployment requires an approved JSON object payload.", error_type="safety")
        if set(json_body) != {"uuid", "force"}:
            raise ProvisioningError("Backend deployment payload fields must be exactly uuid and force.", error_type="safety")
        uuid = str(json_body.get("uuid") or "")
        if not uuid or "," in uuid or uuid != str(self.expected_backend_application_uuid or ""):
            raise ProvisioningError("Backend deployment UUID must match the verified Backend Application binding and must not contain commas.", error_type="safety")
        if json_body.get("force") is not False:
            raise ProvisioningError("Backend deployment payload force must be exactly false.", error_type="safety")
        if any(k in json_body for k in ("tag", "pr", "pull_request_id", "docker_tag")):
            raise ProvisioningError("Backend deployment must not include tag, PR, or docker tag fields.", error_type="safety")
        if not self.mode.backend_deployment_enabled:
            raise ProvisioningError("Backend deployment requires --apply, --deploy-backend, deployment env gates, and exact --confirm-backend-deployment.", error_type="safety")
        if not self.backend_deployment_preconditions_ok:
            raise ProvisioningError("Backend deployment preconditions are not complete.", error_type="safety")


    def _validate_frontend_deploy_payload(self, json_body: dict[str, Any] | None) -> None:
        if not isinstance(json_body, dict):
            raise ProvisioningError("Frontend deployment requires an approved JSON object payload.", error_type="safety")
        if set(json_body) != {"uuid", "force"}:
            raise ProvisioningError("Frontend deployment payload fields must be exactly uuid and force.", error_type="safety")
        uuid = str(json_body.get("uuid") or "")
        if not uuid or "," in uuid or uuid != str(self.expected_frontend_application_uuid or ""):
            raise ProvisioningError("Frontend deployment UUID must match the verified Frontend Application binding and must not contain commas.", error_type="safety")
        if self.expected_backend_application_uuid is not None and uuid == str(self.expected_backend_application_uuid):
            raise ProvisioningError("Frontend deployment must not use the Backend Application UUID binding.", error_type="safety")
        if json_body.get("force") is not False:
            raise ProvisioningError("Frontend deployment payload force must be exactly false.", error_type="safety")
        if any(k in json_body for k in ("tag", "pr", "pull_request_id", "docker_tag")):
            raise ProvisioningError("Frontend deployment must not include tag, PR, or docker tag fields.", error_type="safety")
        if not self.mode.frontend_deployment_enabled:
            raise ProvisioningError("Frontend deployment requires --apply, --deploy-frontend, deployment env gates, and exact --confirm-frontend-deployment.", error_type="safety")
        if not self.frontend_deployment_preconditions_ok:
            raise ProvisioningError("Frontend deployment preconditions are not complete.", error_type="safety")

    def _validate_frontend_application_payload(self, json_body: dict[str, Any] | None) -> None:
        if not isinstance(json_body, dict):
            raise ProvisioningError("Frontend Application creation requires an approved JSON object payload.", error_type="safety")
        if set(json_body) != APPROVED_FRONTEND_APPLICATION_FIELDS:
            raise ProvisioningError("Frontend Application payload fields do not exactly match the approved contract.", error_type="safety")
        expected_domain = f"https://{self.mode.product}.goodmorning.mx"
        checks = {"name": self.mode.frontend_application_name, "git_branch": "main", "build_pack": "dockerfile", "base_directory": "/frontend", "dockerfile_location": "/Dockerfile", "ports_exposes": "80", "domains": expected_domain, "instant_deploy": False, "is_auto_deploy_enabled": False, "autogenerate_domain": False, "health_check_enabled": True, "health_check_path": "/", "health_check_port": "80", "health_check_method": "GET", "health_check_scheme": "http", "health_check_return_code": 200}
        for key, expected in checks.items():
            if json_body.get(key) != expected:
                raise ProvisioningError(f"Frontend Application payload field {key} does not match the approved Stage 6 contract.", error_type="safety")
        if re.search(r":(?:80|3000|8000)(?:/|$)", str(json_body.get("domains") or "")):
            raise ProvisioningError("Frontend Application public domain must not include an internal port.", error_type="safety")
        for key in ("project_uuid", "environment_uuid", "server_uuid", "destination_uuid", "github_app_uuid"):
            expected = self.frontend_application_bindings.get(key)
            if expected not in (None, "") and str(json_body.get(key)) != str(expected):
                raise ProvisioningError(f"Frontend Application payload binding {key} does not match the selected binding.", error_type="safety")
        if json_body.get("environment_name") != "production":
            raise ProvisioningError("Frontend Application payload must target environment_name=", error_type="safety")
        expected_repository = self.frontend_application_bindings.get("git_repository") or self.frontend_application_bindings.get("repository")
        if not expected_repository or normalize_repository(json_body.get("git_repository")) != normalize_repository(expected_repository):
            raise ProvisioningError("Frontend Application payload repository does not match the selected repository binding.", error_type="safety")

    def _validate_frontend_env_payload(self, json_body: dict[str, Any] | None) -> None:
        if not isinstance(json_body, dict):
            raise ProvisioningError("Frontend env write requires an approved JSON object payload.", error_type="safety")
        if self.expected_frontend_env_contract is None:
            raise ProvisioningError("Frontend env writes require a bound desired env contract.", error_type="safety")
        if set(json_body) != APPROVED_FRONTEND_ENV_FIELDS:
            raise ProvisioningError("Frontend env payload fields do not exactly match the approved VITE_API_URL contract.", error_type="safety")
        if json_body.get("key") != "VITE_API_URL":
            raise ProvisioningError("Frontend env payload key must be exactly VITE_API_URL.", error_type="safety")
        expected_payload = self.expected_frontend_env_contract.get("VITE_API_URL")
        if json_body != expected_payload:
            raise ProvisioningError("Frontend env value does not match the bound desired contract.", error_type="safety")
        validate_vite_api_url(str(json_body.get("value") or ""), self.mode.product)

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
        expected_domain = f"https://api.{self.mode.product}.goodmorning.mx"
        checks = {
            "name": expected_name,
            "git_branch": "main",
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
        if json_body.get("environment_name") != "production":
            raise ProvisioningError("Backend Application payload must target environment_name=", error_type="safety")
        expected_repository = self.backend_application_bindings.get("git_repository") or self.backend_application_bindings.get("repository")
        if not expected_repository or not normalize_repository(json_body.get("git_repository")) or normalize_repository(json_body.get("git_repository")) != normalize_repository(expected_repository):
            raise ProvisioningError("Backend Application payload repository does not match the selected repository binding.", error_type="safety")

    def _validate_backend_env_payload(self, json_body: dict[str, Any] | None) -> None:
        if not isinstance(json_body, dict):
            raise ProvisioningError("Backend env write requires an approved JSON object payload.", error_type="safety")
        if self.expected_backend_env_contract is None:
            raise ProvisioningError("Backend env writes require a bound desired env contract.", error_type="safety")
        if set(json_body) - APPROVED_BACKEND_ENV_FIELDS:
            raise ProvisioningError("Backend env payload contains fields outside the approved contract.", error_type="safety")
        key = json_body.get("key")
        if key not in BACKEND_ENV_KEYS:
            raise ProvisioningError("Backend env payload key is not allowlisted.", error_type="safety")
        required = {"key", "value", "is_runtime", "is_buildtime", "is_preview", "is_literal", "is_multiline"}
        if key == "DATABASE_URL":
            if set(json_body) != required | {"is_shown_once"}:
                raise ProvisioningError("Backend env payload fields do not exactly match the approved secret contract.", error_type="safety")
            if json_body.get("is_shown_once") is not True:
                raise ProvisioningError("Backend env payload secret marker does not match the approved contract.", error_type="safety")
            if not str(json_body.get("value") or "").startswith("postgresql+psycopg://"):
                raise ProvisioningError("Backend env value does not match the bound desired contract.", error_type="safety")
        else:
            if set(json_body) != required:
                raise ProvisioningError("Backend env payload fields do not exactly match the approved runtime contract.", error_type="safety")
        expected_flags = {"is_runtime": True, "is_buildtime": False, "is_preview": False, "is_literal": True, "is_multiline": False}
        for flag, expected in expected_flags.items():
            if json_body.get(flag) is not expected:
                raise ProvisioningError(f"Backend env payload flag {flag} does not match the approved contract.", error_type="safety")
        expected_payload = self.expected_backend_env_contract.get(str(key))
        if expected_payload is None or json_body != expected_payload:
            raise ProvisioningError("Backend env value does not match the bound desired contract.", error_type="safety")

    @classmethod
    def from_env(cls, mode: ExecutionMode | None = None) -> "CoolifyClient":
        return cls(os.environ.get("COOLIFY_BASE_URL", ""), os.environ.get("COOLIFY_API_TOKEN", ""), mode=mode)

    def _check_request(self, method: str, path: str, *, post_kind: str | None = None, json_body: dict[str, Any] | None = None) -> None:
        method = method.upper()
        if not path.startswith("/"):
            raise ProvisioningError("Coolify API path must start with '/'.")
        if path == "/deploy" and not (method == "POST" and post_kind in {"backend_deploy", "frontend_deploy"}):
            raise ProvisioningError("GET /deploy and non-allowlisted deployment actions are forbidden.", error_type="safety", method=method, path=path)
        if FORBIDDEN_ENDPOINT_RE.search(path) or ("/start" in path and not (method == "POST" and post_kind == "postgresql_start" and re.fullmatch(r"/databases/[^/]+/start", path))):
            raise ProvisioningError(f"Lifecycle/deployment endpoint is forbidden in this stage: {path}", error_type="safety", method=method, path=path)
        if method in {"PUT", "DELETE"}:
            raise ProvisioningError(f"Refusing non-read-only Coolify method {method}. Method {method} is always forbidden in this stage.", error_type="safety", method=method, path=path)
        if method == "PATCH":
            if post_kind == "backend_env_update" and getattr(self.mode, "backend_env_writes_enabled", False):
                if not self._backend_env_path_matches_binding(path):
                    raise ProvisioningError("PATCH endpoint is limited to env vars for the verified Backend Application UUID binding.", error_type="safety", method=method, path=path)
                self._validate_backend_env_payload(json_body)
            elif post_kind == "frontend_env_update" and getattr(self.mode, "frontend_env_writes_enabled", False):
                if not self._frontend_env_path_matches_binding(path):
                    raise ProvisioningError("PATCH endpoint is limited to env vars for the verified Frontend Application UUID binding.", error_type="safety", method=method, path=path)
                self._validate_frontend_env_payload(json_body)
            else:
                raise ProvisioningError("Refusing non-read-only Coolify method PATCH. PATCH is allowed only for verified Application env variables after env gates.", error_type="safety", method=method, path=path)
        if method == "POST":
            self.mode.assert_write_gates()
            if path.startswith("/applications") and post_kind not in {"backend_application_create", "backend_env_create", "frontend_application_create", "frontend_env_create"}:
                raise ProvisioningError("Only Backend/Frontend Application creation/env writes are allowlisted for applications in Stage 6.", error_type="safety", method=method, path=path)
            if post_kind == "backend_application_create" and not getattr(self.mode, "backend_application_writes_enabled", False):
                raise ProvisioningError("Backend Application creation requires COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES=true and exact --confirm-backend-application.", error_type="safety", method=method, path=path)
            if post_kind == "backend_env_create" and not getattr(self.mode, "backend_env_writes_enabled", False):
                raise ProvisioningError("Backend env writes require COOLIFY_ALLOW_BACKEND_ENV_WRITES=true and exact --confirm-backend-environment.", error_type="safety", method=method, path=path)
            if post_kind == "frontend_application_create" and not getattr(self.mode, "frontend_application_writes_enabled", False):
                raise ProvisioningError("Frontend Application creation requires COOLIFY_ALLOW_FRONTEND_APPLICATION_WRITES=true and exact --confirm-frontend-application.", error_type="safety", method=method, path=path)
            if post_kind == "frontend_env_create" and not getattr(self.mode, "frontend_env_writes_enabled", False):
                raise ProvisioningError("Frontend env writes require COOLIFY_ALLOW_FRONTEND_ENV_WRITES=true and exact --confirm-frontend-environment.", error_type="safety", method=method, path=path)
            if post_kind == "backend_deploy":
                if path != "/deploy":
                    raise ProvisioningError("Backend deployment POST endpoint must be exactly /deploy.", error_type="safety", method=method, path=path)
                self._validate_backend_deploy_payload(json_body)
            if post_kind == "frontend_deploy":
                if path != "/deploy":
                    raise ProvisioningError("Frontend deployment POST endpoint must be exactly /deploy.", error_type="safety", method=method, path=path)
                self._validate_frontend_deploy_payload(json_body)
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
            if post_kind == "frontend_application_create":
                if path != POST_FRONTEND_APPLICATION_CREATE:
                    raise ProvisioningError("Frontend Application creation POST endpoint is not allowlisted.", error_type="safety", method=method, path=path)
                self._validate_frontend_application_payload(json_body)
            if post_kind == "frontend_env_create":
                if not self._frontend_env_path_matches_binding(path):
                    raise ProvisioningError("Frontend env create endpoint is limited to the verified Frontend Application UUID binding.", error_type="safety", method=method, path=path)
                self._validate_frontend_env_payload(json_body)
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
            "User-Agent": "goodmorning-factory-product-prodelopment-provisioner",
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
                response_status_value = getattr(response, "status", None)
                if response_status_value is None:
                    response_status_value = response.getcode()
                response_status = int(response_status_value)
                write_2xx = method in WRITE_METHODS and 200 <= response_status <= 299
                raw = response.read().decode("utf-8")
                try:
                    data = json.loads(raw) if raw else None
                except json.JSONDecodeError as exc:
                    raise ProvisioningError(
                        f"Malformed JSON response for {method} {path}: {exc}",
                        error_type="coolify_api",
                        code=2,
                        http_status=response_status,
                        method=method,
                        path=path,
                        write_performed=write_2xx,
                        write_outcome_unknown=write_2xx,
                        request_attempted=True,
                    ) from exc
                if allowed_statuses is not None and response_status not in allowed_statuses:
                    raise ProvisioningError(
                        f"Unexpected Coolify status {response_status} for {method} {path}.",
                        error_type="coolify_api",
                        code=2,
                        http_status=response_status,
                        method=method,
                        path=path,
                        write_performed=write_2xx,
                        write_outcome_unknown=write_2xx,
                        request_attempted=True,
                    )
                return Response(response_status, data, dict(response.headers))
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

    def create_frontend_application(self, payload: dict[str, Any]) -> Response:
        return self.request("POST", POST_FRONTEND_APPLICATION_CREATE, json_body=payload, allowed_statuses={200, 201, 202}, post_kind="frontend_application_create")

    def create_frontend_env(self, uuid: str, payload: dict[str, Any]) -> Response:
        return self.request("POST", f"/applications/{urllib.parse.quote(str(uuid), safe='')}/envs", json_body=payload, allowed_statuses={200, 201, 202}, post_kind="frontend_env_create")

    def update_frontend_env(self, uuid: str, payload: dict[str, Any]) -> Response:
        return self.request("PATCH", f"/applications/{urllib.parse.quote(str(uuid), safe='')}/envs", json_body=payload, allowed_statuses={200, 201, 202}, post_kind="frontend_env_update")

    def deploy_backend(self, uuid: str) -> Response:
        return self.request("POST", "/deploy", json_body={"uuid": str(uuid), "force": False}, allowed_statuses={200}, post_kind="backend_deploy")

    def deploy_frontend(self, uuid: str) -> Response:
        return self.request("POST", "/deploy", json_body={"uuid": str(uuid), "force": False}, allowed_statuses={200}, post_kind="frontend_deploy")


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
        raise ProvisioningError("Only branch 'main' is supported by this stage.")
    if args.environment != ALLOWED_ENVIRONMENT:
        raise ProvisioningError("Only environment 'production' is supported by this stage.")
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
        "product": args.product,
        "project_uuid": project_summary.get("uuid") or project_summary.get("id"),
        "environment_uuid": environment_summary.get("uuid") or environment_summary.get("id"),
        "environment_name": args.environment,
        "server_uuid": server_summary.get("uuid") or server_summary.get("id"),
        "github_app_uuid": github_app_summary.get("uuid") or github_app_summary.get("id"),
        "github_app_id": github_app_summary.get("id"),
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
        f"{product}-postgres-prod": {"kind": "database", "type": "postgresql"},
        f"{product}-api-prod": {
            "kind": "application",
            "git_repository": repository,
            "git_branch": "main",
            "build_pack": "dockerfile",
            "base_directory": "/backend",
            "dockerfile_location": "/Dockerfile",
            "ports_exposes": "8000",
            "domains": f"https://api.{product}.goodmorning.mx",
            "github_app_name": github_app_name,
        },
        f"{product}-frontend-prod": {
            "kind": "application",
            "git_repository": repository,
            "git_branch": "main",
            "build_pack": "dockerfile",
            "base_directory": "/frontend",
            "dockerfile_location": "/Dockerfile",
            "ports_exposes": "80",
            "domains": f"https://{product}.goodmorning.mx",
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


APPLICATION_SCOPE_FALLBACK_SOURCE = "coolify_application_detail_exact_identity_and_server"
APPLICATION_SCOPE_FALLBACK_REASON = "public_api_omits_project_and_environment_uuid"


def application_scope_identity(item: dict[str, Any], context: dict[str, Any]) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Verify Application scope when Coolify omits both project and environment UUIDs.

    The fallback is allowed only when the server UUID matches. Repository,
    source, build and routing identity remain independently mandatory in the
    application compatibility checks.
    """

    scope_status, details = resource_scope(item, context)
    missing = {detail["field"] for detail in details if detail["status"] == "unverified"}
    meta = {
        "project_scope_api_present": "project_uuid" not in missing,
        "environment_scope_api_present": "environment_uuid" not in missing,
        "server_scope_api_matched": any(
            detail["field"] == "server_uuid" and detail["status"] == "matched"
            for detail in details
        ),
    }
    if scope_status != "scope_unverified":
        return scope_status, details, {**meta, "scope_api_verified": scope_status == "in_scope"}
    if {"project_uuid", "environment_uuid"}.issubset(missing) and meta["server_scope_api_matched"]:
        fallback_details = [
            {**detail, "status": "api_omitted"}
            if detail["field"] in {"project_uuid", "environment_uuid"} and detail["status"] == "unverified"
            else detail
            for detail in details
        ]
        return "in_scope", fallback_details, {
            **meta,
            "scope_api_verified": False,
            "scope_verification_source": APPLICATION_SCOPE_FALLBACK_SOURCE,
            "scope_verification_reason": APPLICATION_SCOPE_FALLBACK_REASON,
        }
    return scope_status, details, {**meta, "scope_api_verified": False}


def candidate_identifier(item: dict[str, Any]) -> Any:
    return field(item, "uuid", "id")


def is_expected_or_related_candidate(item: dict[str, Any], product: str, repository: str) -> bool:
    name = str(field(item, "name") or "")
    if name in expected_resource_specs(product):
        return True
    if name == f"{product}-prod":
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
    """Classify real Compose resources without trusting dormant Coolify fields.

    Coolify can include docker_compose fields for every application, including
    Dockerfile applications. When a build pack is present it is authoritative.
    """

    build_pack = str(field(item, "build_pack", "buildpack") or "").strip().lower()
    normalized_build_pack = re.sub(r"[^a-z0-9]", "", build_pack)
    if normalized_build_pack:
        return normalized_build_pack in {"compose", "dockercompose"}

    explicit_type = " ".join(
        str(field(item, name) or "") for name in ("type", "kind", "resource_type")
    ).lower()
    if "compose" in explicit_type:
        return True

    compose_raw = field(item, "docker_compose_raw")
    if compose_raw not in (None, "", {}, []):
        return True

    compose_location = str(field(item, "docker_compose_location") or "").strip()
    return bool(compose_location)


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
        "fqdn",
        "destination_uuid",
        "github_app_uuid",
        "source_uuid",
        "source_id",
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


def github_app_identifier_matches(actual: Any, context: dict[str, Any]) -> bool:
    accepted = {
        str(value)
        for value in (context.get("github_app_uuid"), context.get("github_app_id"))
        if value not in (None, "")
    }
    return str(actual) in accepted



def application_differences(item: dict[str, Any], expected: dict[str, Any], context: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    field_paths = {
        "git_repository": ("git_repository", "repository", "source.git_repository"),
        "git_branch": ("git_branch", "branch", "source.git_branch"),
        "build_pack": ("build_pack", "buildpack"),
        "base_directory": ("base_directory",),
        "dockerfile_location": ("dockerfile_location", "dockerfile"),
        "ports_exposes": ("ports_exposes",),
        "domains": ("domains", "domain", "fqdn"),
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
    source_uuid = nested_field(item, "github_app_uuid", "source_uuid", "source_id", "source.uuid", "github_app.uuid")
    if source_uuid in (None, ""):
        unverified.append({"field": "github_app_uuid", "expected": context.get("github_app_uuid")})
    elif not github_app_identifier_matches(source_uuid, context):
        differences.append({"field": "github_app_uuid", "expected": context.get("github_app_uuid"), "actual": source_uuid})
    return differences, unverified


def classify_expected_resource(name: str, expected: dict[str, Any], matches: list[dict[str, Any]], context: dict[str, Any]) -> dict[str, Any]:
    if not matches:
        return {"name": name, "status": "absent"}

    if expected["kind"] == "database":
        reports = []
        scoped = []
        for item in matches:
            classification = postgres_classification_from_detail(item, {**context, "name": name})
            scope_status = "in_scope" if classification["status"] not in {"out_of_scope", "scope_unverified"} else classification["status"]
            report = {
                "resource": operational_summary(item),
                "scope": scope_status,
                "scope_details": classification.get("scope_details", []),
            }
            for key in (
                "uuid",
                "scope_api_verified",
                "project_scope_api_present",
                "environment_scope_api_present",
                "server_scope_api_matched",
                "scope_verification_source",
                "scope_verification_reason",
            ):
                if key in classification:
                    report[key] = classification[key]
            if scope_status != "in_scope":
                report["status"] = scope_status
            else:
                scoped.append((item, classification))
            reports.append(report)
        if not scoped:
            status = "out_of_scope" if all(report.get("status") == "out_of_scope" for report in reports) else "scope_unverified"
            return {"name": name, "status": status, "matches": reports}
        if len(scoped) > 1:
            return {"name": name, "status": "duplicate", "matches": [operational_summary(item) for item, _ in scoped]}
        return {**scoped[0][1], "matches": reports}

    scoped = []
    reports = []
    for item in matches:
        scope_status, scope_details, scope_meta = application_scope_identity(item, context)
        report = {"resource": operational_summary(item), "scope": scope_status, "scope_details": scope_details, **scope_meta}
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

    legacy_name = f"{product}-prod"
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
            "git_branch": "main",
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
        "environment_name": "production",
        "github_app_uuid": bindings.get("github_app_uuid"),
    }


def build_plan(args: argparse.Namespace, discovery: dict[str, Any]) -> dict[str, Any]:
    bindings = discovery.get("bindings", {}) if isinstance(discovery, dict) else {}
    api_domain = f"https://api.{args.product}.goodmorning.mx"
    frontend_domain = f"https://{args.product}.goodmorning.mx"
    postgres_desired_state = {
        "name": f"{args.product}-postgres-prod",
        "kind": "database",
        "type": "postgresql",
        "version": POSTGRES_VERSION,
        "is_public": False,
        "domain": None,
    }
    backend_desired_state = application_desired_state(
        name=f"{args.product}-api-prod",
        repository=args.repository,
        github_app_name=args.github_app,
        base_directory="/backend",
        ports_exposes="8000",
        domains=api_domain,
    )
    frontend_desired_state = application_desired_state(
        name=f"{args.product}-frontend-prod",
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
                    "environment_name": "production",
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
        os.environ.get("COOLIFY_ALLOW_FRONTEND_APPLICATION_WRITES") == "true",
        getattr(args, "confirm_frontend_application", None),
        os.environ.get("COOLIFY_ALLOW_FRONTEND_ENV_WRITES") == "true",
        getattr(args, "confirm_frontend_environment", None),
        bool(getattr(args, "deploy_backend", False)),
        bool(getattr(args, "deploy_frontend", False)),
        os.environ.get("COOLIFY_ALLOW_DEPLOYMENTS") == "true",
        os.environ.get("COOLIFY_ALLOW_BACKEND_DEPLOYMENT") == "true",
        getattr(args, "confirm_backend_deployment", None),
        os.environ.get("COOLIFY_ALLOW_FRONTEND_DEPLOYMENT") == "true",
        getattr(args, "confirm_frontend_deployment", None),
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


def is_real_identifier(value: Any) -> bool:
    text = str(value or "").strip()
    return bool(text) and not text.startswith("${")


def postgres_identity_differences(item: dict[str, Any], product: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    differences, unverified = postgres_differences(item)
    expected_sql = deterministic_sql_names(product)
    for key, expected in expected_sql.items():
        actual = nested_field(item, key)
        if actual in (None, ""):
            unverified.append({"field": key, "expected": expected})
        elif str(actual) != expected:
            differences.append({"field": key, "expected": expected, "actual": actual})
    description = nested_field(item, "description")
    if description in (None, ""):
        unverified.append({"field": "description", "expected": "factory_managed_marker"})
    elif str(description) != FACTORY_POSTGRES_DESCRIPTION:
        differences.append({"field": "description", "expected": "factory_managed_marker", "actual": "different"})
    return differences, unverified


def postgresql_scope_identity(item: dict[str, Any], context: dict[str, Any]) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Conservative PostgreSQL-only scope classification for Coolify v4.1.2.

    If Coolify exposes project/environment UUIDs, they must match exactly. If
    both are omitted, a database may still be reused only through the separate
    Factory-managed identity checks in postgres_identity_differences().
    """

    scope_status, details = resource_scope(item, context)
    missing = {detail["field"] for detail in details if detail["status"] == "unverified"}
    meta = {
        "project_scope_api_present": "project_uuid" not in missing,
        "environment_scope_api_present": "environment_uuid" not in missing,
        "server_scope_api_matched": any(detail["field"] == "server_uuid" and detail["status"] == "matched" for detail in details),
    }
    if scope_status != "scope_unverified":
        return scope_status, details, {**meta, "scope_api_verified": scope_status == "in_scope"}
    if {"project_uuid", "environment_uuid"}.issubset(missing) and "server_uuid" not in missing:
        fallback_details = [
            {**detail, "status": "api_omitted"}
            if detail["field"] in {"project_uuid", "environment_uuid"} and detail["status"] == "unverified"
            else detail
            for detail in details
        ]
        return "in_scope", fallback_details, {
            **meta,
            "scope_api_verified": False,
            "scope_verification_source": POSTGRES_SCOPE_FALLBACK_SOURCE,
            "scope_verification_reason": POSTGRES_SCOPE_FALLBACK_REASON,
        }
    return scope_status, details, {**meta, "scope_api_verified": False}


def postgresql_provenance(expected_name: str, returned_uuid: Any, project: dict[str, Any], environment: dict[str, Any], server: dict[str, Any], *, request_attempted: bool, write_performed: bool) -> dict[str, Any]:
    return {
        "expected_name": expected_name,
        "returned_uuid": returned_uuid,
        "project_uuid": project.get("uuid"),
        "environment_name": environment.get("name"),
        "environment_uuid": environment.get("uuid"),
        "server_uuid": server.get("uuid"),
        "request_attempted": request_attempted,
        "write_performed": write_performed,
    }


def deterministic_sql_names(product: str) -> dict[str, str]:
    safe = re.sub(r"[^a-z0-9_]", "_", product.replace("-", "_"))
    return {"postgres_user": f"{safe}_dev_user", "postgres_db": f"{safe}_dev"}


def generate_postgres_password() -> str:
    return secrets.token_urlsafe(36)


def project_create_payload(name: str) -> dict[str, Any]:
    return {"name": name}


def environment_create_payload() -> dict[str, Any]:
    return {"name": "production"}


def postgresql_create_payload(args: argparse.Namespace, project_uuid: str, server_uuid: str, password: str) -> dict[str, Any]:
    names = deterministic_sql_names(args.product)
    return {
        "server_uuid": server_uuid,
        "project_uuid": project_uuid,
        "environment_name": "production",
        "name": f"{args.product}-postgres-prod",
        "description": FACTORY_POSTGRES_DESCRIPTION,
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
    matches = exact_matches(project_environments(client, project_uuid), "production")
    if len(matches) != 1:
        return None
    identifier = field(matches[0], "uuid", "id")
    if not identifier:
        return None
    return {"status": "exists", "name": "production", "uuid": identifier, "project_uuid": project_uuid}


def environment_from_create_response(data: Any, project_uuid: str) -> dict[str, Any] | None:
    item = data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data
    if not isinstance(item, dict):
        return None
    if field(item, "name") != "production":
        return None
    identifier = field(item, "uuid", "id")
    if not identifier:
        return None
    return {"status": "created", "name": "production", "uuid": identifier, "project_uuid": project_uuid}


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
        return {"status": "would_create", "name": "production", "uuid": "${environment.uuid}", "project_uuid": project_uuid}
    verified = verified_environment_from_list(client, str(project_uuid))
    if verified:
        actions.append(ActionRecord("environment", "reuse", "exists", verified=True).to_dict())
        return verified
    if not mode.is_apply:
        actions.append(ActionRecord("environment", "plan_create", "would_create").to_dict())
        return {"status": "would_create", "name": "production", "uuid": "${environment.uuid}", "project_uuid": project_uuid}
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
    scope_status, scope_details, scope_meta = postgresql_scope_identity(item, context)
    if scope_status != "in_scope":
        return {"status": scope_status, "name": name, "uuid": field(item, "uuid", "id"), "scope_details": scope_details, **scope_meta}
    if scope_meta.get("scope_api_verified") is False:
        differences, unverified = postgres_identity_differences(item, context.get("product", ""))
    else:
        differences, unverified = postgres_differences(item)
    base = {
        "name": name,
        "uuid": field(item, "uuid", "id"),
        "internal_db_url_available": bool(nested_field(item, "internal_db_url")),
        "credentials_configured": bool(nested_field(item, "postgres_user", "postgres_db")),
        "scope_details": scope_details,
        **scope_meta,
    }
    if not is_real_identifier(base["uuid"]):
        unverified.append({"field": "uuid", "expected": "real_uuid"})
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
    name = f"{args.product}-postgres-prod"
    context = {"name": name, "product": args.product, "project_uuid": project["uuid"], "environment_uuid": environment["uuid"], "server_uuid": server["uuid"]}
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
            return {"status": "created_unverified", "name": name, "write_performed": exc.write_performed, "write_outcome_unknown": True, "request_attempted": True, "internal_db_url_available": False, "credentials_configured": False}
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
    provenance = postgresql_provenance(name, uuid, project, environment, server, request_attempted=True, write_performed=True)
    try:
        detail = hydrate_database_by_uuid(client, uuid, item if isinstance(item, dict) else {"uuid": uuid, "name": name})
    except ProvisioningError as exc:
        actions.append(ActionRecord("postgresql", "create", "created_unverified", request_attempted=True, write_performed=True).to_dict())
        return {"status": "created_unverified", "name": name, "uuid": uuid, "write_performed": True, "verification_error": exc.message, "post_create_provenance": provenance, "internal_db_url_available": False, "credentials_configured": True}
    if str(field(detail, "uuid", "id") or "") != str(uuid):
        actions.append(ActionRecord("postgresql", "create", "created_unverified", request_attempted=True, write_performed=True).to_dict())
        return {"status": "created_unverified", "name": name, "uuid": uuid, "write_performed": True, "post_create_provenance": provenance, "verification_error": "PostgreSQL detail UUID contradicted create response UUID.", "internal_db_url_available": False, "credentials_configured": True}
    result = postgres_classification_from_detail(detail, context)
    if result["status"] == "exists":
        result["status"] = "created"
        result["credentials_configured"] = True
        result["post_create_provenance"] = provenance
        actions.append(ActionRecord("postgresql", "create", "created", request_attempted=True, write_performed=True, verified=True).to_dict())
        return result
    actions.append(ActionRecord("postgresql", "create", "created_unverified", request_attempted=True, write_performed=True).to_dict())
    return {**result, "status": "created_unverified" if result["status"] in {"compatibility_unverified", "scope_unverified"} else result["status"], "write_performed": True, "credentials_configured": True, "post_create_provenance": provenance}



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


def database_credentials_available(item: dict[str, Any]) -> bool:
    return all(isinstance(nested_field(item, key), str) and bool(str(nested_field(item, key))) for key in ("postgres_user", "postgres_password", "postgres_db"))


def derived_internal_postgres_url(item: dict[str, Any], expected_uuid: Any) -> tuple[str | None, str | None]:
    raw = nested_field(item, "internal_db_url")
    if isinstance(raw, str) and raw.strip():
        prepare_psycopg_database_url(raw)
        return raw, "api_field"
    uuid = field(item, "uuid", "id")
    if not is_real_identifier(uuid) or str(uuid) != str(expected_uuid):
        return None, None
    user = nested_field(item, "postgres_user")
    password = nested_field(item, "postgres_password")
    database = nested_field(item, "postgres_db")
    if not all(isinstance(value, str) and value != "" for value in (user, password, database)):
        return None, None
    encoded_user = urllib.parse.quote(user, safe="")
    encoded_password = urllib.parse.quote(password, safe="")
    encoded_database = urllib.parse.quote(database, safe="")
    return f"postgresql://{encoded_user}:{encoded_password}@{uuid}:5432/{encoded_database}", "verified_components"


def verify_internal_db_url_flags(item: dict[str, Any], expected_uuid: Any | None = None) -> dict[str, Any]:
    expected_uuid = expected_uuid if expected_uuid is not None else field(item, "uuid", "id")
    credentials = database_credentials_available(item)
    available = False
    ready = False
    source = None
    try:
        url, source = derived_internal_postgres_url(item, expected_uuid)
        available = bool(url)
        if url:
            prepare_psycopg_database_url(url)
            ready = True
    except ProvisioningError:
        available = isinstance(nested_field(item, "internal_db_url"), str) and bool(str(nested_field(item, "internal_db_url")).strip())
        ready = False
        source = "api_field" if available else None
    return {"database_credentials_available": credentials, "internal_db_url_available": available, "database_url_driver_ready": ready, "internal_db_url_source": source}


def postgresql_start_preconditions(args: argparse.Namespace, postgres: dict[str, Any]) -> list[dict[str, Any]]:
    blockers = []
    if postgres.get("name") != f"{args.product}-postgres-prod" or not is_real_identifier(postgres.get("uuid")):
        blockers.append({"reason": "identity_unverified"})
    if postgres.get("status") not in {"exists", "created"}:
        blockers.append({"reason": f"postgresql_status_{postgres.get('status')}"})
    if postgres.get("write_outcome_unknown"):
        blockers.append({"reason": "prior_write_outcome_unknown"})
    if postgres.get("scope_api_verified") is not True and postgres.get("scope_verification_source") != POSTGRES_SCOPE_FALLBACK_SOURCE:
        blockers.append({"reason": "scope_unverified"})
    if postgres.get("server_scope_api_matched") is not True:
        blockers.append({"reason": "server_scope_unverified"})
    if postgres.get("differences") or postgres.get("unverified_fields") or postgres.get("blockers"):
        blockers.append({"reason": "identity_or_compatibility_unverified"})
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
            return readiness_result("healthy", uuid, poll_attempts=attempts, **verify_internal_db_url_flags(item, uuid))
        if classified == "unhealthy":
            return readiness_result("unhealthy", uuid, poll_attempts=attempts, **verify_internal_db_url_flags(item, uuid))
        if classified == "readiness_unverified":
            return readiness_result("readiness_unverified", uuid, poll_attempts=attempts, **verify_internal_db_url_flags(item, uuid))
        if attempt < max_attempts - 1:
            sleeper(interval)
    return readiness_result("timeout", uuid, poll_attempts=attempts, **verify_internal_db_url_flags(last_item, uuid))


def ensure_postgresql_ready(client: CoolifyClient, args: argparse.Namespace, postgres: dict[str, Any], mode: ExecutionMode, actions: list[dict[str, Any]], *, sleeper: Callable[[float], None] | None = None) -> dict[str, Any]:
    uuid = postgres.get("uuid")
    if not mode.is_apply and postgres.get("status") == "would_create" and postgres.get("name") == f"{args.product}-postgres-prod" and uuid:
        actions.append(ActionRecord("postgresql", "plan_start", "would_start", verified=False).to_dict())
        return readiness_result("would_start", uuid, reason="postgresql_creation_planned")
    blockers = postgresql_start_preconditions(args, postgres)
    if blockers:
        return readiness_result("blocked", uuid)
    try:
        detail = hydrate_database_by_uuid(client, uuid, postgres)
    except ProvisioningError:
        if mode.is_apply:
            return readiness_result("readiness_unverified", uuid)
        detail = postgres
    raw_state = str(database_raw_status(detail) or "").strip().lower()
    state = "stopped" if raw_state == "exited:unhealthy" else classify_database_readiness_status(raw_state)
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
            reason = "verified_factory_postgresql_requires_start" if raw_state == "exited:unhealthy" else None
            return readiness_result("would_start", uuid, **({"reason": reason} if reason else {}), **flags)
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
                    return poll_postgresql_readiness(client, str(uuid), timeout=getattr(args, "postgres_readiness_timeout", 300.0), interval=getattr(args, "postgres_readiness_interval", 5.0), sleeper=sleeper) if recovered_state == "starting" else readiness_result("healthy", uuid, poll_attempts=1, **verify_internal_db_url_flags(rediscovered, uuid))
                return readiness_result("blocked", uuid, start_requested=True, request_attempted=True, **flags)
            raise
        actions.append(ActionRecord("postgresql", "start", "start_accepted", request_attempted=True, write_performed=True, verified=False).to_dict())
        polled = poll_postgresql_readiness(client, str(uuid), timeout=getattr(args, "postgres_readiness_timeout", 300.0), interval=getattr(args, "postgres_readiness_interval", 5.0), sleeper=sleeper)
        if polled.get("status") == "healthy":
            actions.append(ActionRecord("postgresql", "readiness", "healthy", request_attempted=False, write_performed=False, verified=True).to_dict())
        return {**polled, "start_requested": True, "request_attempted": True, "write_performed": True}
    return readiness_result("readiness_unverified", uuid, **flags)


def backend_application_name(product: str) -> str:
    return f"{product}-api-prod"


def api_domain(product: str) -> str:
    return f"https://api.{product}.goodmorning.mx"


def frontend_domain(product: str) -> str:
    return f"https://{product}.goodmorning.mx"


def validate_coolify_destination_identifier(value: Any) -> str:
    if value is None:
        raise ProvisioningError("--destination-uuid is required for Stage 9 Application planning and creation.", error_type="validation", code=2)
    if not isinstance(value, str):
        raise ProvisioningError("--destination-uuid must be exactly one opaque Coolify identifier string.", error_type="validation", code=2)
    if value != value.strip() or not value:
        raise ProvisioningError("--destination-uuid must not be empty or padded with whitespace.", error_type="validation", code=2)
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ProvisioningError("--destination-uuid must not contain control characters.", error_type="validation", code=2)
    if any(char.isspace() for char in value):
        raise ProvisioningError("--destination-uuid must not contain whitespace.", error_type="validation", code=2)
    if len(value) > COOLIFY_IDENTIFIER_MAX_LENGTH:
        raise ProvisioningError("--destination-uuid exceeds the defensive Coolify identifier length limit.", error_type="validation", code=2)
    if "," in value or value[0] in "[{" or value[-1] in "]}":
        raise ProvisioningError("--destination-uuid must be one identifier, not a list or JSON value.", error_type="validation", code=2)
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme or parsed.netloc or any(marker in value for marker in ("/", "?", "#", ":", "@")):
        raise ProvisioningError("--destination-uuid must be an opaque identifier, not a URL, path, query, fragment, or credential-bearing value.", error_type="validation", code=2)
    if not COOLIFY_IDENTIFIER_RE.fullmatch(value):
        raise ProvisioningError("--destination-uuid must be an opaque Coolify identifier using only safe lowercase URL identifier characters.", error_type="validation", code=2)
    return value


def select_destination(server: dict[str, Any], args: argparse.Namespace, blockers: list[Any]) -> dict[str, Any] | None:
    selected = validate_coolify_destination_identifier(getattr(args, "destination_uuid", None))
    destinations = item_list(server.get("destinations") if isinstance(server, dict) else [])
    if destinations:
        matches = [d for d in destinations if str(field(d, "uuid", "id") or "") == selected]
        if len(matches) != 1:
            blockers.append({"resource": "destination", "status": "blocked", "reason": "destination_not_on_selected_server"})
            return None
        selected_summary = summary(matches[0], "name", "uuid", "id", "network")
        selected_summary.update({"selection_source": "explicit_operator_input", "destination_uuid_present": True, "destination_scope_api_verified": True})
        return selected_summary
    return {
        "uuid": selected,
        "selection_source": "explicit_operator_input",
        "destination_uuid_present": True,
        "destination_scope_api_verified": False,
        "scope_verification_reason": "public_api_does_not_expose_destination_listing",
    }


def backend_create_payload(args: argparse.Namespace, bindings: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": backend_application_name(args.product),
        "project_uuid": bindings.get("project_uuid"),
        "environment_uuid": bindings.get("environment_uuid"),
        "environment_name": "production",
        "server_uuid": bindings.get("server_uuid"),
        "destination_uuid": bindings.get("destination_uuid"),
        "github_app_uuid": bindings.get("github_app_uuid"),
        "git_repository": normalize_repository(args.repository),
        "git_branch": "main",
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
        "ports_exposes": ("ports_exposes",), "domains": ("domains", "domain", "fqdn"), "github_app_uuid": ("github_app_uuid", "source_uuid", "source_id", "source.uuid", "github_app.uuid"),
        "destination_uuid": ("destination_uuid", "destination.uuid", "destination.id"),
    }
    diffs=[]; unver=[]
    for key, ps in paths.items():
        actual = nested_field(item, *ps); exp = expected.get(key)
        if actual in (None, ""):
            unver.append({"field": key, "expected": exp})
        elif key == "github_app_uuid":
            if not github_app_identifier_matches(actual, context):
                diffs.append({"field": key, "expected": exp, "actual": actual})
        elif key == "domains" and ":8000" in normalize_domain(actual):
            diffs.append({"field": key, "expected": exp, "actual": "contains_port"})
        elif not normalized_equal(key, actual, exp):
            diffs.append({"field": key, "expected": exp, "actual": actual})
    for key in ("instant_deploy", "autogenerate_domain"):
        actual = nested_field(item, key)
        if actual in (None, ""):
            continue
        if bool_value(actual) is not False:
            diffs.append({"field": key, "expected": False, "actual": actual})
    auto_deploy = nested_field(item, "is_auto_deploy_enabled")
    if auto_deploy not in (None, "") and bool_value(auto_deploy) is not False:
        diffs.append({"field": "is_auto_deploy_enabled", "expected": False, "actual": auto_deploy})
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
        scope, details, scope_meta = application_scope_identity(item, context)
        reports.append({"resource": operational_summary(item), "scope": scope, "scope_details": details, **scope_meta})
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
    url, _source = derived_internal_postgres_url(detail, uuid)
    if not url: return None
    return prepare_psycopg_database_url(url)


def postgresql_backend_plan_precondition(args: argparse.Namespace, postgres: dict[str, Any], readiness: dict[str, Any]) -> tuple[bool, list[dict[str, Any]]]:
    blockers = []
    expected_name = f"{args.product}-postgres-prod"
    pg_status = postgres.get("status")
    ready_status = readiness.get("status")
    if postgres.get("name") != expected_name or not postgres.get("uuid"):
        blockers.append({"resource": "postgresql", "reason": "identity_or_compatibility_unverified"})
    if pg_status == "would_create":
        if ready_status != "would_start" or readiness.get("reason") != "postgresql_creation_planned":
            blockers.append({"resource": "postgresql_readiness", "reason": "postgresql_creation_plan_unverified"})
    elif pg_status in {"exists", "created"}:
        if ready_status in {"unhealthy", "readiness_unverified", "timeout", "partial", "blocked"}:
            blockers.append({"resource": "postgresql_readiness", "reason": ready_status})
    else:
        blockers.append({"resource": "postgresql", "reason": f"postgresql_status_{pg_status}"})
    if postgres.get("write_outcome_unknown") or readiness.get("write_outcome_unknown"):
        blockers.append({"resource": "postgresql", "reason": "write_outcome_unknown"})
    if postgres.get("blockers") or readiness.get("blockers"):
        blockers.append({"resource": "postgresql", "reason": "blockers_present"})
    if not all(postgres.get(key) for key in ("uuid",)):
        blockers.append({"resource": "postgresql", "reason": "identity_or_compatibility_unverified"})
    return (not blockers), blockers


def postgres_backend_live_precondition(client: CoolifyClient, args: argparse.Namespace, postgres: dict[str, Any], readiness: dict[str, Any]) -> tuple[bool, list[dict[str, Any]], str | None]:
    blockers=[]; url=None
    if postgres.get("name") != f"{args.product}-postgres-prod" or postgres.get("status") not in {"exists","created"} or not postgres.get("uuid"):
        blockers.append({"resource":"postgresql","reason":"identity_or_compatibility_unverified"})
    if readiness.get("status") not in {"healthy","already_healthy"}:
        blockers.append({"resource":"postgresql","reason":"not_healthy"})
    if not readiness.get("internal_db_url_available"):
        blockers.append({"resource":"postgresql","reason":"internal_db_url_unavailable"})
    if not readiness.get("database_url_driver_ready"):
        blockers.append({"resource":"postgresql","reason":"database_url_driver_not_ready"})
    if readiness.get("write_outcome_unknown") or postgres.get("write_outcome_unknown"):
        blockers.append({"resource":"postgresql","reason":"write_outcome_unknown"})
    if not blockers:
        try: url=postgres_driver_url(client, postgres)
        except ProvisioningError: blockers.append({"resource":"postgresql","reason":"database_url_driver_not_ready"})
    return (not blockers), blockers, url


def postgres_backend_precondition(client: CoolifyClient, args: argparse.Namespace, postgres: dict[str, Any], readiness: dict[str, Any]) -> tuple[bool, list[dict[str, Any]], str | None]:
    return postgres_backend_live_precondition(client, args, postgres, readiness)


def ensure_backend_application(client: CoolifyClient, args: argparse.Namespace, context: dict[str, Any], mode: ExecutionMode, actions: list[dict[str, Any]], precondition_ok: bool) -> dict[str, Any]:
    if hasattr(client, "bind_backend_application_bindings"):
        client.bind_backend_application_bindings(dict(context))
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
        return {"name": backend_application_name(args.product), "status":"would_create", "reason": "postgresql_dependency_planned", "create_payload_preview": backend_payload_preview(backend_create_payload(args,context))}
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



def frontend_application_name(product: str) -> str:
    return f"{product}-frontend-prod"


def validate_clean_public_domain(url: str, expected: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if url != expected or parsed.scheme != "https" or parsed.netloc != urllib.parse.urlparse(expected).netloc or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ProvisioningError("Public domain must be the exact clean HTTPS GoodMorning development domain without port, path, query, or fragment.", error_type="safety")
    if parsed.port is not None or re.search(r":(?:80|3000|8000)$", parsed.netloc):
        raise ProvisioningError("Public domain must not include an internal port.", error_type="safety")


def validate_vite_api_url(url: str, product: str) -> None:
    expected = api_domain(product)
    parsed = urllib.parse.urlparse(url)
    if url != expected or parsed.scheme != "https" or parsed.netloc != f"api.{product}.goodmorning.mx" or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ProvisioningError("VITE_API_URL must be the exact clean Backend API HTTPS domain.", error_type="safety")
    if parsed.hostname in {"localhost", "127.0.0.1"} or re.fullmatch(r"\d+\.\d+\.\d+\.\d+", parsed.hostname or "") or parsed.port is not None:
        raise ProvisioningError("VITE_API_URL must not use localhost, IPs, or ports.", error_type="safety")


def frontend_create_payload(args: argparse.Namespace, bindings: dict[str, Any]) -> dict[str, Any]:
    domain = frontend_domain(args.product)
    validate_clean_public_domain(domain, f"https://{args.product}.goodmorning.mx")
    return {
        "name": frontend_application_name(args.product),
        "project_uuid": bindings.get("project_uuid"),
        "environment_uuid": bindings.get("environment_uuid"),
        "environment_name": "production",
        "server_uuid": bindings.get("server_uuid"),
        "destination_uuid": bindings.get("destination_uuid"),
        "github_app_uuid": bindings.get("github_app_uuid"),
        "git_repository": normalize_repository(args.repository),
        "git_branch": "main",
        "build_pack": "dockerfile",
        "base_directory": "/frontend",
        "dockerfile_location": "/Dockerfile",
        "ports_exposes": "80",
        "domains": domain,
        "instant_deploy": False,
        "is_auto_deploy_enabled": False,
        "autogenerate_domain": False,
        "health_check_enabled": True,
        "health_check_path": "/",
        "health_check_port": "80",
        "health_check_method": "GET",
        "health_check_scheme": "http",
        "health_check_return_code": 200,
    }


def frontend_payload_preview(payload: dict[str, Any]) -> dict[str, Any]:
    return redact(dict(payload))


def frontend_application_differences(item: dict[str, Any], args: argparse.Namespace, context: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    expected = frontend_create_payload(args, context)
    paths = {
        "name": ("name",), "git_repository": ("git_repository", "repository"), "git_branch": ("git_branch", "branch"),
        "build_pack": ("build_pack", "buildpack"), "base_directory": ("base_directory",), "dockerfile_location": ("dockerfile_location", "dockerfile"),
        "ports_exposes": ("ports_exposes",), "domains": ("domains", "domain", "fqdn"), "github_app_uuid": ("github_app_uuid", "source_uuid", "source_id", "source.uuid", "github_app.uuid"),
        "destination_uuid": ("destination_uuid", "destination.uuid", "destination.id"),
    }
    diffs=[]; unver=[]
    for key, ps in paths.items():
        actual = nested_field(item, *ps); exp = expected.get(key)
        if actual in (None, ""):
            unver.append({"field": key, "expected": exp})
        elif key == "github_app_uuid":
            if not github_app_identifier_matches(actual, context):
                diffs.append({"field": key, "expected": exp, "actual": actual})
        elif key == "domains" and re.search(r":(?:80|3000|8000)(?:/|$)", normalize_domain(actual)):
            diffs.append({"field": key, "expected": exp, "actual": "contains_port"})
        elif not normalized_equal(key, actual, exp):
            diffs.append({"field": key, "expected": exp, "actual": actual})
    for key in ("instant_deploy", "autogenerate_domain"):
        actual = nested_field(item, key)
        if actual in (None, ""):
            continue
        if bool_value(actual) is not False:
            diffs.append({"field": key, "expected": False, "actual": actual})
    auto_deploy = nested_field(item, "is_auto_deploy_enabled")
    if auto_deploy not in (None, "") and bool_value(auto_deploy) is not False:
        diffs.append({"field": "is_auto_deploy_enabled", "expected": False, "actual": auto_deploy})
    hc = {"health_check_enabled": True, "health_check_path": "/", "health_check_port": "80", "health_check_method": "GET", "health_check_scheme": "http", "health_check_return_code": 200}
    for key, exp in hc.items():
        actual = nested_field(item, key)
        if actual in (None, ""):
            unver.append({"field": key, "expected": exp})
        elif str(actual).lower() != str(exp).lower():
            diffs.append({"field": key, "expected": exp, "actual": actual})
    return diffs, unver


def classify_frontend_application(matches: list[dict[str, Any]], args: argparse.Namespace, context: dict[str, Any]) -> dict[str, Any]:
    name = frontend_application_name(args.product)
    if not matches: return {"name": name, "status": "absent"}
    scoped=[]; reports=[]
    for item in matches:
        scope, details, scope_meta = application_scope_identity(item, context)
        reports.append({"resource": operational_summary(item), "scope": scope, "scope_details": details, **scope_meta})
        if scope == "in_scope": scoped.append(item)
    if not scoped:
        return {"name": name, "status": "out_of_scope" if all(r["scope"]=="out_of_scope" for r in reports) else "scope_unverified", "matches": reports}
    if len(scoped)>1: return {"name": name, "status": "duplicate", "matches": [operational_summary(i) for i in scoped]}
    item=scoped[0]
    diffs, unver = frontend_application_differences(item,args,context)
    if diffs: return {"name": name, "status": "incompatible", "uuid": field(item,"uuid","id"), "differences": diffs, "matches": reports}
    if unver: return {"name": name, "status": "compatibility_unverified", "uuid": field(item,"uuid","id"), "unverified_fields": unver, "matches": reports}
    return {"name": name, "status": "exists", "uuid": field(item,"uuid","id"), "matches": reports}


def exact_frontend_matches(client: CoolifyClient, args: argparse.Namespace) -> list[dict[str, Any]]:
    name=frontend_application_name(args.product)
    return [i for i in item_list(client.get("/applications")) if str(field(i,"name") or "") == name]


def backend_allows_frontend(backend: dict[str, Any]) -> bool:
    """Return True only for a verified compatible Backend Application.

    This is the single Stage 6 precondition for both Frontend Application
    creation and Frontend VITE_API_URL reconciliation. Backend env status is
    intentionally not part of this check: a hidden DATABASE_URL remains a
    deployment blocker, not a Frontend provisioning blocker.
    """

    return backend.get("status") in {"exists", "created"} and bool(backend.get("uuid"))


def ensure_frontend_application(client: CoolifyClient, args: argparse.Namespace, context: dict[str, Any], mode: ExecutionMode, actions: list[dict[str, Any]], backend: dict[str, Any]) -> dict[str, Any]:
    if hasattr(client, "bind_frontend_application_bindings"):
        client.bind_frontend_application_bindings(dict(context))
    matches=[]
    for m in exact_frontend_matches(client,args):
        uuid=field(m,"uuid","id")
        if uuid:
            try: m=hydrate_application_by_uuid(client,uuid,m)
            except ProvisioningError: pass
        matches.append(m)
    classified=classify_frontend_application(matches,args,context)
    st=classified["status"]
    if st == "exists":
        if hasattr(client, "bind_frontend_application_uuid"):
            client.bind_frontend_application_uuid(classified.get("uuid"), context)
        actions.append(ActionRecord("frontend_application","reuse","exists",verified=True).to_dict()); return classified
    if st in {"duplicate","incompatible","compatibility_unverified","scope_unverified","out_of_scope"}:
        return {**classified, "status": "recreate_required" if st=="incompatible" else st}
    if not backend_allows_frontend(backend):
        status = "planned_after_backend" if not mode.is_apply else "blocked"
        reason = "backend_dependency_planned" if not mode.is_apply and backend.get("status") in {"absent", "would_create"} and not backend.get("blockers") else "backend_application_not_verified"
        return {"name": frontend_application_name(args.product), "status": status, "reason": reason}
    if not mode.is_apply:
        actions.append(ActionRecord("frontend_application","plan_create","would_create").to_dict())
        return {"name": frontend_application_name(args.product), "status":"would_create", "create_payload_preview": frontend_payload_preview(frontend_create_payload(args,context))}
    if not mode.frontend_application_writes_enabled:
        return {"name": frontend_application_name(args.product), "status":"blocked", "reason":"frontend_application_gates_incomplete"}
    payload=frontend_create_payload(args,context); actions.append(ActionRecord("frontend_application","attempt_create","attempted").to_dict())
    try:
        resp=client.create_frontend_application(payload)
    except ProvisioningError as exc:
        if exc.write_outcome_unknown:
            actions.append(ActionRecord("frontend_application","create","write_outcome_unknown",request_attempted=True,write_outcome_unknown=True).to_dict())
            return {"name":frontend_application_name(args.product),"status":"write_outcome_unknown","request_attempted":True,"write_outcome_unknown":True}
        if exc.http_status in {409,422}:
            rematches=[hydrate_application_by_uuid(client, field(m,"uuid","id"), m) if field(m,"uuid","id") else m for m in exact_frontend_matches(client,args)]
            recovered=classify_frontend_application(rematches,args,context)
            if recovered.get("status")=="exists":
                if hasattr(client, "bind_frontend_application_uuid"):
                    client.bind_frontend_application_uuid(recovered.get("uuid"), context)
                actions.append(ActionRecord("frontend_application","recover_after_conflict","exists",request_attempted=True,verified=True,recovered_after_conflict=True).to_dict())
                return {**recovered,"recovered_after_conflict":True}
            return {"name": frontend_application_name(args.product), "status":"blocked", "reason":"conflict_without_verified_resource"}
        raise
    item=resp.data.get("data") if isinstance(resp.data,dict) and isinstance(resp.data.get("data"),dict) else resp.data
    uuid=field(item,"uuid","id") if isinstance(item,dict) else None
    if not uuid:
        actions.append(ActionRecord("frontend_application","create","created_unverified",request_attempted=True,write_performed=True).to_dict())
        return {"name":frontend_application_name(args.product),"status":"created_unverified","write_performed":True}
    try: detail=hydrate_application_by_uuid(client,uuid,item)
    except ProvisioningError:
        actions.append(ActionRecord("frontend_application","create","created_unverified",request_attempted=True,write_performed=True).to_dict()); return {"name":frontend_application_name(args.product),"uuid":uuid,"status":"created_unverified","write_performed":True}
    verified=classify_frontend_application([detail],args,context)
    if verified.get("status")=="exists":
        if hasattr(client, "bind_frontend_application_uuid"):
            client.bind_frontend_application_uuid(verified.get("uuid"), context)
        actions.append(ActionRecord("frontend_application","create","created",request_attempted=True,write_performed=True,verified=True).to_dict())
        return {**verified,"status":"created"}
    return {**verified,"status":"created_unverified","write_performed":True}


def desired_frontend_env(product: str, backend_api_url: str) -> list[dict[str, Any]]:
    validate_vite_api_url(backend_api_url, product)
    return [{"key":"VITE_API_URL","value":backend_api_url,"is_runtime":False,"is_buildtime":True,"is_preview":False,"is_literal":True,"is_multiline":False}]


def frontend_env_payload(desired: dict[str, Any]) -> dict[str, Any]:
    payload={k:desired[k] for k in ("key","value","is_runtime","is_buildtime","is_preview","is_literal","is_multiline")}
    if set(payload) != APPROVED_FRONTEND_ENV_FIELDS or payload["key"] != "VITE_API_URL":
        raise ProvisioningError("Frontend env payload must contain exactly the VITE_API_URL build-time contract.", error_type="safety")
    return payload


def frontend_env_write_action(resource_key: str, operation: str, status: str, *, request_attempted: bool, write_performed: bool = False, write_outcome_unknown: bool = False, verified: bool = False, recovered_after_conflict: bool = False) -> dict[str, Any]:
    return ActionRecord("frontend_environment", f"{operation}_{resource_key}", status, request_attempted=request_attempted, write_performed=write_performed, write_outcome_unknown=write_outcome_unknown, verified=verified, recovered_after_conflict=recovered_after_conflict, details={"key": resource_key, "operation": operation}).to_dict()


def frontend_env_observed_metadata(envs: list[dict[str, Any]], desired: dict[str, Any]) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for item in envs:
        if not isinstance(item, dict):
            continue
        # Coolify may return a separate preview row for the same key. The
        # development deployment contract applies only to non-preview rows.
        if bool_value(item.get("is_preview")) is not False:
            continue
        key = item.get("key")
        if key in (None, ""):
            continue
        text = str(key)
        counts[text] = counts.get(text, 0) + 1
    meta: dict[str, Any] = {"observed_variable_name_counts": dict(sorted(counts.items())), "observed_variable_names": sorted(counts)}
    vite_rows = [item for item in envs if isinstance(item, dict) and item.get("key") == "VITE_API_URL" and bool_value(item.get("is_preview")) is False]
    if len(vite_rows) == 1:
        available, value = env_value(vite_rows[0])
        meta["vite_api_url_value_available"] = available
        meta["vite_api_url_value"] = value if available else None
        meta["vite_api_url_value_matches"] = available and value == desired.get("value")
    else:
        meta["vite_api_url_value_available"] = False
        meta["vite_api_url_value"] = None
        meta["vite_api_url_value_matches"] = False
    return meta


def empty_frontend_env_metadata() -> dict[str, Any]:
    return {"observed_variable_name_counts": {}, "observed_variable_names": [], "vite_api_url_value_available": False, "vite_api_url_value": None, "vite_api_url_value_matches": False}


def reconcile_frontend_environment(client: CoolifyClient, args: argparse.Namespace, frontend: dict[str, Any], backend: dict[str, Any], mode: ExecutionMode, actions: list[dict[str, Any]]) -> dict[str, Any]:
    uuid=frontend.get("uuid"); results=[]; blockers=[]
    if not backend_allows_frontend(backend):
        status = "blocked" if mode.is_apply else "planned_after_backend"
        reason = "backend_dependency_planned" if not mode.is_apply and backend.get("status") in {"absent", "would_create"} and not backend.get("blockers") else "backend_application_not_verified"
        return {"status": status, "reason": reason, "variables": [], "blockers": [{"resource":"frontend_environment", "key":"VITE_API_URL", "reason":"backend_application_not_verified", "backend_status": backend.get("status")}] if mode.is_apply else [], "observed": empty_frontend_env_metadata()}
    if frontend.get("status") not in {"exists","created"} or not uuid:
        return {"status":"blocked" if mode.is_apply else "planned", "variables":[], "blockers":[{"reason":"frontend_application_not_verified"}] if mode.is_apply else [], "observed": empty_frontend_env_metadata()}
    if str(uuid) == str(backend.get("uuid")):
        return {"status":"blocked","variables":[],"blockers":[{"resource":"frontend_environment","key":"VITE_API_URL","reason":"frontend_uuid_equals_backend_uuid"}], "observed": empty_frontend_env_metadata()}
    desired_env = desired_frontend_env(args.product, api_domain(args.product))
    if hasattr(client, "bind_frontend_env_contract"):
        client.bind_frontend_env_contract(desired_env)
    envs=item_list(client.get(f"/applications/{urllib.parse.quote(str(uuid), safe='')}/envs"))
    desired=desired_env[0]; key=desired["key"]
    observed = frontend_env_observed_metadata(envs, desired)
    row=locate_env_variable(envs, desired)
    if row["status"] == "duplicate":
        blockers.append({"resource":"frontend_environment","key":key,"reason":"duplicate"}); results.append(row)
    elif row["status"] == "exists":
        results.append(row)
    elif row["status"] == "secret_value_unverified":
        blockers.append({"resource":"frontend_environment","key":key,"reason":"compatibility_unverified"}); results.append({**row,"status":"compatibility_unverified"})
    else:
        operation = "create" if row["status"] == "absent" else "update"
        row["action"] = "would_create" if operation == "create" and not mode.is_apply else ("would_patch" if not mode.is_apply else operation)
        if not mode.is_apply:
            results.append(row)
        elif not mode.frontend_env_writes_enabled:
            row["status"]="blocked"; blockers.append({"resource":"frontend_environment","key":key,"reason":"frontend_env_gates_incomplete"}); results.append(row)
        else:
            try:
                if operation == "create": client.create_frontend_env(str(uuid), frontend_env_payload(desired))
                else: client.update_frontend_env(str(uuid), frontend_env_payload(desired))
            except ProvisioningError as exc:
                if exc.write_outcome_unknown:
                    row.update({"status":"write_outcome_unknown", "write_outcome_unknown": True}); actions.append(frontend_env_write_action(key, operation, "write_outcome_unknown", request_attempted=True, write_outcome_unknown=True)); blockers.append({"resource":"frontend_environment","key":key,"reason":"write_outcome_unknown"}); results.append(row)
                elif exc.http_status in {409,422}:
                    envs2, row2 = rediscover_envs_after_write(client, str(uuid), desired, operation); observed = frontend_env_observed_metadata(envs2 or [], desired); row2["recovered_after_conflict"] = row2.get("status") in {"created","patched"}; actions.append(frontend_env_write_action(key, operation, row2.get("status","blocked"), request_attempted=True, verified=bool(row2.get("verified")), recovered_after_conflict=bool(row2.get("recovered_after_conflict")))); results.append(row2)
                    if row2.get("status") not in {"created","patched"}: blockers.append({"resource":"frontend_environment","key":key,"reason":row2.get("status")})
                else: raise
            else:
                envs2, verified_row = rediscover_envs_after_write(client, str(uuid), desired, operation); observed = frontend_env_observed_metadata(envs2 or [], desired); results.append(verified_row); actions.append(frontend_env_write_action(key, operation, verified_row["status"], request_attempted=True, write_performed=True, verified=bool(verified_row.get("verified"))))
                if verified_row.get("status") not in {"created","patched"}: blockers.append({"resource":"frontend_environment","key":key,"reason":verified_row.get("status")})
    if not mode.is_apply: status="planned"
    elif blockers: status="partial" if any(r.get("status") in {"duplicate","created_unverified","updated_unverified","write_outcome_unknown"} for r in results) else "blocked"
    else: status="exists"
    return {"status":status,"variables":results,"blockers":blockers,"observed":observed}


def desired_backend_env(product: str, database_url: str | None) -> list[dict[str, Any]]:
    base={"is_runtime":True,"is_buildtime":False,"is_preview":False,"is_literal":True,"is_multiline":False}
    return [
        {"key":"DATABASE_URL","value":database_url,"secret":True, **base},
        {"key":"APP_ENV","value":"production","secret":False, **base},
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
    desired_env = desired_backend_env(args.product, db_url)
    if hasattr(client, "bind_backend_env_contract"):
        client.bind_backend_env_contract(desired_env)
    envs=item_list(client.get(f"/applications/{urllib.parse.quote(str(uuid), safe='')}/envs"))
    for desired in desired_env:
        key=desired["key"]
        row=locate_env_variable(envs, desired)
        if row["status"] == "duplicate":
            blockers.append({"resource":"backend_environment","key":key,"reason":"duplicate"}); results.append(row); break
        if row["status"] == "exists":
            results.append(row); continue
        if row["status"] == "secret_value_unverified":
            blockers.append({"resource":"backend_environment","key":key,"reason":"secret_value_unverified"}); results.append(row); continue
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
                if row.get("status") == "secret_value_unverified":
                    blockers.append({"resource":"backend_environment","key":key,"reason":"secret_value_unverified"}); continue
                if row.get("status") not in {"created", "patched"}:
                    blockers.append({"resource":"backend_environment","key":key,"reason":row.get("status")}); break
                continue
            raise
        envs, verified_row = rediscover_envs_after_write(client, str(uuid), desired, operation)
        results.append(verified_row)
        actions.append(env_write_action(key, operation, verified_row["status"], request_attempted=True, write_performed=True, verified=bool(verified_row.get("verified"))))
        if verified_row.get("status") == "secret_value_unverified":
            blockers.append({"resource":"backend_environment","key":key,"reason":"secret_value_unverified"}); continue
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

DEPLOYMENT_OFFICIAL_STATUSES = {"queued", "in_progress", "finished", "failed", "cancelled-by-user"}
DEPLOYMENT_PENDING_STATUSES = {"queued", "in_progress"}
RUNTIME_HEALTHY_STATUSES = {"running:healthy", "healthy"}
RUNTIME_TRANSIENT_STATUSES = {"queued", "starting", "running:starting"}
RUNTIME_UNHEALTHY_STATUSES = {"running:unhealthy", "unhealthy", "exited", "stopped", "failed", "exited:unhealthy"}


def backend_deployment_result(status: str = "not_requested", **updates: Any) -> dict[str, Any]:
    result = {"user_requested": False, "requested": False, "post_attempted": False, "adopted_active": False, "recovered_after_unknown": False, "deployment_uuid": None, "status": status, "terminal": False, "verified": False, "request_attempted": False, "write_performed": False, "write_outcome_unknown": False, "poll_attempts": 0, "poll_errors": 0, "timed_out": False}
    result.update(updates)
    return result


def deployment_failure_diagnostics(item: dict[str, Any]) -> dict[str, Any]:
    """Return a short redacted failure summary from Coolify deployment logs."""

    raw_logs = field(item, "logs")
    if raw_logs in (None, "", []):
        return {"available": False, "messages": []}

    parsed: Any = raw_logs
    if isinstance(raw_logs, str):
        try:
            parsed = json.loads(raw_logs)
        except json.JSONDecodeError:
            parsed = raw_logs.splitlines()

    candidates: list[str] = []
    entries = parsed if isinstance(parsed, list) else [parsed]
    for entry in entries:
        if isinstance(entry, dict):
            for key in ("output", "message", "error"):
                value = entry.get(key)
                if isinstance(value, str) and value.strip():
                    candidates.extend(value.splitlines())
        elif isinstance(entry, str):
            candidates.extend(entry.splitlines())

    failure_pattern = re.compile(
        r"(error|failed|failure|exception|traceback|not found|no such file|"
        r"permission denied|exit code|could not|unable to|connection refused)",
        re.I,
    )
    messages: list[str] = []
    for candidate in candidates:
        clean = sanitize_string(candidate).strip()
        if not clean or not failure_pattern.search(clean):
            continue
        clean = clean[:500] + ("...[truncated]" if len(clean) > 500 else "")
        if clean not in messages:
            messages.append(clean)

    return {"available": bool(messages), "messages": messages[-12:]}


def runtime_readiness_result(status: str = "not_checked", **updates: Any) -> dict[str, Any]:
    result = {"status": status, "healthy": False, "verified": False}
    result.update(updates)
    return result


def smoke_check_result(product: str, status: str = "not_checked", **updates: Any) -> dict[str, Any]:
    result = {"url": f"https://api.{product}.goodmorning.mx/health", "status": status, "http_status": None, "body_verified": False, "attempts": 0, "timed_out": False}
    result.update(updates)
    return result


def backend_deployment_request_action(status: str, exc: ProvisioningError | None = None) -> dict[str, Any]:
    return ActionRecord(
        "backend_deployment",
        "request",
        status,
        request_attempted=True if exc is None else exc.request_attempted,
        write_performed=True if exc is None else exc.write_performed,
        write_outcome_unknown=False if exc is None else exc.write_outcome_unknown,
    ).to_dict()


def validate_stage7_timing_args(args: argparse.Namespace) -> None:
    for name, maximum in (("backend_deployment_timeout", 3600), ("backend_deployment_interval", 30), ("backend_runtime_timeout", 600), ("backend_runtime_interval", 30), ("backend_smoke_timeout", 600), ("backend_smoke_interval", 30), ("backend_smoke_request_timeout", 30), ("frontend_deployment_timeout", 3600), ("frontend_deployment_interval", 30), ("frontend_runtime_timeout", 600), ("frontend_runtime_interval", 30), ("frontend_smoke_timeout", 600), ("frontend_smoke_interval", 30), ("frontend_smoke_request_timeout", 30)):
        value = getattr(args, name, None)
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 or value > maximum:
            raise ProvisioningError(f"--{name.replace('_', '-')} must be finite, greater than zero, and <= {maximum}.", error_type="validation")
    for name in ("backend_deployment_max_poll_errors", "backend_runtime_max_poll_errors", "frontend_deployment_max_poll_errors", "frontend_runtime_max_poll_errors"):
        value = getattr(args, name, None)
        if not isinstance(value, int) or value < 0 or value > 10:
            raise ProvisioningError(f"--{name.replace('_', '-')} must be an integer from 0 through 10.", error_type="validation")


def deployment_history(client: CoolifyClient, backend_uuid: str) -> list[dict[str, Any]]:
    data = client.get(f"/deployments/applications/{urllib.parse.quote(str(backend_uuid), safe='')}?skip=0&take=10")
    if not isinstance(data, dict):
        raise ProvisioningError("Deployment history response must be a JSON object.", error_type="coolify_api", code=2)
    if "deployments" not in data or not isinstance(data.get("deployments"), list):
        raise ProvisioningError("Deployment history response must include deployments list.", error_type="coolify_api", code=2)
    if "count" in data and not isinstance(data.get("count"), int):
        raise ProvisioningError("Deployment history count must be an integer when present.", error_type="coolify_api", code=2)
    items: list[dict[str, Any]] = []
    for item in data["deployments"]:
        if not isinstance(item, dict):
            raise ProvisioningError("Deployment history entries must be objects.", error_type="coolify_api", code=2)
        items.append(item)
    return items


def deployment_uuid_from_item(item: dict[str, Any]) -> str | None:
    value = field(item, "deployment_uuid", "uuid", "id")
    return str(value) if value not in (None, "") else None


def deployment_belongs_to_backend(item: dict[str, Any], backend_uuid: str) -> bool:
    resource_uuid = field(item, "resource_uuid", "application_uuid")
    return resource_uuid in (None, "") or str(resource_uuid) == str(backend_uuid)


def active_deployments(items: list[dict[str, Any]], backend_uuid: str) -> list[str]:
    result=[]
    seen=set()
    for item in items:
        status=str(field(item,"status") or "")
        if status not in DEPLOYMENT_PENDING_STATUSES:
            continue
        if not deployment_belongs_to_backend(item, backend_uuid):
            raise ProvisioningError("Deployment history contains an active deployment for a different Backend UUID.", error_type="coolify_api", code=2)
        du=deployment_uuid_from_item(item)
        if not du:
            raise ProvisioningError("Active deployment history entry is missing deployment UUID.", error_type="coolify_api", code=2)
        if du not in seen:
            result.append(du); seen.add(du)
    return result


def backend_env_flags_match(row: dict[str, Any]) -> bool:
    flags = row.get("desired_flags")
    expected = {"is_runtime": True, "is_buildtime": False, "is_preview": False, "is_literal": True, "is_multiline": False}
    return isinstance(flags, dict) and all(flags.get(key) is value for key, value in expected.items())


def backend_deployment_preflight(args: argparse.Namespace, postgres: dict[str, Any], readiness: dict[str, Any], backend: dict[str, Any], backend_env: dict[str, Any], actions: list[dict[str, Any]], *, require_env_status: bool = True) -> tuple[bool, list[dict[str, Any]]]:
    blockers=[]
    if postgres.get("name") != f"{args.product}-postgres-prod" or postgres.get("status") not in {"exists", "created"}:
        blockers.append({"resource":"backend_deployment","reason":"postgresql_identity_or_compatibility_unverified"})
    if readiness.get("status") not in {"healthy","already_healthy"} or not readiness.get("internal_db_url_available") or not readiness.get("database_url_driver_ready"):
        blockers.append({"resource":"backend_deployment","reason":"postgresql_not_ready"})
    if backend.get("name") != backend_application_name(args.product) or backend.get("status") not in {"exists","created"} or not backend.get("uuid"):
        blockers.append({"resource":"backend_deployment","reason":"backend_application_not_verified"})
    if require_env_status and backend_env.get("status") != "exists":
        blockers.append({"resource":"backend_deployment","reason":"backend_environment_not_verified"})
    env_rows = backend_env.get("variables", [])
    if not isinstance(env_rows, list) or not env_rows:
        blockers.append({"resource":"backend_deployment","reason":"backend_environment_variables_missing"})
        env_rows = [] if not isinstance(env_rows, list) else env_rows
    counts: dict[str, int] = {}
    for row in env_rows:
        key = row.get("key") if isinstance(row, dict) else None
        if key in BACKEND_ENV_KEYS:
            counts[str(key)] = counts.get(str(key), 0) + 1
        else:
            blockers.append({"resource":"backend_deployment","reason":"backend_environment_unexpected_key", "key": key})
    for key in sorted(BACKEND_ENV_KEYS):
        count = counts.get(key, 0)
        if count == 0:
            blockers.append({"resource":"backend_deployment","reason":"backend_environment_key_missing", "key": key})
        elif count > 1:
            blockers.append({"resource":"backend_deployment","reason":"backend_environment_key_duplicate", "key": key})
    for row in env_rows:
        if not isinstance(row, dict):
            blockers.append({"resource":"backend_deployment","reason":"backend_environment_row_invalid"}); continue
        key = row.get("key")
        if key not in BACKEND_ENV_KEYS:
            continue
        if row.get("status") == "secret_value_unverified":
            blockers.append({"resource":"backend_deployment","reason":"secret_value_unverified", "key": key})
            continue
        if row.get("status") != "exists":
            blockers.append({"resource":"backend_deployment","reason":"backend_environment_key_not_exists", "key": key})
        if row.get("value_verified") is not True:
            blockers.append({"resource":"backend_deployment","reason":"backend_environment_value_unverified", "key": key})
        if not backend_env_flags_match(row):
            blockers.append({"resource":"backend_deployment","reason":"backend_environment_flags_incompatible", "key": key})
    if any(a.get("write_outcome_unknown") for a in actions):
        blockers.append({"resource":"backend_deployment","reason":"prior_write_outcome_unknown"})
    return (not blockers), blockers


def parse_deploy_response(data: Any, backend_uuid: str) -> str:
    deployments = data.get("deployments") if isinstance(data, dict) else None
    if not isinstance(deployments, list) or len(deployments) != 1:
        raise ProvisioningError("Coolify deploy response must include exactly one deployment entry.", error_type="coolify_api", code=2)
    item=deployments[0]
    if not isinstance(item, dict) or str(item.get("resource_uuid") or "") != str(backend_uuid):
        raise ProvisioningError("Coolify deploy response resource_uuid did not match Backend Application UUID.", error_type="coolify_api", code=2)
    du=item.get("deployment_uuid")
    if not isinstance(du, str) or not du.strip():
        raise ProvisioningError("Coolify deploy response missing deployment_uuid.", error_type="coolify_api", code=2)
    return du


def poll_backend_deployment(client: CoolifyClient, args: argparse.Namespace, deployment_uuid: str, backend_uuid: str, sleeper: Callable[[float], None] | None = None, clock: Callable[[], float] | None = None) -> dict[str, Any]:
    sleeper=sleeper or (lambda seconds: None if seconds <= 0 else __import__("time").sleep(seconds)); clock=clock or __import__("time").monotonic
    start=clock(); attempts=0; errors=0; consecutive=0
    while clock() - start <= args.backend_deployment_timeout:
        attempts += 1
        try:
            data=client.get(f"/deployments/{urllib.parse.quote(str(deployment_uuid), safe='')}")
        except ProvisioningError:
            errors += 1; consecutive += 1
            if consecutive > args.backend_deployment_max_poll_errors:
                return backend_deployment_result("deployment_poll_failed", deployment_uuid=deployment_uuid, terminal=False, poll_attempts=attempts, poll_errors=errors)
            sleeper(args.backend_deployment_interval); continue
        consecutive=0
        item=data.get("data") if isinstance(data,dict) and isinstance(data.get("data"),dict) else data
        if not isinstance(item,dict):
            return backend_deployment_result("deployment_status_unverified", deployment_uuid=deployment_uuid, poll_attempts=attempts, poll_errors=errors)
        if str(field(item,"deployment_uuid","uuid","id") or "") != str(deployment_uuid):
            return backend_deployment_result("deployment_uuid_mismatch", deployment_uuid=deployment_uuid, poll_attempts=attempts, poll_errors=errors)
        resource_uuid=field(item,"resource_uuid","application_uuid")
        if resource_uuid not in (None, "") and str(resource_uuid) != str(backend_uuid):
            return backend_deployment_result("backend_uuid_mismatch", deployment_uuid=deployment_uuid, poll_attempts=attempts, poll_errors=errors)
        app_name=field(item,"application_name")
        if app_name not in (None, "") and str(app_name) != backend_application_name(args.product):
            return backend_deployment_result("application_name_mismatch", deployment_uuid=deployment_uuid, poll_attempts=attempts, poll_errors=errors)
        status=str(field(item,"status") or "")
        if status == "finished":
            return backend_deployment_result("finished", deployment_uuid=deployment_uuid, terminal=True, verified=True, poll_attempts=attempts, poll_errors=errors)
        if status in {"failed", "cancelled-by-user"}:
            return backend_deployment_result(
                status,
                deployment_uuid=deployment_uuid,
                terminal=True,
                poll_attempts=attempts,
                poll_errors=errors,
                failure_diagnostics=deployment_failure_diagnostics(item),
            )
        if status not in DEPLOYMENT_PENDING_STATUSES:
            return backend_deployment_result("deployment_status_unverified", deployment_uuid=deployment_uuid, poll_attempts=attempts, poll_errors=errors)
        sleeper(args.backend_deployment_interval)
    return backend_deployment_result("deployment_timeout", deployment_uuid=deployment_uuid, poll_attempts=attempts, poll_errors=errors, timed_out=True)


def read_backend_runtime_once(client: CoolifyClient, args: argparse.Namespace, backend_uuid: str) -> dict[str, Any]:
    data=client.get(f"/applications/{urllib.parse.quote(str(backend_uuid), safe='')}")
    item=data.get("data") if isinstance(data,dict) and isinstance(data.get("data"),dict) else data
    if not isinstance(item,dict) or str(field(item,"uuid","id") or "") != str(backend_uuid):
        return runtime_readiness_result("runtime_status_unverified")
    name=field(item,"name")
    if name not in (None, "") and str(name) != backend_application_name(args.product):
        return runtime_readiness_result("runtime_status_unverified")
    status=str(field(item,"status","human_status") or "")
    if status in RUNTIME_HEALTHY_STATUSES:
        return runtime_readiness_result(status, healthy=True, verified=True)
    if status in RUNTIME_UNHEALTHY_STATUSES:
        return runtime_readiness_result(status, healthy=False, verified=True)
    if status in RUNTIME_TRANSIENT_STATUSES:
        return runtime_readiness_result(status, healthy=False, verified=False)
    return runtime_readiness_result("runtime_status_unverified")


def verify_backend_runtime(client: CoolifyClient, args: argparse.Namespace, backend_uuid: str, sleeper: Callable[[float], None] | None = None, clock: Callable[[], float] | None = None) -> dict[str, Any]:
    sleeper=sleeper or (lambda seconds: None if seconds <= 0 else __import__("time").sleep(seconds)); clock=clock or __import__("time").monotonic
    start=clock(); attempts=0; errors=0; consecutive=0; last_status="runtime_timeout"
    while clock() - start <= args.backend_runtime_timeout:
        attempts += 1
        try:
            result = read_backend_runtime_once(client, args, backend_uuid)
        except ProvisioningError:
            errors += 1; consecutive += 1
            if consecutive > args.backend_runtime_max_poll_errors:
                return runtime_readiness_result("runtime_poll_failed", poll_attempts=attempts, poll_errors=errors)
            sleeper(args.backend_runtime_interval); continue
        consecutive = 0
        status = result.get("status")
        if result.get("healthy") and result.get("verified"):
            return {**result, "poll_attempts": attempts, "poll_errors": errors}
        if status in RUNTIME_UNHEALTHY_STATUSES or status == "runtime_status_unverified":
            return {**result, "poll_attempts": attempts, "poll_errors": errors}
        last_status = str(status or "runtime_timeout")
        sleeper(args.backend_runtime_interval)
    return runtime_readiness_result("runtime_timeout", poll_attempts=attempts, poll_errors=errors, timed_out=True, last_status=last_status)


def backend_smoke_url(product: str) -> str:
    return f"https://api.{product}.goodmorning.mx/health"


def validate_backend_smoke_url(url: str, product: str) -> None:
    parsed=urllib.parse.urlparse(url)
    if url != backend_smoke_url(product) or parsed.scheme != "https" or parsed.hostname != f"api.{product}.goodmorning.mx" or parsed.port is not None or parsed.username or parsed.password or parsed.path != "/health" or parsed.query or parsed.fragment:
        raise ProvisioningError("Backend smoke URL must be exact HTTPS /health URL without port, query, fragment, credentials, IP, or localhost.", error_type="safety")


def run_backend_smoke(args: argparse.Namespace, opener: Callable[..., Any] | None = None, sleeper: Callable[[float], None] | None = None, clock: Callable[[], float] | None = None) -> dict[str, Any]:
    url=backend_smoke_url(args.product); validate_backend_smoke_url(url,args.product)
    opener=opener or urllib.request.urlopen; sleeper=sleeper or (lambda seconds: None if seconds <= 0 else __import__("time").sleep(seconds)); clock=clock or __import__("time").monotonic
    start=clock(); attempts=0; last_status=None
    while clock()-start <= args.backend_smoke_timeout:
        attempts += 1
        req=urllib.request.Request(url, method="GET", headers={"Accept":"application/json", "User-Agent":"goodmorning-factory-backend-smoke"})
        try:
            with opener(req, timeout=args.backend_smoke_request_timeout) as resp:
                last_status=getattr(resp,"status",None) or resp.getcode()
                final_url=getattr(resp,"url",url)
                try:
                    validate_backend_smoke_url(str(final_url), args.product)
                except ProvisioningError:
                    return smoke_check_result(args.product,"smoke_failed",http_status=last_status,attempts=attempts)
                ctype=resp.headers.get("Content-Type","") if hasattr(resp,"headers") else ""
                raw=resp.read(4096).decode("utf-8",errors="replace")
                if last_status == 200 and "json" in ctype.lower():
                    body=json.loads(raw)
                    if isinstance(body,dict) and body.get("status")=="ok" and body.get("environment")=="production" and isinstance(body.get("service"),str) and body.get("service"):
                        return smoke_check_result(args.product,"passed",http_status=200,body_verified=True,attempts=attempts)
        except Exception:
            pass
        sleeper(args.backend_smoke_interval)
    return smoke_check_result(args.product,"smoke_timeout",http_status=last_status,attempts=attempts,timed_out=True)


def frontend_deployment_result(status: str = "not_requested", **updates: Any) -> dict[str, Any]:
    return backend_deployment_result(status, **updates)


def frontend_runtime_readiness_result(status: str = "not_checked", **updates: Any) -> dict[str, Any]:
    return runtime_readiness_result(status, **updates)


class RootElementParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.html_element_found = False
        self.root_element_found = False
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "html":
            self.html_element_found = True
        if any(name == "id" and value == "root" for name, value in attrs):
            self.root_element_found = True


def frontend_smoke_url(product: str) -> str:
    return f"https://{product}.goodmorning.mx/"


def frontend_smoke_check_result(product: str, status: str = "not_checked", **updates: Any) -> dict[str, Any]:
    result = {"url": frontend_smoke_url(product), "status": status, "http_status": None, "html_verified": False, "root_element_verified": False, "attempts": 0, "timed_out": False}
    result.update(updates)
    return result


def validate_frontend_smoke_url(url: str, product: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if url != frontend_smoke_url(product) or parsed.scheme != "https" or parsed.hostname != f"{product}.goodmorning.mx" or parsed.port is not None or parsed.username or parsed.password or parsed.path != "/" or parsed.query or parsed.fragment:
        raise ProvisioningError("Frontend smoke URL must be exact HTTPS / URL without port, query, fragment, credentials, IP, or localhost.", error_type="safety")


def run_frontend_smoke(args: argparse.Namespace, opener: Callable[..., Any] | None = None, sleeper: Callable[[float], None] | None = None, clock: Callable[[], float] | None = None) -> dict[str, Any]:
    url = frontend_smoke_url(args.product); validate_frontend_smoke_url(url, args.product)
    opener = opener or urllib.request.urlopen; sleeper = sleeper or (lambda seconds: None if seconds <= 0 else __import__("time").sleep(seconds)); clock = clock or __import__("time").monotonic
    start = clock(); attempts = 0; last_status = None; max_bytes = 1024 * 1024
    while clock() - start <= args.frontend_smoke_timeout:
        attempts += 1
        req = urllib.request.Request(url, method="GET", headers={"Accept":"text/html", "User-Agent":"goodmorning-factory-frontend-smoke"})
        try:
            with opener(req, timeout=args.frontend_smoke_request_timeout) as resp:
                last_status = getattr(resp, "status", None) or resp.getcode()
                try: validate_frontend_smoke_url(str(getattr(resp, "url", url)), args.product)
                except ProvisioningError: return frontend_smoke_check_result(args.product, "smoke_failed", http_status=last_status, attempts=attempts)
                ctype = resp.headers.get("Content-Type", "") if hasattr(resp, "headers") else ""
                raw = resp.read(max_bytes + 1)
                if len(raw) > max_bytes: return frontend_smoke_check_result(args.product, "smoke_failed", http_status=last_status, attempts=attempts)
                if last_status == 200 and "html" in ctype.lower() and raw:
                    parser = RootElementParser(); parser.feed(raw.decode("utf-8", errors="replace"))
                    if parser.html_element_found and parser.root_element_found:
                        return frontend_smoke_check_result(args.product, "passed", http_status=200, html_verified=True, root_element_verified=True, attempts=attempts)
                    if parser.html_element_found or parser.root_element_found:
                        return frontend_smoke_check_result(args.product, "smoke_failed", http_status=last_status, html_verified=parser.html_element_found, root_element_verified=parser.root_element_found, attempts=attempts)
        except Exception:
            pass
        sleeper(args.frontend_smoke_interval)
    return frontend_smoke_check_result(args.product, "smoke_timeout", http_status=last_status, attempts=attempts, timed_out=True)


def deployment_belongs_to_application(item: dict[str, Any], application_uuid: str) -> bool:
    resource_uuid = field(item, "resource_uuid", "application_uuid")
    return resource_uuid in (None, "") or str(resource_uuid) == str(application_uuid)


def active_deployments_for_application(items: list[dict[str, Any]], application_uuid: str, label: str) -> list[str]:
    result=[]; seen=set()
    for item in items:
        status=str(field(item,"status") or "")
        if status not in DEPLOYMENT_PENDING_STATUSES: continue
        if not deployment_belongs_to_application(item, application_uuid):
            raise ProvisioningError(f"Deployment history contains an active deployment for a different {label} UUID.", error_type="coolify_api", code=2)
        du=deployment_uuid_from_item(item)
        if not du: raise ProvisioningError("Active deployment history entry is missing deployment UUID.", error_type="coolify_api", code=2)
        if du not in seen: result.append(du); seen.add(du)
    return result


def recover_new_deployment_candidates_for_application(before_ids: set[str | None], after: list[dict[str, Any]], application_uuid: str) -> list[str]:
    candidates=[]; seen=set(); clean_before={str(item) for item in before_ids if item}
    for item in after:
        du=deployment_uuid_from_item(item)
        if not du or du in clean_before or du in seen: continue
        if not deployment_belongs_to_application(item, application_uuid): continue
        candidates.append(du); seen.add(du)
    return candidates


def parse_deploy_response_for_application(data: Any, application_uuid: str, label: str) -> str:
    deployments = data.get("deployments") if isinstance(data, dict) else None
    if not isinstance(deployments, list) or len(deployments) != 1:
        raise ProvisioningError("Coolify deploy response must include exactly one deployment entry.", error_type="coolify_api", code=2)
    item = deployments[0]
    if not isinstance(item, dict) or str(item.get("resource_uuid") or "") != str(application_uuid):
        raise ProvisioningError(f"Coolify deploy response resource_uuid did not match {label} Application UUID.", error_type="coolify_api", code=2)
    du = item.get("deployment_uuid")
    if not isinstance(du, str) or not du.strip():
        raise ProvisioningError("Coolify deploy response missing deployment_uuid.", error_type="coolify_api", code=2)
    return du


def frontend_env_flags_match(row: dict[str, Any]) -> bool:
    flags = row.get("desired_flags")
    expected = {"is_runtime": False, "is_buildtime": True, "is_preview": False, "is_literal": True, "is_multiline": False}
    return isinstance(flags, dict) and all(flags.get(k) is v for k, v in expected.items())


def frontend_deployment_preflight(args: argparse.Namespace, postgres: dict[str, Any], readiness: dict[str, Any], backend: dict[str, Any], backend_env: dict[str, Any], frontend: dict[str, Any], frontend_env: dict[str, Any], backend_runtime: dict[str, Any], backend_smoke: dict[str, Any], actions: list[dict[str, Any]], *, require_live: bool = True) -> tuple[bool, list[dict[str, Any]]]:
    blockers=[]
    if frontend.get("name") != frontend_application_name(args.product) or frontend.get("status") not in {"exists", "created"} or not frontend.get("uuid"):
        blockers.append({"resource":"frontend_deployment","reason":"frontend_application_not_verified"})
    if frontend.get("uuid") and backend.get("uuid") and str(frontend.get("uuid")) == str(backend.get("uuid")):
        blockers.append({"resource":"frontend_deployment","reason":"frontend_uuid_equals_backend_uuid"})
    if frontend_env.get("status") != "exists" and (require_live or frontend_env.get("status") != "planned"):
        blockers.append({"resource":"frontend_deployment","reason":"frontend_environment_not_verified"})
    rows = frontend_env.get("variables", []) if isinstance(frontend_env.get("variables", []), list) else []
    observed = frontend_env.get("observed") if isinstance(frontend_env.get("observed"), dict) else {}
    if observed.get("observed_variable_name_counts") != {"VITE_API_URL": 1}:
        blockers.append({"resource":"frontend_deployment","reason":"frontend_environment_not_exact", "observed_variable_names": observed.get("observed_variable_names", [])})
    vite = [r for r in rows if isinstance(r, dict) and r.get("key") == "VITE_API_URL"]
    if len(vite) != 1:
        blockers.append({"resource":"frontend_deployment","reason":"vite_api_url_missing_or_duplicate"})
    if observed.get("vite_api_url_value") != api_domain(args.product) or observed.get("vite_api_url_value_matches") is not True:
        blockers.append({"resource":"frontend_deployment","reason":"vite_api_url_value_invalid"})
    for row in vite:
        if row.get("status") != "exists" or row.get("value_verified") is not True or not frontend_env_flags_match(row):
            blockers.append({"resource":"frontend_deployment","reason":"vite_api_url_not_verified"})
    backend_ok, backend_blockers = backend_deployment_preflight(args, postgres, readiness, backend, backend_env, actions, require_env_status=require_live)
    if not backend_ok:
        blockers.append({"resource":"frontend_deployment","reason":"backend_dependency_unverified", "details": backend_blockers})
    if require_live:
        if not backend_runtime.get("healthy") or not backend_runtime.get("verified"):
            blockers.append({"resource":"frontend_deployment","reason":"backend_runtime_not_healthy"})
        if not backend_smoke.get("body_verified"):
            blockers.append({"resource":"frontend_deployment","reason":"backend_smoke_not_verified"})
    if any(a.get("write_outcome_unknown") for a in actions):
        blockers.append({"resource":"frontend_deployment","reason":"prior_write_outcome_unknown"})
    return (not blockers), blockers


def poll_frontend_deployment(client: CoolifyClient, args: argparse.Namespace, deployment_uuid: str, frontend_uuid: str, sleeper: Callable[[float], None] | None = None, clock: Callable[[], float] | None = None) -> dict[str, Any]:
    sleeper=sleeper or (lambda seconds: None if seconds <= 0 else __import__("time").sleep(seconds)); clock=clock or __import__("time").monotonic
    start=clock(); attempts=0; errors=0; consecutive=0
    while clock() - start <= args.frontend_deployment_timeout:
        attempts += 1
        try: data=client.get(f"/deployments/{urllib.parse.quote(str(deployment_uuid), safe='')}")
        except ProvisioningError:
            errors += 1; consecutive += 1
            if consecutive > args.frontend_deployment_max_poll_errors:
                return frontend_deployment_result("deployment_poll_failed", deployment_uuid=deployment_uuid, poll_attempts=attempts, poll_errors=errors)
            sleeper(args.frontend_deployment_interval); continue
        consecutive=0; item=data.get("data") if isinstance(data,dict) and isinstance(data.get("data"),dict) else data
        if not isinstance(item,dict): return frontend_deployment_result("deployment_status_unverified", deployment_uuid=deployment_uuid, poll_attempts=attempts, poll_errors=errors)
        if str(field(item,"deployment_uuid","uuid","id") or "") != str(deployment_uuid): return frontend_deployment_result("deployment_uuid_mismatch", deployment_uuid=deployment_uuid, poll_attempts=attempts, poll_errors=errors)
        resource_uuid=field(item,"resource_uuid","application_uuid")
        if resource_uuid not in (None, "") and str(resource_uuid) != str(frontend_uuid): return frontend_deployment_result("frontend_uuid_mismatch", deployment_uuid=deployment_uuid, poll_attempts=attempts, poll_errors=errors)
        app_name=field(item,"application_name")
        if app_name not in (None, "") and str(app_name) != frontend_application_name(args.product): return frontend_deployment_result("application_name_mismatch", deployment_uuid=deployment_uuid, poll_attempts=attempts, poll_errors=errors)
        status=str(field(item,"status") or "")
        if status == "finished": return frontend_deployment_result("finished", deployment_uuid=deployment_uuid, terminal=True, verified=True, poll_attempts=attempts, poll_errors=errors)
        if status in {"failed", "cancelled-by-user"}: return frontend_deployment_result(status, deployment_uuid=deployment_uuid, terminal=True, poll_attempts=attempts, poll_errors=errors)
        if status not in DEPLOYMENT_PENDING_STATUSES: return frontend_deployment_result("deployment_status_unverified", deployment_uuid=deployment_uuid, poll_attempts=attempts, poll_errors=errors)
        sleeper(args.frontend_deployment_interval)
    return frontend_deployment_result("deployment_timeout", deployment_uuid=deployment_uuid, poll_attempts=attempts, poll_errors=errors, timed_out=True)


def read_frontend_runtime_once(client: CoolifyClient, args: argparse.Namespace, frontend_uuid: str) -> dict[str, Any]:
    data=client.get(f"/applications/{urllib.parse.quote(str(frontend_uuid), safe='')}")
    item=data.get("data") if isinstance(data,dict) and isinstance(data.get("data"),dict) else data
    if not isinstance(item,dict) or str(field(item,"uuid","id") or "") != str(frontend_uuid): return frontend_runtime_readiness_result("runtime_status_unverified")
    name=field(item,"name")
    if name not in (None, "") and str(name) != frontend_application_name(args.product): return frontend_runtime_readiness_result("runtime_status_unverified")
    status=str(field(item,"status","human_status") or "")
    if status in RUNTIME_HEALTHY_STATUSES: return frontend_runtime_readiness_result(status, healthy=True, verified=True)
    if status in RUNTIME_UNHEALTHY_STATUSES: return frontend_runtime_readiness_result(status, healthy=False, verified=True)
    if status in RUNTIME_TRANSIENT_STATUSES: return frontend_runtime_readiness_result(status, healthy=False, verified=False)
    return frontend_runtime_readiness_result("runtime_status_unverified")


def verify_frontend_runtime(client: CoolifyClient, args: argparse.Namespace, frontend_uuid: str, sleeper: Callable[[float], None] | None = None, clock: Callable[[], float] | None = None) -> dict[str, Any]:
    sleeper=sleeper or (lambda seconds: None if seconds <= 0 else __import__("time").sleep(seconds)); clock=clock or __import__("time").monotonic
    start=clock(); attempts=0; errors=0; consecutive=0; last_status="runtime_timeout"
    while clock() - start <= args.frontend_runtime_timeout:
        attempts += 1
        try: result=read_frontend_runtime_once(client,args,frontend_uuid)
        except ProvisioningError:
            errors += 1; consecutive += 1
            if consecutive > args.frontend_runtime_max_poll_errors: return frontend_runtime_readiness_result("runtime_poll_failed", poll_attempts=attempts, poll_errors=errors)
            sleeper(args.frontend_runtime_interval); continue
        consecutive=0; status=result.get("status")
        if result.get("healthy") and result.get("verified"): return {**result,"poll_attempts":attempts,"poll_errors":errors}
        if status in RUNTIME_UNHEALTHY_STATUSES or status == "runtime_status_unverified": return {**result,"poll_attempts":attempts,"poll_errors":errors}
        last_status=str(status or "runtime_timeout"); sleeper(args.frontend_runtime_interval)
    return frontend_runtime_readiness_result("runtime_timeout", poll_attempts=attempts, poll_errors=errors, timed_out=True, last_status=last_status)


def frontend_deployment_request_action(status: str, exc: ProvisioningError | None = None) -> dict[str, Any]:
    return ActionRecord("frontend_deployment", "request", status, request_attempted=True if exc is None else exc.request_attempted, write_performed=True if exc is None else exc.write_performed, write_outcome_unknown=False if exc is None else exc.write_outcome_unknown).to_dict()


def execute_frontend_deployment(client: CoolifyClient, args: argparse.Namespace, mode: ExecutionMode, frontend: dict[str, Any], preflight_ok: bool, actions: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    runtime=frontend_runtime_readiness_result(); smoke=frontend_smoke_check_result(args.product); blockers=[]; uuid=frontend.get("uuid")
    if hasattr(client,"bind_frontend_deployment_preconditions"): client.bind_frontend_deployment_preconditions(preflight_ok)
    user_requested=bool(getattr(args,"deploy_frontend",False))
    if not user_requested:
        actions.append(ActionRecord("frontend_deployment","plan","not_requested").to_dict()); return frontend_deployment_result("not_requested"), runtime, smoke, blockers
    if not preflight_ok:
        actions.append(ActionRecord("frontend_deployment","plan","blocked_by_preconditions").to_dict()); return frontend_deployment_result("blocked_by_preconditions", user_requested=True), runtime, smoke, [{"resource":"frontend_deployment","reason":"preflight_incomplete"}]
    if not mode.is_apply:
        actions.append(ActionRecord("frontend_deployment","plan","would_verify_backend_then_deploy").to_dict()); return frontend_deployment_result("would_verify_backend_then_deploy", user_requested=True), runtime, smoke, blockers
    if not mode.frontend_deployment_enabled:
        return frontend_deployment_result("gates_incomplete", user_requested=True), runtime, smoke, [{"resource":"frontend_deployment","reason":"deployment_gates_incomplete"}]
    try:
        before=deployment_history(client,str(uuid)); before_ids={deployment_uuid_from_item(i) for i in before if deployment_uuid_from_item(i)}; active=active_deployments_for_application(before,str(uuid),"Frontend")
    except ProvisioningError:
        actions.append(ActionRecord("frontend_deployment","history","deployment_history_unverified").to_dict()); return frontend_deployment_result("deployment_history_unverified", user_requested=True), runtime, smoke, [{"resource":"frontend_deployment","reason":"deployment_history_unverified"}]
    if len(active)>1: return frontend_deployment_result("ambiguous_active_deployments", user_requested=True), runtime, smoke, [{"resource":"frontend_deployment","reason":"ambiguous_active_deployments"}]
    if len(active)==1:
        dep=frontend_deployment_result("adopted_active_deployment", user_requested=True, adopted_active=True, deployment_uuid=active[0]); actions.append(ActionRecord("frontend_deployment","adopt_active","adopted_active_deployment").to_dict())
    else:
        try:
            resp=client.deploy_frontend(str(uuid))
            try:
                du=parse_deploy_response_for_application(resp.data,str(uuid),"Frontend")
                dep=frontend_deployment_result("requested", user_requested=True, requested=True, post_attempted=True, deployment_uuid=du, request_attempted=True, write_performed=True); actions.append(frontend_deployment_request_action("requested"))
            except ProvisioningError as parse_exc:
                raise ProvisioningError("Accepted frontend deploy response shape was ambiguous after POST /deploy.", error_type="coolify_api", code=2, method="POST", path="/deploy", write_performed=True, write_outcome_unknown=True, request_attempted=True) from parse_exc
        except ProvisioningError as exc:
            if exc.http_status == 429:
                actions.append(frontend_deployment_request_action("rate_limited", exc)); return frontend_deployment_result("rate_limited", user_requested=True, post_attempted=True, request_attempted=True), runtime, smoke, [{"resource":"frontend_deployment","reason":"rate_limited"}]
            if exc.write_outcome_unknown:
                actions.append(frontend_deployment_request_action("write_outcome_unknown", exc))
                try: new=recover_new_deployment_candidates_for_application(before_ids, deployment_history(client,str(uuid)), str(uuid))
                except ProvisioningError: return frontend_deployment_result("frontend_deployment_outcome_unknown", user_requested=True, post_attempted=True, request_attempted=True, write_performed=exc.write_performed, write_outcome_unknown=True), runtime, smoke, [{"resource":"frontend_deployment","reason":"frontend_deployment_outcome_unknown"}]
                if len(new)==1:
                    dep=frontend_deployment_result("recovered_after_unknown", user_requested=True, post_attempted=True, recovered_after_unknown=True, deployment_uuid=new[0], request_attempted=True, write_performed=exc.write_performed); actions.append(ActionRecord("frontend_deployment","recover_unknown","recovered_after_unknown",request_attempted=True).to_dict())
                elif len(new)==0: return frontend_deployment_result("frontend_deployment_outcome_unknown", user_requested=True, post_attempted=True, request_attempted=True, write_performed=exc.write_performed, write_outcome_unknown=True), runtime, smoke, [{"resource":"frontend_deployment","reason":"frontend_deployment_outcome_unknown"}]
                else: return frontend_deployment_result("ambiguous_frontend_deployment_recovery", user_requested=True, post_attempted=True, request_attempted=True, write_performed=exc.write_performed, write_outcome_unknown=True), runtime, smoke, [{"resource":"frontend_deployment","reason":"ambiguous_frontend_deployment_recovery"}]
            if not exc.write_outcome_unknown:
                actions.append(frontend_deployment_request_action("request_failed", exc)); return frontend_deployment_result("request_failed", user_requested=True, post_attempted=True, request_attempted=True), runtime, smoke, [{"resource":"frontend_deployment","reason":exc.message}]
    polled=poll_frontend_deployment(client,args,str(dep["deployment_uuid"]),str(uuid)); preserved={k:dep.get(k) for k in ("user_requested","post_attempted","requested","adopted_active","recovered_after_unknown","request_attempted","write_performed","write_outcome_unknown")}; dep.update(polled); dep.update({k:v for k,v in preserved.items() if v}); actions.append(ActionRecord("frontend_deployment","poll",dep["status"],verified=dep.get("verified",False)).to_dict())
    if not dep.get("verified"): return dep, runtime, smoke, [{"resource":"frontend_deployment","reason":dep.get("status")}]
    runtime=verify_frontend_runtime(client,args,str(uuid)); actions.append(ActionRecord("frontend_runtime_readiness","verify",runtime["status"],verified=runtime.get("verified",False)).to_dict())
    if not runtime.get("healthy"): return dep, runtime, smoke, [{"resource":"frontend_runtime_readiness","reason":runtime.get("status")}]
    smoke=run_frontend_smoke(args); actions.append(ActionRecord("frontend_smoke_check","smoke",smoke["status"],verified=smoke.get("root_element_verified",False)).to_dict())
    if not smoke.get("root_element_verified"): blockers.append({"resource":"frontend_smoke_check","reason":smoke.get("status")})
    return dep,runtime,smoke,blockers


def recover_new_deployment_candidates(before_ids: set[str | None], after: list[dict[str, Any]], backend_uuid: str) -> list[str]:
    candidates=[]; seen=set()
    clean_before={str(item) for item in before_ids if item}
    for item in after:
        du=deployment_uuid_from_item(item)
        if not du:
            continue
        if du in clean_before or du in seen:
            continue
        if not deployment_belongs_to_backend(item, backend_uuid):
            continue
        candidates.append(du); seen.add(du)
    return candidates


def execute_backend_deployment(client: CoolifyClient, args: argparse.Namespace, mode: ExecutionMode, backend: dict[str, Any], preflight_ok: bool, actions: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    runtime=runtime_readiness_result(); smoke=smoke_check_result(args.product); blockers=[]; uuid=backend.get("uuid")
    if hasattr(client,"bind_backend_deployment_preconditions"):
        client.bind_backend_deployment_preconditions(preflight_ok)
    user_requested = bool(getattr(args,"deploy_backend",False))
    if not user_requested:
        actions.append(ActionRecord("backend_deployment","plan","not_requested",verified=False).to_dict())
        return backend_deployment_result("not_requested", user_requested=False), runtime, smoke, blockers
    if not preflight_ok:
        actions.append(ActionRecord("backend_deployment","plan","blocked_by_preconditions").to_dict())
        return backend_deployment_result("blocked_by_preconditions", user_requested=True), runtime, smoke, [{"resource":"backend_deployment","reason":"preflight_incomplete"}]
    if not mode.is_apply:
        actions.append(ActionRecord("backend_deployment","plan","would_deploy").to_dict())
        return backend_deployment_result("would_deploy", user_requested=True), runtime, smoke, blockers
    if not mode.backend_deployment_enabled:
        return backend_deployment_result("gates_incomplete", user_requested=True), runtime, smoke, [{"resource":"backend_deployment","reason":"deployment_gates_incomplete"}]
    try:
        before=deployment_history(client,str(uuid))
        before_ids={deployment_uuid_from_item(i) for i in before if deployment_uuid_from_item(i)}
        active=active_deployments(before,str(uuid))
    except ProvisioningError as exc:
        actions.append(ActionRecord("backend_deployment","history","deployment_history_unverified",verified=False).to_dict())
        return backend_deployment_result("deployment_history_unverified", user_requested=True), runtime, smoke, [{"resource":"backend_deployment","reason":"deployment_history_unverified"}]
    if len(active)>1:
        return backend_deployment_result("ambiguous_active_deployments", user_requested=True), runtime, smoke, [{"resource":"backend_deployment","reason":"ambiguous_active_deployments"}]
    if len(active)==1:
        dep=backend_deployment_result("adopted_active_deployment", user_requested=True, post_attempted=False, adopted_active=True, deployment_uuid=active[0]); actions.append(ActionRecord("backend_deployment","adopt_active","adopted_active_deployment",verified=False).to_dict())
    else:
        try:
            resp=client.deploy_backend(str(uuid))
            try:
                du=parse_deploy_response(resp.data,str(uuid))
                dep=backend_deployment_result("requested", user_requested=True, post_attempted=True, requested=True, deployment_uuid=du, request_attempted=True, write_performed=True)
                actions.append(backend_deployment_request_action("requested"))
            except ProvisioningError:
                raise ProvisioningError("Accepted deploy response shape was ambiguous after POST /deploy.", error_type="coolify_api", code=2, method="POST", path="/deploy", write_performed=True, write_outcome_unknown=True, request_attempted=True)
        except ProvisioningError as exc:
            if exc.http_status == 429:
                actions.append(backend_deployment_request_action("rate_limited", exc))
                return backend_deployment_result("rate_limited", user_requested=True, post_attempted=True, requested=True, request_attempted=True), runtime, smoke, [{"resource":"backend_deployment","reason":"rate_limited"}]
            if exc.write_outcome_unknown:
                actions.append(backend_deployment_request_action("write_outcome_unknown", exc))
                try:
                    after=deployment_history(client,str(uuid))
                    new=recover_new_deployment_candidates(before_ids, after, str(uuid))
                except ProvisioningError:
                    return backend_deployment_result("deployment_outcome_unknown", user_requested=True, post_attempted=True, request_attempted=True, write_performed=exc.write_performed, write_outcome_unknown=True), runtime, smoke, [{"resource":"backend_deployment","reason":"deployment_outcome_unknown"}]
                if len(new)==1:
                    dep=backend_deployment_result("recovered_after_unknown", user_requested=True, post_attempted=True, recovered_after_unknown=True, deployment_uuid=new[0], request_attempted=True, write_performed=exc.write_performed, write_outcome_unknown=False)
                    actions.append(ActionRecord("backend_deployment","recover_unknown","recovered_after_unknown",request_attempted=True,verified=False).to_dict())
                elif len(new)==0:
                    return backend_deployment_result("deployment_outcome_unknown", user_requested=True, post_attempted=True, request_attempted=True, write_performed=exc.write_performed, write_outcome_unknown=True), runtime, smoke, [{"resource":"backend_deployment","reason":"deployment_outcome_unknown"}]
                else:
                    return backend_deployment_result("ambiguous_deployment_recovery", user_requested=True, post_attempted=True, request_attempted=True, write_performed=exc.write_performed, write_outcome_unknown=True), runtime, smoke, [{"resource":"backend_deployment","reason":"ambiguous_deployment_recovery"}]
            else:
                actions.append(backend_deployment_request_action("request_failed", exc))
                return backend_deployment_result("request_failed", user_requested=True, post_attempted=True, request_attempted=True), runtime, smoke, [{"resource":"backend_deployment","reason":exc.message}]
    polled=poll_backend_deployment(client,args,str(dep["deployment_uuid"]),str(uuid)); preserved={"user_requested":dep.get("user_requested"),"post_attempted":dep.get("post_attempted"),"requested":dep.get("requested"),"adopted_active":dep.get("adopted_active"),"recovered_after_unknown":dep.get("recovered_after_unknown"),"request_attempted":dep.get("request_attempted"),"write_performed":dep.get("write_performed"),"write_outcome_unknown":dep.get("write_outcome_unknown")}; dep.update(polled); dep.update({k:v for k,v in preserved.items() if v}); actions.append(ActionRecord("backend_deployment","poll",dep["status"],verified=dep.get("verified",False)).to_dict())
    if not dep.get("verified"):
        return dep, runtime, smoke, [{"resource":"backend_deployment","reason":dep.get("status")}]
    runtime=verify_backend_runtime(client,args,str(uuid)); actions.append(ActionRecord("backend_runtime_readiness","verify",runtime["status"],verified=runtime.get("verified",False)).to_dict())
    if not runtime.get("healthy"):
        return dep, runtime, smoke, [{"resource":"backend_runtime_readiness","reason":runtime.get("status")}]
    smoke=run_backend_smoke(args); actions.append(ActionRecord("backend_smoke_check","smoke",smoke["status"],verified=smoke.get("body_verified",False)).to_dict())
    if not smoke.get("body_verified"):
        blockers.append({"resource":"backend_smoke_check","reason":smoke.get("status")})
    return dep,runtime,smoke,blockers


def global_status(mode: ExecutionMode, resources: list[dict[str, Any]], blockers: list[Any], actions: list[dict[str, Any]], readiness: dict[str, Any] | None = None, backend_deployment: dict[str, Any] | None = None, runtime: dict[str, Any] | None = None, smoke: dict[str, Any] | None = None, frontend_deployment: dict[str, Any] | None = None, frontend_runtime: dict[str, Any] | None = None, frontend_smoke: dict[str, Any] | None = None) -> str:
    statuses = {resource.get("status") for resource in resources}
    write_performed = any(action.get("write_performed") for action in actions) or any(resource.get("write_performed") for resource in resources)
    write_outcome_unknown = any(action.get("write_outcome_unknown") for action in actions) or any(resource.get("write_outcome_unknown") for resource in resources)
    if not mode.is_apply:
        return "dry_run"
    if backend_deployment and backend_deployment.get("user_requested") and mode.is_apply and (backend_deployment.get("status") != "finished" or not backend_deployment.get("verified") or not (runtime or {}).get("healthy") or not (runtime or {}).get("verified") or not (smoke or {}).get("body_verified")) :
        return "partial"
    if frontend_deployment and frontend_deployment.get("user_requested") and mode.is_apply and (frontend_deployment.get("status") != "finished" or not frontend_deployment.get("verified") or not (frontend_runtime or {}).get("healthy") or not (frontend_runtime or {}).get("verified") or not (frontend_smoke or {}).get("html_verified") or not (frontend_smoke or {}).get("root_element_verified")):
        return "partial"
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
    if mode.is_apply and postgres.get("status") in {"compatibility_unverified", "scope_unverified", "out_of_scope", "duplicate"}:
        blockers.append({"message": f"PostgreSQL status requires manual review: {postgres['status']}", "write_performed": False})
    if postgres.get("status") == "incompatible":
        blockers.append({"message": "Existing PostgreSQL contradicts desired state; manual recreation required.", "write_performed": False})
    context = {
        "product": args.product,
        "project_uuid": project.get("uuid"),
        "environment_uuid": environment.get("uuid"),
        "environment_name": "production",
        "server_uuid": server_uuid,
        "github_app_uuid": github_app_summary.get("uuid") or github_app_summary.get("id"),
        "github_app_id": github_app_summary.get("id"),
        "destination_uuid": destination.get("uuid") if isinstance(destination, dict) else None,
        "git_repository": args.repository,
    }
    discovery = {
        "project": {"name": args.coolify_project, "uuid": project.get("uuid")},
        "environment": {"name": "production", "uuid": environment.get("uuid")},
        "server": server_summary,
        "github_app": github_app_summary,
        "bindings": context,
        "existing_resources": discover_existing_resources(client, args.product, context, args),
    }
    if mode.is_apply:
        precondition_ok, pg_blockers, database_url = postgres_backend_live_precondition(client, args, postgres, postgresql_readiness)
        blockers.extend(pg_blockers)
    else:
        precondition_ok, pg_blockers = postgresql_backend_plan_precondition(args, postgres, postgresql_readiness)
        database_url = None
        if precondition_ok and postgres.get("status") in {"exists", "created"} and postgresql_readiness.get("internal_db_url_available") and postgresql_readiness.get("database_url_driver_ready"):
            try:
                database_url = postgres_driver_url(client, postgres)
            except ProvisioningError:
                precondition_ok = False
                pg_blockers.append({"resource": "postgresql", "reason": "database_url_driver_not_ready"})
        blockers.extend(pg_blockers)
    if not all(context.get(key) for key in ("project_uuid", "environment_uuid", "server_uuid", "github_app_uuid", "destination_uuid")):
        precondition_ok = False
        if not mode.is_apply:
            blockers.append({"resource": "backend_application", "reason": "binding_unverified"})
    backend = ensure_backend_application(client, args, context, mode, actions, precondition_ok)
    if backend.get("status") in {"duplicate", "compatibility_unverified", "scope_unverified", "recreate_required", "blocked", "created_unverified", "write_outcome_unknown"}:
        blockers.append({"resource": "backend_application", "status": backend.get("status")})
    backend_env = reconcile_backend_environment(client, args, backend, database_url, mode, actions) if backend.get("status") in {"exists", "created"} and precondition_ok else {"status": "blocked" if mode.is_apply else "planned", "variables": [], "blockers": []}
    blockers.extend(backend_env.get("blockers", []))
    deploy_preflight_ok, deploy_preflight_blockers = backend_deployment_preflight(args, postgres, postgresql_readiness, backend, backend_env, actions, require_env_status=mode.is_apply)
    blockers.extend(deploy_preflight_blockers if mode.is_apply and getattr(args, "deploy_backend", False) else [])
    backend_deployment, backend_runtime_readiness, backend_smoke_check, deploy_blockers = execute_backend_deployment(client, args, mode, backend, deploy_preflight_ok, actions)
    blockers.extend(deploy_blockers)
    frontend = ensure_frontend_application(client, args, context, mode, actions, backend)
    if frontend.get("status") in {"duplicate", "compatibility_unverified", "scope_unverified", "recreate_required", "blocked", "created_unverified", "write_outcome_unknown"}:
        blockers.append({"resource": "frontend_application", "status": frontend.get("status")})
    frontend_env = reconcile_frontend_environment(client, args, frontend, backend, mode, actions) if frontend.get("status") in {"exists", "created"} else {"status": "blocked" if mode.is_apply else "planned", "variables": [], "blockers": []}
    blockers.extend(frontend_env.get("blockers", []))
    frontend_structural_preflight_ok, frontend_structural_blockers = frontend_deployment_preflight(args, postgres, postgresql_readiness, backend, backend_env, frontend, frontend_env, backend_runtime_readiness, backend_smoke_check, actions, require_live=False)
    frontend_live_preflight_ok = False
    frontend_live_blockers: list[dict[str, Any]] = []
    # Frontend deployment live checks are intentionally after structural checks and Frontend
    # deployment gates. Missing gates must not trigger public Backend smoke.
    if getattr(args, "deploy_frontend", False) and mode.is_apply and not getattr(args, "deploy_backend", False) and frontend_structural_preflight_ok and mode.frontend_deployment_enabled:
        backend_runtime_readiness = verify_backend_runtime(client, args, str(backend.get("uuid")))
        actions.append(ActionRecord("backend_runtime_readiness", "verify_for_frontend", backend_runtime_readiness["status"], verified=backend_runtime_readiness.get("verified", False)).to_dict())
        if backend_runtime_readiness.get("healthy"):
            backend_smoke_check = run_backend_smoke(args)
            actions.append(ActionRecord("backend_smoke_check", "smoke_for_frontend", backend_smoke_check["status"], verified=backend_smoke_check.get("body_verified", False)).to_dict())
        frontend_live_preflight_ok, frontend_live_blockers = frontend_deployment_preflight(args, postgres, postgresql_readiness, backend, backend_env, frontend, frontend_env, backend_runtime_readiness, backend_smoke_check, actions, require_live=True)
    elif getattr(args, "deploy_backend", False):
        frontend_live_preflight_ok, frontend_live_blockers = frontend_deployment_preflight(args, postgres, postgresql_readiness, backend, backend_env, frontend, frontend_env, backend_runtime_readiness, backend_smoke_check, actions, require_live=True)
    frontend_deploy_preflight_ok = frontend_structural_preflight_ok if (not mode.is_apply or not mode.frontend_deployment_enabled) else frontend_live_preflight_ok
    if mode.is_apply and getattr(args, "deploy_frontend", False):
        blockers.extend(frontend_structural_blockers)
        if mode.frontend_deployment_enabled:
            blockers.extend(frontend_live_blockers)
    frontend_deployment, frontend_runtime_readiness, frontend_smoke_check, frontend_deploy_blockers = execute_frontend_deployment(client, args, mode, frontend, frontend_deploy_preflight_ok, actions)
    blockers.extend(frontend_deploy_blockers)
    plan = build_plan(args, discovery)
    plan["mode"] = mode.name
    frontend_backend_ok = backend_allows_frontend(backend)
    frontend_verified = frontend.get("status") in {"exists", "created"} and bool(frontend.get("uuid"))
    frontend_application_operation_enabled = mode.frontend_application_writes_enabled and frontend_backend_ok
    frontend_env_operation_enabled = mode.frontend_env_writes_enabled and frontend_backend_ok and frontend_verified
    allowed_posts = [] if not mode.is_apply else sorted({"project_create", "environment_create", "postgresql_create"} | ({"postgresql_start"} if mode.database_start_enabled else set()) | ({"backend_application_create"} if mode.backend_application_writes_enabled else set()) | ({"backend_env_create"} if mode.backend_env_writes_enabled else set()) | ({"frontend_application_create"} if frontend_application_operation_enabled else set()) | ({"frontend_env_create"} if frontend_env_operation_enabled else set()) | ({"backend_deploy"} if mode.backend_deployment_enabled and deploy_preflight_ok else set()) | ({"frontend_deploy"} if mode.frontend_deployment_enabled and frontend_deploy_preflight_ok else set()))
    plan["safety"] = {
        "writes_enabled": mode.is_apply and mode.allow_resource_writes and mode.confirm_product == mode.product,
        "allowed_post_operations": allowed_posts,
        "allowed_patch_operations": sorted((["backend_env_update"] if mode.is_apply and mode.backend_env_writes_enabled else []) + (["frontend_env_update"] if mode.is_apply and frontend_env_operation_enabled else [])),
        "database_start_enabled": mode.database_start_enabled,
        "forbidden_methods": ["PUT", "DELETE"],
        "deploy_actions_enabled": (mode.backend_deployment_enabled and deploy_preflight_ok) or (mode.frontend_deployment_enabled and frontend_deploy_preflight_ok),
        "backend_deployment_enabled": mode.backend_deployment_enabled and deploy_preflight_ok,
        "frontend_deployment_enabled": mode.frontend_deployment_enabled and frontend_deploy_preflight_ok,
        "non_allowlisted_deployments_forbidden": True,
        "deployment_cancel_forbidden": True,
        "rollback_forbidden": True,
        "application_lifecycle_forbidden": True,
        "application_creation_enabled": mode.backend_application_writes_enabled or mode.frontend_application_writes_enabled,
        "frontend_application_creation_enabled": frontend_application_operation_enabled,
        "frontend_application_gates_enabled": mode.frontend_application_writes_enabled,
        "frontend_backend_precondition_met": frontend_backend_ok,
        "frontend_env_operation_enabled": frontend_env_operation_enabled,
    }
    for resource in plan["resources"]:
        if resource["name"] == f"{args.product}-postgres-prod":
            resource.update({"status": postgres.get("status"), "provisioning": postgres})
        elif resource["name"] == backend_application_name(args.product):
            resource.update({"status": backend.get("status"), "provisioning": backend})
            resource["create_payload_preview"] = backend_payload_preview(backend_create_payload(args, context))
        elif resource["name"] == frontend_application_name(args.product):
            resource.update({"status": frontend.get("status"), "provisioning": frontend})
            if frontend.get("status") == "planned_after_backend" and frontend.get("reason") == "backend_dependency_planned":
                resource["reason"] = frontend["reason"]
            resource["create_payload_preview"] = frontend_payload_preview(frontend_create_payload(args, context))
        else:
            resource["status"] = "planned_later"
    status = global_status(mode, [project, environment, postgres, backend, backend_env, frontend, frontend_env], blockers, actions, postgresql_readiness, backend_deployment, backend_runtime_readiness, backend_smoke_check, frontend_deployment, frontend_runtime_readiness, frontend_smoke_check)
    return redact({
        **plan,
        "status": status,
        "plan_safe": status == "dry_run" and not blockers,
        "plan_blockers": blockers,
        "next_action": "apply" if status == "dry_run" and not blockers else "resolve_blockers",
        "exit_code": 0 if status in {"dry_run", "applied"} and not any(action.get("write_outcome_unknown") for action in actions) else 2,
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
        "frontend_application": frontend,
        "frontend_environment": frontend_env,
        "frontend_deployment": frontend_deployment,
        "frontend_runtime_readiness": frontend_runtime_readiness,
        "frontend_smoke_check": frontend_smoke_check,
        "backend_deployment": backend_deployment,
        "backend_runtime_readiness": backend_runtime_readiness,
        "backend_smoke_check": backend_smoke_check,
        "actions": actions,
        "warnings": warnings,
        "blockers": blockers,
        "contract": {
            "confirmed_endpoints": ["POST /api/v1/projects", "POST /api/v1/projects/{project_uuid}/environments", "POST /api/v1/databases/postgresql", "GET /api/v1/databases/{uuid}", "POST /api/v1/databases/{uuid}/start", "POST /api/v1/applications/private-github-app", "GET /api/v1/applications/{uuid}", "GET /api/v1/applications/{uuid}/envs", "POST /api/v1/applications/{uuid}/envs", "PATCH /api/v1/applications/{uuid}/envs", "POST /api/v1/deploy", "GET /api/v1/deployments/{deployment_uuid}", "GET /api/v1/deployments/applications/{backend_uuid}?skip=0&take=10", "GET /api/v1/deployments/applications/{frontend_uuid}?skip=0&take=10"],
            "postgresql_payload_preview": redact(postgresql_create_payload(args, "${project.uuid}", "${server.uuid}", "${generated_secret}")),
            "postgresql_payload_source": "Coolify OpenAPI/API reference for create PostgreSQL database; version is represented by the image field in this isolated builder pending any upstream contract rename.",
            "backend_application_payload_preview": backend_payload_preview(backend_create_payload(args, context)),
            "backend_env_keys": sorted(BACKEND_ENV_KEYS),
            "frontend_application_payload_preview": frontend_payload_preview(frontend_create_payload(args, context)),
            "frontend_env_keys": sorted(FRONTEND_ENV_KEYS),
            "backend_deploy_payload": {"uuid": "${backend_application.uuid}", "force": False},
            "frontend_deploy_payload": {"uuid": "${frontend_application.uuid}", "force": False},
            "deployment_statuses": sorted(DEPLOYMENT_OFFICIAL_STATUSES),
            "vite_api_url": api_domain(args.product),
            "upstream_sources": ["coollabsio/coolify openapi.yaml v4.x", "coollabsio/coolify routes/api.php v4.x", "coollabsio/coolify DeployController.php v4.x", "coollabsio/coolify ApplicationDeploymentStatus.php v4.x", "coollabsio/coolify ApplicationDeploymentQueue.php v4.x", "goodmorning-mx/goodmorning-mvp-template frontend Dockerfile/nginx/index/src/package docs dev"],
        },
    })


def provision(args: argparse.Namespace, *, client_factory: Callable[[], CoolifyClient] | None = None) -> dict[str, Any]:
    validate_args(args)
    validate_readiness_timing_args(args)
    validate_stage7_timing_args(args)
    mode = mode_from_args(args)
    if mode.is_apply:
        mode.assert_write_gates()
    client = client_factory() if client_factory else CoolifyClient.from_env(mode)
    if hasattr(client, "mode"):
        client.mode = mode
    return build_stage3_result(args, client, mode)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Build or apply a safe Coolify production resource plan.")
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
    parser.add_argument("--confirm-frontend-application")
    parser.add_argument("--confirm-frontend-environment")
    parser.add_argument("--deploy-backend", action="store_true")
    parser.add_argument("--confirm-backend-deployment")
    parser.add_argument("--deploy-frontend", action="store_true")
    parser.add_argument("--confirm-frontend-deployment")
    parser.add_argument("--backend-deployment-timeout", type=float, default=900.0)
    parser.add_argument("--backend-deployment-interval", type=float, default=5.0)
    parser.add_argument("--backend-deployment-max-poll-errors", type=int, default=3)
    parser.add_argument("--backend-runtime-timeout", type=float, default=180.0)
    parser.add_argument("--backend-runtime-interval", type=float, default=5.0)
    parser.add_argument("--backend-runtime-max-poll-errors", type=int, default=3)
    parser.add_argument("--backend-smoke-timeout", type=float, default=120.0)
    parser.add_argument("--backend-smoke-interval", type=float, default=5.0)
    parser.add_argument("--backend-smoke-request-timeout", type=float, default=10.0)
    parser.add_argument("--frontend-deployment-timeout", type=float, default=900.0)
    parser.add_argument("--frontend-deployment-interval", type=float, default=5.0)
    parser.add_argument("--frontend-deployment-max-poll-errors", type=int, default=3)
    parser.add_argument("--frontend-runtime-timeout", type=float, default=180.0)
    parser.add_argument("--frontend-runtime-interval", type=float, default=5.0)
    parser.add_argument("--frontend-runtime-max-poll-errors", type=int, default=3)
    parser.add_argument("--frontend-smoke-timeout", type=float, default=120.0)
    parser.add_argument("--frontend-smoke-interval", type=float, default=5.0)
    parser.add_argument("--frontend-smoke-request-timeout", type=float, default=10.0)
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
