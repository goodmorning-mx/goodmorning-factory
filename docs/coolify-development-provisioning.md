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
- `secrets.COOLIFY_API_TOKEN`: Coolify API token with the least permissions needed to read projects, servers, GitHub Apps, and applications; write permission is only needed when running with `dry_run=false`.
- `vars.COOLIFY_SSH_HOST`: SSH host that exposes the fixed `ensure-dev-env` helper.
- `vars.COOLIFY_SSH_USER`: SSH user authorized only for the provisioning helper.
- `secrets.COOLIFY_SSH_PRIVATE_KEY`: private key for that restricted SSH user.
- `secrets.COOLIFY_SSH_KNOWN_HOSTS`: pinned known-hosts entry for the SSH host.

Never commit or print Coolify tokens or SSH secrets. The workflow masks `COOLIFY_API_TOKEN`, `COOLIFY_SSH_PRIVATE_KEY`, and `COOLIFY_SSH_KNOWN_HOSTS`; the provisioner redacts configured sensitive values from errors.

## What the workflow does

The manual workflow `.github/workflows/provision-coolify-development.yml` calls `scripts/provision_coolify_development.py`. The script queries the current Coolify API under `/api/v1` to discover:

1. Projects from `GET /projects`.
2. Project details from `GET /projects/{project_uuid}` after creating or finding a project. When `dry_run=false`, the script first runs the restricted SSH helper to ensure the Coolify `dev` environment exists, then verifies that `dev` is present in project details.
3. Servers from `GET /servers`.
4. GitHub Apps from `GET /github-apps`, selecting the exact source name from `coolify_github_app_name` (default `goodmorning-mx-coolify`).
5. Existing applications from `GET /applications`.

If the product project does not exist, `dry_run=true` returns `status: dry_run` and reports `would_create_project` without sending writes to Coolify. With `dry_run=false`, the script creates the missing project with `POST /projects`, queries the new project details with `GET /projects/{project_uuid}`, and then continues application provisioning. If the project already exists, including the already-created `factory-smoke-test` project, the script reuses it idempotently and queries its details before building the application payload.

Coolify development applications are created in the Coolify `dev` environment. The workflow does **not** call `POST /projects/{project_uuid}/environment`; when `dry_run=false`, it runs exactly `ensure-dev-env <project_uuid>` over SSH after creating or finding the project. The SSH invocation is built as a fixed argument list, uses the configured key and pinned known-hosts file, validates the helper's JSON response without printing the complete SSH response, and does not accept arbitrary SSH commands.

The GitHub App source lookup must find exactly one source with the requested name. It fails when the source is missing or duplicated, and it rejects `Public GitHub` for private GoodMorning repositories so private repositories use the installed GoodMorning Coolify GitHub App.

If an existing application already references the repository/branch or development domain, the run returns `status: exists` and does not create a duplicate. Existing applications are still reconciled for Docker Compose domains when `dry_run=false`. If no matching application exists and `dry_run=true`, it returns `status: dry_run` plus the sanitized `would_create` payload and planned domain patch. If `dry_run=false`, it sends a create request to `POST /applications/private-github-app` with Docker Compose build-pack settings, the selected source's `github_app_uuid`, and `instant_deploy=false`, then configures Docker Compose service domains with `PATCH /applications/{application_uuid}`. The frontend domain is derived from `development_domain` as `https://dev...`; the backend domain is derived as `https://api.dev...`.

## Safety rules

- The workflow is manual only.
- `dry_run` defaults to `true`.
- `coolify_github_app_name` defaults to `goodmorning-mx-coolify` and must match a Coolify GitHub App source exactly; the script uses the source `uuid` as `github_app_uuid` and refuses to fall back to any numeric `id`.
- The workflow refuses branches other than `dev`.
- The workflow refuses domains that do not start with `dev.`.
- The script never deletes Coolify resources.
- The script only allows the `dev` branch and `dev.` domains.
- With `dry_run=true`, the script never runs SSH.
- With `dry_run=false`, the script runs only `ensure-dev-env <project_uuid>` over SSH and validates that SSH exits with code 0, `project_uuid` matches the requested project, `name` is `dev`, `environment_uuid` is a valid UUID, and `created` is boolean before creating or reconciling the application.
- The application create payload uses `environment_name=dev`; it does not create the development application inside Coolify `production`.
- The script never deploys immediately; the create payload sets `instant_deploy=false`.
- The script configures Docker Compose domains without forcing overrides: `frontend` uses the HTTPS development domain and `backend` uses the same host prefixed with `api.`.
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
      "domain": "https://dev.factory-smoke-test.goodmorning.mx"
    },
    {
      "name": "backend",
      "domain": "https://api.dev.factory-smoke-test.goodmorning.mx"
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
        "domain": "https://dev.factory-smoke-test.goodmorning.mx"
      },
      {
        "name": "backend",
        "domain": "https://api.dev.factory-smoke-test.goodmorning.mx"
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

Do not run the real creation until the dry-run output has been reviewed and the SSH host, restricted user, private key, and pinned known-hosts secret are configured. When approved for development provisioning, manually rerun the workflow with `dry_run=false`. This ensures the Coolify `dev` environment exists, creates or reuses the Coolify development application in `dev`, but still does not deploy production and does not delete anything.

## Rollback

If a development Coolify application is created incorrectly, do not use this automation to delete it. Disable or remove the resource manually in Coolify after review, then rerun the workflow once project, server, GitHub App, branch, compose path, and domain inputs are corrected.
