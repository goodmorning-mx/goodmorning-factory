# Coolify development environment provisioning

This document describes the manual GitHub Actions workflow for preparing a product **development** environment in Coolify. It intentionally defaults to `dry_run=true`; the first safe run only discovers Coolify resources and prints the resource that would be created.

## First supported product

| Field | Value |
| --- | --- |
| Product | `factory-smoke-test` |
| Repository | `goodmorning-mx/factory-smoke-test` |
| Branch | `dev` |
| Docker Compose file | `/compose.yaml` |
| Development domain | `dev.factory-smoke-test.goodmorning.mx` |
| Coolify GitHub App source | `goodmorning-mx-coolify` |

## Required GitHub configuration

Configure these repository-level values before running the workflow:

- `vars.COOLIFY_BASE_URL`: Coolify instance base URL.
- `secrets.COOLIFY_API_TOKEN`: Coolify API token with the least permissions needed to read projects, project environments, servers, GitHub Apps, and applications; write permission is only needed when running with `dry_run=false`.

Never commit or print Coolify tokens. The workflow masks `COOLIFY_API_TOKEN`; the provisioner redacts configured sensitive values from errors.

## What the workflow does

The manual workflow `.github/workflows/provision-coolify-development.yml` calls `scripts/provision_coolify_development.py`. The script queries the current Coolify API under `/api/v1` to discover:

1. Projects from `GET /projects`.
2. Project details from `GET /projects/{project_uuid}` after creating or finding a project. When `dry_run=false`, the script also queries `GET /projects/{project_uuid}/environments` and creates the Coolify `dev` environment with `POST /projects/{project_uuid}/environments` and `{"name":"dev"}` only when it is missing.
3. Servers from `GET /servers`.
4. GitHub Apps from `GET /github-apps`, selecting the exact source name from `coolify_github_app_name` (default `goodmorning-mx-coolify`).
5. Existing applications from `GET /applications`.

If the product project does not exist, `dry_run=true` returns `status: dry_run` and reports `would_create_project` without sending writes to Coolify. With `dry_run=false`, the script creates the missing project with `POST /projects`, queries the new project details with `GET /projects/{project_uuid}`, and then continues application provisioning. If the project already exists, including the already-created `factory-smoke-test` project, the script reuses it idempotently and queries its details before building the application payload.

Coolify development applications are created in the Coolify `dev` environment. The workflow does **not** use SSH. When `dry_run=false`, it first confirms via `GET /projects/{project_uuid}/environments` whether `dev` already exists. If `dev` is missing, it sends `POST /projects/{project_uuid}/environments` with `{"name":"dev"}`. A `201` response is treated as created, a `409` response triggers a second `GET /projects/{project_uuid}/environments` to reuse the environment created by another process, and a `422` response is reported with the complete sanitized validation body.

The GitHub App source lookup must find exactly one source with the requested name. It fails when the source is missing or duplicated, and it rejects `Public GitHub` for private GoodMorning repositories so private repositories use the installed GoodMorning Coolify GitHub App.

If an existing application already references the repository/branch or development domain, the run returns `status: exists` and does not create a duplicate. Existing applications with loaded `docker_compose_raw` are still reconciled for Docker Compose domains and Coolify application environment variables when `dry_run=false`. Missing, redacted, or empty `docker_compose_raw` is not treated as proof that Compose is unconfigured, because Coolify API tokens can lack `read:sensitive`; when the existing application still exposes the expected repository, branch, and Docker Compose path, the script continues with domain reconciliation and deployment. `status: recreate_required` is reserved for existing applications that lack both loaded Compose data and verifiable repository/branch/compose path configuration, and the CLI/workflow fail on that status. If no matching application exists and `dry_run=true`, it returns `status: dry_run` plus the sanitized `would_create` payload. If `dry_run=false`, it sends a single create request to `POST /applications/private-github-app` with Docker Compose build-pack settings, the selected source's `github_app_uuid`, `docker_compose_domains`, and `instant_deploy=false`; it does not send a follow-up domain `PATCH` for newly-created applications. Before queuing deployment, it configures `VITE_API_URL=https://api.dev.<product>.goodmorning.mx`, `CORS_ORIGINS=https://dev.<product>.goodmorning.mx`, and `APP_ENV=dev` as Coolify application environment variables. For Coolify v4.1.2, the provisioner first reads the current variables with `GET /applications/{application_uuid}/envs`; existing keys are updated through `PATCH /applications/{application_uuid}/envs` with the key/value payload returned from the current list, while missing keys are created with `POST /applications/{application_uuid}/envs`. It intentionally does not call `PATCH /applications/{application_uuid}/envs/{env_uuid}`, because the v4.1.2 OpenAPI only documents `DELETE` on that item route. The frontend domain is derived from `development_domain` as `https://dev...`; the backend domain is derived as `https://api.dev...`.

After a successful `dry_run=false` provisioning or idempotent reuse of an existing application, the script configures the required Coolify application environment variables, then triggers the development deployment with `POST /api/v1/deploy?uuid={application_uuid}&force=true`. It reads the deployment UUID from the Coolify response, polls `GET /api/v1/deployments/{deployment_uuid}` until a successful terminal status, a failed terminal status, or timeout, and then verifies both development smoke endpoints: `https://dev.factory-smoke-test.goodmorning.mx/` and `https://api.dev.factory-smoke-test.goodmorning.mx/health`. These deployment and smoke-check calls are never executed for `dry_run=true`.

## Safety rules

- The workflow is manual only.
- `dry_run` defaults to `true`.
- `coolify_github_app_name` defaults to `goodmorning-mx-coolify` and must match a Coolify GitHub App source exactly; the script uses the source `uuid` as `github_app_uuid` and refuses to fall back to any numeric `id`.
- The workflow refuses branches other than `dev`.
- The workflow refuses domains that do not start with `dev.`.
- The script never deletes Coolify resources.
- The script only allows the `dev` branch and `dev.` domains.
- With `dry_run=true`, the script does not create projects, environments, applications, domain patches, Coolify environment variable writes, deployments, or smoke checks.
- With `dry_run=false`, the script uses the official Coolify environment API only: `GET /projects/{project_uuid}/environments`, then `POST /projects/{project_uuid}/environments` with `{"name":"dev"}` if needed. It handles `201`, `409`, and `422` explicitly and does not require SSH configuration.
- New application creation sends `docker_compose_domains` directly in the `POST /applications/private-github-app` payload and does not issue a follow-up domain patch.
- Coolify `422` API errors are reported with sanitized structured details, including `message`, `errors`, `conflicts`, and `warning` when present.
- The application create payload uses `environment_name=dev`; it does not create the development application inside Coolify `production`.
- The application create payload sets `instant_deploy=false`; the explicit deployment step only runs later in the same workflow when `dry_run=false` and provisioning or idempotent reuse succeeds.

- Before any `dry_run=false` rebuild/deploy queue operation, the script upserts Coolify application environment variables with safe dev values: `VITE_API_URL=https://api.dev.<product>.goodmorning.mx`, `CORS_ORIGINS=https://dev.<product>.goodmorning.mx`, and `APP_ENV=dev`. Existing variables are discovered with `GET /applications/{application_uuid}/envs` before each update path; the script updates only variables that Coolify currently returns and uses the Coolify v4.1.2 collection update endpoint `PATCH /applications/{application_uuid}/envs`, not the item delete-only route `PATCH /applications/{application_uuid}/envs/{env_uuid}`.
- The script configures Docker Compose domains without forcing overrides or host port publishing: `frontend` uses the HTTPS development domain and `backend` uses the same host prefixed with `api.`. Public Coolify domains and smoke checks omit explicit ports. Each Docker Compose domain item contains `name`, `domain`, and `port` so Coolify routes public HTTPS traffic to the service port exposed inside the Docker Compose network.
- The script never prints `COOLIFY_API_TOKEN`.
- Production provisioning and production deployments are out of scope and require the release PR flow plus Abel's explicit approval.

## Expected dry-run result

Successful dry-runs print JSON similar to:

```json
{
  "branch": "dev",
  "compose": "/compose.yaml",
  "domain": "dev.factory-smoke-test.goodmorning.mx",
  "dry_run": true,
  "docker_compose_domains": [
    {
      "name": "frontend",
      "domain": "https://dev.factory-smoke-test.goodmorning.mx",
      "port": 8080
    },
    {
      "name": "backend",
      "domain": "https://api.dev.factory-smoke-test.goodmorning.mx",
      "port": 8000
    }
  ],
  "coolify_github_app_name": "goodmorning-mx-coolify",
  "product": "factory-smoke-test",
  "repository": "goodmorning-mx/factory-smoke-test",
  "status": "dry_run",
  "environment": {
    "coolify_name": "dev",
    "created": false,
    "name": "development"
  },
  "would_patch": {
    "docker_compose_domains": [
      {
        "name": "frontend",
        "domain": "https://dev.factory-smoke-test.goodmorning.mx",
        "port": 8080
      },
      {
        "name": "backend",
        "domain": "https://api.dev.factory-smoke-test.goodmorning.mx",
        "port": 8000
      }
    ],
    "force_domain_override": false
  },
  "would_create": {
    "build_pack": "dockercompose",
    "docker_compose_location": "/compose.yaml",
    "git_branch": "dev",
    "environment_name": "dev",
    "github_app_uuid": "github-app-uuid-from-selected-source",
    "git_repository": "goodmorning-mx/factory-smoke-test",
    "name": "factory-smoke-test-development",
    "project_uuid": "project-uuid",
    "server_uuid": "server-uuid",
    "instant_deploy": false
  }
}
```

The JSON also includes the discovered project, server, and GitHub App identifiers needed for review.

## Running the real creation later

Do not run the real creation until the dry-run output has been reviewed and the Coolify API token has the least required read/write permissions. When approved for development provisioning, manually rerun the workflow with `dry_run=false`. This ensures the Coolify `dev` environment exists through the official API, creates or reuses the Coolify development application in `dev`, queues a forced Coolify deployment, waits for completion, and verifies the frontend and API health endpoints. It still does not deploy production and does not delete anything.

## Rollback

If a development Coolify application is created incorrectly, do not use this automation to delete it. Disable or remove the resource manually in Coolify after review, then rerun the workflow once project, server, GitHub App, branch, compose path, and domain inputs are corrected.
