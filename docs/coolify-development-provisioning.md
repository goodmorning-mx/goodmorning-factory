# Coolify product development provisioning

This is the canonical GoodMorning Factory development provisioning architecture for new products.

## Canonical architecture

Each product development environment is modeled as independent Coolify resources:

1. A Coolify Project for the product.
2. The exact Project environment `dev`.
3. A private native PostgreSQL resource named `<product>-postgres-dev`.
4. A Backend Dockerfile Application named `<product>-api-dev`.
5. A Frontend Dockerfile Application named `<product>-frontend-dev`.

The source repository is accessed through the private GoodMorning GitHub App, uses branch `dev`, and uses clean public domains for public applications. Backend and Frontend are independent Dockerfile Applications in the target architecture; legacy Compose is not the source of new product deployments.

No stage of this provisioner uses SSH, rsync, SCP, remote Docker, or manual server file copying. `compose.yaml` remains useful for local development and CI validation only.

## Stage 2: discovery and read-only plan

Stage 2 discovers Coolify resources with GET requests only and builds the complete three-resource plan:

- `<product>-postgres-dev` native PostgreSQL resource;
- `<product>-api-dev` Backend Dockerfile Application using `/backend` and `/Dockerfile`;
- `<product>-frontend-dev` Frontend Dockerfile Application using `/frontend` and `/Dockerfile`.

The Stage 2 plan preserves exact Project, environment, server, and GitHub App selection by name; hydration GETs for candidate resources; scope classification; Application compatibility; legacy Compose detection; repository/domain/port normalization; and recursive redaction.

## Stage 3: controlled creation of primitives

Stage 3 extends the Stage 2 planner with tightly gated writes for only these resources:

- `POST /api/v1/projects` to create the exact product Project.
- `POST /api/v1/projects/{project_uuid}/environments` to create only environment `dev`.
- `POST /api/v1/databases/postgresql` to create a private native PostgreSQL record with `instant_deploy=false`.

Apply requires all gates before any POST:

- `--apply` must be selected instead of `--dry-run`.
- `COOLIFY_ALLOW_RESOURCE_WRITES=true` must be present.
- `--confirm-product` must exactly match `--product`.

Dry-run remains GET-only and never generates a real PostgreSQL password. Apply may generate a PostgreSQL password only in memory and only when the PostgreSQL record will actually be created. The JSON output reports only non-secret state such as resource names, UUIDs, action status, and `internal_db_url_available: true|false`.

Stage 3 still forbids:

- Backend Application creation;
- Frontend Application creation;
- Application variables;
- deployments;
- start, restart, or stop lifecycle calls;
- PATCH, PUT, and DELETE;
- public health checks or smoke tests;
- legacy Compose migration or deletion.

## PostgreSQL payload contract

The PostgreSQL payload builder is isolated in `scripts/provision_product_development.py`. It currently sends:

- `server_uuid`;
- `project_uuid`;
- `environment_name="dev"`;
- `name="<product>-postgres-dev"`;
- `image="postgres:16-alpine"`;
- `postgres_user`;
- `postgres_password`;
- `postgres_db`;
- `is_public=false`;
- `instant_deploy=false`.

The `image` field is used for the PostgreSQL version, matching the upstream Coolify API/CLI database creation contract for an image argument and the official Coolify database documentation that treats PostgreSQL as a native database resource. If upstream `coollabsio/coolify` route validation or generated OpenAPI changes this field, only the isolated builder should change.

Primary contract sources are official Coolify documentation and upstream Coolify repositories. Third-party mirrors are not schema authority.

## Stage 4: PostgreSQL readiness only

Stage 4 materializes and verifies only the native private PostgreSQL resource. Backend and Frontend Dockerfile Applications remain `planned_later`; Application creation, Application environment variables, deployments, database restart/stop, PATCH, PUT, DELETE, and legacy Compose migration remain blocked. No real deployment, real start, or real Coolify call is run by tests.

The upstream contract was confirmed from primary sources: the generated Coolify OpenAPI in `coollabsio/coolify` documents `POST /api/v1/databases/{uuid}/start` with a JSON message response and `GET /api/v1/databases/{uuid}` including fields such as `uuid`, `name`, `type`, `status`, `project_uuid`, `environment_uuid`, `server_uuid`, `is_public`, `public_port`, and `internal_db_url`; upstream `coollabsio/coolify` route/controller code uses the same database start route; official Coolify API docs are generated from that OpenAPI; and upstream `coollabsio/coolify-cli` exposes `coolify database start <uuid>` without a follow-up job identifier. Start therefore returns an acknowledgement/message, not a durable operation id that this provisioner can query.

Stage 4 adds exactly one write operation to the allowlist: `postgresql_start` for `POST /api/v1/databases/{uuid}/start`, where `{uuid}` is the exact UUID selected by discovery. It is never authorized by a broad `/databases/*` prefix. Requesting start requires all gates simultaneously:

- `--apply`;
- `COOLIFY_ALLOW_RESOURCE_WRITES=true`;
- `--confirm-product` exactly equal to `--product`;
- `COOLIFY_ALLOW_DATABASE_START=true`;
- `--confirm-database-start` exactly equal to `<product>-postgres-dev`.

Dry-run remains GET-only. It reports `would_start` only for an existing, compatible, correctly scoped PostgreSQL resource that is not healthy, and `already_healthy` for an explicitly healthy resource. It does not generate a new database secret for an existing PostgreSQL resource.

Readiness polling uses GET `/api/v1/databases/{uuid}` with configurable interval and timeout (`--postgres-readiness-interval`, default 5 seconds; `--postgres-readiness-timeout`, default 300 seconds). Tests inject fake clients and zero-second timings, so they never sleep or contact Coolify. State classification is exact and conservative: `queued` and `starting` are treated as starting; `running:healthy` and `healthy` are healthy; `exited` and `stopped` can be started; `running:unhealthy`, `unhealthy`, and `failed` are unhealthy. Ambiguous strings such as `nothealthy`, `unhealthy-running`, and `running-but-unverified` are `readiness_unverified`. If Coolify reports only `running` without a health marker, Stage 4 does not claim health.

A start POST with HTTP 5xx, timeout, URL error, malformed JSON, or any other situation where Coolify may have received the write but the outcome cannot be proven is marked `request_attempted=true`, `write_performed=false`, `write_outcome_unknown=true`, and `partial`. The provisioner does not retry, roll back, restart, stop, or delete; it recommends a later GET-only discovery run. For 409/422 conflicts, it uses only structured HTTP status, rediscoveries by exact UUID, and never parses message text or sends a second POST.

`internal_db_url` is secret. The provisioner never prints, logs, stores, writes, or returns it; output includes only `internal_db_url_available` and `database_url_driver_ready`. The future Backend `DATABASE_URL` is prepared only in memory by replacing an initial `postgresql://` or `postgres://` scheme with `postgresql+psycopg://` while preserving URL-encoded credentials, host, port, database name, query string, and SSL parameters. Unsupported or malformed schemes are rejected without echoing the input.

Upstream issue `coollabsio/coolify#9235` is tracked only as operational risk evidence that a database API request can be accepted without producing a usable container; it is not schema authority.

## Stage 5 and later

Future stages must handle Applications separately after PostgreSQL readiness is verified:

1. Create Backend and Frontend Applications.
2. Configure Application variables.
3. Deploy only after explicit deployment gates.
4. Fail before creating or deploying Backend if PostgreSQL is not healthy.

Stage 3 creates only the Coolify database record with `instant_deploy=false`; Stage 4 can request exactly one native PostgreSQL start after separate gates, but it still does not create Applications or deploy anything.

## Legacy Compose provisioning — deprecated for new products

The previous single Application Docker Compose provisioner remains in `scripts/provision_coolify_development.py` for legacy environments only. Its behavior is intentionally not changed in this PR.

Detailed legacy documentation was moved to `docs/legacy-coolify-compose-provisioning.md`. New products should use the canonical three-resource architecture described above.
