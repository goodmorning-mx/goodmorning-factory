#!/usr/bin/env python3
"""Adopt or update Factory-managed instructions in existing products."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

try:
    from scripts.product_instructions import InstructionsError, reconcile_remote
    from scripts.register_product_manifest import request
except ModuleNotFoundError:  # Direct execution: python scripts/reconcile_product_instructions.py
    from product_instructions import InstructionsError, reconcile_remote
    from register_product_manifest import request


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile agent instructions for one or more catalog products.")
    parser.add_argument("--manifest", action="append", required=True)
    parser.add_argument("--branch", default="dev")
    args = parser.parse_args(argv)
    try:
        results = []
        for source in args.manifest:
            manifest = json.loads(Path(source).read_text(encoding="utf-8"))
            product = manifest.get("product")
            if not isinstance(product, str):
                raise InstructionsError(f"Manifest {source} has no product slug.")
            results.append(reconcile_remote(product, manifest, request, branch=args.branch))
        print(json.dumps(results, sort_keys=True))
        return 0
    except (InstructionsError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
