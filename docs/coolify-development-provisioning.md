# Coolify development provisioning

Stage 5 is the current canonical development provisioning architecture for GoodMorning product repositories. Each product uses three independent Coolify resources:

1. a private native PostgreSQL 16 database named `<product>-postgres-dev`;
2. a Backend Dockerfile Application named `<product>-api-dev`;
3. a Frontend Dockerfile Application named `<product>-frontend-dev` planned for Stage 6.

Legacy Compose provisioning is deprecated for new products and documented separately in `docs/legacy-coolify-compose-provisioning.md`. The legacy Compose Application named `<product>-dev` remains migration context only. Stage 5 does not create, patch, deploy, stop, restart, delete, or otherwise modify the legacy Compose Application.

## Confirmed Coolify API contract

The Stage 5 implementation is pinned to the upstream Coolify v4.x API surface verified from the official `coollabsio/coolify` `openapi.yaml`, upstream `ApplicationsController.php`, official Coolify documentation, and `coollabsio/coolify-cli` command metadata:

- `POST /api/v1/applications/private-github-app` creates the Backend Private GitHub App Application.
- `GET /api/v1/applications/{uuid}` reads Application detail.
- `GET /api/v1/applications/{uuid}/envs` lists Application environment variables.
- `POST /api/v1/applications/{uuid}/envs` creates one Application environment variable.
- `PATCH /api/v1/applications/{uuid}/envs` updates one Application environment variable.

If a future upstream contract contradicts these endpoints or the approved field names, Stage 5 must fail closed for the affected write path and keep dry-run/discovery read-only.

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

Stage 5 does not create Frontend, deploy Frontend, deploy Backend, create Compose Applications, modify Compose Applications, delete resources, or run remote commands. Frontend Dockerfile Application provisioning remains planned for Stage 6.
