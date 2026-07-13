# Verify Coolify connection workflow

The manual **Verify Coolify connection** GitHub Actions workflow verifies that this repository can authenticate to the configured Coolify API without creating, changing, or deploying any Coolify resources.

## Required GitHub configuration

Configure these repository-level values before running the workflow:

- `vars.COOLIFY_BASE_URL`: the base URL for the Coolify instance, without a required trailing slash.
- `secrets.COOLIFY_API_TOKEN`: a Coolify API token authorized to read the teams endpoint.

## What the workflow does

When triggered with `workflow_dispatch`, the workflow sends a single `GET` request to:

```text
/api/v1/teams
```

The request uses an `Authorization: Bearer` header sourced from `secrets.COOLIFY_API_TOKEN`. The workflow masks the token and does not print response bodies or sensitive Coolify data.

The workflow fails clearly when configuration is missing, the API returns HTTP `401` or `403`, or the connection cannot be established.
