# Coolify development environment provisioning

This document describes the manual GitHub Actions workflow for preparing and optionally deploying a product **development** environment in Coolify. The workflow is API-only: Coolify gets code from GitHub through the private GoodMorning GitHub App and deploys `/compose.yaml` from branch `dev`. SSH, rsync, SCP, local server services, Docker commands on the server, and manual file copies are not supported.

The workflow defaults to `dry_run=true` and `deploy=false`; the first safe run only discovers Coolify resources and prints planned changes.

## First supported product

| Field | Value |
| --- | --- |
| Product | `factory-smoke-test` |
| Repository | `goodmorning-mx/factory-smoke-test` |
| Branch | `dev` |
| Docker Compose file | `/compose.yaml` |
| Frontend domain | `https://dev.factory-smoke-test.goodmorning.mx` |
| Backend domain | `https://api.dev.factory-smoke-test.goodmorning.mx` |
| Coolify GitHub App source | `goodmorning-mx-coolify` |

## Required GitHub configuration

- `vars.COOLIFY_BASE_URL`: Coolify instance base URL.
- `secrets.COOLIFY_API_TOKEN`: Coolify API token with least-privilege read/write/deploy scopes for projects, environments, servers, GitHub Apps, applications, application env vars, deployments, and health verification.
- `vars.COOLIFY_ALLOW_REAL_DEPLOYMENT`: must be exactly `true` before `deploy=true` can request a real Coolify deployment.

Never commit or print Coolify tokens. The workflow masks `COOLIFY_API_TOKEN`; the provisioner redacts configured sensitive values from errors and result summaries.

## Architecture

The factory keeps one Docker Compose application with three services:

- `frontend`: Nginx, internal port `80`, `expose: ["80"]`, public domain `https://dev.<product>.goodmorning.mx`.
- `backend`: HTTP API, internal port `8000`, `GET /health`, `expose: ["8000"]`, public domain `https://api.dev.<product>.goodmorning.mx`.
- `postgres`: PostgreSQL 16, internal port `5432`, persistent volume `/var/lib/postgresql/data`, no public domain, `data` network only.

Networks:

- `app`: frontend/backend/proxy-facing communication.
- `data`: backend/PostgreSQL communication, `internal: true`; PostgreSQL is not connected to `app`.

Public URLs never include `:80`, `:8000`, or `:5432`. Internal routing ports are declared in `compose.yaml` with Traefik service labels because the current Coolify OpenAPI exposes `docker_compose_domains[].name` and `docker_compose_domains[].domain` but no separate Docker Compose domain service-port field.

## Variables configured through Coolify API

The provisioner upserts these application environment variables:

| Key | Purpose | Secret? | Build-time/runtime note |
| --- | --- | --- | --- |
| `VITE_API_URL` | Public API URL for Vite frontends | No | Public frontend value; must be available during build for Vite bundles. |
| `NEXT_PUBLIC_API_URL` | Public API URL for Next.js frontends | No | Public frontend value; must be available during build for Next.js bundles. |
| `CORS_ORIGINS` | Backend allowed frontend origin | No | Runtime backend value. |
| `APP_ENV` | Development environment marker | No | Runtime value. |
| `POSTGRES_DB` | PostgreSQL database name | No | Runtime value. |
| `POSTGRES_USER` | PostgreSQL username | No | Runtime value. |
| `POSTGRES_PASSWORD` | PostgreSQL password reference | Yes | Runtime secret reference; result JSON redacts it. |

Do not put secrets in `VITE_*` or `NEXT_PUBLIC_*` variables because those values are public frontend bundle inputs.

## Provisioning flow

`scripts/provision_coolify_development.py` performs these API steps under `/api/v1`:

1. Discover or create the Coolify project for the product.
2. Discover or create the Coolify `dev` environment.
3. Select exactly one non-production development server.
4. Select exactly one private GitHub App source named `goodmorning-mx-coolify`.
5. Discover an existing matching application or create a private GitHub App Docker Compose application.
6. Configure the application payload with:
   - `git_repository=goodmorning-mx/<product>`;
   - `git_branch=dev`;
   - `build_pack=dockercompose`;
   - `base_directory=/`;
   - `docker_compose_location=/compose.yaml`;
   - `environment_name=dev`;
   - clean `docker_compose_domains` for `frontend` and `backend`;
   - `instant_deploy=false`.
7. Wait until Coolify loads `docker_compose_raw` before reconciliation.
8. Reconcile Docker Compose domains and application environment variables idempotently.
9. If `--deploy=true` and `COOLIFY_ALLOW_REAL_DEPLOYMENT=true`, call the Coolify deployment API, wait for a terminal success state, and run smoke checks.
10. Print structured JSON with public URLs and redacted secret values.

Conflict handling is idempotent: project/environment conflicts trigger a second read; environment variable conflicts reload and patch the existing key; application creation conflict reloads applications and reuses the matching one if it is safe.

Before modifying an existing application, the provisioner must verify repository, branch, compose path, Coolify environment, and GitHub App source. If it cannot verify those fields safely, it returns `manual_review_required` rather than modifying an ambiguous resource.

## Deployment modes

### Dry run

```bash
python scripts/provision_coolify_development.py \
  --dry-run true \
  --deploy false \
  --product factory-smoke-test \
  --repository goodmorning-mx/factory-smoke-test \
  --branch dev \
  --compose /compose.yaml \
  --domain dev.factory-smoke-test.goodmorning.mx \
  --coolify-github-app-name goodmorning-mx-coolify
```

No writes, deployments, or smoke checks are performed.

### Provision or reconcile without deploying

```bash
python scripts/provision_coolify_development.py \
  --dry-run false \
  --deploy false \
  --product factory-smoke-test \
  --repository goodmorning-mx/factory-smoke-test \
  --branch dev \
  --compose /compose.yaml \
  --domain dev.factory-smoke-test.goodmorning.mx \
  --coolify-github-app-name goodmorning-mx-coolify
```

This creates/reuses resources and reconciles domains/variables, but it does not deploy.

### Provision or reconcile and deploy

Only run this after review and after setting `COOLIFY_ALLOW_REAL_DEPLOYMENT=true`:

```bash
COOLIFY_ALLOW_REAL_DEPLOYMENT=true \
python scripts/provision_coolify_development.py \
  --dry-run false \
  --deploy true \
  --product factory-smoke-test \
  --repository goodmorning-mx/factory-smoke-test \
  --branch dev \
  --compose /compose.yaml \
  --domain dev.factory-smoke-test.goodmorning.mx \
  --coolify-github-app-name goodmorning-mx-coolify
```

The script requests exactly one deployment for the Compose application, waits for a final status, fails on failed/cancelled/timeout states, then checks:

- `GET https://dev.<product>.goodmorning.mx/`
- `GET https://api.dev.<product>.goodmorning.mx/health`

Smoke checks have timeouts and retries, classify DNS/TLS/HTTP/timeout/network failures, and never print response bodies.

## Expected JSON shape

Successful dry-runs and live runs return structured JSON similar to:

```json
{
  "status": "dry_run",
  "dry_run": true,
  "product": "factory-smoke-test",
  "repository": "goodmorning-mx/factory-smoke-test",
  "branch": "dev",
  "compose": "/compose.yaml",
  "domain": "dev.factory-smoke-test.goodmorning.mx",
  "docker_compose_domains": [
    {"name": "frontend", "domain": "https://dev.factory-smoke-test.goodmorning.mx"},
    {"name": "backend", "domain": "https://api.dev.factory-smoke-test.goodmorning.mx"}
  ],
  "internal_routing": [
    {"service": "frontend", "internal_port": 80, "mechanism": "compose_traefik_label"},
    {"service": "backend", "internal_port": 8000, "mechanism": "compose_traefik_label"}
  ],
  "environment_variables": [
    {"key": "VITE_API_URL", "value": "https://api.dev.factory-smoke-test.goodmorning.mx", "secret": false},
    {"key": "NEXT_PUBLIC_API_URL", "value": "https://api.dev.factory-smoke-test.goodmorning.mx", "secret": false},
    {"key": "POSTGRES_PASSWORD", "value": "[REDACTED]", "secret": true}
  ],
  "would_create": {
    "build_pack": "dockercompose",
    "base_directory": "/",
    "docker_compose_location": "/compose.yaml",
    "environment_name": "dev",
    "instant_deploy": false
  }
}
```

Secret values are redacted. Public frontend values may appear because they are intentionally public build/runtime configuration.

## Safety rules

- Manual workflow only.
- `dry_run` defaults to `true`; `deploy` defaults to `false`.
- `instant_deploy=false` is always preserved in the create payload.
- Real deployment requires both `deploy=true` and `COOLIFY_ALLOW_REAL_DEPLOYMENT=true`.
- Only branch `dev` and domains beginning with `dev.` are accepted.
- The script never deletes Coolify resources.
- The script never prints `COOLIFY_API_TOKEN` or values for secret-like keys.
- Production provisioning and production deployments are out of scope and require the release PR flow plus Abel's explicit approval.

## Rollback

If a development Coolify application is created incorrectly, do not use this automation to delete it. Disable or remove the resource manually in Coolify after review, then rerun the workflow once project, server, GitHub App, branch, compose path, domains, and variables are corrected. To roll back this factory change, revert the commit that introduced it and rerun the previous reviewed workflow.

## Stage 3: Product development primitives

The product development provisioner now follows the three-resource architecture for a development product environment:

- `<product>-postgres-dev` — native PostgreSQL 16 database resource.
- `<product>-api-dev` — backend Application, still `planned_later` in this stage.
- `<product>-frontend-dev` — frontend Application, still `planned_later` in this stage.

This stage implements controlled writes only for the Coolify Project, exact Project Environment `dev`, and a private native PostgreSQL 16 resource. Implementing this capability does **not** mean it has been executed against GoodMorning Coolify. Normal planning remains `--dry-run`, and operational apply runs require explicit approval and gates.

### Confirmed Coolify API contract

The stage-3 payload builders are isolated in `scripts/provision_product_development.py` for future Coolify API changes. The confirmed API surface for this stage is:

- `POST /api/v1/projects` with JSON body `{ "name": "<project>" }`.
- `POST /api/v1/projects/{project_uuid}/environments` with JSON body `{ "name": "dev" }`.
- `POST /api/v1/databases/postgresql` with `server_uuid`, `project_uuid`, `environment_name`, `name`, `image`, PostgreSQL credential fields, `is_public=false`, and `instant_deploy=false`.
- `GET /api/v1/databases/{uuid}` for PostgreSQL detail verification.

Success statuses accepted by the client for these creation endpoints are `200`, `201`, and `202`. Coolify uses `/api/v1` and Bearer authentication. PostgreSQL creation may return `internal_db_url`; Factory treats that value as a secret and reports only `internal_db_url_available: true|false`. The PostgreSQL version is represented in the isolated payload builder with `image=postgres:16-alpine`; if upstream OpenAPI renames this field in a future Coolify release, only that builder should change.

`instant_deploy=false` is sent during PostgreSQL creation so this stage creates the Coolify record without starting, restarting, stopping, or deploying anything.

### Execution modes and gates

`--dry-run`:

- permits only GET discovery;
- rejects all POST, PATCH, PUT, and DELETE;
- plans missing resources with safe symbolic references;
- never generates a real password;
- maintains recursive redaction.

`--apply`:

- requires `COOLIFY_ALLOW_RESOURCE_WRITES=true`;
- requires `--confirm-product <product>` exactly matching `--product`;
- permits only the three allowlisted POST operations above;
- still forbids PATCH, PUT, DELETE, Application creation, deployments, `/start`, `/restart`, and `/stop`.

SSH, rsync, SCP, and remote Docker are not used by this provisioner.

### Idempotency and safety

Project selection is by exact `--coolify-project` name only. Environment selection is exact `dev` inside the selected Project; `production`, `prod`, UUID, slug, and “first environment” fallbacks are not accepted.

PostgreSQL identity is `<product>-postgres-dev`. Existing database resources are reused only if list and detail GET discovery verify type PostgreSQL, version/image 16 when exposed, selected Project, exact `dev` environment, selected server, `is_public=false`, and no public port when exposed. If compatibility cannot be proven, the result is `compatibility_unverified` and apply is blocked. If the existing resource contradicts desired state, the result is `incompatible` and global status becomes `recreate_required`; the provisioner does not PATCH, delete, or create a duplicate same-name resource.

### PostgreSQL credentials and secrets

Non-secret SQL identifiers are deterministic and avoid hyphens:

- database: `<product-with-underscores>_dev`;
- user: `<product-with-underscores>_dev_user`.

A PostgreSQL password is generated with Python `secrets` only during apply and only when the PostgreSQL resource will actually be created. It is not printed, written to `.env`, committed, or included in JSON results/errors. `internal_db_url`, connection strings, `DATABASE_URL`, `postgres_password`, Bearer tokens, and nested secret-like fields are redacted.

### Partial failures and legacy Compose

If apply creates a Project but a later step fails, the result is `partial`; no destructive rollback is attempted. The next execution should continue idempotently from the existing resources after review.

The legacy Application Compose `<product>-dev` is reported only. It is not modified, deleted, or migrated automatically in this stage.

### Later stages

Backend Application and Frontend Application creation, Application variables, conversion of `internal_db_url` for backend use, deployments, public health checks, and smoke tests remain future stages with separate safety gates.
