# GoodMorning modular modules

Products declare reusable modules in their product manifest. The Factory
validates the declaration and compiles the package, environment, dependency,
and migration inputs; product repositories remain responsible for installing
the packages and mounting their entrypoints.

Example of a module entry using the currently approved `gm-payments` release:

```json
{
  "modules": [
    {
      "name": "gm-payments",
      "version": "0.1.1",
      "source_commit": "77502f1d732f9c33bb145c90d2af13ec076be0d6",
      "kind": "backend",
      "backend_package": "gm-payments",
      "python_requires": ["stripe>=12,<13"],
      "deployment_modes": ["embedded", "service"],
      "default_deployment_mode": "embedded",
      "resource_requirements": [
        {"name": "postgres", "kind": "postgresql", "resource_name": "postgres", "shared": true}
      ],
      "bindings": [
        {"key": "DATABASE_URL", "source": "database_url", "requirement": "postgres", "target": "backend"}
      ],
      "dependencies": [],
      "env": [
        {"key": "PAYMENT_PROVIDER", "required": true, "default": "stripe"},
        {"key": "PAYMENT_MODE", "required": true, "default": "demo"}
      ],
      "required_secrets": {
        "STRIPE_SECRET_KEY": {
          "source": "GOODMORNING_MANAGED",
          "binding": "goodmorning/stripe-secret-key",
          "target": "backend"
        },
        "STRIPE_WEBHOOK_SECRET": {
          "source": "GOODMORNING_MANAGED",
          "binding": "goodmorning/stripe-webhook-secret",
          "target": "backend"
        }
      },
      "migrations": {"mode": "sql", "path": "migrations", "minimum_schema": 1},
      "compatibility": {"python": ">=3.12", "factory_api": "^1.0", "products": ["*"]}
    }
  ]
}
```

`validate_product_modules` rejects duplicate modules, undeclared dependencies,
secret defaults, unsafe migration paths, invalid package names, and module
kind/package mismatches. A `source_commit`, when present, must be a full
40-character lowercase Git SHA and must match an installable, formally released,
tested version in `modules/registry.json`. Legacy manifests can omit this field;
new module dependency changes in prompt-generated PRs must use an approved SHA.
`compile_installation` emits direct Python and npm Git references pinned to that
commit, in addition to package names, environment, secret-binding, and migration
inputs. The prompt workflow validates changed module references before running
product tests and regenerates package lockfiles itself; generated code cannot
edit dependency lockfiles.

The current registry marks only `gm-payments` v`0.1.1` installable. Other module
releases remain blocked until their metadata, formal release, and check evidence
meet the same contract. Existing version-tag references remain readable but
are not accepted as a new or changed dependency.

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
