# Coolify resources-v3 advanced product contract

`resources-v3` extends Factory for products that need more than one public
application, private workers, Redis, or a reviewed PostgreSQL-compatible image.
It does not replace `resources-v2`; simple products retain the smaller canonical
contract.

## Safety model

- Development manifests accept only branch/environment `dev`; production
  manifests use the distinct `resources-v3-production` topology and accept only
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

## Environment sources

Application variables support only:

- `literal`: reviewed non-secret configuration;
- `database_url`: the selected Coolify database's private URL;
- `redis_url`: the selected Coolify Redis private URL;
- `legacy_env`: an allowlisted value copied from the retained legacy resource.
- `generated_secret`: a new high-entropy value created inside reconciliation,
  never stored in Git or output, and preserved on later runs. Reconciliation
  fails closed instead of rotating it when Coolify cannot return the real value.

Database URL variables may request `"driver": "psycopg"` to convert only the
URL scheme to SQLAlchemy's `postgresql+psycopg://` form. Applications may also
declare an exact `base_directory`, allowing `resources-v3` to adopt an existing
`resources-v2` Dockerfile Application without replacing it.

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
   adoption, reuse the exact API, Frontend and Database names and declare only
   additive applications or environment keys.
3. Factory plans the complete topology using read-only Coolify requests.
4. Factory creates missing private data services and independent applications.
5. Factory transfers allowlisted runtime configuration internally.
6. Factory deploys the new resources and verifies external database backups.
7. Smoke-test every public domain and the live-data path.
8. Move traffic only after data migration and rollback verification.
9. Keep the legacy resource until the documented retention window ends.

Legacy data is never deleted automatically. A product-specific export/restore
runbook is required when an existing database or volume is non-empty.
