# Coolify resources-v3 product contract

`resources-v3` is the single declarative contract for every product, from a
simple API/frontend pair to products with gateways, workers, Redis, TimescaleDB,
multiple domains, and cross-application connections. `resources-v2` and
`resources-v2-production` are temporary compatibility inputs normalized by
`scripts/resources_v3_migration.py`.

## Safety model

- Development manifests accept only branch/environment `dev`; production
  manifests use the environment-specific `resources-v3-production` compatibility
  spelling and accept only
  branch `main`, environment `production`, `confirm_production=true`, and the
  reviewed `auto-on-main` promotion policy.
- Additive migration: a declared legacy Coolify application is retained and is
  never deleted, stopped, renamed, or rolled back by Factory. Products already
  running as `resources-v2` may omit `legacy` when no `legacy_env` source is
  requested; their exact native resource names are adopted in place.
- Exact scope: every resource name, project, environment, repository, branch,
  Dockerfile, port and domain is validated before a write.
- Private data services: PostgreSQL/TimescaleDB and Redis cannot declare public
  ports.
- Secrets are references, never manifest literals. Reviewed keys can be copied
  from the retained legacy application inside Coolify without appearing in
  Factory output.
- Development apply requires `COOLIFY_ALLOW_ADVANCED_WRITES=true` plus exact
  product and environment confirmations. Production has a separate
  `COOLIFY_ALLOW_ADVANCED_PRODUCTION_WRITES=true` gate, so the development
  permission cannot authorize a production write.
- Production dispatches are bound to the current product `main` SHA and a
  successful `CI` push run for that same SHA. The workflow takes and verifies an
  external database backup before and after deployment, then smoke-tests every
  declared public health endpoint.

## Resource model

One product may declare:

- exactly one API;
- zero or more desktop frontends and gateways;
- at most one lightweight mobile frontend;
- private workers;
- one or more private PostgreSQL 16 or TimescaleDB/PostgreSQL 16 databases;
- private persistent Redis 7.4 resources.

Each application is a native Coolify GitHub Application with its own Dockerfile,
health contract, deployment history and rollback boundary. Data services are
native Coolify database resources and receive external backup reconciliation.
Applications may declare typed `dependencies` on another application, database,
or Redis resource. Factory validates the graph and reconciles application
resources in dependency order; cycles and undeclared references fail closed.

## Frontend profiles

`frontend_profile` is independent from `gm-*` modules and defaults to
`react` when omitted. `react` composes `/frontend` on port 80, `next`
composes only `/seo` on port 3000, and `hybrid` composes private `/frontend`
and `/seo` applications plus the public `/gateway` application on port 80.
The API remains `api.{slug}.goodmorning.mx`; the gateway owns
`{slug}.goodmorning.mx`; no `app` subdomain is generated.

The hybrid routing metadata identifies the gateway as the public entrypoint.
Its declared routes are `/` and `/seo` to Next, `/app` to React, and `/api` to
the API. Gateway upstreams are typed application connections and reconcile to
internal resource URLs; they do not expose private application domains.

## Environment sources

Application variables support only:

- `literal`: reviewed non-secret configuration;
- `database_url`: the selected Coolify database's private URL;
- `redis_url`: the selected Coolify Redis private URL;
- `legacy_env`: an allowlisted value copied from the retained legacy resource.
- `generated_secret`: a new high-entropy value created inside reconciliation,
  never stored in Git or output, and preserved on later runs. Reconciliation
  fails closed instead of rotating it when Coolify cannot return the real value.
- `service_url`: the declared public URL of another application in the same
  product, resolved by application name rather than a hardcoded hostname.
- `managed_secret`: a reference to a Factory-managed binding such as
  `goodmorning/openai-default`; Factory maps that binding to its configured
  runtime secret and supplies the value only during reconciliation. It is never
  stored in the manifest or logs.

Database URL variables may request `"driver": "psycopg"` to convert only the
URL scheme to SQLAlchemy's `postgresql+psycopg://` form. Applications may also
declare an exact `base_directory`, allowing v3 to adopt an existing v2
Dockerfile Application without replacing it.

Top-level `connections` compile database, Redis, and application sources into
`database_url`, `redis_url`, and `service_url` environment entries. Coolify UUIDs
and private URLs are discovered during reconciliation; manifests contain no
hostnames or credentials.

The reconciler outputs variable names only. Values matching credential, token,
password, key, database URL or Redis URL contracts are redacted.

On a control-plane implementation change, CI plans the full `resources-v3`
catalog but applies and deploys only manifests changed in that push. A manual
workflow dispatch may explicitly select one manifest. This keeps an unrelated
advanced product from being redeployed as a side effect of Factory maintenance.

Production reconciliation is intentionally separate. Factory checks out its
reviewed `dev` control plane, validates the exact `production/<product>.json`
manifest, verifies the product repository's `main` commit and CI run, and then
uses the production-only write gate. Production manifests cannot import a legacy
application environment.

## Migration sequence

1. Merge and validate the product's `dev` runtime contract.
2. Add its reviewed `resources-v3` catalog manifest. For a `resources-v2`
   adoption, run `resources_v3_migration.py` and review the generated manifest;
   reuse exact names, source repository, and supplied UUIDs while declaring only
   additive resources or environment keys.
3. Factory plans the complete topology using read-only Coolify requests.
4. Factory creates missing private data services and independent applications.
5. Factory transfers allowlisted runtime configuration internally.
6. Factory deploys the new resources and verifies external database backups.
7. Smoke-test every public domain and the live-data path.
8. Move traffic only after data migration and rollback verification.
9. Keep the legacy resource until the documented retention window ends.

Legacy data is never deleted automatically. A product-specific export/restore
runbook is required when an existing database or volume is non-empty.
