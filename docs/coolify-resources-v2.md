# Coolify resources-v2 contract

`resources-v2` is the canonical GoodMorning topology for products that own
persistent data or reusable modules. One Coolify Project represents one product
and its `dev` Environment contains three independently visible resources:

- `<product>-frontend-dev`: private GitHub Application, Dockerfile `/frontend/Dockerfile`, port 80;
- `<product>-api-dev`: private GitHub Application, Dockerfile `/backend/Dockerfile`, port 8000;
- `<product>-postgres-dev`: native private PostgreSQL 16 Database.

The public development domains remain clean:

- Frontend: `https://dev.<product>.goodmorning.mx`;
- API: `https://api.dev.<product>.goodmorning.mx`.

PostgreSQL has no public port. The Backend receives its private `DATABASE_URL`
as a secret runtime variable. Frontend and Backend may be deployed or rolled
back independently without replacing the PostgreSQL resource.

Both Applications use the native Coolify GitHub App with automatic deployment
enabled. After Factory performs the initial installation, pushes to the
product's `dev` branch deploy directly through the native GitHub connection.

## Catalog contract

```json
{
  "topology": "resources-v2",
  "product": "example",
  "repository": "goodmorning-mx/example",
  "branch": "dev",
  "development_domain": "dev.example.goodmorning.mx",
  "coolify_github_app_name": "goodmorning-mx-coolify"
}
```

A push to Factory `dev` plans every `resources-v2` manifest before applying any
writes. A safe plan is followed by idempotent creation, PostgreSQL readiness,
environment reconciliation, Backend deployment and Frontend deployment. All
real actions are guarded by exact resource confirmations and repository-level
deployment authorization.

`compose-v1` remains supported for disposable prototypes and is archived as a
rollback implementation. A manifest is reconciled by exactly one topology; the
Compose and resources workflows must never manage the same product at once.

## Backups

The native Database resource makes Coolify scheduled backups visible and
manageable. External S3 storage and a retention policy remain required before a
product can be promoted to production. Factory must not create paid storage or
rotate credentials without Abel's approval.

## Migration

1. Archive the source and catalog state.
2. Move the Compose application to legacy development domains.
3. Change the manifest from `compose-v1` to `resources-v2`.
4. Provision and verify the three new resources on the official domains.
5. Export and restore PostgreSQL data when the source database is non-empty.
6. Keep the legacy Compose application available until rollback retention ends.
7. Delete legacy resources only with explicit approval.
