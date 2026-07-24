# GoodMorning Factory

GoodMorning Factory provisions reviewed product contracts into Coolify from
GitHub. The canonical topology is now `resources-v2`: one Project per product
with independently visible Frontend, Backend and PostgreSQL resources.

## Canonical deployment model

One Coolify Project represents one product. Its `dev` environment contains a
Frontend Application, Backend Application and native private PostgreSQL 16
Database. Production will reuse the same contract from branch `main` only after
the development path is verified and Abel explicitly approves production.

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

Development onboarding is catalog-driven: a reviewed `resources-v2` manifest in
`products/` is planned and reconciled automatically when it reaches `dev`.

Development PostgreSQL resources declare daily external backups that Factory verifies against Oracle Object Storage.

See `docs/coolify-resources-v2.md` for the canonical contract and
`scripts/provision_product_development.py` for the audited provisioner.

## Advanced products

Products that require multiple public applications, private workers, Redis, or
a reviewed PostgreSQL-compatible image use the additive `resources-v3`
contract. `resources-v2` remains the default and is not widened implicitly.
Advanced resources are independently deployable native Coolify resources, while
the previous Compose application is retained throughout migration.

See `docs/coolify-resources-v3.md`,
`scripts/advanced_product_contract.py`, and
`scripts/reconcile_advanced_product.py`.

## Compose v1 rollback path

The previous single-Application Docker Compose topology remains supported as
`compose-v1` for disposable prototypes and rollback. Its frozen source is also
preserved on `archive/compose-v1-2026-07-18`. A product manifest is owned by
exactly one topology at a time.

