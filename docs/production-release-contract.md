# GoodMorning production contract

Production is an explicit promotion of a reviewed product release. It never runs
from a normal push and never reuses development data or resources.

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

## Promotion

The workflow `Promote one GoodMorning product to production` is manual by
design. It requires:

1. an existing reviewed `production/<product>.json` manifest;
2. `confirm_production=true` in that manifest;
3. the exact typed confirmation `deploy:<product>:production`;
4. Factory branch `dev`;
5. the GitHub Environment `production`;
6. the existing Coolify and Oracle credentials.

The workflow performs a GET-only plan, provisions isolated resources without
deploying, verifies an external pre-deployment backup, deploys the API and waits
for its migration/health smoke before deploying the frontend, then verifies a
second external backup.

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
