#!/usr/bin/env python3
"""Reconcile development PostgreSQL backups from a reviewed product manifest."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable
from urllib import error, request


class BackupError(RuntimeError):
    """Raised when backup reconciliation cannot proceed safely."""


SENSITIVE_ENV_NAMES = (
    "COOLIFY_API_TOKEN",
    "OCI_S3_ACCESS_KEY",
    "OCI_S3_SECRET_KEY",
)

STORAGE_NAME = "goodmorning-factory-oracle-backups"

TINKER_PROGRAM = r"""
$payload = json_decode(stream_get_contents(STDIN), true, 512, JSON_THROW_ON_ERROR);
$storage = \App\Models\S3Storage::firstOrNew([
    'team_id' => (int) $payload['team_id'],
    'name' => $payload['name'],
]);
$storage->fill([
    'description' => $payload['description'],
    'region' => $payload['region'],
    'key' => $payload['key'],
    'secret' => $payload['secret'],
    'bucket' => $payload['bucket'],
    'endpoint' => $payload['endpoint'],
]);
$storage->testConnection(shouldSave: true);
echo json_encode([
    'uuid' => $storage->uuid,
    'name' => $storage->name,
    'is_usable' => (bool) $storage->is_usable,
], JSON_THROW_ON_ERROR);
""".strip()


def redact(value: str) -> str:
    result = value
    for name in SENSITIVE_ENV_NAMES:
        secret = os.environ.get(name, "")
        if secret:
            result = result.replace(secret, "[REDACTED]")
    return result


def required_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise BackupError(f"{name} is required")
    return value


def bool_arg(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized not in {"true", "false"}:
        raise argparse.ArgumentTypeError("expected true or false")
    return normalized == "true"


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError(f"Could not read manifest {path}: {exc}") from exc
    if manifest.get("topology") != "resources-v2":
        raise BackupError("Database backups are restricted to resources-v2 products")
    if manifest.get("branch") != "dev":
        raise BackupError("Database backup reconciliation is restricted to dev")
    product = manifest.get("product")
    if not isinstance(product, str) or not product or not all(
        character.islower() or character.isdigit() or character == "-" for character in product
    ):
        raise BackupError("Manifest product must be a lowercase slug")
    backup = manifest.get("backup")
    if not isinstance(backup, dict) or backup.get("enabled") is not True:
        raise BackupError("Manifest must explicitly enable its backup contract")
    frequency = backup.get("frequency")
    if not isinstance(frequency, str) or len(frequency.split()) != 5:
        raise BackupError("Backup frequency must be an explicit five-field cron expression")
    for field in (
        "local_retention_count",
        "local_retention_days",
        "s3_retention_count",
        "s3_retention_days",
    ):
        if not isinstance(backup.get(field), int) or backup[field] < 1:
            raise BackupError(f"Backup field {field} must be a positive integer")
    return manifest


class CoolifyClient:
    def __init__(self, base_url: str, token: str, timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        if not path.startswith("/") or ".." in path:
            raise BackupError("Refusing malformed Coolify API path")
        data = None if body is None else json.dumps(body).encode("utf-8")
        api_request = request.Request(
            f"{self.base_url}/api/v1{path}",
            data=data,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
        )
        try:
            with request.urlopen(api_request, timeout=self.timeout) as response:
                payload = response.read().decode("utf-8")
        except error.HTTPError as exc:
            response_body = exc.read().decode("utf-8", errors="replace")
            raise BackupError(
                redact(f"Coolify {method} {path} returned HTTP {exc.code}: {response_body[:1000]}")
            ) from exc
        except error.URLError as exc:
            raise BackupError(redact(f"Coolify {method} {path} failed: {exc.reason}")) from exc
        if not payload:
            return None
        try:
            return json.loads(payload)
        except json.JSONDecodeError as exc:
            raise BackupError(f"Coolify {method} {path} returned invalid JSON") from exc


def select_database(databases: Any, product: str) -> dict[str, Any]:
    if not isinstance(databases, list):
        raise BackupError("Coolify databases response must be a list")
    expected_name = f"{product}-postgres-dev"
    matches = [item for item in databases if isinstance(item, dict) and item.get("name") == expected_name]
    if len(matches) != 1:
        raise BackupError(f"Expected exactly one Coolify database named {expected_name}; found {len(matches)}")
    database = matches[0]
    database_type = str(database.get("type", "")).lower()
    image = str(database.get("image", "")).lower()
    if "postgres" not in database_type and "postgres" not in image:
        raise BackupError(f"{expected_name} is not PostgreSQL")
    if database.get("is_public") not in {False, 0, None}:
        raise BackupError(f"{expected_name} must remain private")
    uuid = database.get("uuid")
    if not isinstance(uuid, str) or not uuid:
        raise BackupError(f"{expected_name} has no stable UUID")
    return database


def parse_json_object(output: str, required_key: str) -> dict[str, Any]:
    decoder = json.JSONDecoder()
    for index, character in enumerate(output):
        if character != "{":
            continue
        try:
            value, _end = decoder.raw_decode(output[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and required_key in value:
            return value
    raise BackupError("Command completed without the expected sanitized JSON result")


def register_storage(team_id: int, runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run) -> dict[str, Any]:
    host = required_env("COOLIFY_SSH_HOST")
    user = required_env("COOLIFY_SSH_USER")
    identity = required_env("COOLIFY_SSH_IDENTITY_FILE")
    known_hosts = required_env("COOLIFY_SSH_KNOWN_HOSTS_FILE")
    payload = {
        "team_id": team_id,
        "name": STORAGE_NAME,
        "description": "GoodMorning Factory managed Oracle Object Storage backups",
        "region": required_env("OCI_S3_REGION"),
        "key": required_env("OCI_S3_ACCESS_KEY"),
        "secret": required_env("OCI_S3_SECRET_KEY"),
        "bucket": required_env("OCI_S3_BUCKET"),
        "endpoint": required_env("OCI_S3_ENDPOINT"),
    }
    remote_command = "docker exec -i coolify php artisan tinker --execute=" + shlex.quote(TINKER_PROGRAM)
    command = [
        "ssh",
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=yes",
        "-o", f"UserKnownHostsFile={known_hosts}",
        "-o", f"IdentityFile={identity}",
        f"{user}@{host}",
        remote_command,
    ]
    completed = runner(
        command,
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        timeout=90,
        check=False,
    )
    if completed.returncode != 0:
        details = redact((completed.stderr or completed.stdout or "unknown SSH error")[-2000:])
        raise BackupError(f"Could not register or verify Coolify S3 storage: {details}")
    result = parse_json_object(completed.stdout, "uuid")
    if result.get("name") != STORAGE_NAME or result.get("is_usable") is not True:
        raise BackupError("Coolify did not mark the managed S3 storage as usable")
    return result


def desired_backup_payload(backup: dict[str, Any], storage_uuid: str, backup_now: bool) -> dict[str, Any]:
    return {
        "enabled": True,
        "save_s3": True,
        "dump_all": False,
        "frequency": backup["frequency"],
        "s3_storage_uuid": storage_uuid,
        "database_backup_retention_amount_locally": backup["local_retention_count"],
        "database_backup_retention_days_locally": backup["local_retention_days"],
        "database_backup_retention_amount_s3": backup["s3_retention_count"],
        "database_backup_retention_days_s3": backup["s3_retention_days"],
        "timeout": int(backup.get("timeout_seconds", 3600)),
        "backup_now": backup_now,
    }


def reconcile_schedule(
    client: CoolifyClient,
    database_uuid: str,
    backup: dict[str, Any],
    storage_uuid: str,
    backup_now: bool,
) -> tuple[str, set[str]]:
    current = client.call("GET", f"/databases/{database_uuid}/backups")
    if not isinstance(current, list):
        raise BackupError("Coolify backup configuration response must be a list")
    if len(current) > 1:
        raise BackupError("Multiple backup configurations exist; refusing to select one implicitly")
    payload = desired_backup_payload(backup, storage_uuid, backup_now)
    previous_execution_ids: set[str] = set()
    if current:
        backup_uuid = current[0].get("uuid")
        if not isinstance(backup_uuid, str) or not backup_uuid:
            raise BackupError("Existing backup configuration has no UUID")
        previous = client.call(
            "GET", f"/databases/{database_uuid}/backups/{backup_uuid}/executions"
        )
        if isinstance(previous, dict):
            previous_execution_ids = {
                item.get("uuid")
                for item in previous.get("executions", [])
                if isinstance(item, dict) and isinstance(item.get("uuid"), str)
            }
        client.call("PATCH", f"/databases/{database_uuid}/backups/{backup_uuid}", payload)
    else:
        created = client.call("POST", f"/databases/{database_uuid}/backups", payload)
        backup_uuid = created.get("uuid") if isinstance(created, dict) else None
        if not isinstance(backup_uuid, str) or not backup_uuid:
            raise BackupError("Coolify did not return the created backup UUID")
    return backup_uuid, previous_execution_ids


def wait_for_backup(
    client: CoolifyClient,
    database_uuid: str,
    backup_uuid: str,
    previous_execution_ids: set[str],
    timeout_seconds: int,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout_seconds
    path = f"/databases/{database_uuid}/backups/{backup_uuid}/executions"
    while time.monotonic() < deadline:
        response = client.call("GET", path)
        executions = response.get("executions", []) if isinstance(response, dict) else []
        new_executions = [
            item
            for item in executions
            if isinstance(item, dict) and item.get("uuid") not in previous_execution_ids
        ]
        if new_executions:
            execution = new_executions[0]
            status = str(execution.get("status", "")).lower()
            message = str(execution.get("message", ""))
            if status == "failed":
                raise BackupError(f"Coolify backup failed: {redact(message)[:1500]}")
            if status == "success":
                if "s3 upload failed" in message.lower() or "warning:" in message.lower():
                    raise BackupError(f"Coolify local backup succeeded but S3 upload failed: {redact(message)[:1500]}")
                filename = execution.get("filename")
                if not isinstance(filename, str) or not filename:
                    raise BackupError("Successful Coolify backup has no filename")
                return execution
        sleeper(10)
    raise BackupError(f"Timed out after {timeout_seconds}s waiting for the Coolify backup")


def verify_object(
    filename: str,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> dict[str, Any]:
    endpoint = required_env("OCI_S3_ENDPOINT")
    bucket = required_env("OCI_S3_BUCKET")
    region = required_env("OCI_S3_REGION")
    expected_basename = Path(filename).name
    config_text = f"[default]\nregion = {region}\ns3 =\n    addressing_style = path\n"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8") as config:
        config.write(config_text)
        config.flush()
        environment = os.environ.copy()
        environment.update(
            {
                "AWS_ACCESS_KEY_ID": required_env("OCI_S3_ACCESS_KEY"),
                "AWS_SECRET_ACCESS_KEY": required_env("OCI_S3_SECRET_KEY"),
                "AWS_DEFAULT_REGION": region,
                "AWS_CONFIG_FILE": config.name,
                "AWS_EC2_METADATA_DISABLED": "true",
            }
        )
        completed = runner(
            [
                "aws", "s3api", "list-objects-v2",
                "--endpoint-url", endpoint,
                "--bucket", bucket,
                "--region", region,
                "--output", "json",
            ],
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
            env=environment,
        )
    if completed.returncode != 0:
        raise BackupError(f"Could not list Oracle backup objects: {redact(completed.stderr)[-1500:]}")
    try:
        listing = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise BackupError("Oracle S3 listing returned invalid JSON") from exc
    matches = [
        item
        for item in listing.get("Contents", [])
        if isinstance(item, dict) and Path(str(item.get("Key", ""))).name == expected_basename
    ]
    if len(matches) != 1:
        raise BackupError(
            f"Expected one Oracle object ending in {expected_basename}; found {len(matches)}"
        )
    return {"key": matches[0]["Key"], "size": matches[0].get("Size")}


def reconcile(manifest_path: Path, backup_now: bool) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    client = CoolifyClient(required_env("COOLIFY_BASE_URL"), required_env("COOLIFY_API_TOKEN"))
    team = client.call("GET", "/teams/current")
    team_id = team.get("id") if isinstance(team, dict) else None
    if not isinstance(team_id, int):
        raise BackupError("Coolify current team has no numeric ID")
    database = select_database(client.call("GET", "/databases"), manifest["product"])
    storage = register_storage(team_id)
    backup_uuid, previous_ids = reconcile_schedule(
        client,
        database["uuid"],
        manifest["backup"],
        storage["uuid"],
        backup_now,
    )
    result: dict[str, Any] = {
        "status": "scheduled",
        "product": manifest["product"],
        "environment": "dev",
        "database_uuid": database["uuid"],
        "backup_uuid": backup_uuid,
        "storage_uuid": storage["uuid"],
        "frequency": manifest["backup"]["frequency"],
    }
    if backup_now:
        execution = wait_for_backup(
            client,
            database["uuid"],
            backup_uuid,
            previous_ids,
            int(manifest["backup"].get("timeout_seconds", 3600)),
        )
        oracle_object = verify_object(execution["filename"])
        result.update(
            {
                "status": "verified",
                "execution_uuid": execution.get("uuid"),
                "filename": execution["filename"],
                "backup_size": execution.get("size"),
                "oracle_object": oracle_object,
            }
        )
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--backup-now", type=bool_arg, default=False)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        result = reconcile(args.manifest, args.backup_now)
    except BackupError as exc:
        print(json.dumps({"status": "error", "message": redact(str(exc))}), file=sys.stderr)
        return 1
    rendered = json.dumps(result, sort_keys=True)
    print(rendered)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
