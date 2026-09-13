#!/usr/bin/env python3
"""Safely attach declarative production domains to existing Coolify apps.

This command is intentionally narrow: it never creates applications, changes
DNS, forces domain conflicts, or deploys. Coolify handles routing and TLS for
HTTPS domains after the next normal application deployment.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

try:
    import advanced_product_contract as contract
    import reconcile_advanced_product as advanced
except ModuleNotFoundError:
    from scripts import advanced_product_contract as contract
    from scripts import reconcile_advanced_product as advanced


def _domains(value: Any) -> list[str]:
    values = value if isinstance(value, list) else str(value or "").split(",")
    return [str(item).strip().rstrip("/") for item in values if str(item).strip()]


def _binding_urls(manifest: dict[str, Any], application: str) -> list[str]:
    urls: list[str] = []
    for binding in manifest["domain_bindings"]:
        if binding["application"] == application and binding["desired_state"] == "active":
            urls.extend(f"https://{host}" for host in [binding["apex"], *binding["aliases"]])
    return urls


def _check_dns(hostname: str) -> bool:
    try:
        return bool(socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM))
    except OSError:
        return False


def _probe_https(url: str, *, timeout: float = 10.0) -> tuple[bool, int | None]:
    request = urllib.request.Request(url, headers={"User-Agent": "goodmorning-factory-domain-check"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return True, response.status
    except urllib.error.HTTPError as exc:
        # HTTPError means TLS completed and the endpoint returned a response.
        return True, exc.code
    except urllib.error.URLError as exc:
        return False, None
    except (TimeoutError, OSError):
        return False, None


def validate_public_endpoints(
    manifest: dict[str, Any],
    *,
    attempts: int = 6,
    retry_base_seconds: float = 2.0,
    sleeper: Callable[[float], None] = time.sleep,
    dns_check: Callable[[str], bool] = _check_dns,
    https_check: Callable[[str], tuple[bool, int | None]] = _probe_https,
) -> dict[str, Any]:
    """Retry read-only public DNS/HTTPS checks and return only safe status data."""
    if attempts < 1 or retry_base_seconds < 0:
        raise ValueError("Validation attempts must be positive and retry base cannot be negative.")
    checks: list[dict[str, Any]] = []
    public_urls: list[tuple[str, str]] = []
    for binding in manifest["domain_bindings"]:
        for host in [binding["apex"], *binding["aliases"]]:
            public_urls.append((host, f"https://{host}/"))
    for app in manifest["applications"]:
        if app["role"] == "api" and app["domain"]:
            public_urls.append((urllib.parse.urlparse(app["domain"]).hostname or "", f"{app['domain']}{app['health_path']}"))

    for hostname, url in public_urls:
        dns_ok = False
        tls_ok = False
        http_status: int | None = None
        for attempt in range(1, attempts + 1):
            dns_ok = dns_check(hostname)
            if not dns_ok:
                if attempt < attempts:
                    sleeper(min(retry_base_seconds * (2 ** (attempt - 1)), 60.0))
                continue
            tls_ok, http_status = https_check(url)
            if tls_ok and http_status == 200:
                break
            if not tls_ok and attempt < attempts:
                sleeper(min(retry_base_seconds * (2 ** (attempt - 1)), 60.0))
            elif tls_ok:
                break
        endpoint_state = (
            "pending" if not dns_ok else
            "ssl_pending" if not tls_ok else
            "healthy" if http_status == 200 else
            "error"
        )
        checks.append({
            "host": hostname,
            "dns": "dns_configured" if dns_ok else "pending",
            "https": "healthy" if tls_ok else ("ssl_pending" if dns_ok else "pending"),
            "http_status": http_status,
            "http_status_200": http_status == 200,
            "state": endpoint_state,
            "attempts": attempt,
        })
    healthy = bool(checks) and all(item["state"] == "healthy" for item in checks)
    any_dns_configured = any(item["dns"] == "dns_configured" for item in checks)
    endpoint_error = any(item["state"] == "error" for item in checks)
    ssl_pending = any(item["state"] == "ssl_pending" for item in checks)
    return {
        "status": "healthy" if healthy else ("error" if endpoint_error else ("ssl_pending" if ssl_pending else ("dns_configured" if any_dns_configured else "pending"))),
        "checks": checks,
    }


def reconcile_domains(
    manifest: dict[str, Any],
    *,
    client: Any,
    server_name: str,
    destination_uuid: str,
    confirm_product: str | None,
    confirm_environment: str | None,
    confirmation: str | None = None,
    validate_live: bool = False,
    validator: Callable[[dict[str, Any]], dict[str, Any]] = validate_public_endpoints,
) -> dict[str, Any]:
    if manifest["topology"] != contract.PRODUCTION_TOPOLOGY or not manifest["domain_bindings"]:
        raise advanced.ReconcileError("Domain reconciliation requires active bindings in a production manifest.")
    if client.apply:
        if not advanced._bool_env("COOLIFY_ALLOW_ADVANCED_PRODUCTION_WRITES"):
            raise advanced.ReconcileError("Apply requires COOLIFY_ALLOW_ADVANCED_PRODUCTION_WRITES=true.")
        if confirm_product != manifest["product"] or confirm_environment != "production":
            raise advanced.ReconcileError("Apply requires exact product and production environment confirmations.")
        if confirmation != f"domain:{manifest['product']}:production":
            raise advanced.ReconcileError("Apply requires --confirmation domain:<product>:production.")

    context = advanced._discover_context(
        client,
        manifest,
        server_name,
        manifest["coolify_github_app_name"],
        destination_uuid,
    )
    app_by_name = {app["name"]: app for app in manifest["applications"]}
    listed = advanced._items(client.get("/applications"))
    actions: list[dict[str, Any]] = []
    for app_name in sorted({binding["application"] for binding in manifest["domain_bindings"]}):
        app = app_by_name[app_name]
        resource_name = f"{manifest['product']}-{app_name}-prod"
        found = advanced._unique(listed, resource_name, "application")
        if not found:
            raise advanced.ReconcileError(f"Existing Coolify application {resource_name!r} was not found; domain reconciliation never creates applications.")
        if advanced._scope_conflicts(found, context):
            raise advanced.ReconcileError(f"Application {resource_name!r} exists outside the selected production scope.")
        advanced._verify_adopted_uuid(manifest, "application", resource_name, found)
        identifier = advanced._uuid(found, "application")
        detail = client.get(f"/applications/{urllib.parse.quote(identifier, safe='')}")
        if str(advanced._field(detail, "name") or "") != resource_name:
            raise advanced.ReconcileError("Coolify application identity did not match the selected production frontend.")
        if str(advanced._field(detail, "git_branch") or "") != "main":
            raise advanced.ReconcileError("Coolify application is not bound to the production main branch.")

        desired = [app["domain"], *_binding_urls(manifest, app_name)]
        desired = [value for value in desired if value]
        desired_hosts = {urllib.parse.urlparse(value).hostname for value in desired}
        for other in listed:
            if str(advanced._field(other, "uuid", "id") or "") == identifier:
                continue
            conflict_hosts = desired_hosts & {
                urllib.parse.urlparse(value).hostname
                for value in _domains(advanced._field(other, "domains", "domain", "fqdn"))
            }
            if conflict_hosts:
                raise advanced.ReconcileError("A requested domain is already assigned to another Coolify application.")

        current = _domains(advanced._field(detail, "domains", "domain", "fqdn"))
        desired_domains = desired
        if current == desired_domains:
            actions.append({"resource": resource_name, "action": "unchanged", "fields": []})
        elif not client.apply:
            actions.append({"resource": resource_name, "action": "would_update", "fields": ["domains"]})
        else:
            client.patch(
                f"/applications/{urllib.parse.quote(identifier, safe='')}",
                {"domains": ",".join(desired_domains)},
            )
            verified = client.get(f"/applications/{urllib.parse.quote(identifier, safe='')}")
            if _domains(advanced._field(verified, "domains", "domain", "fqdn")) != desired_domains:
                raise advanced.ReconcileError(f"Coolify did not verify requested domains for {resource_name!r}.")
            actions.append({"resource": resource_name, "action": "updated", "fields": ["domains"]})

    result: dict[str, Any] = {
        "status": "applied" if client.apply else "planned",
        "product": manifest["product"],
        "environment": "production",
        "state": "ssl_pending" if client.apply else "pending",
        "actions": actions,
        "dns": "unchecked",
        "https": "unchecked",
        "deployment_requested": False,
    }
    # Live checks are opt-in so a local/default plan remains strictly read-only
    # against Coolify and does not unexpectedly contact production endpoints.
    if validate_live:
        live = validator(manifest)
        result["state"] = live["status"]
        result["checks"] = live["checks"]
        result["dns"] = "dns_configured" if all(item["dns"] == "dns_configured" for item in live["checks"]) else "pending"
        result["https"] = "healthy" if live["status"] == "healthy" else live["status"]
    return result


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Plan or reconcile custom production domains in Coolify.")
    result.add_argument("--manifest", required=True)
    result.add_argument("--phase", choices=("plan", "apply"), default="plan")
    result.add_argument("--server", default=os.environ.get("COOLIFY_SERVER_NAME", "localhost"))
    result.add_argument("--destination-uuid", default=os.environ.get("COOLIFY_DESTINATION_UUID"))
    result.add_argument("--confirm-product")
    result.add_argument("--confirm-environment", choices=("production",))
    result.add_argument("--confirmation", help="Exact apply confirmation: domain:<product>:production.")
    result.add_argument("--validate-live", action="store_true", help="Read-only DNS, HTTPS, frontend 200, and API health checks.")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        manifest = contract.load_manifest(args.manifest)
        if not args.destination_uuid:
            raise advanced.ReconcileError("COOLIFY_DESTINATION_UUID is required.")
        client = advanced.Coolify(
            os.environ.get("COOLIFY_BASE_URL", ""),
            os.environ.get("COOLIFY_API_TOKEN", ""),
            apply=args.phase == "apply",
        )
        result = reconcile_domains(
            manifest,
            client=client,
            server_name=args.server,
            destination_uuid=args.destination_uuid,
            confirm_product=args.confirm_product,
            confirm_environment=args.confirm_environment,
            confirmation=args.confirmation,
            validate_live=args.validate_live,
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (contract.ContractError, advanced.ReconcileError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
