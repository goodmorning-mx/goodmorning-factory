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

Both Applications use the native Coolify GitHub App as their private source, but
automatic deployment is disabled. A product commit reaches Coolify only through
the reviewed Factory deployment workflow after CI succeeds; webhook delivery can
never race or bypass the quality gates.

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

A push to Factory `dev` selects changed `resources-v2` manifests; workflow dispatch
selects the full catalog. A safe plan is followed by bounded convergence through
idempotent creation, PostgreSQL readiness, environment reconciliation, Backend
deployment and Frontend deployment. Only `partial` progress is retried, with
increasing delay and a hard attempt limit. Errors fail immediately, and backups
run only after every selected product is healthy. All real actions are guarded by
exact resource confirmations and repository-level deployment authorization.

`compose-v1` remains supported for disposable prototypes and is archived as a
rollback implementation. A manifest is reconciled by exactly one topology; the
Compose and resources workflows must never manage the same product at once.

## Backups

Every persistent `resources-v2` product declares a development backup policy
and the UUID of a prevalidated, team-scoped S3-compatible storage in its reviewed
manifest. Factory refuses missing or malformed storage UUIDs and idempotently
creates or updates one scheduled backup for the product's exact private
PostgreSQL resource.

The development baseline is one daily backup, one local recovery copy and seven
external Oracle Object Storage copies retained for seven days. Trusted Factory
reconciliation on `dev` triggers a real dump, rejects local-only success or S3
warnings, and independently confirms that the exact backup object exists in the
bucket. Product promotion to production remains blocked until its external
backup and restore drill have passed.

Storage credentials remain encrypted in Coolify and GitHub Actions secrets; they
are never committed or printed. Coolify uses its validated storage record for
the scheduled upload, while Factory independently lists Oracle with the Actions
credentials to prove the exact object exists. Registering or rotating a global
Coolify storage remains an explicit administrative action because Coolify 4.1.2
does not expose a public API for it. Factory must not create paid storage, rotate
credentials, delete backup objects, or enable production without Abel's explicit
approval.

## Migration

1. Archive the source and catalog state.
2. Move the Compose application to legacy development domains.
3. Change the manifest from `compose-v1` to `resources-v2`.
4. Provision and verify the three new resources on the official domains.
5. Export and restore PostgreSQL data when the source database is non-empty.
6. Keep the legacy Compose application available until rollback retention ends.
7. Delete legacy resources only with explicit approval.
