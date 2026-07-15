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
SECRET_KEY_RE = re.compile(r"(PASSWORD|SECRET|TOKEN|KEY|PRIVATE|CREDENTIAL)", re.I)
PRODUCT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$")
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
            body = exc.read().decode("utf-8", errors="replace")
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


def validate_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    parsed = urllib.parse.urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ProvisioningError("COOLIFY_BASE_URL must be an http(s) URL.")
    return value


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


def select_unique(items: list[dict[str, Any]], expected: str, kind: str, *field_names: str) -> dict[str, Any]:
    matches = [item for item in items if any(str(field(item, name) or "") == expected for name in field_names)]
    if not matches:
        raise ProvisioningError(f"Could not find Coolify {kind} named exactly '{expected}'.", error_type="discovery")
    if len(matches) > 1:
        raise ProvisioningError(f"Found multiple Coolify {kind} records named exactly '{expected}'.", error_type="discovery")
    return matches[0]


def discover(client: CoolifyClient, args: argparse.Namespace) -> dict[str, Any]:
    projects = item_list(client.get("/projects"))
    project = select_unique(projects, args.coolify_project, "project", "name", "slug", "uuid")
    project_id = field(project, "uuid", "id")
    if not project_id:
        raise ProvisioningError("Selected Coolify project is missing an identifier.", error_type="discovery")

    detailed_project = client.get(f"/projects/{project_id}")
    project_details = detailed_project.get("data") if isinstance(detailed_project, dict) and isinstance(detailed_project.get("data"), dict) else detailed_project
    environments = item_list(project_details.get("environments") if isinstance(project_details, dict) else [])
    if not environments:
        environments = item_list(client.get(f"/projects/{project_id}/environments"))
    environment = select_unique(environments, args.environment, "environment", "name", "slug", "uuid")

    server = select_unique(item_list(client.get("/servers")), args.server, "server", "name", "uuid")
    github_app = select_unique(item_list(client.get("/github-apps")), args.github_app, "GitHub App", "name")

    existing = discover_existing_resources(client, args.product)
    return {
        "project": summary(project, "name", "uuid", "id"),
        "environment": summary(environment, "name", "uuid", "id"),
        "server": summary(server, "name", "uuid", "id"),
        "github_app": summary(github_app, "name", "uuid", "id"),
        "existing_resources": existing,
    }


def expected_resource_specs(product: str) -> dict[str, dict[str, Any]]:
    return {
        f"{product}-postgres-dev": {"kind": "database", "type": "postgresql"},
        f"{product}-api-dev": {"kind": "application"},
        f"{product}-frontend-dev": {"kind": "application"},
    }


def resource_kind(item: dict[str, Any]) -> str:
    source_kind = str(item.get("_discovery_source") or "")
    raw = str(field(item, "kind", "type", "resource_type") or "").lower()
    if source_kind == "databases" or "postgres" in raw:
        return "database"
    return "application"


def is_compose_application(item: dict[str, Any]) -> bool:
    raw = " ".join(str(field(item, name) or "") for name in ("build_pack", "buildpack", "type", "docker_compose_raw"))
    return "compose" in raw.lower()


def classify_expected_resource(name: str, expected: dict[str, Any], matches: list[dict[str, Any]]) -> dict[str, Any]:
    if not matches:
        return {"name": name, "status": "absent"}

    summaries = [summary(item, "name", "uuid", "id", "type", "kind", "build_pack", "buildpack") for item in matches]
    compatible = [item for item in matches if resource_kind(item) == expected["kind"]]
    compose_matches = [item for item in matches if expected["kind"] == "application" and is_compose_application(item)]

    if compose_matches:
        return {
            "name": name,
            "status": "legacy_compose_migration_required",
            "matches": summaries,
            "note": "Existing Compose Application detected; this stage only reports and does not migrate it.",
        }
    if len(matches) > 1:
        return {"name": name, "status": "duplicate", "matches": summaries}
    if not compatible:
        return {"name": name, "status": "incompatible", "matches": summaries}
    return {"name": name, "status": "found", "matches": summaries}


def discover_existing_resources(client: CoolifyClient, product: str) -> dict[str, Any]:
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

    expected = expected_resource_specs(product)
    classifications = []
    for name, spec in expected.items():
        matches = [item for item in collected if str(field(item, "name") or "") == name]
        classifications.append(classify_expected_resource(name, spec, matches))

    unexpected_same_product_prefix = [
        summary(item, "name", "uuid", "id", "type", "kind", "build_pack", "buildpack")
        for item in collected
        if str(field(item, "name") or "").startswith(f"{product}-")
        and str(field(item, "name") or "") not in expected
    ]

    return {
        "classification": classifications,
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


def application_create_payload_preview(desired_state: dict[str, Any]) -> dict[str, Any]:
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
    }


def build_plan(args: argparse.Namespace, discovery: dict[str, Any]) -> dict[str, Any]:
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
                    "name": postgres_desired_state["name"],
                    "type": "postgresql",
                    "version": POSTGRES_VERSION,
                    "is_public": False,
                },
            },
            {
                "name": backend_desired_state["name"],
                "kind": "application",
                "desired_state": backend_desired_state,
                "create_payload_preview": application_create_payload_preview(backend_desired_state),
            },
            {
                "name": frontend_desired_state["name"],
                "kind": "application",
                "desired_state": frontend_desired_state,
                "create_payload_preview": application_create_payload_preview(frontend_desired_state),
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
