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

Never commit or print Coolify tokens. The workflow masks `COOLIFY_API_TOKEN`, and the provisioner redacts it from errors.

## What the workflow does

The manual workflow `.github/workflows/provision-coolify-development.yml` calls `scripts/provision_coolify_development.py`. The script queries the current Coolify API under `/api/v1` to discover:

1. Projects from `GET /projects`.
2. Project details from `GET /projects/{project_uuid}` after creating or finding a project, so the script can reuse Coolify 4.1.2's default internal `production` environment.
3. Servers from `GET /servers`.
4. GitHub Apps from `GET /github-apps`, selecting the exact source name from `coolify_github_app_name` (default `goodmorning-mx-coolify`).
5. Existing applications from `GET /applications`.

If the product project does not exist, `dry_run=true` returns `status: dry_run` and reports `would_create_project` without sending writes to Coolify. With `dry_run=false`, the script creates the missing project with `POST /projects`, queries the new project details with `GET /projects/{project_uuid}`, and then continues application provisioning. If the project already exists, including the already-created `factory-smoke-test` project, the script reuses it idempotently and queries its details before building the application payload.

Coolify 4.1.2 creates a default internal environment named `production` for each project. This workflow does **not** call `POST /projects/{project_uuid}/environment`; instead, it maps GoodMorning stage `development` to that Coolify internal `production` environment while preserving the `dev` branch, `dev.*` domain, and application name `factory-smoke-test-development`.

The GitHub App source lookup must find exactly one source with the requested name. It fails when the source is missing or duplicated, and it rejects `Public GitHub` for private GoodMorning repositories so private repositories use the installed GoodMorning Coolify GitHub App.

If an existing application already references the repository/branch or development domain, the run returns `status: exists` and does not create a duplicate. If no matching application exists and `dry_run=true`, it returns `status: dry_run` plus the sanitized `would_create` payload. If `dry_run=false`, it sends a create request to `POST /applications/dockercompose` with Docker Compose build-pack settings and `instant_deploy=false`.

## Safety rules

- The workflow is manual only.
- `dry_run` defaults to `true`.
- `coolify_github_app_name` defaults to `goodmorning-mx-coolify` and must match a Coolify GitHub App source exactly.
- The workflow refuses branches other than `dev`.
- The workflow refuses domains that do not start with `dev.`.
- The script never deletes Coolify resources.
- The script only allows the `dev` branch and `dev.` domains. Coolify's internal environment name is `production`, but this workflow uses it only as the Coolify 4.1.2 default environment representing the GoodMorning `development` stage.
- The script never deploys immediately; the create payload sets `instant_deploy=false`.
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
  "coolify_github_app_name": "goodmorning-mx-coolify",
  "product": "factory-smoke-test",
  "repository": "goodmorning-mx/factory-smoke-test",
  "status": "dry_run",
  "environment": {
    "coolify_name": "production",
    "created": false,
    "name": "development"
  },
  "would_create": {
    "build_pack": "dockercompose",
    "docker_compose_location": "/compose.yaml",
    "domains": "dev.factory-smoke-test.goodmorning.mx",
    "git_branch": "dev",
    "environment_name": "production",
    "git_repository": "goodmorning-mx/factory-smoke-test",
    "instant_deploy": false
  }
}
```

The JSON also includes the discovered project, server, and GitHub App identifiers needed for review.

## Running the real creation later

Do not run the real creation until the dry-run output has been reviewed. When approved for development provisioning, manually rerun the workflow with `dry_run=false`. This creates or reuses the Coolify development application but still does not deploy production and does not delete anything.

## Rollback

If a development Coolify application is created incorrectly, do not use this automation to delete it. Disable or remove the resource manually in Coolify after review, then rerun the workflow once project, server, GitHub App, branch, compose path, and domain inputs are corrected.
