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
        manifests = []
        for source in args.manifest:
            manifest = json.loads(Path(source).read_text(encoding="utf-8"))
            product = manifest.get("product")
            if not isinstance(product, str):
                raise InstructionsError(f"Manifest {source} has no product slug.")
            manifests.append((source, product, manifest))

        # Historical catalogs may contain aliases that point multiple product
        # slugs at one repository.  Only a manifest whose slug matches the
        # repository name is canonical; silently overwriting one repository
        # with whichever alias happens to sort last would be unsafe.
        by_repository: dict[str, list[tuple[str, str, dict]]]= {}
        for item in manifests:
            repository = item[2].get("repository")
            by_repository.setdefault(str(repository), []).append(item)
        results = []
        for repository, items in by_repository.items():
            repo_slug = repository.rsplit("/", 1)[-1]
            canonical = [item for item in items if item[1] == repo_slug]
            if len(canonical) != 1:
                results.append({"status": "skipped", "repository": repository, "reason": "no_unique_canonical_manifest"})
                continue
            _, product, manifest = canonical[0]
            results.append(reconcile_remote(product, manifest, request, branch=args.branch))
        print(json.dumps(results, sort_keys=True))
        return 0
    except (InstructionsError, OSError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "error", "error": str(exc)}, sort_keys=True))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
