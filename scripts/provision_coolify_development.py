#!/usr/bin/env python3
"""Provision a Coolify development environment for a GoodMorning product."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

DEFAULT_PRODUCT = "factory-smoke-test"
DEFAULT_REPOSITORY = "goodmorning-mx/factory-smoke-test"
DEFAULT_BRANCH = "dev"
DEFAULT_COMPOSE = "/compose.yaml"
DEFAULT_DOMAIN = "dev.factory-smoke-test.goodmorning.mx"
DEFAULT_ENVIRONMENT = "development"
COOLIFY_DEFAULT_ENVIRONMENT = "production"
COOLIFY_DEVELOPMENT_ENVIRONMENT = "dev"
DEFAULT_COOLIFY_GITHUB_APP_NAME = "goodmorning-mx-coolify"
TIMEOUT_SECONDS = 30
COMPOSE_READY_TIMEOUT_SECONDS = 120
COMPOSE_READY_POLL_SECONDS = 5
SENSITIVE_ENV = ("COOLIFY_API_TOKEN",)
ENVIRONMENT_UUID_RE = re.compile(r"^[a-z0-9]{24}$")


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


def request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    allowed_statuses: tuple[int, ...] = (),
) -> Response:
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
        if exc.code in allowed_statuses:
            return Response(exc.code, parsed, dict(exc.headers))
        if isinstance(parsed, dict):
            detail = {
                key: parsed[key]
                for key in ("message", "errors", "conflicts", "warning")
                if key in parsed
            }
            message = json.dumps(detail or parsed, sort_keys=True)
        else:
            message = raw
        raise ProvisioningError(
            f"Coolify API {exc.code} for {method} {path}: {message}", code=2
        )
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ProvisioningError(
            f"Network timeout or connection failure for {method} {path}: {exc}", code=2
        )
    except json.JSONDecodeError as exc:
        raise ProvisioningError(
            f"Malformed JSON response for {method} {path}: {exc}", code=2
        )


def items(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in (
            "data",
            "items",
            "projects",
            "servers",
            "sources",
            "applications",
            "github_apps",
            "githubApps",
        ):
            value = data.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def field(item: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in item and item[name] not in (None, ""):
            return item[name]
    return None


def discover_project(
    projects: list[dict[str, Any]], product: str
) -> dict[str, Any] | None:
    for project in projects:
        name = str(field(project, "name", "slug", "uuid") or "").lower()
        if name == product.lower() or product.lower() in name:
            return project
    return None


def project_environment(
    project: dict[str, Any], environment: str
) -> dict[str, Any] | None:
    raw_environments = field(project, "environments", "environment")
    if isinstance(raw_environments, list):
        environments = [item for item in raw_environments if isinstance(item, dict)]
        for item in environments:
            name = str(field(item, "name", "slug", "uuid") or "").lower()
            if name == environment.lower():
                return item
    return None


def project_environment_exists(project: dict[str, Any], environment: str) -> bool:
    return project_environment(project, environment) is not None


def create_project(args: argparse.Namespace) -> dict[str, Any]:
    created = request("POST", "/projects", body={"name": args.product}).data
    if not isinstance(created, dict):
        raise ProvisioningError("Malformed Coolify project creation response.")
    project = created.get("data") if isinstance(created.get("data"), dict) else created
    if not isinstance(project, dict) or not field(project, "uuid", "id"):
        raise ProvisioningError(
            "Coolify project creation response did not include a project identifier."
        )
    return project


def project_details(project: dict[str, Any], *, dry_run: bool) -> dict[str, Any]:
    project_uuid = field(project, "uuid", "id")
    if not project_uuid:
        if dry_run:
            return project
        raise ProvisioningError(
            "Coolify project is missing an identifier required to query project details."
        )
    detailed = request("GET", f"/projects/{project_uuid}").data
    if not isinstance(detailed, dict):
        raise ProvisioningError("Malformed Coolify project details response.")
    data = detailed.get("data") if isinstance(detailed.get("data"), dict) else detailed
    if not isinstance(data, dict):
        raise ProvisioningError("Malformed Coolify project details response.")
    return data


def coolify_environment_for_stage(
    project: dict[str, Any], stage: str
) -> dict[str, Any]:
    if stage != DEFAULT_ENVIRONMENT:
        raise ProvisioningError(
            "Only the GoodMorning development stage can be provisioned by this workflow."
        )
    environment = project_environment(project, COOLIFY_DEVELOPMENT_ENVIRONMENT)
    if environment is None:
        raise ProvisioningError(
            "Coolify project details did not include the dev environment required for the GoodMorning development stage."
        )
    return environment


def environment_summary(
    environment: dict[str, Any], *, created: bool = False
) -> dict[str, Any]:
    return {
        "uuid": field(environment, "uuid", "id"),
        "name": field(environment, "name", "slug"),
        "created": created,
    }


def project_environments(project_uuid: Any) -> list[dict[str, Any]]:
    if not project_uuid:
        raise ProvisioningError(
            "Coolify project identifier is required before querying environments."
        )
    return items(request("GET", f"/projects/{project_uuid}/environments").data)


def create_project_environment(project_uuid: Any, name: str) -> Response:
    return request(
        "POST",
        f"/projects/{project_uuid}/environments",
        body={"name": name},
        allowed_statuses=(201, 409, 422),
    )


def ensure_dev_environment(project_uuid: Any) -> dict[str, Any]:
    environments = project_environments(project_uuid)
    existing = project_environment(
        {"environments": environments}, COOLIFY_DEVELOPMENT_ENVIRONMENT
    )
    if existing is not None:
        return environment_summary(existing, created=False)

    response = create_project_environment(project_uuid, COOLIFY_DEVELOPMENT_ENVIRONMENT)
    if response.status == 201:
        data = (
            response.data.get("data")
            if isinstance(response.data, dict)
            and isinstance(response.data.get("data"), dict)
            else response.data
        )
        if not isinstance(data, dict):
            raise ProvisioningError("Malformed Coolify environment creation response.")
        environment = {
            "uuid": field(data, "uuid", "id"),
            "name": COOLIFY_DEVELOPMENT_ENVIRONMENT,
        }
        if not environment["uuid"]:
            raise ProvisioningError(
                "Coolify environment creation response did not include an environment identifier."
            )
        return environment_summary(environment, created=True)

    if response.status == 409:
        environments = project_environments(project_uuid)
        existing = project_environment(
            {"environments": environments}, COOLIFY_DEVELOPMENT_ENVIRONMENT
        )
        if existing is not None:
            return environment_summary(existing, created=False)
        raise ProvisioningError(
            "Coolify reported the dev environment already exists, but it was not returned by the environments endpoint.",
            code=2,
        )

    if response.status == 422:
        detail = (
            response.data
            if isinstance(response.data, dict)
            else {"message": response.data}
        )
        raise ProvisioningError(
            "Coolify API 422 for POST "
            f"/projects/{project_uuid}/environments: "
            f"{json.dumps(detail, sort_keys=True)}",
            code=2,
        )

    raise ProvisioningError(
        f"Unexpected Coolify API {response.status} for POST /projects/{project_uuid}/environments.",
        code=2,
    )


def docker_compose_raw_loaded(application: Any) -> bool:
    data = (
        application.get("data")
        if isinstance(application, dict) and isinstance(application.get("data"), dict)
        else application
    )
    return isinstance(data, dict) and bool(data.get("docker_compose_raw"))


def wait_for_docker_compose_raw(application_uuid: Any) -> None:
    if not application_uuid:
        raise ProvisioningError(
            "Coolify application identifier is required before waiting for Docker Compose parsing."
        )
    deadline = time.monotonic() + COMPOSE_READY_TIMEOUT_SECONDS
    while True:
        response = request("GET", f"/applications/{application_uuid}")
        if docker_compose_raw_loaded(response.data):
            return
        if time.monotonic() >= deadline:
            raise ProvisioningError(
                "Timed out waiting for Coolify to load docker_compose_raw before applying Docker Compose domains.",
                code=2,
            )
        time.sleep(COMPOSE_READY_POLL_SECONDS)


def discover_server(servers: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [
        s
        for s in servers
        if not field(s, "is_build_server")
        and str(field(s, "name", "description", "uuid") or "").lower() != "production"
    ]
    if len(candidates) == 1:
        return candidates[0]
    for server in candidates:
        if str(field(server, "name", "description") or "").lower() in {
            "development",
            "dev",
            "staging",
        }:
            return server
    raise ProvisioningError(
        "Unable to identify one non-production Coolify server unambiguously."
    )


def is_private_repository(repository: str) -> bool:
    return repository.lower().startswith("goodmorning-mx/")


def is_public_github_source(source: dict[str, Any]) -> bool:
    name = str(field(source, "name") or "").strip().lower()
    source_type = str(field(source, "type") or "").strip().lower()
    return name == "public github" or source_type == "public github"


def discover_github_app(
    sources: list[dict[str, Any]], app_name: str, repository: str
) -> dict[str, Any]:
    matches = [s for s in sources if str(field(s, "name") or "") == app_name]
    if not matches:
        raise ProvisioningError(
            f"Coolify GitHub App source named {app_name!r} was not found."
        )
    if len(matches) > 1:
        raise ProvisioningError(
            f"Coolify GitHub App source named {app_name!r} is duplicated; exact source selection is ambiguous."
        )
    match = matches[0]
    if is_private_repository(repository) and is_public_github_source(match):
        raise ProvisioningError(
            "Public GitHub cannot be used for private GoodMorning repositories; select the GoodMorning Coolify GitHub App source."
        )
    return match


def docker_compose_domains(development_domain: str) -> list[dict[str, str]]:
    frontend_host = (
        development_domain.removeprefix("https://")
        .removeprefix("http://")
        .strip()
        .rstrip("/")
    )
    return [
        {"name": "frontend", "domain": f"https://{frontend_host}"},
        {"name": "backend", "domain": f"https://api.{frontend_host}"},
    ]


def domain_patch_payload(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "docker_compose_domains": docker_compose_domains(args.domain),
        "force_domain_override": False,
    }


def application_summary(application: dict[str, Any]) -> dict[str, Any]:
    data = (
        application.get("data")
        if isinstance(application.get("data"), dict)
        else application
    )
    return {"uuid": field(data, "uuid", "id"), "name": field(data, "name")}


def reconcile_application_domains(
    application_uuid: Any, args: argparse.Namespace
) -> dict[str, Any]:
    if not application_uuid:
        raise ProvisioningError(
            "Coolify application response did not include an identifier required to configure Docker Compose domains."
        )
    return request(
        "PATCH", f"/applications/{application_uuid}", body=domain_patch_payload(args)
    ).data


def existing_application(
    applications: list[dict[str, Any]], args: argparse.Namespace
) -> dict[str, Any] | None:
    repo_url = f"https://github.com/{args.repository}"
    for app in applications:
        values = {
            str(v).lower()
            for v in app.values()
            if isinstance(v, (str, int, float, bool))
        }
        text = " ".join(values)
        if (
            args.domain.lower() in text
            or (args.repository.lower() in text and args.branch.lower() in text)
            or repo_url.lower() in text
        ):
            return app
    return None


def github_app_uuid(github_app: dict[str, Any]) -> Any:
    uuid = field(github_app, "uuid")
    if not uuid:
        raise ProvisioningError(
            "Selected Coolify GitHub App source did not include a uuid; refusing to use its numeric id for private repository creation."
        )
    return uuid


def creation_payload(
    args: argparse.Namespace,
    project: dict[str, Any],
    server: dict[str, Any],
    github_app: dict[str, Any],
) -> dict[str, Any]:
    return {
        "project_uuid": field(project, "uuid", "id"),
        "server_uuid": field(server, "uuid", "id"),
        "environment_name": COOLIFY_DEVELOPMENT_ENVIRONMENT,
        "github_app_uuid": github_app_uuid(github_app),
        "git_repository": args.repository,
        "git_branch": args.branch,
        "build_pack": "dockercompose",
        "base_directory": "/",
        "docker_compose_location": args.compose,
        "name": f"{args.product}-{args.environment}",
        "instant_deploy": False,
    }


def validate_inputs(args: argparse.Namespace) -> None:
    if args.branch != DEFAULT_BRANCH:
        raise ProvisioningError(
            "Only the dev branch can be provisioned by this development workflow."
        )
    parsed = urlparse(args.domain if "://" in args.domain else f"//{args.domain}")
    host = parsed.netloc or parsed.path
    if not host.startswith("dev."):
        raise ProvisioningError(
            "Development domain must start with dev.; production domains are not allowed."
        )
    if "/" in host or not host.strip() or parsed.path not in ("", host):
        raise ProvisioningError(
            "Development domain must be a host name without a path."
        )


def provision(args: argparse.Namespace) -> dict[str, Any]:
    validate_inputs(args)
    projects = items(request("GET", "/projects").data)
    servers = items(request("GET", "/servers").data)
    github_apps = items(request("GET", "/github-apps").data)
    applications = items(request("GET", "/applications").data)
    project = discover_project(projects, args.product)
    planned_project = None
    project_created = False
    if project is None:
        planned_project = {"name": args.product}
        if args.dry_run:
            project = {"uuid": None, "name": args.product}
        else:
            project = create_project(args)
            project_created = True
    if field(project, "uuid", "id"):
        project_uuid = field(project, "uuid", "id")
        project = project_details(project, dry_run=args.dry_run)
        if args.dry_run:
            coolify_environment = project_environment(
                project, COOLIFY_DEVELOPMENT_ENVIRONMENT
            ) or {"name": COOLIFY_DEVELOPMENT_ENVIRONMENT}
        else:
            ensured_environment = ensure_dev_environment(project_uuid)
            project = project_details(project, dry_run=args.dry_run)
            coolify_environment = (
                project_environment(project, COOLIFY_DEVELOPMENT_ENVIRONMENT)
                or ensured_environment
            )
    else:
        coolify_environment = {"name": COOLIFY_DEVELOPMENT_ENVIRONMENT}
    server = discover_server(servers)
    github_app = discover_github_app(
        github_apps, args.coolify_github_app_name, args.repository
    )
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
        "docker_compose_domains": docker_compose_domains(args.domain),
        "project": {
            "uuid": field(project, "uuid", "id"),
            "name": field(project, "name", "slug"),
            "created": project_created,
        },
        "environment": {
            "name": args.environment,
            "coolify_name": field(coolify_environment, "name", "slug", "uuid"),
            "created": False,
        },
        "server": {
            "uuid": field(server, "uuid", "id"),
            "name": field(server, "name", "description"),
        },
        "github_app": {
            "uuid": github_app_uuid(github_app),
            "name": field(github_app, "name"),
        },
        "coolify_github_app_name": args.coolify_github_app_name,
    }
    if planned_project is not None:
        result["would_create_project"] = planned_project
    if existing:
        result["application"] = application_summary(existing)
        if args.dry_run:
            result["would_patch"] = domain_patch_payload(args)
        else:
            wait_for_docker_compose_raw(result["application"]["uuid"])
            reconcile_application_domains(result["application"]["uuid"], args)
            result["domain_reconciled"] = True
        return result
    if args.dry_run:
        result["would_create"] = payload
        result["would_patch"] = domain_patch_payload(args)
        return result
    created = request("POST", "/applications/private-github-app", body=payload).data
    if not isinstance(created, dict):
        raise ProvisioningError("Malformed Coolify application creation response.")
    result["application"] = application_summary(created)
    wait_for_docker_compose_raw(result["application"]["uuid"])
    reconcile_application_domains(result["application"]["uuid"], args)
    result["domain_reconciled"] = True
    return result


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Provision a Coolify development environment for a GoodMorning product."
    )
    p.add_argument("--product", default=DEFAULT_PRODUCT)
    p.add_argument("--repository", default=DEFAULT_REPOSITORY)
    p.add_argument("--branch", default=DEFAULT_BRANCH)
    p.add_argument("--compose", default=DEFAULT_COMPOSE)
    p.add_argument("--domain", default=DEFAULT_DOMAIN)
    p.add_argument("--environment", default=DEFAULT_ENVIRONMENT)
    p.add_argument("--coolify-github-app-name", default=DEFAULT_COOLIFY_GITHUB_APP_NAME)
    p.add_argument("--dry-run", default="true", choices=("true", "false"))
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    args.dry_run = args.dry_run == "true"
    try:
        print(json.dumps(provision(args), sort_keys=True))
        return 0
    except ProvisioningError as exc:
        print(
            json.dumps({"status": exc.status, "error": exc.message}, sort_keys=True),
            file=sys.stderr,
        )
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
