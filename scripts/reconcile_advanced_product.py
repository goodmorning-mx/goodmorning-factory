#!/usr/bin/env python3
"""Plan or safely reconcile a resources-v3 product in Coolify development.

The advanced reconciler is additive: it never deletes, renames, stops or mutates
the retained legacy resource.  Apply is guarded by an environment switch and an
exact product confirmation.  Secret values may be copied from the legacy
application to newly created applications, but are never returned or logged.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

try:
    from advanced_product_contract import ContractError, load_manifest, resource_names
except ModuleNotFoundError:  # Imported as scripts.reconcile_advanced_product in tests.
    from scripts.advanced_product_contract import ContractError, load_manifest, resource_names

TIMEOUT = 30
TERMINAL_DEPLOYMENT = {"finished", "failed", "cancelled", "canceled"}
SUCCESS_DEPLOYMENT = {"finished"}
SENSITIVE_NAME = ("PASSWORD", "SECRET", "TOKEN", "KEY", "DATABASE_URL", "REDIS_URL")


class ReconcileError(RuntimeError):
    pass


def _bool_env(name: str) -> bool:
    return os.environ.get(name, "").lower() == "true"


def _items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("data", "items", "results"):
            if isinstance(value.get(key), list):
                return [item for item in value[key] if isinstance(item, dict)]
    return []


def _field(item: dict[str, Any], *names: str) -> Any:
    for name in names:
        current: Any = item
        for part in name.split("."):
            if not isinstance(current, dict):
                current = None
                break
            current = current.get(part)
        if current not in (None, ""):
            return current
    return None


def _redacted(value: Any) -> Any:
    if isinstance(value, dict):
        environment_key = value.get("key")
        return {
            key: (
                "[REDACTED]"
                if any(part in key.upper() for part in SENSITIVE_NAME)
                or (
                    key in {"value", "real_value"}
                    and isinstance(environment_key, str)
                    and any(part in environment_key.upper() for part in SENSITIVE_NAME)
                )
                else _redacted(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redacted(item) for item in value]
    return value


@dataclass
class Response:
    status: int
    data: Any


class Coolify:
    def __init__(self, base_url: str, token: str, *, apply: bool):
        parsed = urllib.parse.urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ReconcileError("COOLIFY_BASE_URL must be an HTTPS URL.")
        if not token:
            raise ReconcileError("COOLIFY_API_TOKEN is required.")
        self.base_url = base_url.rstrip("/") + "/api/v1"
        self.token = token
        self.apply = apply

    def request(self, method: str, path: str, body: dict[str, Any] | None = None, expected=(200,)) -> Response:
        if method not in {"GET", "POST", "PATCH"}:
            raise ReconcileError(f"Forbidden Coolify method {method}.")
        if method != "GET" and not self.apply:
            raise ReconcileError("Plan mode is read-only.")
        if any(word in path.lower() for word in ("/delete", "/stop", "/restart", "/rollback", "/cancel")):
            raise ReconcileError("Destructive or lifecycle Coolify endpoint is forbidden.")
        payload = None if body is None else json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=payload,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "goodmorning-factory-resources-v3",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                raw = response.read().decode("utf-8")
                data = json.loads(raw) if raw else {}
                if response.status not in expected:
                    raise ReconcileError(f"Unexpected Coolify response {response.status} for {method} {path}.")
                return Response(response.status, data)
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                message = json.loads(raw).get("message", "Coolify request failed.")
            except (json.JSONDecodeError, AttributeError):
                message = "Coolify request failed."
            raise ReconcileError(f"Coolify {method} {path} failed with HTTP {exc.code}: {message}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise ReconcileError(f"Coolify {method} {path} failed: {exc}") from exc

    def get(self, path: str) -> Any:
        return self.request("GET", path).data

    def post(self, path: str, body: dict[str, Any], expected=(200, 201)) -> Any:
        return self.request("POST", path, body, expected).data

    def patch(self, path: str, body: dict[str, Any], expected=(200, 201)) -> Any:
        return self.request("PATCH", path, body, expected).data


def _unique(items: list[dict[str, Any]], name: str, kind: str) -> dict[str, Any] | None:
    matches = [item for item in items if str(_field(item, "name") or "") == name]
    if len(matches) > 1:
        raise ReconcileError(f"Duplicate {kind} resources named {name!r}.")
    return matches[0] if matches else None


def _uuid(value: Any, kind: str) -> str:
    identifier = _field(value, "uuid", "id") if isinstance(value, dict) else None
    if not identifier:
        raise ReconcileError(f"Coolify did not return a {kind} UUID.")
    return str(identifier)


def _discover_context(client: Coolify, product: str, server_name: str, github_app_name: str, destination_uuid: str) -> dict[str, str | None]:
    servers = _items(client.get("/servers"))
    server = _unique(servers, server_name, "server")
    if not server:
        raise ReconcileError(f"Coolify server {server_name!r} was not found.")
    projects = _items(client.get("/projects"))
    project = _unique(projects, product, "project")
    environment = None
    if project:
        project_uuid = _uuid(project, "project")
        detail = client.get(f"/projects/{urllib.parse.quote(project_uuid, safe='')}")
        environment = _unique(_items(_field(detail, "environments") or detail), "dev", "environment")
    apps = _items(client.get("/github-apps"))
    github_app = _unique(apps, github_app_name, "GitHub App")
    if not github_app:
        raise ReconcileError(f"Coolify GitHub App {github_app_name!r} was not found.")
    return {
        "server_uuid": _uuid(server, "server"),
        "project_uuid": _uuid(project, "project") if project else None,
        "environment_uuid": _uuid(environment, "environment") if environment else None,
        "github_app_uuid": _uuid(github_app, "GitHub App"),
        "destination_uuid": destination_uuid,
    }


def _ensure_project_environment(client: Coolify, manifest: dict[str, Any], context: dict[str, str | None], actions: list[dict[str, Any]]) -> None:
    product = manifest["product"]
    if context["project_uuid"] is None:
        if not client.apply:
            actions.append({"resource": product, "kind": "project", "action": "would_create"})
            return
        created = client.post("/projects", {"name": product, "description": f"GoodMorning {product} resources-v3 development project."})
        context["project_uuid"] = _uuid(created, "project")
        actions.append({"resource": product, "kind": "project", "action": "created"})
    else:
        actions.append({"resource": product, "kind": "project", "action": "reused"})
    if context["environment_uuid"] is None:
        if not client.apply:
            actions.append({"resource": "dev", "kind": "environment", "action": "would_create"})
            return
        path = f"/projects/{urllib.parse.quote(str(context['project_uuid']), safe='')}/environments"
        created = client.post(path, {"name": "dev", "description": f"{product} integrated development."})
        context["environment_uuid"] = _uuid(created, "environment")
        actions.append({"resource": "dev", "kind": "environment", "action": "created"})
    else:
        actions.append({"resource": "dev", "kind": "environment", "action": "reused"})


def _scoped(item: dict[str, Any], context: dict[str, str | None]) -> bool:
    project = str(_field(item, "project_uuid", "project.uuid") or "")
    environment = str(_field(item, "environment_uuid", "environment.uuid") or "")
    return project == str(context["project_uuid"]) and environment == str(context["environment_uuid"])


def _password() -> str:
    return secrets.token_urlsafe(32)


def _ensure_databases(
    client: Coolify,
    manifest: dict[str, Any],
    context: dict[str, str | None],
    actions: list[dict[str, Any]],
) -> tuple[dict[str, str], dict[str, str]]:
    if context["project_uuid"] is None or context["environment_uuid"] is None:
        for item in manifest["databases"]:
            actions.append({"resource": item["name"], "kind": "database", "action": "planned_after_environment"})
        for item in manifest["redis"]:
            actions.append({"resource": item["name"], "kind": "redis", "action": "planned_after_environment"})
        return {}, {}
    existing = _items(client.get("/databases"))
    database_urls: dict[str, str] = {}
    redis_urls: dict[str, str] = {}
    product = manifest["product"]
    common = {
        "server_uuid": context["server_uuid"],
        "project_uuid": context["project_uuid"],
        "environment_name": "dev",
        "environment_uuid": context["environment_uuid"],
        "destination_uuid": context["destination_uuid"],
        "is_public": False,
        "instant_deploy": False,
    }
    for item in manifest["databases"]:
        resource_name = f"{product}-{item['name']}-dev"
        found = _unique(existing, resource_name, "database")
        if found and not _scoped(found, context):
            raise ReconcileError(f"Database {resource_name!r} exists outside the selected scope.")
        if not found:
            if not client.apply:
                actions.append({"resource": resource_name, "kind": "database", "action": "would_create"})
                continue
            payload = {
                **common,
                "name": resource_name,
                "description": f"Factory-managed private {item['engine']} development database.",
                "image": item["image"],
                "postgres_user": f"{product.replace('-', '_')}_dev",
                "postgres_password": _password(),
                "postgres_db": f"{product.replace('-', '_')}_dev",
            }
            created = client.post("/databases/postgresql", payload)
            identifier = _uuid(created, "database")
            client.post(f"/databases/{urllib.parse.quote(identifier, safe='')}/start", {}, expected=(200, 201, 202))
            found = client.get(f"/databases/{urllib.parse.quote(identifier, safe='')}")
            actions.append({"resource": resource_name, "kind": "database", "action": "created"})
        else:
            identifier = _uuid(found, "database")
            found = client.get(f"/databases/{urllib.parse.quote(identifier, safe='')}")
            actions.append({"resource": resource_name, "kind": "database", "action": "reused"})
        url = _field(found, "internal_db_url", "internal_url")
        if isinstance(url, str) and url.startswith(("postgres://", "postgresql://")):
            database_urls[item["name"]] = url
        elif client.apply:
            raise ReconcileError(f"Database {resource_name!r} does not expose a private internal URL.")
    for item in manifest["redis"]:
        resource_name = f"{product}-{item['name']}-dev"
        found = _unique(existing, resource_name, "Redis")
        if found and not _scoped(found, context):
            raise ReconcileError(f"Redis {resource_name!r} exists outside the selected scope.")
        if not found:
            if not client.apply:
                actions.append({"resource": resource_name, "kind": "redis", "action": "would_create"})
                continue
            payload = {
                **common,
                "name": resource_name,
                "description": "Factory-managed private persistent Redis development resource.",
                "image": item["image"],
                "redis_password": _password(),
                "redis_conf": "appendonly yes\nmaxmemory 256mb\nmaxmemory-policy allkeys-lru",
            }
            created = client.post("/databases/redis", payload)
            identifier = _uuid(created, "Redis")
            client.post(f"/databases/{urllib.parse.quote(identifier, safe='')}/start", {}, expected=(200, 201, 202))
            found = client.get(f"/databases/{urllib.parse.quote(identifier, safe='')}")
            actions.append({"resource": resource_name, "kind": "redis", "action": "created"})
        else:
            identifier = _uuid(found, "Redis")
            found = client.get(f"/databases/{urllib.parse.quote(identifier, safe='')}")
            actions.append({"resource": resource_name, "kind": "redis", "action": "reused"})
        url = _field(found, "internal_db_url", "internal_url")
        if isinstance(url, str) and url.startswith(("redis://", "rediss://")):
            redis_urls[item["name"]] = url
        elif client.apply:
            raise ReconcileError(f"Redis {resource_name!r} does not expose a private internal URL.")
    return database_urls, redis_urls


def _legacy_environment(client: Coolify, manifest: dict[str, Any]) -> dict[str, str]:
    identifier = urllib.parse.quote(manifest["legacy"]["application_uuid"], safe="")
    detail = client.get(f"/applications/{identifier}")
    if _uuid(detail, "legacy application") != manifest["legacy"]["application_uuid"]:
        raise ReconcileError("Legacy application identity could not be verified.")
    values: dict[str, str] = {}
    allowed = set(manifest["legacy"]["environment_keys"])
    for item in _items(client.get(f"/applications/{identifier}/envs")):
        key = _field(item, "key")
        value = _field(item, "real_value", "value")
        if key in allowed and isinstance(value, str) and value:
            values[str(key)] = value
    return values


def _application_payload(manifest: dict[str, Any], app: dict[str, Any], context: dict[str, str | None]) -> dict[str, Any]:
    product = manifest["product"]
    role = app["role"]
    volume = app.get("persistent_volume")
    run_options = ""
    if volume:
        run_options = f"--mount source={volume['name']},target={volume['mount_path']}"
    return {
        "name": f"{product}-{app['name']}-dev",
        "project_uuid": context["project_uuid"],
        "environment_uuid": context["environment_uuid"],
        "environment_name": "dev",
        "server_uuid": context["server_uuid"],
        "destination_uuid": context["destination_uuid"],
        "github_app_uuid": context["github_app_uuid"],
        "git_repository": manifest["repository"],
        "git_branch": "dev",
        "build_pack": "dockerfile",
        "base_directory": "/",
        "dockerfile_location": app["dockerfile"],
        "ports_exposes": str(app["port"]),
        "domains": app["domain"] or "",
        "instant_deploy": False,
        "is_auto_deploy_enabled": True,
        "autogenerate_domain": False,
        "connect_to_docker_network": True,
        "health_check_enabled": role != "worker",
        "health_check_path": app["health_path"],
        "health_check_port": str(app["port"]),
        "health_check_method": "GET",
        "health_check_scheme": "http",
        "health_check_return_code": 200,
        "custom_docker_run_options": run_options,
    }


def _environment_values(
    app: dict[str, Any],
    database_urls: dict[str, str],
    redis_urls: dict[str, str],
    legacy_values: dict[str, str],
) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for item in app["environment"]:
        source = item["source"]
        if source == "literal":
            value = item["value"]
        elif source == "database_url":
            value = database_urls.get(item["resource"])
        elif source == "redis_url":
            value = redis_urls.get(item["resource"])
        else:
            value = legacy_values.get(item["legacy_key"])
        if not isinstance(value, str) or not value:
            raise ReconcileError(f"Environment source for {item['key']!r} is unavailable.")
        values.append(
            {
                "key": item["key"],
                "value": value,
                "is_runtime": True,
                "is_buildtime": False,
                "is_preview": False,
                "is_literal": True,
                "is_multiline": False,
                "is_shown_once": source in {"legacy_env", "database_url", "redis_url"},
            }
        )
    return values


def _ensure_applications(
    client: Coolify,
    manifest: dict[str, Any],
    context: dict[str, str | None],
    database_urls: dict[str, str],
    redis_urls: dict[str, str],
    legacy_values: dict[str, str],
    actions: list[dict[str, Any]],
    deploy: bool,
) -> None:
    product = manifest["product"]
    if context["project_uuid"] is None or context["environment_uuid"] is None:
        for app in manifest["applications"]:
            actions.append({"resource": f"{product}-{app['name']}-dev", "kind": "application", "action": "planned_after_environment"})
        return
    existing = _items(client.get("/applications"))
    for app in manifest["applications"]:
        name = f"{product}-{app['name']}-dev"
        found = _unique(existing, name, "application")
        if found and not _scoped(found, context):
            raise ReconcileError(f"Application {name!r} exists outside the selected scope.")
        if not found:
            if not client.apply:
                actions.append(
                    {
                        "resource": name,
                        "kind": "application",
                        "action": "would_create",
                        "payload": _redacted(_application_payload(manifest, app, context)),
                    }
                )
                continue
            created = client.post("/applications/private-github-app", _application_payload(manifest, app, context))
            identifier = _uuid(created, "application")
            found = client.get(f"/applications/{urllib.parse.quote(identifier, safe='')}")
            actions.append({"resource": name, "kind": "application", "action": "created"})
        else:
            identifier = _uuid(found, "application")
            found = client.get(f"/applications/{urllib.parse.quote(identifier, safe='')}")
            expected = _application_payload(manifest, app, context)
            for key in ("git_repository", "git_branch", "build_pack", "base_directory", "dockerfile_location", "ports_exposes"):
                actual = str(_field(found, key) or "")
                if actual != str(expected[key]):
                    raise ReconcileError(f"Existing application {name!r} is incompatible in {key!r}; automatic replacement is forbidden.")
            actions.append({"resource": name, "kind": "application", "action": "reused"})
        desired = _environment_values(app, database_urls, redis_urls, legacy_values)
        current = {str(_field(item, "key")): item for item in _items(client.get(f"/applications/{urllib.parse.quote(identifier, safe='')}/envs"))}
        for variable in desired:
            existing_variable = current.get(variable["key"])
            if existing_variable is None:
                client.post(f"/applications/{urllib.parse.quote(identifier, safe='')}/envs", variable)
            else:
                client.patch(f"/applications/{urllib.parse.quote(identifier, safe='')}/envs", variable)
        actions.append(
            {
                "resource": name,
                "kind": "environment",
                "action": "reconciled",
                "keys": [item["key"] for item in desired],
            }
        )
        if deploy:
            deployment = client.post("/deploy", {"uuid": identifier, "force": False})
            deployment_uuid = _field(deployment, "deployment_uuid")
            if not deployment_uuid and isinstance(deployment, dict):
                entries = _items(deployment.get("deployments"))
                deployment_uuid = _field(entries[0], "deployment_uuid") if entries else None
            if not deployment_uuid:
                raise ReconcileError(f"Deployment identity for {name!r} was not returned.")
            actions.append({"resource": name, "kind": "deployment", "action": "queued", "deployment_uuid": str(deployment_uuid)})


def reconcile(
    manifest: dict[str, Any],
    *,
    client: Coolify,
    server_name: str,
    destination_uuid: str,
    confirm_product: str | None,
    deploy: bool,
) -> dict[str, Any]:
    product = manifest["product"]
    if client.apply:
        if not _bool_env("COOLIFY_ALLOW_ADVANCED_WRITES"):
            raise ReconcileError("Apply requires COOLIFY_ALLOW_ADVANCED_WRITES=true.")
        if confirm_product != product:
            raise ReconcileError("Apply requires an exact --confirm-product.")
    actions: list[dict[str, Any]] = []
    context = _discover_context(
        client,
        product,
        server_name,
        manifest["coolify_github_app_name"],
        destination_uuid,
    )
    _ensure_project_environment(client, manifest, context, actions)
    database_urls, redis_urls = _ensure_databases(client, manifest, context, actions)
    legacy_values = _legacy_environment(client, manifest) if client.apply else {}
    _ensure_applications(
        client,
        manifest,
        context,
        database_urls,
        redis_urls,
        legacy_values,
        actions,
        deploy,
    )
    return {
        "status": "applied" if client.apply else "planned",
        "topology": manifest["topology"],
        "product": product,
        "legacy_retained": True,
        "resources": resource_names(manifest),
        "actions": _redacted(actions),
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--manifest", required=True)
    result.add_argument("--phase", choices=("plan", "apply"), default="plan")
    result.add_argument("--server", default=os.environ.get("COOLIFY_SERVER_NAME", "localhost"))
    result.add_argument("--destination-uuid", default=os.environ.get("COOLIFY_DESTINATION_UUID"))
    result.add_argument("--confirm-product")
    result.add_argument("--deploy", action="store_true")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
        if not args.destination_uuid:
            raise ReconcileError("COOLIFY_DESTINATION_UUID is required.")
        client = Coolify(
            os.environ.get("COOLIFY_BASE_URL", ""),
            os.environ.get("COOLIFY_API_TOKEN", ""),
            apply=args.phase == "apply",
        )
        result = reconcile(
            manifest,
            client=client,
            server_name=args.server,
            destination_uuid=args.destination_uuid,
            confirm_product=args.confirm_product,
            deploy=args.deploy,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ContractError, ReconcileError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
