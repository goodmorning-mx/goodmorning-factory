# GoodMorning DEV deployment standard

Every `resources-v2` product uses this immutable daily deployment contract:

```text
product repository dev merge
        ↓
GitHub App installation / webhook
        ↓
Coolify native Application auto-deploy
        ↓
Docker build, healthcheck, and public DEV domain
```

Factory provisions and reconciles the two Coolify Applications with:

- the exact product repository;
- branch `dev`;
- the existing `goodmorning-mx-coolify` GitHub App source;
- the existing DEV domains;
- `instant_deploy=true` and `is_auto_deploy_enabled=true`;
- application health checks (`/health:8000` for Backend and `/ :80` for Frontend).

Reconciliation updates an existing in-scope Application through Coolify's
allowlisted application PATCH contract. It never deletes or recreates an
Application, database, domain, volume, or secret. Duplicate or out-of-scope
resources remain blocked for review.

Factory's provisioning workflow may perform an explicit onboarding or repair
deployment when requested. It is not the trigger for normal product changes.
After a product is provisioned, a regular merge to product `dev` must be
observed as a native Coolify deployment, without dispatching a Factory deploy
workflow.
