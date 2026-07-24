# Coolify resources-v3 advanced product contract

`resources-v3` extends Factory for products that need more than one public
application, private workers, Redis, or a reviewed PostgreSQL-compatible image.
It does not replace `resources-v2`; simple products retain the smaller canonical
contract.

## Safety model

- Development only: the contract accepts branch and environment `dev`.
- Additive migration: the legacy Coolify application is retained and is never
  deleted, stopped, renamed, or rolled back by Factory.
- Exact scope: every resource name, project, environment, repository, branch,
  Dockerfile, port and domain is validated before a write.
- Private data services: PostgreSQL/TimescaleDB and Redis cannot declare public
  ports.
- Secrets are references, never manifest literals. Reviewed keys can be copied
  from the retained legacy application inside Coolify without appearing in
  Factory output.
- Apply requires both `COOLIFY_ALLOW_ADVANCED_WRITES=true` and an exact product
  confirmation.
- Production remains outside this workflow and requires Abel's approval.

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

The reconciler outputs variable names only. Values matching credential, token,
password, key, database URL or Redis URL contracts are redacted.

## Migration sequence

1. Merge and validate the product's `dev` runtime contract.
2. Add its reviewed `resources-v3` catalog manifest.
3. Factory plans the complete topology using read-only Coolify requests.
4. Factory creates missing private data services and independent applications.
5. Factory transfers allowlisted runtime configuration internally.
6. Factory deploys the new resources and verifies external database backups.
7. Smoke-test every public domain and the live-data path.
8. Move traffic only after data migration and rollback verification.
9. Keep the legacy resource until the documented retention window ends.

Legacy data is never deleted automatically. A product-specific export/restore
runbook is required when an existing database or volume is non-empty.
