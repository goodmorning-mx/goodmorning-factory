"""Conservative recovery for Coolify application create conflicts.

A normal 409/422 recovery only exposes the conflicting UUID to the audited
provisioner, which then hydrates and validates the resource before reuse.

A production manifest may additionally authorize migration of one exact legacy
Frontend UUID. That path verifies every recorded legacy field, PATCHes only the
allowlisted target application fields, and then returns the UUID to the same
post-write hydration and compatibility checks. Any drift fails closed.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from typing import Any, Callable


_UUID_KEYS = (
    "uuid",
    "application_uuid",
    "resource_uuid",
)
_UUID_RE = re.compile(
    r'(?i)["\'](?:uuid|application_uuid|resource_uuid)["\']\s*:\s*["\']([a-z0-9][a-z0-9_-]{5,63})["\']'
)


def _collect_uuids(value: Any, collected: list[str]) -> None:
    if isinstance(value, dict):
        for key in _UUID_KEYS:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip() and candidate.strip() not in collected:
                collected.append(candidate.strip())
        for nested in value.values():
            _collect_uuids(nested, collected)
    elif isinstance(value, list):
        for nested in value:
            _collect_uuids(nested, collected)


def _json_fragments(message: str) -> list[Any]:
    fragments: list[Any] = []
    for start in (message.find("{"), message.find("[")):
        if start < 0:
            continue
        try:
            fragments.append(json.loads(message[start:]))
        except json.JSONDecodeError:
            continue
    return fragments


def conflict_uuids(exc: Any) -> list[str]:
    """Extract all ordered, unique application UUIDs from a 409/422 error."""

    collected: list[str] = []
    for attr in ("data", "response_data", "details", "body"):
        _collect_uuids(getattr(exc, attr, None), collected)

    message = str(getattr(exc, "message", exc) or "")
    for match in _UUID_RE.finditer(message):
        candidate = match.group(1)
        if candidate not in collected:
            collected.append(candidate)
    for fragment in _json_fragments(message):
        _collect_uuids(fragment, collected)
    return collected


def conflict_uuid(exc: Any) -> str | None:
    """Return the first structured UUID for backward-compatible callers."""

    identifiers = conflict_uuids(exc)
    return identifiers[0] if identifiers else None


def _unwrap_application(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict) and isinstance(value.get("data"), dict):
        value = value["data"]
    return value if isinstance(value, dict) else None


def _application_value(item: dict[str, Any], key: str) -> Any:
    aliases = {
        "uuid": ("uuid", "id"),
        "git_repository": ("git_repository", "repository"),
        "git_branch": ("git_branch", "branch"),
        "domains": ("domains", "domain", "fqdn"),
        "dockerfile_location": ("dockerfile_location", "dockerfile"),
    }
    for candidate in aliases.get(key, (key,)):
        value = item.get(candidate)
        if value not in (None, ""):
            return value
    return None


def legacy_frontend_differences(
    module: Any,
    item: dict[str, Any],
    migration: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compare a hydrated Coolify application with the exact legacy contract."""

    expected = migration.get("expected")
    if not isinstance(expected, dict):
        return [{"field": "expected", "reason": "missing_contract"}]

    differences: list[dict[str, Any]] = []
    actual_uuid = _application_value(item, "uuid")
    expected_uuid = migration.get("uuid")
    if str(actual_uuid or "") != str(expected_uuid or ""):
        differences.append({"field": "uuid", "expected": expected_uuid, "actual": actual_uuid})

    for key, expected_value in expected.items():
        actual_value = _application_value(item, key)
        equal = module.normalized_equal(key, actual_value, expected_value)
        if not equal:
            differences.append(
                {"field": key, "expected": expected_value, "actual": actual_value}
            )
    return differences


def migrate_legacy_frontend(
    module: Any,
    client: Any,
    identifier: str,
    target_payload: dict[str, Any],
    migration: dict[str, Any],
) -> None:
    """Migrate one exact legacy Frontend or raise before any PATCH."""

    if identifier != str(migration.get("uuid") or ""):
        raise module.ProvisioningError(
            "Coolify conflict UUID is not the manifest-authorized legacy Frontend.",
            error_type="safety",
            code=2,
        )

    encoded = urllib.parse.quote(identifier, safe="")
    current = _unwrap_application(client.get(f"/applications/{encoded}"))
    if current is None:
        raise module.ProvisioningError(
            "Legacy Frontend detail could not be verified before migration.",
            error_type="coolify_api",
            code=2,
        )
    differences = legacy_frontend_differences(module, current, migration)
    if differences:
        fields = ",".join(sorted(str(entry.get("field")) for entry in differences))
        raise module.ProvisioningError(
            f"Legacy Frontend changed since authorization; refusing migration ({fields}).",
            error_type="safety",
            code=2,
        )
    if not hasattr(client, "migrate_frontend_application"):
        raise module.ProvisioningError(
            "Coolify client does not expose the audited legacy Frontend migration.",
            error_type="safety",
            code=2,
        )
    client.migrate_frontend_application(identifier, target_payload)


def install_application_conflict_recovery(
    module: Any,
    *,
    legacy_frontend_migration: dict[str, Any] | None = None,
    frontend_source_verification: dict[str, Any] | None = None,
    frontend_source_replacement: dict[str, Any] | None = None,
) -> None:
    """Install exact manifest contracts without weakening default checks."""

    client_class = module.CoolifyClient
    response_class = module.Response
    error_class = module.ProvisioningError
    setattr(
        client_class,
        "_gm_legacy_frontend_migration_contract",
        dict(legacy_frontend_migration) if legacy_frontend_migration else None,
    )
    setattr(
        client_class,
        "_gm_frontend_source_verification_contract",
        dict(frontend_source_verification) if frontend_source_verification else None,
    )
    setattr(
        client_class,
        "_gm_frontend_source_replacement_contract",
        dict(frontend_source_replacement) if frontend_source_replacement else None,
    )

    def wrap(method_name: str, *, frontend: bool = False) -> None:
        original: Callable[..., Any] = getattr(client_class, method_name)
        if getattr(original, "_gm_conflict_recovery", False):
            return

        def recovered(self: Any, payload: dict[str, Any]) -> Any:
            try:
                return original(self, payload)
            except error_class as exc:
                if getattr(exc, "http_status", None) not in {409, 422}:
                    raise
                identifiers = conflict_uuids(exc)

                migrated = False
                migration = getattr(
                    self,
                    "_gm_legacy_frontend_migration_contract",
                    None,
                )
                if frontend and isinstance(migration, dict):
                    authorized = str(migration.get("uuid") or "")
                    # Some Coolify 422 domain-conflict responses contain no
                    # resource UUID. The manifest already binds one exact
                    # legacy application, so hydrate and verify that UUID
                    # directly in that case. If Coolify does return UUIDs,
                    # keep requiring the authorized UUID to be among them.
                    if identifiers and authorized not in identifiers:
                        raise error_class(
                            "Coolify conflict does not reference the authorized legacy Frontend.",
                            error_type="safety",
                            code=2,
                        )
                    migrate_legacy_frontend(
                        module,
                        self,
                        authorized,
                        payload,
                        migration,
                    )
                    identifier = authorized
                    migrated = True
                else:
                    if not identifiers:
                        raise
                    identifier = identifiers[0]

                # The provisioner will GET /applications/{uuid} and revalidate
                # repository, branch, build, domain, source and destination.
                return response_class(
                    200,
                    {
                        "data": {
                            "uuid": identifier,
                            "_factory_legacy_migrated": migrated,
                        }
                    },
                    {},
                )

        recovered._gm_conflict_recovery = True  # type: ignore[attr-defined]
        setattr(client_class, method_name, recovered)

    wrap("create_backend_application")
    wrap("create_frontend_application", frontend=True)
