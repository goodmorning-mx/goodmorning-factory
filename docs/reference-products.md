# Reference products

## Ballet Pour Tous

`balletpourtous` is the canonical simple GoodMorning product. Its DEV and
production manifests use the Resources V3 contract and describe only:

- one `frontend` application;
- one `api` application;
- one private PostgreSQL database.

The product installs `gm-auth`, `gm-agent`, `gm-mcp`, `gm-billing`, and
`gm-pwa` as modules. They remain embedded by default; their optional service
and gateway capabilities are declared in the module contract but do not add
resources to Ballet's topology. `gm-auth` contributes the shared PostgreSQL
requirement and Factory compiles its `DATABASE_URL` binding into the API.

The migration descriptor is `adopt-in-place` and retains the existing
Coolify resources, UUIDs, domains, volumes, secrets, configuration, and
database. It is declarative metadata only: Factory never deletes or replaces
an existing resource during this migration.

A new simple product can follow the same shape by declaring `frontend`, `api`,
`postgres`, and its modules in a Resources V3 manifest. No Ballet-specific
Factory branch or provisioning logic is required.
