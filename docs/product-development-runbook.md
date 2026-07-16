# Product development operations runbook

Stage 9 is the current manual operating interface for development provisioning and deployment. It wraps the Stage 2-8 Coolify provisioner in a small fail-closed orchestrator for GitHub Actions and local operator use.

## Architecture

`workflow_dispatch` in this factory repository runs on branch `dev`, enters the GitHub Environment `dev`, reads `COOLIFY_BASE_URL` and `COOLIFY_API_TOKEN` from secrets, then calls `scripts/orchestrate_product_development.py`. The orchestrator imports `scripts/provision_product_development.py`, derives all product resource names, and uses the Coolify API only through the existing Stage 2-8 provisioner.

Canonical resources are:

- Project: `<product>`.
- Environment: `dev`.
- PostgreSQL: `<product>-postgres-dev`.
- Backend Application: `<product>-api-dev` at `https://api.dev.<product>.goodmorning.mx`.
- Frontend Application: `<product>-frontend-dev` at `https://dev.<product>.goodmorning.mx`.
- Repository: `goodmorning-mx/<product>` on branch `dev`.

Compose remains local/CI-only for the factory baseline and legacy migration context. Stage 9 does not convert `compose.yaml` into a remote deployment source and does not use SSH, rsync, SCP, remote Docker, or server-local paths.

## Operations

- `plan`: dry-run only. It is GET-only against Coolify, performs no public runtime/smoke checks, and exits successfully only when the structural plan is safe.
- `provision`: converges Project, `dev` environment, PostgreSQL, PostgreSQL readiness, Backend Application/env, and Frontend Application/env. It does not deploy Backend or Frontend.
- `deploy-backend`: runs all provisioning convergence, then deploys Backend, waits for deployment/runtime readiness, and performs Backend smoke. It never deploys Frontend.
- `deploy-full`: runs all provisioning convergence, deploys Backend first, verifies Backend runtime/smoke, deploys Frontend second, and verifies Frontend runtime/smoke.

There is no automatic deployment on push. The workflow trigger is manual `workflow_dispatch` only.

## Required confirmations

`plan` does not require a confirmation. Every mutating operation requires an exact, case-sensitive confirmation:

```text
<operation>:<product>:dev
```

Examples: `provision:ledger:dev`, `deploy-backend:ledger:dev`, `deploy-full:ledger:dev`. A missing or mismatched confirmation fails before write gates are accepted.

## Gate matrix

| Operation | Required true gates | Required false/absent gates |
| --- | --- | --- |
| `plan` | none | all `COOLIFY_ALLOW_*` write/deploy gates |
| `provision` | `COOLIFY_ALLOW_RESOURCE_WRITES`, `COOLIFY_ALLOW_DATABASE_START`, `COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES`, `COOLIFY_ALLOW_BACKEND_ENV_WRITES`, `COOLIFY_ALLOW_FRONTEND_APPLICATION_WRITES`, `COOLIFY_ALLOW_FRONTEND_ENV_WRITES` | `COOLIFY_ALLOW_DEPLOYMENTS`, `COOLIFY_ALLOW_BACKEND_DEPLOYMENT`, `COOLIFY_ALLOW_FRONTEND_DEPLOYMENT` |
| `deploy-backend` | provision gates plus `COOLIFY_ALLOW_DEPLOYMENTS`, `COOLIFY_ALLOW_BACKEND_DEPLOYMENT` | `COOLIFY_ALLOW_FRONTEND_DEPLOYMENT` |
| `deploy-full` | provision gates plus `COOLIFY_ALLOW_DEPLOYMENTS`, `COOLIFY_ALLOW_BACKEND_DEPLOYMENT`, `COOLIFY_ALLOW_FRONTEND_DEPLOYMENT` | none of the known gates may be missing or false |

Unexpected true gates fail closed; required missing gates fail closed.

## Plan safety

A plan is safe when resources are compatible/existing or absent with planned `would_create`, `would_start`, or `would_deploy` actions. It is unsafe for duplicates, incompatible resources, recreate requirements, out-of-scope resources, scope or compatibility unverified states, legacy Compose migration requirements, ambiguous/missing destinations, ambiguous project/environment/server/GitHub App identity, identity contradictions, UUID ambiguity, created-unverified or write-outcome-unknown states.

Mutating workflow runs always execute a separate `plan` phase before apply. The apply phase re-discovers current state and fails closed independently; it does not trust the previous JSON blindly.

## GitHub Environment `dev`

Configure GitHub Environment `dev` before enabling mutating operations:

- Add required reviewers for mutating operations.
- Store `COOLIFY_BASE_URL` and `COOLIFY_API_TOKEN` as environment secrets or repository secrets.
- Do not provide secrets through workflow inputs.
- Run the workflow only from factory branch `dev`.

Concurrency is per product: `goodmorning-factory-dev-${{ inputs.product }}` with `cancel-in-progress: false`, so a second operation waits instead of cancelling an active operation.

## Interpreting JSON

The orchestrator prints one redacted JSON document per execution. The `orchestration` object contains `operation`, `phase`, `confirmation_required`, `confirmation_matches`, `plan_safe`, `apply_authorized`, `deployment_order`, and `next_action`. Runtime/smoke outputs are booleans/statuses only; secrets, tokens, `DATABASE_URL`, `internal_db_url`, HTML bodies, response bodies, and deployment logs are not printed.

Successful apply states have overall status outside the non-success set and verified resource-specific states. Partial failures, unknown writes, unhealthy runtime, failed smoke, or timeouts return non-zero and must be inspected before re-running. Re-running is safe after the cause is corrected because the provisioner re-discovers resources and reuses compatible existing resources. Deployment POSTs are not automatically retried after unknown outcomes, and Stage 9 performs zero automatic rollback.

## Manual local use

Use only with placeholder/test credentials unless Abel has approved real dev operations:

```bash
python scripts/orchestrate_product_development.py \
  --operation plan \
  --product ledger \
  --server development \
  --github-app goodmorning-mx-coolify
```

For apply operations, set only the exact gates for the operation and pass the exact confirmation. Abort before apply by running `plan` only or by not approving the GitHub Environment reviewer gate.

## Manual workflow use

Open **Actions → Product development operations → Run workflow** from branch `dev`, enter product, operation, optional destination UUID, server, GitHub App, and the exact confirmation for mutating operations. For `operation=plan`, leave confirmation empty. Do not dispatch from feature branches.
