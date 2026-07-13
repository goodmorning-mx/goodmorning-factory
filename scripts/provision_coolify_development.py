#!/usr/bin/env python3
"""Provision a Coolify development environment for a GoodMorning product."""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_PRODUCT = "factory-smoke-test"
DEFAULT_REPOSITORY = "goodmorning-mx/factory-smoke-test"
DEFAULT_BRANCH = "dev"
DEFAULT_COMPOSE = "/compose.yaml"
DEFAULT_DOMAIN = "dev.factory-smoke-test.goodmorning.mx"
DEFAULT_ENVIRONMENT = "development"
TIMEOUT_SECONDS = 30
SENSITIVE_ENV = ("COOLIFY_API_TOKEN",)


class ProvisioningError(Exception):
    def __init__(self, message: str, *, status: str = "error", code: int = 1):
        super().__init__(message)
        self.message = redact(message)
        self.status = status
        self.code = code


@dataclass
class Response:
    status: int
    data: Any
    headers: dict[str, str]


def redact(text: str) -> str:
    for key in SENSITIVE_ENV:
        value = os.environ.get(key)
        if value:
            text = text.replace(value, "[REDACTED]")
    return text


def base_url() -> str:
    value = os.environ.get("COOLIFY_BASE_URL", "").strip().rstrip("/")
    if not value:
        raise ProvisioningError("COOLIFY_BASE_URL environment variable is required.")
    return value


def token() -> str:
    value = os.environ.get("COOLIFY_API_TOKEN", "")
    if not value:
        raise ProvisioningError("COOLIFY_API_TOKEN environment variable is required.")
    return value


def api_url(path: str) -> str:
    return f"{base_url()}/api/v1{path}"


def request(method: str, path: str, *, body: dict[str, Any] | None = None) -> Response:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        api_url(path),
        data=data,
        method=method,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token()}",
            "Content-Type": "application/json",
            "User-Agent": "goodmorning-factory-coolify-provisioner",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8")
            parsed = json.loads(raw) if raw else None
            return Response(resp.status, parsed, dict(resp.headers))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"message": raw}
        message = parsed.get("message") if isinstance(parsed, dict) else raw
        raise ProvisioningError(f"Coolify API {exc.code} for {method} {path}: {message}", code=2)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ProvisioningError(f"Network timeout or connection failure for {method} {path}: {exc}", code=2)
    except json.JSONDecodeError as exc:
        raise ProvisioningError(f"Malformed JSON response for {method} {path}: {exc}", code=2)


def items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("data", "items", "projects", "servers", "sources", "applications", "github_apps", "githubApps"):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def field(item: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in item and item[name] not in (None, ""):
            return item[name]
    return None


def discover_project(projects: list[dict[str, Any]], product: str) -> dict[str, Any]:
    for project in projects:
        name = str(field(project, "name", "slug", "uuid") or "").lower()
        if name in {product.lower(), "goodmorning", "goodmorning products", "products"} or product.lower() in name:
            return project
    if len(projects) == 1:
        return projects[0]
    raise ProvisioningError(f"Unable to identify a Coolify project for {product}; create or name the project unambiguously.")


def discover_server(servers: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [s for s in servers if not field(s, "is_build_server") and str(field(s, "name", "description", "uuid") or "").lower() != "production"]
    if len(candidates) == 1:
        return candidates[0]
    for server in candidates:
        if str(field(server, "name", "description") or "").lower() in {"development", "dev", "staging"}:
            return server
    raise ProvisioningError("Unable to identify one non-production Coolify server unambiguously.")


def discover_github_app(sources: list[dict[str, Any]]) -> dict[str, Any]:
    github_sources = [s for s in sources if "github" in str(field(s, "type", "name", "html_url", "api_url") or "").lower()]
    if len(github_sources) == 1:
        return github_sources[0]
    raise ProvisioningError("Unable to identify one Coolify GitHub App source unambiguously.")


def existing_application(applications: list[dict[str, Any]], args: argparse.Namespace) -> dict[str, Any] | None:
    repo_url = f"https://github.com/{args.repository}"
    for app in applications:
        values = {str(v).lower() for v in app.values() if isinstance(v, (str, int, float, bool))}
        text = " ".join(values)
        if args.domain.lower() in text or (args.repository.lower() in text and args.branch.lower() in text) or repo_url.lower() in text:
            return app
    return None


def creation_payload(args: argparse.Namespace, project: dict[str, Any], server: dict[str, Any], github_app: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": f"{args.product}-{args.environment}",
        "project_uuid": field(project, "uuid", "id"),
        "server_uuid": field(server, "uuid", "id"),
        "environment_name": args.environment,
        "git_repository": args.repository,
        "git_branch": args.branch,
        "git_source_id": field(github_app, "id", "uuid"),
        "build_pack": "dockercompose",
        "docker_compose_location": args.compose,
        "base_directory": "/",
        "domains": args.domain,
        "instant_deploy": False,
    }


def validate_inputs(args: argparse.Namespace) -> None:
    if args.branch != DEFAULT_BRANCH:
        raise ProvisioningError("Only the dev branch can be provisioned by this development workflow.")
    if not args.domain.startswith("dev."):
        raise ProvisioningError("Development domain must start with dev.; production domains are not allowed.")


def provision(args: argparse.Namespace) -> dict[str, Any]:
    validate_inputs(args)
    projects = items(request("GET", "/projects").data)
    servers = items(request("GET", "/servers").data)
    github_apps = items(request("GET", "/github-apps").data)
    applications = items(request("GET", "/applications").data)
    project = discover_project(projects, args.product)
    server = discover_server(servers)
    github_app = discover_github_app(github_apps)
    existing = existing_application(applications, args)
    payload = creation_payload(args, project, server, github_app)
    result = {
        "status": "exists" if existing else ("dry_run" if args.dry_run else "created"),
        "dry_run": args.dry_run,
        "product": args.product,
        "repository": args.repository,
        "branch": args.branch,
        "compose": args.compose,
        "domain": args.domain,
        "project": {"uuid": field(project, "uuid", "id"), "name": field(project, "name", "slug")},
        "server": {"uuid": field(server, "uuid", "id"), "name": field(server, "name", "description")},
        "github_app": {"id": field(github_app, "id", "uuid"), "name": field(github_app, "name")},
    }
    if existing:
        result["application"] = {"uuid": field(existing, "uuid", "id"), "name": field(existing, "name")}
        return result
    if args.dry_run:
        result["would_create"] = payload
        return result
    created = request("POST", "/applications/dockercompose", body=payload).data
    if not isinstance(created, dict):
        raise ProvisioningError("Malformed Coolify application creation response.")
    result["application"] = {"uuid": field(created, "uuid", "id"), "name": field(created, "name")}
    return result


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Provision a Coolify development environment for a GoodMorning product.")
    p.add_argument("--product", default=DEFAULT_PRODUCT)
    p.add_argument("--repository", default=DEFAULT_REPOSITORY)
    p.add_argument("--branch", default=DEFAULT_BRANCH)
    p.add_argument("--compose", default=DEFAULT_COMPOSE)
    p.add_argument("--domain", default=DEFAULT_DOMAIN)
    p.add_argument("--environment", default=DEFAULT_ENVIRONMENT)
    p.add_argument("--dry-run", default="true", choices=("true", "false"))
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.dry_run = args.dry_run == "true"
    try:
        print(json.dumps(provision(args), sort_keys=True))
        return 0
    except ProvisioningError as exc:
        print(json.dumps({"status": exc.status, "error": exc.message}, sort_keys=True), file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
