# GoodMorning Factory

GoodMorning Factory provisions each product as one private GitHub-backed Docker
Compose application per Coolify environment.

## Canonical deployment model

One Coolify Project represents one product. The `dev` environment contains one
Compose Application sourced from branch `dev`; production will reuse the same
provisioner with branch `main` after the development path is verified and Abel
explicitly approves production enablement.

The product repository owns the runtime contract:

- `frontend`, `backend`, and `postgres` are the standard service names;
- `/compose.yaml` is the single source of truth;
- Coolify owns the isolated network, proxy, domains, generated secrets, and
  deployment lifecycle;
- PostgreSQL has no host port and no public domain;
- the native Coolify GitHub App enables automatic redeployments after the first
  provisioned deployment.

Factory owns only the thin installation path: exact resource discovery, Project
and Environment creation, Compose Application creation, environment-variable
reconciliation, initial deployment, polling, and smoke verification.

Development onboarding is catalog-driven: a reviewed manifest in `products/`
is reconciled automatically when it reaches `dev`. After the first deployment,
the native Coolify GitHub App redeploys product changes directly from GitHub.

See `docs/coolify-compose.md` for the contract and
`scripts/provision_coolify_development.py` for the current dev implementation.

## Previous resource-by-resource provisioner

The Stage 9 PostgreSQL/Backend/Frontend provisioner remains in the repository as
audited predecessor code and migration context. It is not the canonical path for
new GoodMorning products. New work must not extend it with additional resource
stages when the capability belongs in a product's Compose contract.
