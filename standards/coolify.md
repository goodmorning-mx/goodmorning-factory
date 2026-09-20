# Coolify Deployment Standard

Coolify is the default hosting control plane for GoodMorning development and production environments. This document defines the deployment contract only; it does not provide real credentials, server addresses, tokens, or fabricated Coolify configuration.

## Branch deployment contract

- `dev` deploys to development or staging environments.
- Development applications must use `instant_deploy=true` and `is_auto_deploy_enabled=true`; every push or merge to `dev` is eligible for an automatic Coolify deployment.
- `main` deploys to production environments.
- Production deployments must come from the release pull request flow from `dev` to `main`.

## Deployment model

- Use Docker-based deployments.
- Define health checks for each service.
- Use HTTPS for public services.
- Keep private services private when public exposure is unnecessary.
- Run database migrations intentionally and document migration and rollback behavior.

## Domains

Suggested domain convention:

```text
product.dev.goodmorning.mx
product.goodmorning.mx
api.product.goodmorning.mx
```

Use `api.product.goodmorning.mx` only when a public API is necessary. Internal APIs should remain private whenever possible.

## Database migrations

- Non-destructive migrations may run with normal deployment checks when the repository documents the process.
- Destructive migrations require Abel's explicit approval and a verified backup plan.
- Migration failures must block or roll back deployment when practical.

## Rollback expectations

Each deployable product should document:

- How to identify the previously known good version.
- How to roll back the application image or deployment.
- How to handle database changes that cannot be automatically reversed.
- How to verify service health after rollback.
