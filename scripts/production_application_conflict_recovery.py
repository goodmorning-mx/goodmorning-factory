"""Conservative recovery for Coolify application create conflicts.

Coolify can answer HTTP 409/422 while returning the conflicting application UUID
inside the error body. The production provisioner already hydrates and validates
an application when creation returns a UUID, so this adapter converts only that
specific, structured conflict into a normal response. Exact contract validation
still happens in ``provision_product_production`` before the resource is reused.
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable


_UUID_KEYS = (
    "uuid",
    "application_uuid",
    "resource_uuid",
)
_UUID_RE = re.compile(
    r'(?i)["\'](?:uuid|application_uuid|resource_uuid)["\']\s*:\s*["\']([a-z0-9][a-z0-9_-]{5,63})["\']'
)


def _find_uuid(value: Any) -> str | None:
    if isinstance(value, dict):
        for key in _UUID_KEYS:
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        for nested in value.values():
            candidate = _find_uuid(nested)
            if candidate:
                return candidate
    elif isinstance(value, list):
        for nested in value:
            candidate = _find_uuid(nested)
            if candidate:
                return candidate
    return None


def conflict_uuid(exc: Any) -> str | None:
    """Extract one opaque application UUID from a 409/422 exception."""

    for attr in ("data", "response_data", "details", "body"):
        candidate = _find_uuid(getattr(exc, attr, None))
        if candidate:
            return candidate

    message = str(getattr(exc, "message", exc) or "")
    match = _UUID_RE.search(message)
    if match:
        return match.group(1)

    # Some API clients embed a JSON object after a short textual prefix.
    for start in (message.find("{"), message.find("[")):
        if start < 0:
            continue
        try:
            parsed = json.loads(message[start:])
        except json.JSONDecodeError:
            continue
        candidate = _find_uuid(parsed)
        if candidate:
            return candidate
    return None


def install_application_conflict_recovery(module: Any) -> None:
    """Patch the Coolify client without weakening compatibility checks."""

    client_class = module.CoolifyClient
    response_class = module.Response
    error_class = module.ProvisioningError

    def wrap(method_name: str) -> None:
        original: Callable[..., Any] = getattr(client_class, method_name)
        if getattr(original, "_gm_conflict_recovery", False):
            return

        def recovered(self: Any, payload: dict[str, Any]) -> Any:
            try:
                return original(self, payload)
            except error_class as exc:
                if getattr(exc, "http_status", None) not in {409, 422}:
                    raise
                identifier = conflict_uuid(exc)
                if not identifier:
                    raise
                # The provisioner will GET /applications/{uuid} and validate
                # repository, branch, build, domain, source and destination.
                return response_class(200, {"data": {"uuid": identifier}}, {})

        recovered._gm_conflict_recovery = True  # type: ignore[attr-defined]
        setattr(client_class, method_name, recovered)

    wrap("create_backend_application")
    wrap("create_frontend_application")
