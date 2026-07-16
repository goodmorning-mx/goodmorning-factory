# Coolify development provisioning

Stage 7 is the current canonical development provisioning architecture for GoodMorning product repositories. Each product uses three independent Coolify resources:

1. a private native PostgreSQL 16 database named `<product>-postgres-dev`;
2. a Backend Dockerfile Application named `<product>-api-dev`;
3. a Frontend Dockerfile Application named `<product>-frontend-dev`.

Legacy Compose provisioning is deprecated for new products and documented separately in `docs/legacy-coolify-compose-provisioning.md`. The legacy Compose Application named `<product>-dev` remains migration context only. Stage 6 does not create, patch, deploy, stop, restart, delete, or otherwise modify the legacy Compose Application.

## Confirmed Coolify API contract

The Stage 6 implementation is pinned to the upstream Coolify v4.x API surface verified from the official `coollabsio/coolify` `openapi.yaml`, upstream `ApplicationsController.php`, official Coolify documentation, and `coollabsio/coolify-cli` command metadata:

- `POST /api/v1/applications/private-github-app` creates the Backend or Frontend Private GitHub App Application.
- `GET /api/v1/applications/{uuid}` reads Application detail.
- `GET /api/v1/applications/{uuid}/envs` lists Application environment variables.
- `POST /api/v1/applications/{uuid}/envs` creates one Application environment variable.
- `PATCH /api/v1/applications/{uuid}/envs` updates one Application environment variable.

If a future upstream contract contradicts these endpoints or the approved field names, Stage 6 must fail closed for the affected write path and keep dry-run/discovery read-only.

## Backend Application contract

The Backend Application is a Coolify Private GitHub App Dockerfile Application. Coolify clones the private GitHub repository directly through the selected GitHub App; the provisioner never uses SSH, rsync, SCP, Docker remote commands, or server-local paths.

Approved redacted creation payload shape:

```json
{
  "name": "<product>-api-dev",
  "project_uuid": "<selected project>",
  "environment_uuid": "<selected dev environment>",
  "environment_name": "dev",
  "server_uuid": "<selected server>",
  "destination_uuid": "<selected destination>",
  "github_app_uuid": "<selected GitHub App>",
  "git_repository": "goodmorning-mx/<product>",
  "git_branch": "dev",
  "build_pack": "dockerfile",
  "base_directory": "/backend",
  "dockerfile_location": "/Dockerfile",
  "ports_exposes": "8000",
  "domains": "https://api.dev.<product>.goodmorning.mx",
  "instant_deploy": false,
  "is_auto_deploy_enabled": false,
  "autogenerate_domain": false,
  "health_check_enabled": true,
  "health_check_path": "/health",
  "health_check_port": "8000",
  "health_check_method": "GET",
  "health_check_scheme": "http",
  "health_check_return_code": 200
}
```

The public URL is always clean: `https://api.dev.<product>.goodmorning.mx`. It must never include `:8000`. Port `8000` is only the internal port exposed to Coolify/Traefik through `ports_exposes` and the health check port.

Stage 5 sets `instant_deploy=false`, `is_auto_deploy_enabled=false`, and `autogenerate_domain=false`; it never deploys the Backend and never invokes Application start, restart, stop, or deployment endpoints.

## Destination selection

Destination selection is conservative:

- zero destinations on the selected server is a blocker;
- exactly one destination is selected automatically;
- multiple destinations require `--destination-uuid <uuid>`;
- an explicit destination must belong to the selected server.

## PostgreSQL precondition

The Backend Application can be created or configured only when PostgreSQL is verified as:

- exact resource `<product>-postgres-dev`;
- in the selected project, `dev` environment, and server;
- PostgreSQL 16 and private;
- readiness `healthy` or `already_healthy`;
- `internal_db_url` present;
- `internal_db_url` safely convertible in memory to `postgresql+psycopg://` with `prepare_psycopg_database_url()`.

`internal_db_url` and the derived `DATABASE_URL` are never printed, returned, persisted, added to action details, or included in payload previews.

## Runtime environment variables

Stage 5 reconciles exactly these Backend runtime variables:

| Key | Value | Secret | Flags |
| --- | --- | --- | --- |
| `DATABASE_URL` | in-memory `postgresql+psycopg://...` derived from private PostgreSQL | yes | `is_runtime=true`, `is_buildtime=false`, `is_preview=false`, `is_literal=true`, `is_multiline=false` |
| `APP_ENV` | `dev` | no | `is_runtime=true`, `is_buildtime=false`, `is_preview=false`, `is_literal=true`, `is_multiline=false` |
| `CORS_ORIGINS` | `https://dev.<product>.goodmorning.mx` | no | `is_runtime=true`, `is_buildtime=false`, `is_preview=false`, `is_literal=true`, `is_multiline=false` |

The `DATABASE_URL` value remains secret. If Coolify hides an existing secret value, Stage 5 reports `secret_value_unverified` and does not overwrite it blindly.

## Safety gates

Base writes still require:

- `--apply`;
- `COOLIFY_ALLOW_RESOURCE_WRITES=true`;
- `--confirm-product <product>`.

Backend Application creation additionally requires:

- `COOLIFY_ALLOW_BACKEND_APPLICATION_WRITES=true`;
- `--confirm-backend-application <product>-api-dev`;
- healthy driver-ready PostgreSQL;
- unambiguous project, environment, server, destination, and GitHub App bindings.

Backend environment variable writes additionally require:

- all Backend Application gates;
- `COOLIFY_ALLOW_BACKEND_ENV_WRITES=true`;
- `--confirm-backend-environment <product>-api-dev`;
- a verified compatible Backend Application;
- PostgreSQL still healthy and driver-ready.

## Idempotency and partial failures

Stage 5 discovery distinguishes `absent`, `found`, `duplicate`, `incompatible`, `compatibility_unverified`, `scope_unverified`, `out_of_scope`, `created`, `created_unverified`, `write_outcome_unknown`, `partial`, `blocked`, `recreate_required`, `would_create`, and `exists`.

Creation is idempotent: compatible existing resources are reused, absent resources are planned in dry-run, apply uses a single POST, 409/422 responses trigger exact rediscovery without parsing message text, and no second POST or rollback is attempted. Any 5xx, timeout, network failure, invalid JSON, or invalid 2xx response after a write is reported conservatively as `write_outcome_unknown`/`partial` and dependent env writes are skipped.

## Stage boundaries

Stage 6 may create/configure the Frontend Application and VITE_API_URL only; it does not deploy Frontend, deploy Backend, create Compose Applications, modify Compose Applications, delete resources, or run remote commands.

## Backend UUID binding and env mutation verification

Stage 5 binds a single verified Backend Application UUID before any env mutation is allowlisted. The client starts with no authorized Backend UUID, binds only after a compatible existing Backend is verified or after a newly-created Backend is re-read through `GET /api/v1/applications/{uuid}` and passes compatibility checks, and refuses to silently rebind to another UUID. `POST` and `PATCH` env requests are valid only for `/api/v1/applications/{bound_backend_uuid}/envs`; env writes without a binding or with another Application UUID fail before the HTTP request is built.

`PATCH` is therefore conditionally allowed, not globally allowed: `PUT` and `DELETE` remain always forbidden, while `PATCH` is scoped only to env vars on the bound Backend UUID after env gates are complete.

A successful `POST` or `PATCH` response only means Coolify accepted a write attempt. Stage 5 does not mark env writes verified until it performs a fresh `GET /api/v1/applications/{bound_backend_uuid}/envs`, locates the exact key with `is_preview=false`, detects duplicates, verifies flags, and verifies the value when Coolify exposes it. If the follow-up GET fails, the variable is missing, flags are wrong, or values are not verifiable, the result is `created_unverified`, `updated_unverified`, `partial`, `duplicate`, or `secret_value_unverified` as appropriate.

For `DATABASE_URL`, a hidden value remains `secret_value_unverified`; Stage 5 does not overwrite hidden secrets blindly. `POST`/`PATCH` 409 or 422 conflicts trigger one exact env rediscovery and compatibility check, never a retry, rollback, or delete. PATCH failures after the request is sent, including 5xx, timeouts, URL errors, malformed JSON, or unexpected 2xx statuses, are treated as mutation `write_outcome_unknown` just like POST failures.

Operationally, an unverified hidden `DATABASE_URL` is a deployment dependency blocker, not a reconciliation dependency for independent non-secret variables: `APP_ENV` and `CORS_ORIGINS` should still be created or updated and verified through the post-write GET loop whenever their own writes are safe.

## Stage 6: Frontend Dockerfile Application provisioning

Stage 6 is now the canonical development provisioning architecture. The plan contains three independent resources: native private PostgreSQL 16 (`<product>-postgres-dev`), Backend Dockerfile Application (`<product>-api-dev`), and Frontend Dockerfile Application (`<product>-frontend-dev`). `compose.yaml` remains local-development and CI-only; it is not the Coolify production/dev provisioning unit.

The Frontend is created only through the confirmed Coolify Private GitHub App Application endpoint: `POST /api/v1/applications/private-github-app`. Discovery and verification use `GET /api/v1/applications/{uuid}`, `GET /api/v1/applications/{uuid}/envs`, `POST /api/v1/applications/{uuid}/envs`, and `PATCH /api/v1/applications/{uuid}/envs`. The upstream sources checked for this contract are Coolify `openapi.yaml` on `v4.x`, `ApplicationsController.php` on `v4.x`, the official Coolify GitHub App documentation, and the Coolify CLI/API surface where useful.

The redacted Frontend Application payload is limited to: `name`, `project_uuid`, `environment_uuid`, `environment_name=dev`, `server_uuid`, `destination_uuid`, `github_app_uuid`, `git_repository`, `git_branch=dev`, `build_pack=dockerfile`, `base_directory=/frontend`, `dockerfile_location=/Dockerfile`, `ports_exposes="80"`, `domains=https://dev.<product>.goodmorning.mx`, `instant_deploy=false`, `is_auto_deploy_enabled=false`, `autogenerate_domain=false`, and the healthcheck fields `health_check_enabled=true`, `health_check_path=/`, `health_check_port="80"`, `health_check_method=GET`, `health_check_scheme=http`, `health_check_return_code=200`. Public frontend URLs must stay clean and must never include `:80`, `:3000`, or `:8000`; port 80 is only the internal Nginx/Coolify/Traefik port.

`VITE_API_URL` is the only Frontend variable reconciled in Stage 6. Its value is public and must be exactly `https://api.dev.<product>.goodmorning.mx`. The variable is build-time (`is_buildtime=true`) and not runtime (`is_runtime=false`), not preview, literal, and single-line. The GoodMorning MVP template builds the Frontend with Node 22, runs `npm ci`, passes `ARG/ENV VITE_API_URL`, runs `npm run build`, and serves the built SPA with Nginx on internal port 80; therefore changing `VITE_API_URL` requires a rebuild and later deployment. `DATABASE_URL`, backend env vars, tokens, and secrets never belong to the Frontend.

Frontend Application writes require all base gates plus `COOLIFY_ALLOW_FRONTEND_APPLICATION_WRITES=true` and `--confirm-frontend-application <product>-frontend-dev`. Frontend env writes additionally require `COOLIFY_ALLOW_FRONTEND_ENV_WRITES=true` and `--confirm-frontend-environment <product>-frontend-dev`, a verified immutable Frontend UUID binding, and a `VITE_API_URL` value bound in memory to the verified Backend API domain.

Backend is a precondition for Frontend writes. The Backend Application must be exact, in scope, non-duplicated, compatible, use the selected GitHub App, repository and branch `dev`, have clean domain `https://api.dev.<product>.goodmorning.mx`, internal port `8000`, `base_directory=/backend`, and `dockerfile_location=/Dockerfile`. `DATABASE_URL` with `secret_value_unverified` remains a global blocker for future deployment readiness but does not prevent creating/configuring Frontend or writing `VITE_API_URL`.

Idempotency is conservative: compatible resources are reused; absent Frontend resources are planned in dry-run and created once in apply; duplicates block; incompatible Applications are marked `recreate_required`; scope or compatibility gaps block. HTTP 409/422 conflicts trigger GET rediscovery without retrying or parsing response text. 5xx, timeouts, URL errors, or invalid JSON on writes produce `write_outcome_unknown`, stop dependent writes, and do not retry or roll back.

Stage 6 performed zero deployments, starts, restarts, stops, deletes, public smoke checks, SSH, rsync, SCP, Docker remote commands, server-local file operations, general Application PATCHes, PUTs, DELETEs, env bulk writes, or Compose Application creation. Controlled Backend deployment is implemented by Stage 7; Frontend deployment remains pending for Stage 8.

### Stage 6 Backend precondition for Frontend env writes

Stage 6 discovery may report an existing compatible Frontend Application even when the Backend Application is absent or not yet verified; discovery is read-only and may keep the discovered Frontend UUID for reporting. That does not authorize `VITE_API_URL` writes. Frontend env create/update requires the same reusable Backend Application precondition as Frontend Application creation: Backend status must be `exists` or `created`, include a verified UUID, and have passed the Stage 5 compatibility checks. Complete gates alone never substitute for this structural precondition.

If Backend is `absent`, `duplicate`, `recreate_required`, `incompatible`, `out_of_scope`, `scope_unverified`, `compatibility_unverified`, `created_unverified`, or `write_outcome_unknown`, Stage 6 reports the Frontend env as blocked in apply or planned after Backend in dry-run and performs zero POST/PATCH env writes. A `backend_environment` result of `secret_value_unverified` for hidden `DATABASE_URL` remains a deployment-readiness blocker only; it does not block Frontend Application discovery/provisioning or `VITE_API_URL` reconciliation when the Backend Application itself is verified. Stage 6 still performs zero deployment, lifecycle, or smoke-check operations.

## Stage 7: controlled Backend deployment

Stage 7 is the current canonical development architecture. It keeps the Stage 2-6 provisioning model and adds only a controlled Backend Application deployment path. Frontend deployment is explicitly deferred to Stage 8; Stage 7 reports `frontend_deployment=planned_later`, never calls `POST /deploy` with the Frontend UUID, and never performs a Frontend smoke check.

### Confirmed Coolify deployment API contract

The Stage 7 deployment contract was rechecked against upstream Coolify v4.x sources: `coollabsio/coolify` `openapi.yaml`, `routes/api.php`, `DeployController.php`, `ApplicationDeploymentStatus.php`, `ApplicationDeploymentQueue.php`, and official Coolify documentation. The controlled Backend deployment request is exactly:

```http
POST /api/v1/deploy
Content-Type: application/json
```

```json
{
  "uuid": "<verified-backend-application-uuid>",
  "force": false
}
```

The provisioner does not send `tag`, `pr`, `pull_request_id`, `docker_tag`, comma-separated UUIDs, query parameters, or `force=true`. Coolify returns a deployment collection shaped like:

```json
{
  "deployments": [
    {
      "message": "...",
      "resource_uuid": "<backend_uuid>",
      "deployment_uuid": "<deployment_uuid>"
    }
  ]
}
```

Stage 7 accepts only one deployment entry, requires `resource_uuid` to match the bound Backend Application UUID, captures a non-empty `deployment_uuid`, and never parses `message` to determine success.

Tracking uses `GET /api/v1/deployments/{deployment_uuid}`. Outcome recovery and active-deployment detection use read-only `GET /api/v1/deployments/applications/{backend_uuid}?skip=0&take=10`, whose expected response is `{ "count": <integer>, "deployments": [...] }`.

### Why `/deploy`, not `/applications/{uuid}/start`

Coolify exposes Application lifecycle endpoints, but Stage 7 exclusively uses `POST /deploy` because it returns a trackable `deployment_uuid`. `/applications/{uuid}/start` is not used for Backend deployment and remains blocked, along with restart, stop, and deployment cancel endpoints.

### Deployment preflight

No deployment is requested until these are verified:

- PostgreSQL is exactly `<product>-postgres-dev`, in the selected scope, PostgreSQL 16, private, healthy or already healthy, has `internal_db_url`, and that URL converts in memory to `postgresql+psycopg://`.
- Backend Application is exactly `<product>-api-dev`, `exists` or `created`, bound to one UUID, in the selected project/environment/server/destination, uses the selected GitHub App and repository, branch `dev`, `build_pack=dockerfile`, `base_directory=/backend`, `dockerfile_location=/Dockerfile`, `ports_exposes="8000"`, clean domain `https://api.dev.<product>.goodmorning.mx`, healthcheck `/health` on port `8000`, `instant_deploy=false`, and auto deploy disabled.
- Backend env has exactly verified `DATABASE_URL`, `APP_ENV=dev`, and `CORS_ORIGINS=https://dev.<product>.goodmorning.mx` with runtime-only flags. Duplicate, incompatible, hidden, pending, or unknown-write env states block deployment.

`DATABASE_URL` `secret_value_unverified` blocks deployment. There is no unsafe override. A token used for controlled deployment must have enough read/sensitive-read permission to verify the existing secret value, or the variable must be reconciled safely before deployment.

### Gates

Backend deployment requires all of the following simultaneously:

- `--apply`;
- `--deploy-backend`;
- `COOLIFY_ALLOW_RESOURCE_WRITES=true`;
- exact `--confirm-product <product>`;
- `COOLIFY_ALLOW_DEPLOYMENTS=true`;
- `COOLIFY_ALLOW_BACKEND_DEPLOYMENT=true`;
- exact `--confirm-backend-deployment <product>-api-dev`;
- complete preflight;
- exact Backend UUID binding;
- no prior unknown write outcome;
- no PostgreSQL, Backend Application, or Backend env blockers.

Dry-run never posts to `/deploy` and never performs public smoke HTTP. Apply without `--deploy-backend` preserves Stage 2-6 behavior and reports deployment `not_requested`.

### Deployment status, recovery, polling, runtime, and smoke

Official deployment statuses are exact-match only: `queued`, `in_progress`, `finished`, `failed`, and `cancelled-by-user`. Internally, `queued` and `in_progress` are pending, `finished` is succeeded, `failed` is failed, `cancelled-by-user` is cancelled, and any other value is `deployment_status_unverified`.

Before `POST /deploy`, Stage 7 snapshots existing deployment UUIDs for the Backend and adopts exactly one pre-existing active `queued`/`in_progress` deployment. Multiple active deployments block as ambiguous. If a POST has an unknown outcome because of HTTP 5xx, timeout, URL error, or invalid JSON, the provisioner performs only a second read-only deployment-history GET, compares UUIDs to the preflight snapshot, recovers exactly one new deployment UUID, or reports unknown/ambiguous recovery. It never retries the POST, adopts an old deployment, cancels, rolls back, or selects arbitrarily.

Polling is bounded by `--backend-deployment-timeout` (default `900`, max `3600` seconds), `--backend-deployment-interval` (default `5`, max `30` seconds), and `--backend-deployment-max-poll-errors` (default `3`, max `10`). Polling validates the deployment UUID, validates `application_name` when present, ignores logs and snapshots, and stops on finished, failed, cancelled, unknown status, too many transient GET errors, or timeout. It never prints deployment logs or configuration snapshots.

After `finished`, Stage 7 rereads `GET /api/v1/applications/{backend_uuid}` and accepts runtime health only for exact statuses `running:healthy` or `healthy`. Exact transient statuses are `queued`, `starting`, and `running:starting`; exact unhealthy statuses are `running:unhealthy`, `unhealthy`, `exited`, `stopped`, `failed`, and `exited:unhealthy`; any other value is `runtime_status_unverified`.

The Backend smoke check runs only after deployment is finished, Backend identity is verified, and runtime is healthy. The URL is exactly `https://api.dev.<product>.goodmorning.mx/health`; HTTP, ports, IPs, localhost, credentials, query strings, fragments, cross-host redirects, and disabled TLS verification are forbidden. The expected JSON object is:

```json
{
  "service": "<app name>",
  "status": "ok",
  "environment": "dev"
}
```

Stage 7 requires HTTP 200, JSON-compatible content type, object body, `status == "ok"`, `environment == "dev"`, and a non-empty string `service`. It retries only the smoke GET within `--backend-smoke-timeout` (default `120`, max `600` seconds), using `--backend-smoke-interval` (default `5`, max `30`) and `--backend-smoke-request-timeout` (default `10`, max `30`). Smoke failure never repeats deployment and never restarts, stops, cancels, rolls back, or deploys Frontend.

### Output and forbidden operations

Stage 7 adds redacted JSON objects `backend_deployment`, `backend_runtime_readiness`, and `backend_smoke_check`, plus structured actions for deployment planning/request/adoption/recovery/polling/runtime/smoke. Output never includes deployment logs, `DATABASE_URL`, `internal_db_url`, secret env values, Authorization, configuration snapshots/diffs, or response bodies.

Stage 7 still performs zero SSH, rsync, SCP, Docker remote commands, server process execution, PUT, DELETE, Application PATCH general, env bulk writes, Frontend deployment, previews/PR deployments, tags, multiple UUID deploys, `force=true`, rollback, cancel, restart, stop, or resource deletion.
