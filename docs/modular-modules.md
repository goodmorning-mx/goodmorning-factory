# GoodMorning modular modules

Products declare reusable modules in their product manifest. The factory
validates the declaration and compiles the package, environment, dependency,
and migration inputs; product repositories remain responsible for installing
the packages and mounting their entrypoints.

```json
{
  "modules": [
    {
      "name": "gm-agent",
      "version": "0.1.3",
      "kind": "full-stack",
      "backend_package": "gm-agent",
      "frontend_package": "@goodmorning/gm-agent",
      "python_requires": ["gm-mcp>=0.1.0", "gm-billing>=0.1.0"],
      "dependencies": ["gm-mcp", "gm-billing"],
      "env": [],
      "required_secrets": {
        "OPENAI_API_KEY": {
          "source": "GOODMORNING_MANAGED",
          "binding": "goodmorning/openai-default",
          "target": "backend"
        }
      },
      "migrations": {"mode": "alembic", "path": "migrations", "minimum_schema": 1},
      "compatibility": {"python": ">=3.12", "node": ">=20", "factory_api": "^1.0", "products": ["*"]},
      "entrypoints": {"backend": "gm_agent:install", "frontend": "@goodmorning/gm-agent"}
    }
  ]
}
```

`validate_product_modules` rejects duplicate modules, undeclared dependencies,
secret defaults, unsafe migration paths, invalid package names, and module
kind/package mismatches. `required_secrets` contains metadata only: it never
contains a secret value. `GOODMORNING_MANAGED` bindings are resolved by the
Factory deployment workflow from its protected central secret store and are
injected only into the declared backend or frontend resource. `CUSTOMER_MANAGED`
is accepted by the contract for future support but is not provisioned yet.
`compile_installation` provides deterministic package lists, environment,
secret-binding, and migration inputs for product onboarding.

## Deployment and infrastructure contract

Modules remain embedded unless a product explicitly selects another mode:

```json
{
  "name": "gm-agent",
  "deployment_modes": ["embedded", "service", "gateway"],
  "default_deployment_mode": "embedded",
  "deployment_mode": "gateway",
  "resource_requirements": [
    {"name": "queue", "kind": "redis", "resource_name": "shared-redis", "shared": true},
    {
      "name": "agent-gateway",
      "kind": "gateway",
      "resource_name": "agent-gateway",
      "deployment_modes": ["gateway"],
      "port": 8100,
      "public": true,
      "health_path": "/health"
    }
  ],
  "bindings": [
    {"key": "REDIS_URL", "source": "redis_url", "requirement": "queue", "target": "backend"}
  ]
}
```

Supported requirement kinds are `postgresql`, `timescaledb`, `redis`,
`service`, `worker`, and `gateway`. Data resources are private by contract;
application requirements become Resources V3 applications with native
auto-deploy and healthchecks. `shared: true` plus a stable `resource_name`
allows modules to consume an existing compatible resource without creating a
duplicate.

Factory composes `modules` into `applications`, `databases`, `redis`, and
`connections` before validating the normal Resources V3 contract. It records
`resource_ownership` entries with owner module, requirement, shared flag, and
consumers. Ownership is reference metadata only: module uninstall/update never
deletes a shared resource.

Bindings compile to `DATABASE_URL`, `REDIS_URL`, and `SERVICE_URL` connections,
or to generated/managed runtime secrets. Secret bindings are rejected for
frontend targets. A module dependency or resource requirement cycle fails
before any Coolify write.

The module updater compares infrastructure metadata as well as package
compatibility. Additive requirements produce a reconciliation plan and are
reconciled through Resources V3 before the optional DEV deployment; removed or
changed resources, bindings, or default deployment modes block auto-update for
manual review.
