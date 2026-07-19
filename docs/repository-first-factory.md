# Repository-first product factory

The canonical GoodMorning product lifecycle begins with a dedicated private GitHub repository. Shared smoke-test repositories are not the product source contract.

## One-command pipeline

Dispatch **Create GoodMorning product repository** with a DNS-safe product name, description, and confirmation.

The pipeline:

1. creates `goodmorning-mx/<product>` from `goodmorning-mx/goodmorning-mvp-template`;
2. creates `dev` from the template's `main` branch and makes `dev` the default;
3. verifies privacy, both branches, and factory ownership topics;
4. registers `products/<product>.json` with repository `goodmorning-mx/<product>`;
5. dispatches a targeted resources-v2 reconciliation for that manifest only;
6. creates the Coolify project and `dev` environment;
7. creates private PostgreSQL, backend, and frontend resources;
8. waits through safe transient `partial` states;
9. verifies the API and frontend;
10. configures and verifies the Oracle Object Storage database backup;
11. completes only after the deployment verification issue closes automatically.

## Idempotency and safety

- A compatible factory-managed repository is reused.
- An incompatible existing repository or manifest fails closed and is never overwritten.
- Catalog writes are restricted to the factory `dev` branch.
- The catalog commit does not perform a second deployment; the targeted workflow dispatch owns the deployment.
- Production is not created or modified.
- Existing product databases are never deleted or recreated by this pipeline.

## Agent handoff

After provisioning, agents work only in the dedicated repository:

```text
Repository: goodmorning-mx/<product>
Base branch: dev
Delivery: pull request into dev
Development frontend: https://dev.<product>.goodmorning.mx
Development API: https://api.dev.<product>.goodmorning.mx/health
```

Changes to one product repository cannot alter another product's source code.
