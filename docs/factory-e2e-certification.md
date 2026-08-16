# Factory E2E certification: `factory-e2e-test`

This is the verified Resources V3 path for a temporary product. It is kept
alive intentionally; this document records the real automation and the safe
cleanup procedure.

## Provisioning and DEV

1. Dispatch `create-product.yml` on Factory `dev` with the product slug and
   `confirm_provision=true`.
2. The workflow creates the private product repository, registers
   `products/<product>.json` and `production/<product>.json`, and reconciles
   Coolify through the V3 reconciler.
3. The development manifest must bind API `DATABASE_URL` to `database_url`,
   frontend `VITE_API_URL` to the API domain, and declare the additional Redis
   resource and `REDIS_URL` binding.
4. A product change is made from a new branch based on `origin/dev`, opened as
   a PR, checked by CI, and squash-merged to `dev`. Coolify's native webhook
   then deploys the exact `dev` SHA.

Observed certification evidence:

- Product repository: `goodmorning-mx/factory-e2e-test`.
- DEV release SHA: `8e9351a` (product change); final DEV automation SHA:
  `ac9d4d0`.
- Factory V3 development run: `31936226108` (success).
- DEV domains: `https://dev.factory-e2e-test.goodmorning.mx/` and
  `https://api.dev.factory-e2e-test.goodmorning.mx/health` (HTTP 200).
- DEV resources: API, frontend, PostgreSQL and persistent Redis; all healthy.

## Production

1. The product repository's `promote-production.yml` listens for successful
   `CI` on `main` and creates a GitHub App installation token without logging
   the private key.
2. It dispatches `promote-advanced-product` to Factory with the exact main
   SHA, CI run ID and `production/<product>.json`.
3. Factory validates the source release, plans read-only, bootstraps missing
   production resources, verifies a pre-deployment backup, applies/deploys
   Resources V3, performs public smoke and migration checks, then verifies the
   post-deployment backup.

Observed certification evidence:

- Production SHA: `48ae437`.
- Main CI run: `31936773169` (success).
- Product dispatch run: `31936829671` (success; exact SHA dispatch).
- Factory production run: `31937235643` (success).
- PROD domains: `https://factory-e2e-test.goodmorning.mx/` and
  `https://api.factory-e2e-test.goodmorning.mx/health` (HTTP 200).
- PROD resources: `factory-e2e-test-api-prod`,
  `factory-e2e-test-frontend-prod`, `factory-e2e-test-postgres-prod`, and
  `factory-e2e-test-redis-prod`; all healthy.
- PROD backup executions completed successfully and uploaded to S3.

The organization secret `GOODMORNING_FACTORY_PRIVATE_KEY` and variable
`GOODMORNING_FACTORY_APP_ID` remain selected for Factory, Ballet and this
product. No new private key was generated.

## Safe cleanup procedure

Do not run cleanup as part of certification. Before cleanup, retain/export the
successful PROD backup and record the final workflow run IDs.

1. Disable the product's native Coolify auto-deploy webhook and confirm no
   deployment is running.
2. Remove `goodmorning-mx/factory-e2e-test` from the selected repositories for
   the organization secret and variable.
3. In Coolify, delete only the project named `factory-e2e-test` after checking
   that its DEV/PROD PostgreSQL backups are present in S3. This removes the
   four DEV and four PROD resources created by this certification.
4. Archive or delete the private GitHub repository only after the Coolify
   project is gone and the backup retention decision is recorded.
5. Remove the two catalog manifests from Factory through a reviewed PR if the
   product should no longer be discoverable. Never delete the Factory secret
   or the Factory/Ballet repository authorizations.
