#!/usr/bin/env python3
"""Plan or safely reconcile a resources-v3 product in Coolify.

The advanced reconciler is additive: it never deletes, renames, stops or mutates
the retained legacy resource.  Apply is guarded by an environment switch and an
exact product confirmation.  Secret values may be copied from the legacy
application to newly created applications, but are never returned or logged.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

try:
    from advanced_product_contract import (
        ContractError,
        PRODUCTION_TOPOLOGY,
        load_manifest,
        resource_names,
    )
except ModuleNotFoundError:  # Imported as scripts.reconcile_advanced_product in tests.
    from scripts.advanced_product_contract import (
        ContractError,
        PRODUCTION_TOPOLOGY,
        load_manifest,
        resource_names,
    )

TIMEOUT = 30
DEFAULT_DEPLOYMENT_TIMEOUT_SECONDS = 900
TERMINAL_DEPLOYMENT = {"finished", "failed", "cancelled", "canceled"}
SUCCESS_DEPLOYMENT = {"finished"}
SENSITIVE_NAME = ("PASSWORD", "SECRET", "TOKEN", "KEY", "DATABASE_URL", "REDIS_URL")
MANAGED_SECRET_BINDINGS = {
    "goodmorning/openai-default": "GOODMORNING_MANAGED_OPENAI_API_KEY",
    "goodmorning/stripe-secret-key": "GOODMORNING_MANAGED_STRIPE_SECRET_KEY",
    "goodmorning/stripe-webhook-secret": "GOODMORNING_MANAGED_STRIPE_WEBHOOK_SECRET",
    "hosting/admin-bootstrap-password-hash": "HOSTING_ADMIN_BOOTSTRAP_PASSWORD_HASH",
}


def _environment(manifest: dict[str, Any]) -> str:
    return "production" if manifest["topology"] == PRODUCTION_TOPOLOGY else "dev"


def _suffix(manifest: dict[str, Any]) -> str:
    return "prod" if _environment(manifest) == "production" else "dev"


class ReconcileError(RuntimeError):
    pass


def _deployment_timeout_seconds() -> float:
    """Return the bounded wait used for Coolify's asynchronous deploy queue."""
    raw = os.environ.get(
        "COOLIFY_DEPLOYMENT_TIMEOUT_SECONDS",
        str(DEFAULT_DEPLOYMENT_TIMEOUT_SECONDS),
    )
    try:
        value = float(raw)
    except ValueError as exc:
        raise ReconcileError("COOLIFY_DEPLOYMENT_TIMEOUT_SECONDS must be numeric.") from exc
    if value <= 0:
        raise ReconcileError("COOLIFY_DEPLOYMENT_TIMEOUT_SECONDS must be positive.")
    return value


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


def _coolify_error_detail(raw: str) -> str:
    """Summarize API validation failures without echoing submitted values."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return "Coolify request failed."
    if not isinstance(data, dict):
        return "Coolify request failed."
    message = data.get("message")
    message = message if isinstance(message, str) and message else "Coolify request failed."
    errors = data.get("errors")
    if isinstance(errors, dict):
        fields = sorted(key for key in errors if isinstance(key, str))
        if fields:
            message += f" Rejected fields: {', '.join(fields)}."
    return message


def _strip_coolify_literal_quotes(value: str) -> str:
    """Return a secret value without Coolify's display-only literal quotes.

    Coolify exposes ``real_value`` with single quotes when an environment
    variable is marked literal. Those quotes are for its generated env file,
    not part of the secret. Reusing them as the API value causes the next
    deployment to produce a malformed value such as ``''secret''``.
    """
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1]
    return value


def _normalize_repository(value: Any) -> str:
    """Compare Coolify's repository URL forms by GitHub identity."""
    text = str(value or "").strip().removesuffix(".git")
    prefixes = (
        "https://github.com/",
        "http://github.com/",
        "ssh://git@github.com/",
        "git@github.com:",
    )
    for prefix in prefixes:
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    return text.strip("/")


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
            message = _coolify_error_detail(raw)
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


def _expected_migration_uuid(manifest: dict[str, Any], kind: str, resource_name: str) -> str | None:
    migration = manifest.get("migration") or {}
    uuids = migration.get("resource_uuids") or {}
    value = uuids.get(resource_name)
    return str(value) if value else None


def _resolve_deployment_uuid(client: Coolify, application_uuid: str, requested_uuid: str) -> str:
    """Resolve Coolify's persisted deployment after queue deduplication."""
    path = f"/deployments/applications/{urllib.parse.quote(application_uuid, safe='')}?skip=0&take=10"
    try:
        response = client.get(path)
    except ReconcileError:
        return requested_uuid
    entries = _items(response.get("deployments") if isinstance(response, dict) else response)
    for entry in entries:
        deployment_uuid = _field(entry, "deployment_uuid", "uuid", "id")
        if str(deployment_uuid or "") == requested_uuid:
            return requested_uuid
    if entries:
        latest_uuid = _field(entries[0], "deployment_uuid", "uuid", "id")
        if latest_uuid:
            return str(latest_uuid)
    return requested_uuid


def _wait_for_deployments(client: Coolify, deployment_ids: list[str], actions: list[dict[str, Any]]) -> None:
    """Wait for queued deployments so a green reconcile means runnable apps."""
    for deployment_id in deployment_ids:
        deadline = time.monotonic() + _deployment_timeout_seconds()
        last_status = "queued"
        while time.monotonic() < deadline:
            try:
                current = client.get(f"/deployments/{urllib.parse.quote(deployment_id, safe='')}")
            except ReconcileError as exc:
                if "HTTP 404" in str(exc):
                    time.sleep(10)
                    continue
                raise
            source = current.get("data") if isinstance(current, dict) and isinstance(current.get("data"), dict) else current
            last_status = str(_field(source, "status", "state", "deployment_status") or "unknown").lower()
            if last_status in {"success", "successful", "finished", "succeeded", "complete", "completed", "failed", "failure", "error", "cancelled", "canceled"}:
                if last_status not in {"success", "successful", "finished", "succeeded", "complete", "completed"}:
                    detail = json.dumps(_redacted(current), sort_keys=True)[-4000:]
                    try:
                        logs = client.get(f"/deployments/{urllib.parse.quote(deployment_id, safe='')}/logs")
                        detail = f"state={detail}; logs={json.dumps(_redacted(logs), sort_keys=True)[-4000:]}"
                    except ReconcileError as exc:
                        detail = f"state={detail}; logs={exc}"
                    raise ReconcileError(
                        f"Coolify deployment {deployment_id} ended with status {last_status}. Logs: {detail}"
                    )
                actions.append({"kind": "deployment", "action": "finished", "deployment_uuid": deployment_id})
                break
            time.sleep(10)
        else:
            raise ReconcileError(
                f"Timed out waiting for Coolify deployment {deployment_id}; last status was {last_status}."
            )


def _verify_adopted_uuid(manifest: dict[str, Any], kind: str, resource_name: str, found: dict[str, Any]) -> None:
    expected = _expected_migration_uuid(manifest, kind, resource_name)
    if expected is not None and _uuid(found, kind) != expected:
        raise ReconcileError(
            f"Migrated {kind} {resource_name!r} resolved to an unexpected UUID; refusing replacement."
        )


def _coolify_project_name(product: str) -> str:
    """Keep short product slugs while satisfying Coolify's project-name minimum."""
    return product if len(product) >= 3 else f"{product}-app"


def _discover_context(client: Coolify, manifest: dict[str, Any], server_name: str, github_app_name: str, destination_uuid: str) -> dict[str, str | None]:
    product = manifest["product"]
    project_name = _coolify_project_name(product)
    environment_name = _environment(manifest)
    servers = _items(client.get("/servers"))
    server = _unique(servers, server_name, "server")
    if not server:
        raise ReconcileError(f"Coolify server {server_name!r} was not found.")
    projects = _items(client.get("/projects"))
    project = _unique(projects, project_name, "project")
    environment = None
    if project:
        project_uuid = _uuid(project, "project")
        detail = client.get(f"/projects/{urllib.parse.quote(project_uuid, safe='')}")
        environment = _unique(_items(_field(detail, "environments") or detail), environment_name, "environment")
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
    environment_name = _environment(manifest)
    if context["project_uuid"] is None:
        if not client.apply:
            actions.append({"resource": product, "kind": "project", "action": "would_create"})
            return
        created = client.post("/projects", {"name": _coolify_project_name(product), "description": f"GoodMorning {product} resources-v3 project."})
        context["project_uuid"] = _uuid(created, "project")
        actions.append({"resource": product, "kind": "project", "action": "created"})
    else:
        actions.append({"resource": product, "kind": "project", "action": "reused"})
    if context["environment_uuid"] is None:
        if not client.apply:
            actions.append({"resource": environment_name, "kind": "environment", "action": "would_create"})
            return
        path = f"/projects/{urllib.parse.quote(str(context['project_uuid']), safe='')}/environments"
        # Coolify's environment endpoint accepts the environment name only.
        # Project descriptions remain useful, but sending one here causes
        # Coolify 4.1.x to reject the otherwise valid create request with 422.
        created = client.post(path, {"name": environment_name})
        context["environment_uuid"] = _uuid(created, "environment")
        actions.append({"resource": environment_name, "kind": "environment", "action": "created"})
    else:
        actions.append({"resource": environment_name, "kind": "environment", "action": "reused"})


def _scope_conflicts(item: dict[str, Any], context: dict[str, str | None]) -> bool:
    """Reject an explicit scope mismatch without inventing absent list fields.

    Coolify's collection endpoints do not consistently include project and
    environment UUIDs.  Names are globally unique in those collections, so an
    omitted scope is safe to resolve by UUID and reuse; a UUID that is present
    and differs from the selected scope remains a hard failure.
    """
    project = _field(item, "project_uuid", "project.uuid")
    environment = _field(item, "environment_uuid", "environment.uuid")
    return (
        (project is not None and str(project) != str(context["project_uuid"]))
        or (
            environment is not None
            and str(environment) != str(context["environment_uuid"])
        )
    )


def _password() -> str:
    return secrets.token_urlsafe(32)


def _redis_conf() -> str:
    return base64.b64encode(
        b"appendonly yes\nmaxmemory 256mb\nmaxmemory-policy allkeys-lru\n"
    ).decode("ascii")


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
    environment_name = _environment(manifest)
    suffix = _suffix(manifest)
    common = {
        "server_uuid": context["server_uuid"],
        "project_uuid": context["project_uuid"],
        "environment_name": environment_name,
        "environment_uuid": context["environment_uuid"],
        "destination_uuid": context["destination_uuid"],
        "is_public": False,
        "instant_deploy": False,
    }
    for item in manifest["databases"]:
        resource_name = f"{product}-{item['name']}-{suffix}"
        found = _unique(existing, resource_name, "database")
        if found and _scope_conflicts(found, context):
            raise ReconcileError(f"Database {resource_name!r} exists outside the selected scope.")
        if found:
            _verify_adopted_uuid(manifest, "database", resource_name, found)
        if not found:
            if not client.apply:
                actions.append({"resource": resource_name, "kind": "database", "action": "would_create"})
                continue
            payload = {
                **common,
                "name": resource_name,
                "description": f"Factory-managed private {item['engine']} {environment_name} database.",
                "image": item["image"],
                "postgres_user": f"{product.replace('-', '_')}_{suffix}",
                "postgres_password": _password(),
                "postgres_db": f"{product.replace('-', '_')}_{suffix}",
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
        resource_name = f"{product}-{item['name']}-{suffix}"
        found = _unique(existing, resource_name, "Redis")
        if found and _scope_conflicts(found, context):
            raise ReconcileError(f"Redis {resource_name!r} exists outside the selected scope.")
        if found:
            _verify_adopted_uuid(manifest, "Redis", resource_name, found)
        if not found:
            if not client.apply:
                actions.append({"resource": resource_name, "kind": "redis", "action": "would_create"})
                continue
            payload = {
                **common,
                "name": resource_name,
                "description": f"Factory-managed private persistent Redis {environment_name} resource.",
                "image": item["image"],
                "redis_password": _password(),
                "redis_conf": _redis_conf(),
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


def _application_payload(manifest: dict[str, Any], app: dict[str, Any], context: dict[str, str | None], preview_source: dict[str, str] | None = None) -> dict[str, Any]:
    product = manifest["product"]
    environment_name = _environment(manifest)
    suffix = _suffix(manifest)
    resource_name = f"{product}-{app['name']}-{suffix}"
    role = app["role"]
    volume = app.get("persistent_volume")
    run_options = ""
    if volume:
        run_options = f"--mount type=volume,source={volume['name']},target={volume['mount_path']}"
    domains = [app["domain"]] if app["domain"] else []
    domains.extend(app.get("custom_domains", []))
    payload = {
        "name": resource_name,
        "project_uuid": context["project_uuid"],
        "environment_uuid": context["environment_uuid"],
        "environment_name": environment_name,
        "server_uuid": context["server_uuid"],
        "destination_uuid": context["destination_uuid"],
        "github_app_uuid": context["github_app_uuid"],
        "git_repository": manifest["repository"],
        "git_branch": preview_source["branch"] if preview_source else manifest["branch"],
        **({"git_commit_sha": preview_source["sha"]} if preview_source else {}),
        "build_pack": "dockerfile",
        "base_directory": app["base_directory"],
        "dockerfile_location": app["dockerfile"],
        "ports_exposes": str(app["port"]),
        # Coolify's application API accepts comma-separated HTTPS domains.
        # Keep the manifest's established primary domain first, then append
        # explicitly reviewed custom-domain bindings.
        "domains": ",".join(domains),
        # Native Coolify auto-deploy is part of the v3 desired state.  The
        # initial create may still be followed by an explicit deploy request.
        "instant_deploy": False if preview_source else True,
        "is_auto_deploy_enabled": False if preview_source else True,
        "autogenerate_domain": False,
        "connect_to_docker_network": True,
        "health_check_enabled": role != "worker",
        "health_check_path": app["health_path"],
        "health_check_port": str(app["port"]),
        "health_check_method": "GET",
        "health_check_scheme": "http",
        "health_check_return_code": 200,
        "custom_docker_run_options": run_options,
        # Coolify's shared Docker network uses generated container names by
        # default. Declare the stable Resources V3 name as an alias so
        # gateway/service bindings can resolve private application upstreams.
        "custom_network_aliases": resource_name,
    }
    # Coolify persists generated proxy labels on the application.  When a
    # resource changes from public to private, clearing only ``domains`` leaves
    # the old Host rule on the next container and can steal the gateway host.
    # Manage the label field only for private applications; public resources
    # keep Coolify's generated routing labels for their declared domain.
    if not domains:
        # Coolify persists generated proxy labels as base64 and its API does
        # not regenerate them when ``domains`` is cleared. Keep the private
        # application explicitly out of Traefik while preserving the public
        # gateway/API labels managed by Coolify.
        payload["custom_labels"] = base64.b64encode(b"traefik.enable=false").decode("ascii")
    return payload


def _application_mutable_changes(found: dict[str, Any], expected: dict[str, Any], app: dict[str, Any]) -> dict[str, Any]:
    mutable_fields = (
        "domains",
        "custom_network_aliases",
        *( ("custom_labels",) if "custom_labels" in expected else () ),
        *( ("git_commit_sha",) if "git_commit_sha" in expected else () ),
        "instant_deploy",
        "is_auto_deploy_enabled",
        "autogenerate_domain",
        "health_check_enabled",
        "health_check_path",
        "health_check_port",
        "health_check_method",
        "health_check_scheme",
        "health_check_return_code",
    )
    # An adoption manifest may intentionally omit a v2 volume because the
    # volume exists only in Coolify state. Never clear that state implicitly;
    # a v3 volume declaration is required to manage it.
    if app.get("persistent_volume") is not None:
        mutable_fields = (*mutable_fields, "custom_docker_run_options")
    return {
        key: expected[key]
        for key in mutable_fields
        if str(_field(found, key) or "") != str(expected[key] or "")
    }


def _normalized_domain_list(value: Any) -> list[str]:
    if isinstance(value, list):
        values = value
    else:
        values = str(value or "").split(",")
    return [str(item).strip().rstrip("/") for item in values if str(item).strip()]


def _application_domains(value: dict[str, Any]) -> Any:
    """Prefer Coolify's explicit domain field, including an explicit clear."""
    for name in ("domains", "domain", "fqdn"):
        if name in value:
            return value[name]
    return None


def _decoded_custom_labels(value: dict[str, Any]) -> str:
    """Decode Coolify's base64 custom-label field, tolerating raw API values."""
    labels = str(_field(value, "custom_labels") or "")
    if not labels:
        return ""
    try:
        return base64.b64decode(labels, validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError):
        return labels


def _reset_disabled_labels_for_public_gateway(
    found: dict[str, Any], expected: dict[str, Any], app: dict[str, Any]
) -> bool:
    """Ask Coolify to regenerate defaults only when a gateway was explicitly disabled."""
    if app.get("role") != "gateway" or not app.get("domain"):
        return False
    labels = {line.strip() for line in _decoded_custom_labels(found).splitlines() if line.strip()}
    if "traefik.enable=false" not in labels:
        return False
    expected["custom_labels"] = ""
    return True


def _environment_values(
    app: dict[str, Any],
    database_urls: dict[str, str],
    redis_urls: dict[str, str],
    legacy_values: dict[str, str],
    current: dict[str, dict[str, Any]] | None = None,
    application_urls: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    current = current or {}
    application_urls = application_urls or {}
    values: list[dict[str, Any]] = []
    for item in app["environment"]:
        source = item["source"]
        if source == "literal":
            value = item["value"]
        elif source == "database_url":
            value = database_urls.get(item["resource"])
            if isinstance(value, str) and item.get("driver") == "psycopg":
                if value.startswith("postgresql://"):
                    value = "postgresql+psycopg://" + value.removeprefix("postgresql://")
                elif value.startswith("postgres://"):
                    value = "postgresql+psycopg://" + value.removeprefix("postgres://")
        elif source == "redis_url":
            value = redis_urls.get(item["resource"])
        elif source == "service_url":
            value = application_urls.get(item["resource"])
        elif source == "managed_secret":
            binding = item["binding"]
            environment_key = MANAGED_SECRET_BINDINGS.get(binding, binding)
            value = os.environ.get(environment_key)
        elif source == "generated_secret":
            existing = current.get(item["key"])
            if existing is None:
                value = secrets.token_urlsafe(48)
            else:
                value = _field(existing, "real_value")
                if isinstance(value, str):
                    value = _strip_coolify_literal_quotes(value)
                if not isinstance(value, str) or not value:
                    raise ReconcileError(
                        f"Existing generated secret {item['key']!r} cannot be read; refusing implicit rotation."
                    )
        else:
            value = legacy_values.get(item["legacy_key"])
        if not isinstance(value, str) or (source != "literal" and not value):
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
                "is_shown_once": source
                in {"legacy_env", "database_url", "redis_url", "generated_secret", "managed_secret"},
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
    preview_source: dict[str, str] | None = None,
) -> None:
    deployment_ids: list[str] = []
    product = manifest["product"]
    suffix = _suffix(manifest)
    if context["project_uuid"] is None or context["environment_uuid"] is None:
        for app in manifest["applications"]:
            actions.append({"resource": f"{product}-{app['name']}-{suffix}", "kind": "application", "action": "planned_after_environment"})
        return
    existing = _items(client.get("/applications"))
    # Application-to-application bindings must stay on Coolify's shared
    # network. Using a public HTTPS domain from inside the gateway re-enters
    # the proxy with the gateway Host header and can route to the wrong app.
    # The stable network aliases declared in the payload are the internal
    # contract for every service URL binding.
    application_urls = {
        item["name"]: f"http://{product}-{item['name']}-{suffix}:{item['port']}"
        for item in manifest["applications"]
    }
    applications_by_name = {app["name"]: app for app in manifest["applications"]}
    ordered: list[dict[str, Any]] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(name: str) -> None:
        if name in visiting:
            raise ReconcileError("Application dependency cycle reached reconciliation.")
        if name in visited:
            return
        visiting.add(name)
        for dependency in applications_by_name[name].get("dependencies", []):
            if dependency["type"] == "application":
                visit(dependency["name"])
        visiting.remove(name)
        visited.add(name)
        ordered.append(applications_by_name[name])

    for app in manifest["applications"]:
        visit(app["name"])
    for app in ordered:
        name = f"{product}-{app['name']}-{suffix}"
        found = _unique(existing, name, "application")
        if found and _scope_conflicts(found, context):
            raise ReconcileError(f"Application {name!r} exists outside the selected scope.")
        if found:
            _verify_adopted_uuid(manifest, "application", name, found)
        if not found:
            if not client.apply:
                actions.append(
                    {
                        "resource": name,
                        "kind": "application",
                        "action": "would_create",
                        "payload": _redacted(_application_payload(manifest, app, context, preview_source)),
                    }
                )
                continue
            created = client.post("/applications/private-github-app", _application_payload(manifest, app, context, preview_source))
            identifier = _uuid(created, "application")
            found = client.get(f"/applications/{urllib.parse.quote(identifier, safe='')}")
            actions.append({"resource": name, "kind": "application", "action": "created"})
        else:
            identifier = _uuid(found, "application")
            found = client.get(f"/applications/{urllib.parse.quote(identifier, safe='')}")
            expected = _application_payload(manifest, app, context, preview_source)
            reset_disabled_gateway = _reset_disabled_labels_for_public_gateway(found, expected, app)
            for key in ("git_repository", "git_branch", "build_pack", "base_directory", "dockerfile_location", "ports_exposes"):
                actual = str(_field(found, key) or "")
                if key == "git_repository":
                    actual = _normalize_repository(actual)
                    expected_value = _normalize_repository(expected[key])
                else:
                    expected_value = str(expected[key])
                if actual != expected_value:
                    if key == "git_repository":
                        raise ReconcileError(
                            f"Existing application {name!r} is incompatible in {key!r}; "
                            f"found {actual!r}, expected {expected_value!r}; automatic replacement is forbidden."
                        )
                    raise ReconcileError(f"Existing application {name!r} is incompatible in {key!r}; automatic replacement is forbidden.")
            changes = _application_mutable_changes(found, expected, app)
            if changes and client.apply:
                client.patch(f"/applications/{urllib.parse.quote(identifier, safe='')}", changes)
                found = client.get(f"/applications/{urllib.parse.quote(identifier, safe='')}")
                actual_domains = _normalized_domain_list(_application_domains(found))
                expected_domains = _normalized_domain_list(expected["domains"])
                if actual_domains != expected_domains:
                    raise ReconcileError(
                        f"Coolify did not verify the requested domains for application {name!r}: "
                        f"found {actual_domains!r}, expected {expected_domains!r}."
                    )
                if reset_disabled_gateway and "traefik.enable=false" in {
                    line.strip() for line in _decoded_custom_labels(found).splitlines() if line.strip()
                }:
                    raise ReconcileError(
                        f"Coolify did not clear the disabled proxy label for public gateway application {name!r}."
                    )
                actions.append({"resource": name, "kind": "application", "action": "reconciled", "fields": sorted(changes)})
            elif changes:
                actions.append({"resource": name, "kind": "application", "action": "would_update", "fields": sorted(changes)})
            else:
                actions.append({"resource": name, "kind": "application", "action": "reused"})
        if not client.apply:
            actions.append(
                {
                    "resource": name,
                    "kind": "environment",
                    "action": "would_reconcile",
                    "keys": [item["key"] for item in app["environment"]],
                }
            )
            continue
        # Persistent site data must be represented by Coolify's native storage
        # resource.  custom_docker_run_options alone is not sufficient for an
        # existing application: Coolify will accept the option but can start a
        # replacement container without creating/mounting the volume.
        volume = app.get("persistent_volume")
        if volume:
            storage_payload = client.get(f"/applications/{urllib.parse.quote(identifier, safe='')}/storages")
            storages = []
            if isinstance(storage_payload, dict):
                for key in ("persistent_storages", "file_storages"):
                    storages.extend(_items(storage_payload.get(key)))
            else:
                storages = _items(storage_payload)
            expected_name = f"{identifier}-{volume['name']}"
            has_storage = any(
                str(_field(item, "name") or "") == expected_name
                and str(_field(item, "mount_path") or "") == str(volume["mount_path"])
                for item in storages
            )
            if not has_storage:
                client.post(
                    f"/applications/{urllib.parse.quote(identifier, safe='')}/storages",
                    {
                        "type": "persistent",
                        "name": volume["name"],
                        "mount_path": volume["mount_path"],
                    },
                )
                actions.append({"resource": name, "kind": "storage", "action": "created"})
        current = {str(_field(item, "key")): item for item in _items(client.get(f"/applications/{urllib.parse.quote(identifier, safe='')}/envs"))}
        desired = _environment_values(
            app,
            database_urls,
            redis_urls,
            legacy_values,
            current,
            application_urls=application_urls,
        )
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
            requested_deployment_id = str(deployment_uuid)
            deployment_id = _resolve_deployment_uuid(client, identifier, requested_deployment_id)
            deployment_ids.append(deployment_id)
            action = {"resource": name, "kind": "deployment", "action": "queued", "deployment_uuid": deployment_id}
            if deployment_id != requested_deployment_id:
                action["requested_deployment_uuid"] = requested_deployment_id
                action["action"] = "resolved_persisted_deployment"
            actions.append(action)
    if deploy and deployment_ids:
        _wait_for_deployments(client, deployment_ids, actions)


def reconcile(
    manifest: dict[str, Any],
    *,
    client: Coolify,
    server_name: str,
    destination_uuid: str,
    confirm_product: str | None,
    confirm_environment: str | None = None,
    deploy: bool = False,
    preview_source: dict[str, str] | None = None,
) -> dict[str, Any]:
    product = manifest["product"]
    environment_name = _environment(manifest)
    if preview_source is not None:
        # Explicit, narrow DEV exception for the unmerged Hosting MVP. The
        # caller verifies the source SHA and PR in GitHub before any write.
        if (product != "hosting" or manifest["repository"] != "goodmorning-mx/hosting"
                or environment_name != "dev" or manifest["branch"] != "dev"
                or set(preview_source) != {"branch", "sha"}
                or preview_source["branch"] != "feature/hosting-mvp"
                or not re.fullmatch(r"[0-9a-f]{40}", preview_source["sha"])):
            raise ReconcileError("Preview source is restricted to the pinned Hosting DEV MVP.")
    if client.apply:
        switch = (
            "COOLIFY_ALLOW_ADVANCED_PRODUCTION_WRITES"
            if environment_name == "production"
            else "COOLIFY_ALLOW_ADVANCED_WRITES"
        )
        if not _bool_env(switch):
            raise ReconcileError(f"Apply requires {switch}=true.")
        if confirm_product != product:
            raise ReconcileError("Apply requires an exact --confirm-product.")
        if confirm_environment != environment_name:
            raise ReconcileError("Apply requires an exact --confirm-environment.")
    actions: list[dict[str, Any]] = []
    context = _discover_context(
        client,
        manifest,
        server_name,
        manifest["coolify_github_app_name"],
        destination_uuid,
    )
    _ensure_project_environment(client, manifest, context, actions)
    database_urls, redis_urls = _ensure_databases(client, manifest, context, actions)
    legacy_values = (
        _legacy_environment(client, manifest)
        if client.apply and manifest.get("legacy") is not None
        else {}
    )
    _ensure_applications(
        client,
        manifest,
        context,
        database_urls,
        redis_urls,
        legacy_values,
        actions,
        deploy,
        preview_source,
    )
    return {
        "status": "applied" if client.apply else "planned",
        "topology": manifest["topology"],
        "product": product,
        "legacy_retained": manifest.get("legacy") is not None,
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
    result.add_argument("--confirm-environment", choices=("dev", "production"))
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
            confirm_environment=args.confirm_environment,
            deploy=args.deploy,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ContractError, ReconcileError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
