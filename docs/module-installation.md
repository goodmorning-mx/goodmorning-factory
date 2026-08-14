# Installing GoodMorning modules

Modules are private versioned packages. A product installs, configures,
registers, and mounts them; it does not copy their implementation.

## Backend

Pin the module tags in the product backend image. Install dependencies first
when using the private Git repositories directly:

```bash
pip install \
  "gm-mcp @ git+ssh://git@github.com/goodmorning-mx/gm-mcp.git@v0.1.0" \
  "gm-billing @ git+ssh://git@github.com/goodmorning-mx/gm-billing.git@v0.1.0" \
  "gm-agent @ git+ssh://git@github.com/goodmorning-mx/gm-agent.git@v0.1.0"
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
`gm_agent.api.create_app(...)` in its FastAPI application. `OPENAI_API_KEY` is
provided through Coolify secrets; it is never committed to a manifest.

## Frontend

The React packages are exposed at repository root and can be installed from
their version tags:

```bash
npm install \
  "@goodmorning/gm-billing@git+ssh://git@github.com/goodmorning-mx/gm-billing.git#v0.1.1" \
  "@goodmorning/gm-agent@git+ssh://git@github.com/goodmorning-mx/gm-agent.git#v0.1.1"
```

Mount the generic UI from the product shell rather than reimplementing it:

```tsx
import { AgentChat, AgentLauncher } from '@goodmorning/gm-agent'
import { UsageSummaryCard } from '@goodmorning/gm-billing'
```

## Factory registration

Add a `modules` list to `products/<product>.json`. Factory validates package
names, semantic versions, module kind, dependencies, environment declarations,
migration mode/path, and compatibility before provisioning:

```json
{
  "modules": [
    {
      "name": "gm-agent",
      "version": "0.1.1",
      "kind": "full-stack",
      "backend_package": "gm-agent",
      "frontend_package": "@goodmorning/gm-agent",
      "python_requires": ["gm-mcp>=0.1.0", "gm-billing>=0.1.0"],
      "dependencies": ["gm-mcp", "gm-billing"],
      "env": [{"key": "OPENAI_API_KEY", "required": true, "secret": true}],
      "migrations": {"mode": "alembic", "path": "migrations", "minimum_schema": 1},
      "compatibility": {"python": ">=3.12", "node": ">=20", "factory_api": "^1.0", "products": ["*"]}
    }
  ]
}
```

Factory returns deterministic package lists and migration order through
`compile_installation`. Product migrations still run through the product's
normal migration service against its own PostgreSQL database.
