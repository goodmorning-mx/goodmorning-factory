# GoodMorning Factory

GoodMorning Factory provisions reviewed product contracts into Coolify from
GitHub. The canonical topology is now `resources-v3`: one declarative Project
per product with independently visible applications and native data services.

## Canonical deployment model

One Coolify Project represents one product. Its `dev` environment contains a
Frontend Application, Backend Application and native private PostgreSQL 16
Database. Production reuses the same isolated-resource contract from branch `main`.
It is promoted only through the explicitly confirmed production workflow after
a read-only plan and verified pre-deployment backup.

The product repository owns the runtime contract:

- `/frontend/Dockerfile` exposes port 80 and consumes `VITE_API_URL`;
- `/backend/Dockerfile` exposes port 8000 and serves `/health`;
- Backend migrations and database connectivity use a private `DATABASE_URL`;
- PostgreSQL has no public port;
- each Application can be observed, deployed and rolled back independently.
- the native Coolify GitHub App automatically deploys product `dev` pushes.

Factory owns exact discovery, Project and Environment creation, native Database
creation/readiness, Application creation, environment reconciliation,
deployment polling, smoke verification, and external database-backup reconciliation.

Development onboarding is catalog-driven: a reviewed `resources-v3` manifest in
`products/` is planned and reconciled automatically when it reaches `dev`.

Development PostgreSQL resources declare daily external backups that Factory verifies against Oracle Object Storage.

See `docs/coolify-resources-v3.md` and `docs/production-release-contract.md` for the canonical contracts and
`scripts/provision_product_development.py` for the audited provisioner.

## Legacy resources-v2 compatibility

`resources-v2` is accepted temporarily through the safe v2→v3 migration adapter.
It preserves reviewed names and supplied UUIDs, adopts existing resources in
place, and never deletes or replaces databases, volumes, domains, secrets, or
configuration. New products and capabilities use v3.

See `docs/coolify-resources-v3.md`,
`scripts/advanced_product_contract.py`, and
`scripts/reconcile_advanced_product.py`, plus
`scripts/resources_v3_migration.py`.

## Compose v1 rollback path

The previous single-Application Docker Compose topology remains supported as
`compose-v1` for disposable prototypes and rollback. Its frozen source is also
preserved on `archive/compose-v1-2026-07-18`. A product manifest is owned by
exactly one topology at a time.
