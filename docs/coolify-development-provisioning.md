# Coolify development provisioning

Stage 9 is the current operational interface, and Stage 8 remains the canonical development provisioning architecture for GoodMorning product repositories. Each product uses three independent Coolify resources:

1. a private native PostgreSQL 16 database named `<product>-postgres-dev`;
2. a Backend Dockerfile Application named `<product>-api-dev`;
3. a Frontend Dockerfile Application named `<product>-frontend-dev`.

Legacy Compose provisioning is deprecated for new products and documented separately in `docs/legacy-coolify-compose-provisioning.md`. The legacy Compose Application named `<product>-dev` remains migration context only. Stage 6 does not create, patch, deploy, stop, restart, delete, or otherwise modify the legacy Compose Application.



## Stage 9 orchestration

Stage 9 adds the controlled `scripts/orchestrate_product_development.py` interface and the manual `.github/workflows/product-development.yml` workflow. Operators use `plan`, `provision`, `deploy-backend`, or `deploy-full`; mutating operations require an exact `<operation>:<product>:dev` confirmation and a separate safe dry-run plan before apply. The workflow is `workflow_dispatch` only, runs from factory branch `dev`, uses GitHub Environment `dev`, and does not add automatic deployment on push. See `docs/product-development-runbook.md` for the runbook.

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

- Stage 9 requires an explicit `--destination-uuid <coolify-destination-identifier>`;
- obtain it from Coolify → Destinations → open the destination → copy the destination identifier from the URL;
- do not infer it from `server.destinations`;
- the current public Coolify API has no documented `GET /destinations`;
- when `/servers` omits destinations, the identifier is treated as explicit operator input with `destination_scope_api_verified=false`;
- when `/servers` includes a destinations collection, the explicit identifier must match that collection exactly.

## PostgreSQL precondition

The Backend Application can be created or configured only when PostgreSQL is verified as:

- exact resource `<product>-postgres-dev`;
- in the selected project, `dev` environment, and server;
- PostgreSQL 16 and private;
- readiness `healthy` or `already_healthy`;
- `internal_db_url` present;
- `internal_db_url` safely convertible in memory to `postgresql+psycopg://` with `prepare_psycopg_database_url()`.

`internal_db_url` and the derived `DATABASE_URL` are never printed, returned, persisted, added to action details, or included in payload previews.

Coolify v4.1.2 may omit `project_uuid` and `environment_uuid` from `GET /databases/{uuid}`. Applications still require API-verified scope through the normal `resource_scope()` checks; the PostgreSQL exception is narrower and conservative. When Coolify returns either field, it must match the selected Project and exact `dev` Environment, and contradictions are `out_of_scope`. When both are omitted, an existing PostgreSQL can be reused only if there is exactly one exact-name match with a real UUID, the selected server UUID matches, engine is PostgreSQL, version is exactly 16, it is private with no public port, deterministic `postgres_user` and `postgres_db` match the product, the Factory-managed description marker matches, no contradictions or unknown writes exist, and Project plus `dev` Environment selection was unambiguous. The output then reports `scope_api_verified=false`, `scope_verification_source=factory_managed_identity_and_explicit_selected_scope`, and `scope_verification_reason=public_api_omits_project_and_environment_scope`; it does not claim the public API verified omitted Project or Environment fields.

After a create attempt, PostgreSQL can be marked `created` only after a 2xx `POST /databases/postgresql` returns a real UUID and a subsequent `GET /databases/{same_uuid}` verifies the same UUID and the conservative identity policy above. The internal post-create provenance is non-secret and limited to expected name, returned UUID, selected Project/dev Environment/server identifiers, and request/write booleans. Invalid JSON, timeout, 5xx, contradictory UUID, or `write_outcome_unknown` remain `created_unverified` or `partial`, do not trigger a retry, and block Backend/Frontend progression.

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

Stage 6 performed zero deployments, starts, restarts, stops, deletes, public smoke checks, SSH, rsync, SCP, Docker remote commands, server-local file operations, general Application PATCHes, PUTs, DELETEs, env bulk writes, or Compose Application creation. Controlled Backend deployment is implemented by Stage 7; Frontend deployment is implemented by Stage 8.

### Stage 6 Backend precondition for Frontend env writes

Stage 6 discovery may report an existing compatible Frontend Application even when the Backend Application is absent or not yet verified; discovery is read-only and may keep the discovered Frontend UUID for reporting. That does not authorize `VITE_API_URL` writes. Frontend env create/update requires the same reusable Backend Application precondition as Frontend Application creation: Backend status must be `exists` or `created`, include a verified UUID, and have passed the Stage 5 compatibility checks. Complete gates alone never substitute for this structural precondition.

If Backend is `absent`, `duplicate`, `recreate_required`, `incompatible`, `out_of_scope`, `scope_unverified`, `compatibility_unverified`, `created_unverified`, or `write_outcome_unknown`, Stage 6 reports the Frontend env as blocked in apply or planned after Backend in dry-run and performs zero POST/PATCH env writes. A `backend_environment` result of `secret_value_unverified` for hidden `DATABASE_URL` remains a deployment-readiness blocker only; it does not block Frontend Application discovery/provisioning or `VITE_API_URL` reconciliation when the Backend Application itself is verified. Stage 6 still performs zero deployment, lifecycle, or smoke-check operations.

## Stage 7: controlled Backend deployment

Stage 7 was the previous canonical development architecture. It keeps the Stage 2-6 provisioning model and adds only a controlled Backend Application deployment path. Frontend deployment is explicitly deferred to Stage 8; Stage 7 reports `frontend_deployment=planned_later`, never calls `POST /deploy` with the Frontend UUID, and never performs a Frontend smoke check.

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

Before `POST /deploy`, Stage 7 snapshots existing deployment UUIDs for the Backend and adopts exactly one pre-existing active `queued`/`in_progress` deployment. Multiple active deployments block as ambiguous. If a POST has an unknown outcome because of HTTP 5xx, timeout, URL error, invalid JSON, or any ambiguous HTTP 2xx response, the provisioner performs only a second read-only deployment-history GET, compares UUIDs to the preflight snapshot, recovers exactly one new deployment UUID, or reports unknown/ambiguous recovery. An ambiguous HTTP 2xx means Coolify accepted the write request, so `write_performed=true`, but it does not mean the deployment is verified; only exact response parsing plus polling, or unequivocal recovery plus polling, can verify it. Stage 7 never retries the POST, adopts an old deployment, cancels, rolls back, or selects arbitrarily.

Polling is bounded by `--backend-deployment-timeout` (default `900`, max `3600` seconds), `--backend-deployment-interval` (default `5`, max `30` seconds), and `--backend-deployment-max-poll-errors` (default `3`, max `10`). Polling validates the deployment UUID, validates `application_name` when present, ignores logs and snapshots, and stops on finished, failed, cancelled, unknown status, too many transient GET errors, or timeout. It never prints deployment logs or configuration snapshots.

After `finished`, Stage 7 polls `GET /api/v1/applications/{backend_uuid}` within `--backend-runtime-timeout` (default `180` seconds), `--backend-runtime-interval` (default `5` seconds), and `--backend-runtime-max-poll-errors` (default `3`) and accepts runtime health only for exact statuses `running:healthy` or `healthy`. Exact transient statuses are `queued`, `starting`, and `running:starting`; exact unhealthy statuses are `running:unhealthy`, `unhealthy`, `exited`, `stopped`, `failed`, and `exited:unhealthy`; any other value is `runtime_status_unverified`.

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

## Stage 8: controlled Frontend deployment

Stage 9 is the current operational interface, and Stage 8 remains the canonical development provisioning architecture. It keeps three independent Coolify resources per product: `<product>-postgres-dev` as private native PostgreSQL 16 without public domain, `<product>-api-dev` as a Backend Dockerfile Application from `/backend` with internal port `8000` and public domain `https://api.dev.<product>.goodmorning.mx`, and `<product>-frontend-dev` as a Frontend Dockerfile Application from `/frontend` with internal port `80` and public domain `https://dev.<product>.goodmorning.mx` without `:80`. GitHub private repository access through the Coolify GitHub App on branch `dev` remains the only remote source. `compose.yaml` remains local/CI-only and is not a remote deployment source; do not add Traefik labels, `docker_compose_domains`, server-local paths, SSH, rsync, SCP, or remote Docker workflows to product deployment automation.

The verified Frontend template contract is Dockerfile build stage `node:22-alpine`, `npm ci`, `ARG/ENV VITE_API_URL`, `npm run build`, Nginx `1.27-alpine` runtime, `EXPOSE 80`, internal healthcheck `wget -qO- http://127.0.0.1/`, Nginx `listen 80`, SPA `try_files $uri $uri/ /index.html`, and an HTML React mount element with `id="root"`. `VITE_API_URL` is build-time only and must be exactly `https://api.dev.<product>.goodmorning.mx` for dev. Changing it requires rebuilding/deploying the Frontend image. `DATABASE_URL` must never be present in the Frontend build or runtime environment.

Stage 8 adds only controlled Frontend deployment. It never deploys Frontend automatically and never deploys it merely because gates are present. Frontend deployment is enabled only when all of these are true: `--apply`, `--deploy-frontend`, `COOLIFY_ALLOW_RESOURCE_WRITES=true`, `--confirm-product == --product`, `COOLIFY_ALLOW_DEPLOYMENTS=true`, `COOLIFY_ALLOW_FRONTEND_DEPLOYMENT=true`, `--confirm-frontend-deployment == <product>-frontend-dev`, Frontend Application verified, Frontend env verified, Backend dependency verified, and no blockers or previous writes with unknown outcome exist. Backend and Frontend deployment gates and UUID bindings are independent; `COOLIFY_ALLOW_BACKEND_DEPLOYMENT`, `--confirm-backend-deployment`, and the Backend UUID binding never authorize Frontend deployment.

The Frontend deployment request is exactly `POST /api/v1/deploy` with payload `{"uuid":"<verified-frontend-application-uuid>","force":false}`. `POST /deploy` is allowed only for `backend_deploy` with the bound Backend UUID or `frontend_deploy` with the bound Frontend UUID. Empty UUIDs, comma-separated UUIDs, unbound UUIDs, Backend UUID for Frontend deploy, Frontend UUID for Backend deploy, `force=true`, extra fields, tags, PR previews, `pull_request_id`, `docker_tag`, query parameters, and `GET /deploy` are rejected. Application lifecycle endpoints, deployment cancel, rollback, start, restart, and stop remain forbidden.

Frontend preflight requires the exact Frontend Application name, UUID, Project/dev/server/destination/GitHub App scope, repository, branch `dev`, Dockerfile build pack, `/frontend`, `/Dockerfile`, `ports_exposes="80"`, domain `https://dev.<product>.goodmorning.mx` with no port, `instant_deploy=false`, `is_auto_deploy_enabled=false`, `autogenerate_domain=false`, and healthcheck `/` on internal port `80`. It also requires the observed remote Frontend environment to contain exactly one key: `VITE_API_URL`. That single variable must equal `https://api.dev.<product>.goodmorning.mx`, have `is_buildtime=true`, `is_runtime=false`, `is_preview=false`, `is_literal=true`, and `is_multiline=false`, be visible and value-verified, have no duplicate occurrence, and have no public port/path/query/fragment. Any additional Frontend environment key blocks deployment, including `DATABASE_URL`, `APP_ENV`, `CORS_ORIGINS`, `SECRET`, or arbitrary product-specific keys; the provisioner may report extra key names and counts, but never their values.

The Backend dependency is conservative. Frontend deployment requires exact verified Backend Application and env, Backend UUID distinct from Frontend UUID, healthy PostgreSQL, internal database URL available and driver-ready without printing it, Backend runtime healthy, and Backend smoke success at `https://api.dev.<product>.goodmorning.mx/health`. If `--deploy-backend` and `--deploy-frontend` are used together, Backend deployment runs first and must finish, runtime-check healthy, and smoke-check successfully before Frontend deployment begins. If only `--deploy-frontend` is requested, Backend is not deployed automatically; read-only dependency checks, runtime polling, and mockable Backend smoke must pass or Frontend is blocked with zero Frontend POSTs. Public Backend smoke for Frontend-only deployment is gated: it runs only after the Frontend deployment gates are complete, so missing `COOLIFY_ALLOW_DEPLOYMENTS`, `COOLIFY_ALLOW_FRONTEND_DEPLOYMENT`, the Frontend confirmation, or the base write gate yields `gates_incomplete` with zero public smoke and zero deployment POSTs.

Before a Frontend POST, Stage 8 reads `GET /api/v1/deployments/applications/{frontend_uuid}?skip=0&take=10`. Invalid or unverifiable history fails closed and does not become an empty list. Exactly one active `queued` or `in_progress` deployment is adopted; multiple active deployments block as ambiguous; no active deployment permits exactly one POST. Ambiguous HTTP 2xx responses, including missing/empty/multiple deployment entries, wrong resource UUID, or missing deployment UUID, mark `request_attempted=true`, `write_performed=true`, `write_outcome_unknown=true`; HTTP 5xx/network/timeout marks `request_attempted=true`, `write_performed=false`, `write_outcome_unknown=true`. Recovery performs one read-only history GET, compares before/after UUIDs, adopts exactly one new scoped candidate, never retries the POST, and never cancels or rolls back.

Frontend deployment polling uses `GET /api/v1/deployments/{deployment_uuid}` with `--frontend-deployment-timeout` default `900`, interval `5`, and max poll errors `3`. It accepts official statuses only: `queued`, `in_progress`, `finished`, `failed`, and `cancelled-by-user`; identity mismatches, unknown statuses, too many GET errors, and timeout fail closed. After `finished`, runtime readiness polls `GET /api/v1/applications/{frontend_uuid}` with timeout `180`, interval `5`, max poll errors `3`, accepts only `running:healthy` or `healthy`, treats `queued`, `starting`, and `running:starting` as transient, and fails closed for unhealthy or unknown statuses.

Frontend smoke runs only after healthy runtime and targets exactly `https://dev.<product>.goodmorning.mx/`. Redirect final URL must remain HTTPS, same host, no port, path `/`, no query, no fragment, no credentials, no IP, and no localhost. TLS is not disabled. A valid response is HTTP 200, HTML Content-Type, non-empty bounded body, parseable HTML document, an actual `<html>` element, and an element with `id="root"`. `html_verified` means the document contains an HTML element; it is not merely a Content-Type check, and a fragment such as `<div id="root"></div>` fails. Output may include clean URL, status, HTTP status, `html_verified`, `root_element_verified`, attempts, and timeout, but never HTML body, fragments, full headers, deployment logs, `DATABASE_URL`, `internal_db_url`, tokens, or secrets.

Stage 8 JSON output preserves Backend deployment, Backend runtime readiness, and Backend smoke fields and adds `frontend_deployment`, `frontend_runtime_readiness`, and `frontend_smoke_check`. Apply with `--deploy-frontend` succeeds only when Frontend deployment is `finished` and verified, Frontend runtime is healthy and verified, and Frontend smoke verifies HTML plus the root element. All other apply results are non-zero/partial or blocked. Dry-run performs zero POST/PATCH, zero public smoke, and zero live runtime verification; when structural preflight passes it may report `would_deploy` or `would_verify_backend_then_deploy` while leaving live checks planned/not checked and keeping the global status `dry_run`. Legacy Coolify Compose deployment documentation remains deprecated; Compose is local/CI-only.


### Stage 9 dry-run dependency planning

Stage 9 separates structural dry-run planning from live apply preconditions. In dry-run, a missing native PostgreSQL resource can be represented as `postgresql.status=would_create`; the corresponding readiness result is `postgresql_readiness.status=would_start` with `reason=postgresql_creation_planned`. That means the future start is planned, not that PostgreSQL is already running. The Backend Application may then be planned as `would_create` with `reason=postgresql_dependency_planned`, its env can remain `planned` without inventing or printing `DATABASE_URL`, and the Frontend can be `planned_after_backend` with `reason=backend_dependency_planned`. This path is GET-only: it does not hydrate `/databases/${postgres.uuid}`, start PostgreSQL, poll readiness, run public smoke checks, or perform POST/PATCH writes.

Apply remains live and sequential. Backend creation and Backend env reconciliation are still blocked until PostgreSQL exists or was created, has a real UUID, is `healthy` or `already_healthy`, exposes a verifiable private `internal_db_url`, converts in memory to `postgresql+psycopg://`, and has no unknown write outcome. A safe dry-run plan is therefore only a fail-closed structural sequence; it is not proof that PostgreSQL is currently running.

## Stage 9 live destination contract

The first real Stage 9 `workflow_dispatch` against GoodMorning observed Coolify v4.1.2, while current upstream `v4.x` remains the moving HEAD used for API research. Primary-source review of upstream `v4.x` confirms that Application creation accepts `destination_uuid`, but the public API does not currently document `GET /destinations`; `GET /servers` and `GET /servers/{uuid}` are not relied on to provide a destinations collection. Therefore Stage 9 must not invent automatic destination discovery through undocumented endpoints.

The manual workflow requires the operator to provide exactly one explicit opaque Coolify destination identifier in the `destination_uuid` field. Empty values, whitespace, comma-separated values, list/JSON shapes, URLs, paths, queries, fragments, credential-bearing values, control characters, excessive length, and unsafe identifier characters fail closed before planning. The length limit is defensive and is not a formal upstream guarantee. The same validated identifier is bound into both Backend and Frontend Application payload previews. Plan mode remains GET-only, performs zero POST/PATCH/PUT/DELETE calls, and an unsafe plan never passes to apply. `not_checked` runtime readiness and smoke statuses are acceptable only when the corresponding deployment was not requested; requested deployments with `not_checked` remain unsafe.
