#!/usr/bin/env python3
"""Resolve the latest verified Software PROD backup for an isolated restore."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from scripts.reconcile_database_backups import (
    CoolifyClient,
    load_manifest,
    required_env,
    select_database,
    validate_postgres_database,
    verify_object,
)


def _timestamp(item: dict[str, Any], fallback: int) -> tuple[str, int]:
    for field in ("created_at", "createdAt", "started_at", "startedAt", "updated_at", "updatedAt"):
        value = item.get(field)
        if isinstance(value, str) and value:
            return value, fallback
    return "", -fallback


def prepare(manifest_path: Path, output_path: Path) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    if manifest.get("product") != "software" or manifest.get("_backup_environment") != "production":
        raise ValueError("Restore drill is restricted to the Software production manifest.")

    client = CoolifyClient(required_env("COOLIFY_BASE_URL"), required_env("COOLIFY_API_TOKEN"))
    database = select_database(
        client.call("GET", "/databases"),
        "software",
        manifest.get("_backup_database_name"),
    )
    database = validate_postgres_database(
        client.call("GET", f"/databases/{database['uuid']}"),
        manifest["_backup_database_name"],
    )
    backups = client.call("GET", f"/databases/{database['uuid']}/backups")
    if not isinstance(backups, list) or len(backups) != 1:
        raise ValueError("Expected exactly one configured Software production backup.")
    backup_uuid = backups[0].get("uuid")
    if not isinstance(backup_uuid, str) or not backup_uuid:
        raise ValueError("Software production backup has no identifier.")
    execution_response = client.call(
        "GET", f"/databases/{database['uuid']}/backups/{backup_uuid}/executions"
    )
    executions = execution_response.get("executions", []) if isinstance(execution_response, dict) else []
    successful = [
        item for item in executions
        if isinstance(item, dict)
        and str(item.get("status", "")).lower() == "success"
        and isinstance(item.get("filename"), str)
        and item["filename"].strip()
    ]
    if not successful:
        raise ValueError("No successful Software production backup is available for restore.")
    execution = max(enumerate(successful), key=lambda pair: _timestamp(pair[1], pair[0]))[1]
    object_info = verify_object(execution["filename"])
    key = object_info.get("key")
    size = object_info.get("size")
    if not isinstance(key, str) or not key or "\n" in key or "\r" in key:
        raise ValueError("Verified production backup has an invalid object key.")
    if not isinstance(size, int) or size < 1:
        raise ValueError("Verified production backup has an invalid object size.")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as output:
        output.write(f"backup_key={key}\n")
        output.write(f"backup_size={size}\n")
        output.write(f"backup_name={Path(execution['filename']).name}\n")
    return {"size": size, "filename": Path(execution["filename"]).name}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path(os.environ.get("GITHUB_OUTPUT", "/dev/null")))
    args = parser.parse_args()
    result = prepare(args.manifest, args.output)
    print(json.dumps({"status": "verified", **result}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
