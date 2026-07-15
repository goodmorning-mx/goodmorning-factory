#!/usr/bin/env python3
"""Provision (or dry-run) GoodMorning Coolify development primitives.

Stage 3 may create only: Project, exact ``dev`` Environment, and private native
PostgreSQL 16. Applications, deployments, lifecycle actions, SSH/rsync/SCP, and
remote Docker are intentionally out of scope.
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
SECRET_KEY_RE = re.compile(r"(PASSWORD|PASS|SECRET|TOKEN|KEY|PRIVATE|CREDENTIAL|DATABASE_URL|DB_URL|URL$|AUTHORIZATION)", re.I)
PRODUCT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
FORBIDDEN_PATH_RE = re.compile(r"/(deploy|deployments|start|restart|stop)(?:/|$)", re.I)
POST_PROJECT_CREATE = "/projects"
POST_ENV_CREATE_TEMPLATE = "/projects/{project_uuid}/environments"
POST_POSTGRES_CREATE = "/databases/postgresql"
ALLOWED_POST_KINDS = {"project_create", "environment_create", "postgresql_create"}


class ProvisioningError(Exception):
    def __init__(self, message: str, *, error_type: str = "validation", code: int = 1):
        super().__init__(redact(message))
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
            raise ProvisioningError("Dry-run mode rejects all POST requests.", error_type="safety")
        if not self.allow_resource_writes:
            raise ProvisioningError("Apply requires COOLIFY_ALLOW_RESOURCE_WRITES=true before any POST.", error_type="safety")
        if self.confirm_product != self.product:
            raise ProvisioningError("Apply requires --confirm-product to match --product exactly before any POST.", error_type="safety")


def sanitize_string(text: str) -> str:
    for key in SENSITIVE_ENV:
        secret = os.environ.get(key)
        if secret:
            text = text.replace(secret, "[REDACTED]")
    text = re.sub(r"Authorization:\s*Bearer\s+[^\s,}]+", "Authorization: Bearer [REDACTED]", text, flags=re.I)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", text, flags=re.I)
    text = re.sub(r"(https?://)([^:/\s]+):([^@/\s]+)@", r"\1[REDACTED]@", text)
    text = re.sub(r"postgres(?:ql)?://[^\s'\"}]+", "postgresql://[REDACTED]", text, flags=re.I)
    text = re.sub(r"DATABASE_URL\s*[=:]\s*[^\s,&}]+", "DATABASE_URL=[REDACTED]", text, flags=re.I)
    text = re.sub(r"(password|postgres_password|token|secret|internal_db_url)([=:]\s*)([^\s,&}]+)", r"\1\2[REDACTED]", text, flags=re.I)
    return text


def sanitize_for_output(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if SECRET_KEY_RE.search(str(k)) else sanitize_for_output(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_for_output(v) for v in value]
    if isinstance(value, str):
        return sanitize_string(value)
    return value


def redact(value: Any) -> Any:
    return sanitize_for_output(value)


def safe_http_body(raw: str, *, limit: int = 1000) -> str:
    try:
        text = json.dumps(redact(json.loads(raw)), sort_keys=True)
    except json.JSONDecodeError:
        text = sanitize_string(raw)
    return text[:limit] + ("...[truncated]" if len(text) > limit else "")


def validate_base_url(value: str) -> str:
    parsed = urllib.parse.urlparse(value.strip())
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise ProvisioningError("COOLIFY_BASE_URL must be an https URL without credentials, query, fragment, or extra path.")
    return f"{parsed.scheme}://{parsed.netloc}"


class CoolifyClient:
    def __init__(self, base_url: str, api_token: str, *, mode: ExecutionMode | None = None, timeout: int = TIMEOUT_SECONDS):
        self.base_url = validate_base_url(base_url).rstrip("/")
        if not api_token:
            raise ProvisioningError("COOLIFY_API_TOKEN is required.")
        self.api_token = api_token
        self.mode = mode or ExecutionMode("dry_run", "", None, False)
        self.timeout = timeout

    @classmethod
    def from_env(cls, mode: ExecutionMode | None = None) -> "CoolifyClient":
        return cls(os.environ.get("COOLIFY_BASE_URL", ""), os.environ.get("COOLIFY_API_TOKEN", ""), mode=mode)

    def _check_request(self, method: str, path: str, *, post_kind: str | None = None) -> None:
        method = method.upper()
        if not path.startswith("/"):
            raise ProvisioningError("Coolify API path must start with '/'.")
        if FORBIDDEN_PATH_RE.search(path):
            raise ProvisioningError(f"Lifecycle/deployment endpoint is forbidden in this stage: {path}", error_type="safety")
        if method in {"PATCH", "PUT", "DELETE"}:
            raise ProvisioningError(f"Method {method} is always forbidden in this stage.", error_type="safety")
        if method == "POST":
            self.mode.assert_write_gates()
            if path.startswith("/applications"):
                raise ProvisioningError("Application creation is forbidden in this stage.", error_type="safety")
            if post_kind not in ALLOWED_POST_KINDS:
                raise ProvisioningError("POST requires a stage-3 allowlisted operation kind.", error_type="safety")
            if post_kind == "project_create" and path != POST_PROJECT_CREATE:
                raise ProvisioningError("Project creation POST endpoint is not allowlisted.", error_type="safety")
            if post_kind == "environment_create" and not re.fullmatch(r"/projects/[^/]+/environments", path):
                raise ProvisioningError("Environment creation POST endpoint is not allowlisted.", error_type="safety")
            if post_kind == "postgresql_create" and path != POST_POSTGRES_CREATE:
                raise ProvisioningError("PostgreSQL creation POST endpoint is not allowlisted.", error_type="safety")
        elif method != "GET":
            raise ProvisioningError(f"Method {method} is not implemented.", error_type="safety")

    def request(self, method: str, path: str, *, json_body: dict[str, Any] | None = None, allowed_statuses: set[int] | None = None, post_kind: str | None = None) -> Response:
        method = method.upper()
        self._check_request(method, path, post_kind=post_kind)
        headers = {"Accept": "application/json", "Authorization": f"Bearer {self.api_token}", "User-Agent": "goodmorning-factory-provisioner"}
        body = None
        if json_body is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(json_body).encode("utf-8")
        req = urllib.request.Request(f"{self.base_url}/api/v1{path}", method=method, headers=headers, data=body)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                raw = response.read().decode("utf-8")
                data = json.loads(raw) if raw else None
                if allowed_statuses and response.status not in allowed_statuses:
                    raise ProvisioningError(f"Unexpected Coolify status {response.status} for {method} {path}.", error_type="coolify_api", code=2)
                return Response(response.status, data, dict(response.headers))
        except urllib.error.HTTPError as exc:
            body_text = safe_http_body(exc.read().decode("utf-8", errors="replace"))
            raise ProvisioningError(f"Coolify API {exc.code} for {method} {path}: {body_text}", error_type="coolify_api", code=2) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ProvisioningError(f"Coolify connection failure for {method} {path}: {exc}", error_type="network", code=2) from exc
        except json.JSONDecodeError as exc:
            raise ProvisioningError(f"Malformed JSON response for {method} {path}: {exc}", error_type="coolify_api", code=2) from exc

    def get(self, path: str) -> Any:
        return self.request("GET", path).data

    def create_project(self, payload: dict[str, Any]) -> Response:
        return self.request("POST", POST_PROJECT_CREATE, json_body=payload, allowed_statuses={200, 201, 202}, post_kind="project_create")

    def create_environment(self, project_uuid: str, payload: dict[str, Any]) -> Response:
        return self.request("POST", POST_ENV_CREATE_TEMPLATE.format(project_uuid=urllib.parse.quote(str(project_uuid), safe="")), json_body=payload, allowed_statuses={200, 201, 202}, post_kind="environment_create")

    def create_postgresql(self, payload: dict[str, Any]) -> Response:
        return self.request("POST", POST_POSTGRES_CREATE, json_body=payload, allowed_statuses={200, 201, 202}, post_kind="postgresql_create")


def item_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("data", "items", "projects", "environments", "servers", "github_apps", "githubApps", "applications", "databases"):
            if isinstance(data.get(key), list):
                return [x for x in data[key] if isinstance(x, dict)]
    return []


def unwrap(data: Any) -> Any:
    return data.get("data") if isinstance(data, dict) and isinstance(data.get("data"), dict) else data


def field(item: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = item.get(name)
        if value not in (None, ""):
            return value
    return None


def nested_field(item: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        cur: Any = item
        for part in path.split("."):
            if not isinstance(cur, dict):
                cur = None; break
            cur = cur.get(part)
        if cur not in (None, ""):
            if isinstance(cur, dict):
                return field(cur, "uuid", "id", "name")
            return cur
    return None


def summary(item: dict[str, Any], *fields: str) -> dict[str, Any]:
    return redact({name: field(item, name) for name in fields if field(item, name) is not None})


def select_exact(items: list[dict[str, Any]], name: str, kind: str, *, required: bool = True) -> list[dict[str, Any]]:
    matches = [x for x in items if str(x.get("name") or "") == name]
    if required and not matches:
        raise ProvisioningError(f"Could not find Coolify {kind} named exactly '{name}'.", error_type="discovery")
    if len(matches) > 1:
        raise ProvisioningError(f"Found multiple Coolify {kind} records named exactly '{name}'.", error_type="discovery")
    return matches


def require_identifier(item: dict[str, Any], kind: str) -> str:
    identifier = field(item, "uuid", "id")
    if not identifier:
        raise ProvisioningError(f"Selected Coolify {kind} is missing uuid/id.", error_type="discovery")
    return str(identifier)


def deterministic_sql_names(product: str) -> dict[str, str]:
    safe = re.sub(r"[^a-z0-9_]", "_", product.replace("-", "_"))
    return {"postgres_user": f"{safe}_dev_user", "postgres_db": f"{safe}_dev"}


def generate_postgres_password() -> str:
    return secrets.token_urlsafe(36)


def project_payload(name: str) -> dict[str, Any]:
    return {"name": name}


def environment_payload(name: str = "dev") -> dict[str, Any]:
    return {"name": name}


def postgresql_payload(args: argparse.Namespace, project_uuid: str, server_uuid: str, password: str) -> dict[str, Any]:
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


def ensure_project(client: CoolifyClient, args: argparse.Namespace, mode: ExecutionMode) -> dict[str, Any]:
    matches = select_exact(item_list(client.get("/projects")), args.coolify_project, "project", required=False)
    if matches:
        uuid = require_identifier(matches[0], "project")
        return {"status": "exists", "name": args.coolify_project, "uuid": uuid}
    if not mode.is_apply:
        return {"status": "would_create", "name": args.coolify_project, "uuid": "${project.uuid}"}
    try:
        data = unwrap(client.create_project(project_payload(args.coolify_project)).data)
    except ProvisioningError as exc:
        if "409" in exc.message or "422" in exc.message:
            matches = select_exact(item_list(client.get("/projects")), args.coolify_project, "project", required=False)
            if len(matches) == 1:
                return {"status": "exists", "name": args.coolify_project, "uuid": require_identifier(matches[0], "project"), "recovered_after_conflict": True}
        raise
    if not isinstance(data, dict):
        raise ProvisioningError("Project creation returned malformed response.", error_type="coolify_api", code=2)
    return {"status": "created", "name": args.coolify_project, "uuid": require_identifier(data, "project")}


def get_environments(client: CoolifyClient, project_uuid: str) -> list[dict[str, Any]]:
    detail = unwrap(client.get(f"/projects/{project_uuid}"))
    envs = item_list(detail.get("environments") if isinstance(detail, dict) else [])
    if not envs:
        envs = item_list(client.get(f"/projects/{project_uuid}/environments"))
    return envs


def ensure_environment(client: CoolifyClient, project: dict[str, Any], args: argparse.Namespace, mode: ExecutionMode) -> dict[str, Any]:
    if project["uuid"].startswith("${"):
        return {"status": "would_create", "name": "dev", "uuid": "${environment.uuid}", "project_uuid": project["uuid"]}
    matches = select_exact(get_environments(client, project["uuid"]), "dev", "environment", required=False)
    if matches:
        return {"status": "exists", "name": "dev", "uuid": require_identifier(matches[0], "environment"), "project_uuid": project["uuid"]}
    if not mode.is_apply:
        return {"status": "would_create", "name": "dev", "uuid": "${environment.uuid}", "project_uuid": project["uuid"]}
    try:
        data = unwrap(client.create_environment(project["uuid"], environment_payload()).data)
    except ProvisioningError as exc:
        if "409" in exc.message or "422" in exc.message:
            matches = select_exact(get_environments(client, project["uuid"]), "dev", "environment", required=False)
            if len(matches) == 1:
                return {"status": "exists", "name": "dev", "uuid": require_identifier(matches[0], "environment"), "project_uuid": project["uuid"], "recovered_after_conflict": True}
        raise
    if not isinstance(data, dict) or not field(data, "uuid", "id"):
        matches = select_exact(get_environments(client, project["uuid"]), "dev", "environment", required=False)
        if len(matches) != 1:
            raise ProvisioningError("Environment creation response did not include uuid/id and follow-up discovery was inconclusive.", error_type="coolify_api", code=2)
        data = matches[0]
    return {"status": "created", "name": "dev", "uuid": require_identifier(data, "environment"), "project_uuid": project["uuid"]}


def bool_value(v: Any) -> bool | None:
    if isinstance(v, bool): return v
    if isinstance(v, str) and v.lower() in {"true", "1", "yes"}: return True
    if isinstance(v, str) and v.lower() in {"false", "0", "no"}: return False
    return None


def classify_postgres(item: dict[str, Any], context: dict[str, Any]) -> tuple[str, list[dict[str, Any]], bool]:
    diffs=[]; unverified=[]
    checks = [("project_uuid", context["project_uuid"], nested_field(item,"project_uuid","project.uuid","project.id")), ("environment_uuid", context["environment_uuid"], nested_field(item,"environment_uuid","environment.uuid","environment.id")), ("server_uuid", context["server_uuid"], nested_field(item,"server_uuid","server.uuid","destination.server.uuid","destination.server.id"))]
    for k,e,a in checks:
        if a in (None, ""): unverified.append({"field": k, "expected": e})
        elif str(a)!=str(e): return "out_of_scope", [{"field": k, "expected": e, "actual": a}], False
    engine = str(nested_field(item,"database_type","type","kind") or "").lower()
    if not engine: unverified.append({"field":"type","expected":"postgresql"})
    elif "postgres" not in engine: diffs.append({"field":"type","expected":"postgresql","actual":engine})
    version = nested_field(item,"version","postgres_version","image_tag","image")
    if not version: unverified.append({"field":"version","expected":POSTGRES_VERSION})
    elif POSTGRES_VERSION not in str(version): diffs.append({"field":"version","expected":POSTGRES_VERSION,"actual":version})
    pub = bool_value(nested_field(item,"is_public","public"))
    if pub is None: unverified.append({"field":"is_public","expected":False})
    elif pub is not False: diffs.append({"field":"is_public","expected":False,"actual":pub})
    port = nested_field(item,"public_port","ports_mappings")
    if port not in (None, "", [], False): diffs.append({"field":"public_port","expected":None,"actual":"configured"})
    if diffs: return "incompatible", diffs, bool(nested_field(item,"internal_db_url","database_url"))
    if unverified: return "compatibility_unverified", unverified, bool(nested_field(item,"internal_db_url","database_url"))
    return "exists", [], bool(nested_field(item,"internal_db_url","database_url"))


def hydrate_database(client: CoolifyClient, item: dict[str, Any]) -> dict[str, Any]:
    uuid = field(item, "uuid", "id")
    if not uuid:
        return item
    detail = unwrap(client.get(f"/databases/{uuid}"))
    return {**item, **detail} if isinstance(detail, dict) else item


def ensure_postgresql(client: CoolifyClient, project: dict[str, Any], environment: dict[str, Any], server: dict[str, Any], args: argparse.Namespace, mode: ExecutionMode) -> dict[str, Any]:
    name = f"{args.product}-postgres-dev"
    context = {"project_uuid": project["uuid"], "environment_uuid": environment["uuid"], "server_uuid": server["uuid"]}
    matches = [x for x in item_list(client.get("/databases")) if str(field(x,"name") or "") == name]
    if len(matches) > 1:
        raise ProvisioningError(f"Found multiple PostgreSQL resources named exactly '{name}'.", error_type="discovery")
    if matches:
        detail = hydrate_database(client, matches[0])
        status, info, url_available = classify_postgres(detail, context)
        result = {"status": status, "name": name, "uuid": field(detail,"uuid","id"), "internal_db_url_available": url_available, "credentials_configured": bool(nested_field(detail,"postgres_user","postgres_db")), "scope": context}
        if info: result["details"] = redact(info)
        return result
    if not mode.is_apply:
        return {"status":"would_create", "name":name, "uuid":"${postgres.uuid}", "internal_db_url_available": False, "credentials_configured": False, "secret_requirements":["Generate postgres_password in-memory only during apply creation."], "scope": context}
    password = generate_postgres_password()
    try:
        data = unwrap(client.create_postgresql(postgresql_payload(args, project["uuid"], server["uuid"], password)).data)
    except ProvisioningError as exc:
        if "409" in exc.message or "422" in exc.message:
            matches = [x for x in item_list(client.get("/databases")) if str(field(x,"name") or "") == name]
            if len(matches) == 1:
                detail = hydrate_database(client, matches[0]); status, info, url = classify_postgres(detail, context)
                if status == "exists":
                    return {"status":"exists", "name":name, "uuid":field(detail,"uuid","id"), "internal_db_url_available":url, "credentials_configured": True, "recovered_after_conflict": True, "scope": context}
        raise
    if not isinstance(data, dict):
        raise ProvisioningError("PostgreSQL creation returned malformed response.", error_type="coolify_api", code=2)
    uuid = require_identifier(data, "PostgreSQL")
    detail = hydrate_database(client, {"uuid": uuid, "name": name})
    status, info, url = classify_postgres(detail, context)
    if status != "exists":
        raise ProvisioningError(f"Created PostgreSQL could not be verified as private and in-scope: {info}", error_type="coolify_api", code=2)
    return {"status":"created", "name":name, "uuid":uuid, "internal_db_url_available":url or bool(data.get("internal_db_url")), "credentials_configured": True, "scope": context}


def discover_legacy(client: CoolifyClient, product: str) -> list[dict[str, Any]]:
    legacy=[]
    for item in item_list(client.get("/applications")):
        if str(field(item,"name") or "") == f"{product}-dev":
            legacy.append(summary(item,"name","uuid","id","build_pack","type"))
    return legacy


def compute_status(mode: ExecutionMode, parts: list[dict[str, Any]], blockers: list[str]) -> str:
    if blockers:
        if any(p.get("status") == "created" for p in parts): return "partial"
        return "blocked" if not any(p.get("status") == "incompatible" for p in parts) else "recreate_required"
    if not mode.is_apply: return "dry_run"
    statuses = {p.get("status") for p in parts}
    if "created" in statuses and statuses <= {"created", "exists"}: return "created"
    if statuses == {"exists"}: return "exists"
    if "created" in statuses: return "partial"
    return "ready"


def validate_args(args: argparse.Namespace) -> None:
    if not PRODUCT_RE.fullmatch(args.product): raise ProvisioningError("product must be a lowercase DNS-safe slug.")
    if not REPOSITORY_RE.fullmatch(args.repository): raise ProvisioningError("repository must use owner/name format.")
    if args.branch != "dev": raise ProvisioningError("Only branch 'dev' is supported by this stage.")
    if args.environment != "dev": raise ProvisioningError("Only environment 'dev' is supported by this stage.")
    if bool(args.dry_run) == bool(args.apply): raise ProvisioningError("Choose exactly one of --dry-run or --apply.", error_type="safety")
    if not args.coolify_project.strip() or not args.server.strip() or not args.github_app.strip(): raise ProvisioningError("Coolify project, server, and GitHub App are required.")


def provision(args: argparse.Namespace, *, client_factory: Callable[[], CoolifyClient] | None = None) -> dict[str, Any]:
    validate_args(args)
    mode = ExecutionMode("apply" if args.apply else "dry_run", args.product, args.confirm_product, os.environ.get("COOLIFY_ALLOW_RESOURCE_WRITES") == "true")
    if mode.is_apply:
        mode.assert_write_gates()
    client = client_factory() if client_factory else CoolifyClient.from_env(mode)
    if hasattr(client, "mode"):
        client.mode = mode
    warnings=[]; blockers=[]; completed=[]
    server_matches = select_exact(item_list(client.get("/servers")), args.server, "server")
    server = {"name": args.server, "uuid": require_identifier(server_matches[0], "server")}
    github_matches = select_exact(item_list(client.get("/github-apps")), args.github_app, "GitHub App")
    github_app = summary(github_matches[0], "name", "uuid", "id")
    try:
        project = ensure_project(client, args, mode); completed.append(project)
        environment = ensure_environment(client, project, args, mode); completed.append(environment)
        postgres = ensure_postgresql(client, project, environment, server, args, mode); completed.append(postgres)
    except ProvisioningError as exc:
        if any(p.get("status") == "created" for p in completed):
            blockers.append(f"Partial failure after created resources: {exc.message}. Re-run idempotently after review; no rollback was attempted.")
            project = completed[0] if completed else {"status":"unknown"}
            environment = completed[1] if len(completed)>1 else {"status":"blocked"}
            postgres = {"status":"blocked"}
        else:
            raise
    if postgres.get("status") in {"compatibility_unverified", "out_of_scope"}:
        blockers.append(f"PostgreSQL status requires manual review: {postgres['status']}.")
    if postgres.get("status") == "incompatible":
        blockers.append("Existing PostgreSQL contradicts desired state; recreate required manually (no delete/PATCH performed).")
    legacy = discover_legacy(client, args.product)
    if legacy:
        warnings.append("Legacy Application Compose resource reported only; no migration, deletion, or modification performed.")
    backend = {"name": f"{args.product}-api-dev", "kind": "application", "status": "planned_later"}
    frontend = {"name": f"{args.product}-frontend-dev", "kind": "application", "status": "planned_later"}
    parts = [project, environment, postgres]
    return redact({
        "status": compute_status(mode, parts, blockers),
        "mode": mode.name,
        "safety_gates": mode.gates(),
        "inputs": {"product":args.product,"repository":args.repository,"branch":args.branch,"coolify_project":args.coolify_project,"environment":args.environment,"server":args.server,"github_app":args.github_app},
        "selected_server": server,
        "github_app": github_app,
        "project": project,
        "environment_resource": environment,
        "postgresql": postgres,
        "applications": [backend, frontend],
        "legacy_compose": legacy,
        "warnings": warnings,
        "blockers": blockers,
        "contract": {"confirmed_endpoints": ["POST /api/v1/projects", "POST /api/v1/projects/{project_uuid}/environments", "POST /api/v1/databases/postgresql", "GET /api/v1/databases/{uuid}"], "postgresql_payload_preview": redact(postgresql_payload(args, "${project.uuid}", "${server.uuid}", "${generated_secret}"))},
    })


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Provision GoodMorning Coolify dev primitives safely.")
    p.add_argument("--product", required=True); p.add_argument("--repository", required=True); p.add_argument("--branch", required=True, choices=[ALLOWED_BRANCH])
    p.add_argument("--coolify-project", required=True); p.add_argument("--environment", required=True, choices=[ALLOWED_ENVIRONMENT]); p.add_argument("--server", required=True); p.add_argument("--github-app", required=True)
    group = p.add_mutually_exclusive_group(required=True); group.add_argument("--dry-run", action="store_true"); group.add_argument("--apply", action="store_true")
    p.add_argument("--confirm-product")
    return p


def main(argv: list[str] | None = None) -> int:
    try:
        result = provision(build_parser().parse_args(argv))
    except ProvisioningError as exc:
        print(json.dumps(exc.to_dict(), indent=2, sort_keys=True), file=sys.stderr); return exc.code
    print(json.dumps(redact(result), indent=2, sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
