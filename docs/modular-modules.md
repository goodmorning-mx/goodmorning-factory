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
