#!/usr/bin/env python3
"""Compatibility entrypoint that reconciles every product through resources-v3.

Legacy resources-v2 manifests are normalized in memory and then handed to the
same v3 desired-state reconciler. This keeps the v2 catalog/workflow usable
temporarily without maintaining a second Coolify resource architecture.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    from advanced_product_contract import ContractError, load_manifest
    from reconcile_advanced_product import Coolify, ReconcileError, reconcile
    from resources_v3_migration import migrate_v2_manifest
except ModuleNotFoundError:
    from scripts.advanced_product_contract import ContractError, load_manifest, validate_manifest
    from scripts.reconcile_advanced_product import Coolify, ReconcileError, reconcile
    from scripts.resources_v3_migration import migrate_v2_manifest


def normalize_manifest(path: str | Path) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ContractError("Resource manifest must be a JSON object.")
    if raw.get("topology") in {"resources-v2", "resources-v2-production"}:
        return migrate_v2_manifest(raw)
    # load_manifest gives the canonical v3 validation and error semantics.
    return load_manifest(path)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Reconcile resources-v2/v3 through the v3 reconciler.")
    result.add_argument("--manifest", required=True)
    result.add_argument("--phase", choices=("plan", "apply"), default="plan")
    result.add_argument("--operation", choices=("plan", "provision", "deploy-backend", "deploy-full"), default="plan")
    result.add_argument("--server", default=os.environ.get("COOLIFY_SERVER_NAME", "localhost"))
    result.add_argument("--github-app", help="Compatibility argument; v3 uses the reviewed app in the manifest.")
    result.add_argument("--destination-uuid", default=os.environ.get("COOLIFY_DESTINATION_UUID"))
    result.add_argument("--confirm-product")
    result.add_argument("--confirm-environment")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        manifest = normalize_manifest(args.manifest)
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
            # The legacy workflow already selected one exact manifest. Keep
            # that scoped confirmation while routing it through v3.
            confirm_product=args.confirm_product or (manifest["product"] if args.phase == "apply" else None),
            confirm_environment=args.confirm_environment or (("production" if manifest["topology"] == "resources-v3-production" else "dev") if args.phase == "apply" else None),
            deploy=args.operation in {"deploy-backend", "deploy-full"},
        )
        print(json.dumps(result, sort_keys=True))
        return 0
    except (ContractError, ReconcileError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "message": str(exc)}))
        return 1


if __name__ == "__main__":
    sys.exit(main())
