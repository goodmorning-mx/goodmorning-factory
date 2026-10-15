# Installing GoodMorning modules

Modules are versioned GitHub packages. A product installs, configures,
registers, and mounts them; it does not copy their implementation.

Factory maintains reviewed release metadata in `modules/registry.json`. Every
new or updated GoodMorning module dependency must match an approved formal
release and a full source commit SHA. A mutable branch or semantic tag is not
an installation pin. Currently only `gm-payments` v`0.1.1` is installable.

## Backend

Install the approved module source by its exact commit:

```bash
pip install \
  "gm-payments @ git+https://github.com/goodmorning-mx/gm-payments.git@77502f1d732f9c33bb145c90d2af13ec076be0d6"
```

Products create their own service adapters and register them with `gm-mcp`:

```python
from gm_mcp import ToolRegistry, tool

registry = ToolRegistry()

@tool(name="students.find", description="Find students", input_schema={"type": "object"})
async def find_students(*, context, query):
    # The adapter calls the product service; it never queries PostgreSQL here.
    return await student_service.search(context.organization_id, query)

registry.register(find_students)
```

The product constructs `AgentService(provider, registry, billing)` and mounts
`gm_agent.api.create_app(...)` in its FastAPI application. The module declares
`OPENAI_API_KEY` through a `GOODMORNING_MANAGED` binding; Factory resolves the
central value and reconciles it as a runtime-only Coolify backend secret. The
value is never committed to a manifest, package, frontend, or deployment log.

## Frontend

No frontend module release is currently approved for installation. When a
frontend module is approved, use the exact source SHA recorded in the Factory
registry rather than a version tag:

```bash
npm install "@goodmorning/gm-example@git+https://github.com/goodmorning-mx/gm-example.git#<approved-40-character-sha>"
```

Mount the generic UI from the product shell rather than reimplementing it:

```tsx
import { AgentChat, AgentLauncher } from '@goodmorning/gm-agent'
import { UsageSummaryCard } from '@goodmorning/gm-billing'
```

## Factory registration

Add a `modules` list to `products/<product>.json`. Factory validates package
names, semantic versions, module kind, dependencies, environment declarations,
migration mode/path, compatibility, and any declared source SHA against the
release registry before provisioning:

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

Factory returns deterministic package lists, exact Git installation references,
secret bindings, and migration order through `compile_installation`. Product
migrations still run through the product's normal migration service against its
own PostgreSQL database.

Modules may additionally declare `deployment_modes`, `resource_requirements`,
and `bindings`. Keep `embedded` as the default for libraries and simple cores;
select `service`, `worker`, or `gateway` only when the product explicitly needs
that Resources V3 application. Factory composes the selected requirements and
reconciles shared resources before a controlled DEV deployment. Frontend
targets can never receive a secret binding.
