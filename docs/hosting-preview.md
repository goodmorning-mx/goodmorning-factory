# Hosting MVP: explicit DEV preview without merging

The requested product is `goodmorning-mx/hosting`. Its repository was created
using Factory's `scripts/create_product.py` from the existing private template.
This initializes the template branches only; product changes go on
`feature/hosting-mvp` and through a PR into `dev`.

The normal catalog path requires a merge to Factory dev and product dev. The
MVP request explicitly forbids those merges. `previews/hosting.json` therefore
contains an opt-in preview input, not an automatically reconciled catalog entry.
The normal `products/` catalog and all production manifests remain untouched.

The existing advanced-development workflow has an explicit Hosting preview
job. Dispatch it on Factory `feature/hosting-mvp` with `hosting_preview_sha`.
The job checks that the same SHA is the product feature HEAD, has an open PR
against dev, and passes the three product CI checks. It validates the canonical
V3 manifest, resolves the existing backup storage from a runtime variable,
and invokes the existing V3 reconciler with a narrowly scoped preview source.
The source exception is restricted to Hosting, DEV and that one feature branch.
The Coolify application uses the exact commit SHA, not a floating branch build.
Auto-deploy and instant deployment are disabled; deployment follows runtime
secret/database reconciliation. A conflicting source identity fails closed.
No other product is selected and no production write gate is set.

Runtime variables required are the existing Factory `COOLIFY_BASE_URL`,
`COOLIFY_API_TOKEN`, `COOLIFY_RESOURCES_DESTINATION_UUID`,
`COOLIFY_BACKUP_STORAGE_UUID`, and the existing Factory GitHub App credentials.
They are not copied into Git, product code or reports.

The backend volume is the existing V3 contract `/app/data`. API, worker and
shared Nginx run as separate supervised processes in the backend resource.
The frontend remains an independent Coolify Application; PostgreSQL is a native
private Coolify database. There is no per-static-site container.

Future integration after authorized merge needs a reviewed transition of the
preview application's source branch from the pinned feature commit to dev.
The normal reconciler deliberately rejects automatic branch adoption. Do not
work around that check, delete databases or remove volumes. Promotion to main
is outside this preview.

Validation: focused preview/advanced-contract/reconciler suite: 53 passed.
Actual deployment evidence belongs in the product PR and must be recorded only
after healthchecks and real HTTP checks succeed.
