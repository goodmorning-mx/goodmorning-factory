# GoodMorning production contract

Production is an explicit promotion of a reviewed product release. A product's
green `main` CI completion dispatches the exact SHA to Factory; Factory is the
only component that deploys production. It never reuses development data or
resources.

## Resource isolation

One Coolify Project owns both environments, but every resource is independent:

| Environment | Frontend | API | PostgreSQL |
| --- | --- | --- | --- |
| dev | `<product>-frontend-dev` | `<product>-api-dev` | `<product>-postgres-dev` |
| production | `<product>-frontend-prod` | `<product>-api-prod` | `<product>-postgres-prod` |

Production source is the private product repository branch `main`.

Public domains are clean:

- Frontend: `https://<product>.goodmorning.mx`
- API: `https://api.<product>.goodmorning.mx`
- PostgreSQL: private, with no public domain or host port.

Both applications have `instant_deploy=false` and
`is_auto_deploy_enabled=false`. Only the reviewed Factory workflow can deploy.

## CV Stripe secret isolation

CV development continues to use the generic test-mode bindings
`GOODMORNING_MANAGED_STRIPE_SECRET_KEY` and
`GOODMORNING_MANAGED_STRIPE_WEBHOOK_SECRET`. CV production instead requires the
separate GitHub Actions `production` environment secrets
`GOODMORNING_MANAGED_CV_PRODUCTION_STRIPE_SECRET_KEY` and
`GOODMORNING_MANAGED_CV_PRODUCTION_STRIPE_WEBHOOK_SECRET`. The production
workflow rejects missing or non-live key prefixes before backups or Coolify
changes; it never falls back to development credentials. Configure those two
secrets only after the matching live Stripe endpoint is ready. Never place their
values in a manifest, commit, issue, or workflow input.

## Promotion

The product release workflow dispatches `promote-advanced-product` to Factory
after the `CI` workflow succeeds on `main`. The Factory workflow also retains a
manual dispatch for recovery and operator-approved retries. An automatic
promotion requires:

1. an existing reviewed `production/<product>.json` manifest;
2. `confirm_production=true` in that manifest;
3. the repository's production release workflow, with the Factory GitHub App
   ID variable and private key secret configured;
4. the exact `main` SHA and successful `CI` push run for that SHA;
5. Factory branch `dev` and the GitHub Environment `production`;
6. the existing Coolify and Oracle credentials.

The workflow performs a GET-only plan, provisions isolated resources without
deploying, verifies an external pre-deployment backup, deploys the API and waits
for its migration/health smoke before deploying the frontend, then verifies a
second external backup.

Every successful promotion records the product, exact source SHA, environment,
timestamp, reconciled resources, migrations, deployment IDs, healthchecks, and
final status in the GitHub Actions summary. A failure stops the workflow and
creates or updates a sanitized tracking issue.

## Daily development and release flow

```text
feature → PR → dev → Coolify native DEV auto-deploy
dev → release PR → main → product CI → Factory → Coolify PROD promotion
```

The product repository's `CI` workflow runs on pull requests and on pushes to
both `dev` and `main`. Coolify observes only the product `dev` branch. The
production application is not a passive observer of `main`; Factory resolves
the exact green SHA and performs the production reconciliation.

## Retrying a failed promotion

Do not delete or recreate Coolify resources. Inspect the failed Factory run,
resolve the reported preflight, backup, migration, or health issue, and rerun
the same production workflow with the same manifest. The reconciler reuses
resource names and UUIDs, and the backup gate runs again before deployment. If
the release itself is invalid, merge a reviewed revert or corrective release to
`main`; never copy the DEV database into production.

## Database migrations

The backend remains fail-closed: Alembic must reach `head` before Uvicorn can
serve traffic. Production migrations must use expand/migrate/contract:

- additive schema first;
- backward-compatible application release;
- bounded data backfill;
- validation;
- destructive cleanup in a later release.

Dropping or renaming a live column in the same release is not an approved
production migration. Database rollback is restore-driven; application rollback
is a reviewed revert and redeploy.

## Backup baseline

Production declares:

- daily external backup at 02:00;
- two local recovery copies retained for two days;
- thirty Oracle Object Storage copies retained for thirty days;
- a verified backup immediately before deployment;
- a verified backup immediately after deployment.

A Coolify success is insufficient. Factory also confirms that the exact backup
object exists in Oracle Object Storage.

## Canary

`jose` is the first production canary because its repository contains the
canonical template and no client production data. Its promotion remains inert
until an operator dispatches the workflow with
`deploy:jose:production`.
