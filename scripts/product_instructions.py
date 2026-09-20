#!/usr/bin/env python3
"""Render and reconcile the agent instructions contract for a product.

The renderer is deliberately side-effect free.  GitHub synchronization is a
small adapter around it so tests can verify generated content without network
access.  Only the managed section is replaced; product-owned text between the
custom markers is retained verbatim.
"""
from __future__ import annotations

import argparse
import base64
import json
import re
import urllib.parse
from pathlib import Path
from typing import Any, Callable

TEMPLATE_VERSION = "1"
TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates" / "product-instructions.md"
MANAGED_START = "<!-- GOODMORNING FACTORY: MANAGED START -->"
MANAGED_END = "<!-- GOODMORNING FACTORY: MANAGED END -->"
CUSTOM_START = "<!-- GOODMORNING PRODUCT: CUSTOM START -->"
CUSTOM_END = "<!-- GOODMORNING PRODUCT: CUSTOM END -->"
PRODUCT_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
REPOSITORY_RE = re.compile(r"^goodmorning-mx/[A-Za-z0-9_.-]+$")
SECRET_VALUE = re.compile(r"(?:gh[pousr]_[A-Za-z0-9_]+|sk-[A-Za-z0-9_-]{16,}|-----BEGIN [^-]+-----|postgres(?:ql)?://[^\s]+:[^\s@]+@)")


class InstructionsError(ValueError):
    """Raised when instructions cannot be safely rendered or reconciled."""


def _value(value: Any, fallback: str = "—") -> str:
    return str(value) if value not in (None, "") else fallback


def _manifest_product(manifest: dict[str, Any]) -> str:
    product = manifest.get("product")
    if not isinstance(product, str) or not PRODUCT_RE.fullmatch(product):
        raise InstructionsError("Manifest product must be a lowercase DNS-safe slug.")
    return product


def _manifest_repository(manifest: dict[str, Any]) -> str:
    repository = manifest.get("repository")
    if not isinstance(repository, str) or not REPOSITORY_RE.fullmatch(repository):
        raise InstructionsError("Manifest repository must be a goodmorning-mx repository.")
    return repository


def _safe_environment(environment: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    if not isinstance(environment, list):
        return result
    for entry in environment:
        if not isinstance(entry, dict):
            continue
        key = entry.get("key")
        source = entry.get("source")
        if not isinstance(key, str) or not key or not isinstance(source, str):
            continue
        item = {"key": key, "source": source}
        for field in ("resource", "binding", "driver", "legacy_key"):
            if isinstance(entry.get(field), str):
                item[field] = entry[field]
        # Never render literal values, even when a manifest is malformed.
        result.append(item)
    return result


def _environment_label(item: dict[str, str]) -> str:
    details = [item["source"]]
    for field in ("resource", "binding", "driver", "legacy_key"):
        if item.get(field):
            details.append(f"{field}={item[field]}")
    return f"`{item['key']}` [{', '.join(details)}]"


def _render_context(manifest: dict[str, Any]) -> str:
    product = _manifest_product(manifest)
    topology = _value(manifest.get("topology"), "unknown")
    branch = _value(manifest.get("branch"), "dev")
    repository = _value(manifest.get("repository"), f"goodmorning-mx/{product}")
    development_domain = _value(manifest.get("development_domain"))
    production_domain = _value(manifest.get("production_domain"), f"https://{product}.goodmorning.mx")

    lines = [
        f"## Contexto administrado · contrato v{TEMPLATE_VERSION}",
        "",
        "GoodMorning Factory administra este producto. El repositorio del producto es la fuente de verdad de la aplicación; Factory es la fuente de verdad de su provisionamiento y operación.",
        "",
        f"- Producto: `{product}`",
        f"- Repositorio: `{repository}`",
        f"- Topología: `{topology}`",
        f"- Rama de desarrollo integrada: `{branch}`",
        f"- DEV frontend: `{development_domain}`",
        f"- PROD esperado: `{production_domain}`",
        "",
        "### Cómo trabajar",
        "",
        "1. Inspecciona primero el estado real del repositorio y lee `AGENTS.md`, `CLAUDE.md`, README, contratos y documentación aplicables.",
        "2. Trabaja desde una rama de feature/fix/chore creada desde `dev`; integra mediante pull request hacia `dev`.",
        "3. Mantén `main` como producción. La promoción a producción usa el flujo de release de Factory y no se hace con pushes directos.",
        "4. Preserva compatibilidad, migraciones, autenticación, APIs, módulos core, healthchecks, datos y despliegues existentes.",
        "5. Verifica con los comandos del repositorio y con los flujos reales de CI/DEV. No inventes comportamiento de GitHub, Coolify ni secretos.",
        "6. Completa el goal de punta a punta y reporta archivos, validaciones, riesgos, impacto de configuración y rollback.",
        "",
        "### Operación DEV/PROD",
        "",
        "- DEV sigue `dev`, usa recursos aislados y auto-deploy de Coolify cuando el contrato lo declara.",
        "- PROD sigue `main`, requiere promoción explícita, validación previa y backup verificado cuando hay base de datos.",
        "- Las aplicaciones se observan por sus healthchecks; una configuración no verificada debe detener la reconciliación.",
        "- PostgreSQL es privado. Las URLs de base de datos, secretos administrados y valores generados se inyectan en runtime; nunca se escriben en este archivo.",
        "- Las bases persistentes conservan backups externos cuando el manifiesto los declara.",
        "",
        "### Arquitectura y recursos",
        "",
    ]

    applications = manifest.get("applications") if isinstance(manifest.get("applications"), list) else []
    if applications:
        lines.append("#### Aplicaciones")
        lines.append("")
        for app in applications:
            if not isinstance(app, dict):
                continue
            name = _value(app.get("name"))
            role = _value(app.get("role"))
            domain = _value(app.get("domain"), "privado")
            lines.append(f"- `{name}` · rol `{role}` · puerto `{_value(app.get('port'))}` · healthcheck `{_value(app.get('health_path'))}` · dominio `{domain}` · auto-deploy `{_value(app.get('auto_deploy'))}`")
            env = _safe_environment(app.get("environment"))
            if env:
                lines.append("  - Variables declaradas (sin valores): " + ", ".join(_environment_label(item) for item in env))
        lines.append("")

    for label, key in (("Bases de datos", "databases"), ("Redis", "redis")):
        resources = manifest.get(key) if isinstance(manifest.get(key), list) else []
        if resources:
            lines.append(f"#### {label}")
            lines.append("")
            for resource in resources:
                if not isinstance(resource, dict):
                    continue
                lines.append(f"- `{_value(resource.get('name'))}` · versión `{_value(resource.get('version'))}` · privado `{_value(not resource.get('public', True))}` · persistencia/backups declarados en el manifiesto")
            lines.append("")

    modules = manifest.get("modules") if isinstance(manifest.get("modules"), list) else []
    if modules:
        lines.extend(["#### Módulos core y servicios", ""])
        for module in modules:
            if not isinstance(module, dict):
                continue
            modes = module.get("deployment_modes") if isinstance(module.get("deployment_modes"), list) else ["embedded"]
            lines.append(f"- `{_value(module.get('name'))}` v`{_value(module.get('version'))}` · tipo `{_value(module.get('kind'))}` · modo seleccionado `{_value(module.get('deployment_mode'), _value(module.get('default_deployment_mode'), 'embedded'))}` · modos permitidos: {', '.join(f'`{mode}`' for mode in modes)}")
            requirements = module.get("resource_requirements") if isinstance(module.get("resource_requirements"), list) else []
            for requirement in requirements:
                if isinstance(requirement, dict):
                    lines.append(f"  - Recurso `{_value(requirement.get('resource_name'), _value(requirement.get('name')))}` · clase `{_value(requirement.get('kind'))}` · owner `{_value(module.get('name'))}` · shared `{_value(requirement.get('shared'), 'false')}`")
            bindings = module.get("bindings") if isinstance(module.get("bindings"), list) else []
            safe_bindings = _safe_environment(bindings)
            if safe_bindings:
                lines.append("  - Bindings (sin valores): " + ", ".join(_environment_label(item) for item in safe_bindings))
        lines.append("")

    ownership = manifest.get("resource_ownership") if isinstance(manifest.get("resource_ownership"), list) else []
    if ownership:
        lines.extend(["#### Ownership y bindings", "", "Factory conserva el ownership declarado; los recursos compartidos no deben eliminarse desde un módulo consumidor.", ""])
        for item in ownership:
            if isinstance(item, dict):
                lines.append(f"- recurso `{_value(item.get('resource'))}` · owner `{_value(item.get('owner_module'))}` · requirement `{_value(item.get('requirement'))}` · shared `{_value(item.get('shared'), 'false')}`")
        lines.append("")

    lines.extend([
        "### Validación y Definition of Done",
        "",
        "- Ejecutar los checks documentados del producto: typecheck/lint, tests unitarios e integración, build, `docker compose config` o builds de servicios cuando apliquen.",
        "- Validar healthchecks y smoke checks de DEV después de cambios de despliegue; no afirmar que un entorno está funcionando sin evidencia.",
        "- Confirmar que no se agregaron secretos, tokens, credenciales, UUIDs privados ni identificadores de infraestructura.",
        "- Confirmar que las capacidades existentes y las migraciones siguen siendo compatibles.",
        "- Documentar cualquier check omitido, riesgo restante y procedimiento de rollback.",
        "",
        "### Límites importantes",
        "",
        "No modifiques contratos de Resources V3, bindings, managed secrets, ownership, healthchecks, auto-deploy, backups o migraciones sin entender su efecto en DEV y PROD. Prefiere cambiar el manifiesto y el reconciliador versionado antes que realizar pasos manuales en Coolify.",
    ])
    return "\n".join(lines).rstrip() + "\n"


def _custom_content(existing: str | None) -> str:
    if not existing:
        return "Agrega aquí instrucciones específicas del producto. Esta sección no es administrada por Factory.\n"
    start = existing.find(CUSTOM_START)
    end = existing.find(CUSTOM_END, start + len(CUSTOM_START)) if start >= 0 else -1
    if start >= 0 and end >= 0:
        content = existing[start + len(CUSTOM_START):end].strip("\n")
        return content + "\n" if content else "Agrega aquí instrucciones específicas del producto. Esta sección no es administrada por Factory.\n"
    return existing.strip("\n") + "\n"


def render_instructions(manifest: dict[str, Any], existing: str | None = None) -> str:
    """Render managed instructions while retaining product-owned text."""
    product = _manifest_product(manifest)
    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    rendered = template.replace("{{PRODUCT_TITLE}}", product)
    rendered = rendered.replace("{{FACTORY_MANAGED_CONTENT}}", _render_context(manifest).rstrip())
    rendered = rendered.replace("{{PRODUCT_CUSTOM_CONTENT}}", _custom_content(existing).rstrip())
    if SECRET_VALUE.search(rendered):
        raise InstructionsError("Rendered instructions contain a secret-shaped value.")
    if "{{" in rendered or "}}" in rendered:
        raise InstructionsError("Rendered instructions contain unresolved template placeholders.")
    return rendered.rstrip() + "\n"


def _decode_content(payload: dict[str, Any]) -> str:
    content = payload.get("content")
    if not isinstance(content, str):
        raise InstructionsError("GitHub instructions response has no content.")
    try:
        return base64.b64decode(content).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise InstructionsError("Existing product instructions are not valid UTF-8 base64 content.") from exc


RequestFn = Callable[..., tuple[int, Any]]


def reconcile_remote(product: str, manifest: dict[str, Any], request_fn: RequestFn, *, branch: str = "dev") -> dict[str, Any]:
    """Upsert instructions.md in a product repository using a supplied API client."""
    if product != _manifest_product(manifest):
        raise InstructionsError("Product argument does not match the manifest.")
    repository = _manifest_repository(manifest)
    path = f"instructions.md"
    encoded = urllib.parse.quote(path, safe="/")
    endpoint = f"/repos/{repository}/contents/{encoded}?ref={urllib.parse.quote(branch, safe='')}"
    status, current = request_fn("GET", endpoint)
    existing = _decode_content(current) if status == 200 and isinstance(current, dict) else None
    rendered = render_instructions(manifest, existing)
    if existing == rendered:
        return {"status": "unchanged", "product": product, "path": path, "branch": branch}
    body: dict[str, Any] = {
        "message": f"factory: reconcile agent instructions for {product}",
        "content": base64.b64encode(rendered.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if status == 200 and isinstance(current, dict) and isinstance(current.get("sha"), str):
        body["sha"] = current["sha"]
    elif status != 404:
        raise InstructionsError(f"Unable to inspect {repository}/instructions.md (HTTP {status}).")
    updated_status, updated = request_fn("PUT", f"/repos/{repository}/contents/{encoded}", body=body)
    if updated_status not in {200, 201} or not isinstance(updated, dict):
        raise InstructionsError(f"GitHub did not confirm instructions update for {product} (HTTP {updated_status}).")
    commit = updated.get("commit") if isinstance(updated.get("commit"), dict) else {}
    return {"status": "updated", "product": product, "path": path, "branch": branch, "commit_sha": commit.get("sha")}


def _api_request(method: str, path: str, *, body: dict[str, Any] | None = None) -> tuple[int, Any]:
    # Imported lazily so rendering and unit tests never require credentials.
    try:
        from scripts.register_product_manifest import request
    except ModuleNotFoundError:  # Direct execution from the scripts directory.
        from register_product_manifest import request
    return request(method, path, body=body)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile Factory-managed agent instructions into a product repository.")
    parser.add_argument("--manifest", action="append", required=True, help="Local products/<product>.json manifest; repeat for multiple products.")
    parser.add_argument("--branch", default="dev")
    args = parser.parse_args(argv)
    try:
        results = []
        for source in args.manifest:
            manifest = json.loads(Path(source).read_text(encoding="utf-8"))
            results.append(reconcile_remote(_manifest_product(manifest), manifest, _api_request, branch=args.branch))
        print(json.dumps(results, sort_keys=True))
        return 0
    except (InstructionsError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
