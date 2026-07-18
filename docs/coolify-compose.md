# Canonical Coolify Docker Compose contract

Each GoodMorning product deploys as one private GitHub-backed Docker Compose
Application inside each Coolify Environment. Coolify reads `/compose.yaml`
directly from the configured branch; SSH, rsync, SCP, server-local files, and
manual dashboard configuration are outside the supported path.

## Required services

The canonical service names are `frontend`, `backend`, and `postgres`.

- `frontend` exposes container port `80` and has a healthcheck.
- `backend` exposes container port `8000`, serves `GET /health`, depends on the
  healthy PostgreSQL service, and uses `postgres:5432` through Compose DNS.
- `postgres` uses PostgreSQL 16, a named persistent volume, a healthcheck, no
  host `ports`, and no public domain.

Do not define custom Compose networks. Coolify creates one isolated network for
the stack and connects its reverse proxy to it. A service without a domain or
host port remains private inside that network.

## Domains

Factory assigns domains by service with `docker_compose_domains`:

- frontend: `https://dev.<product>.goodmorning.mx`;
- backend: `https://api.dev.<product>.goodmorning.mx:8000`.

The Backend `:8000` suffix is Coolify's documented internal container-port
routing hint. The public browser URL and smoke-check URL remain clean:
`https://api.dev.<product>.goodmorning.mx`.

## Variables and secrets

`compose.yaml` declares variables; Coolify stores environment-specific values.
Factory derives only non-secret configuration such as `APP_ENV`, `CORS_ORIGINS`,
and the public frontend API URL.

Internal secrets use Coolify magic variables directly in Compose. For example,
both Backend and PostgreSQL reference `SERVICE_PASSWORD_64_POSTGRES`, so Coolify
generates one stable random value per Compose Application without Factory ever
reading or storing it. `.env.example` contains safe local placeholders only.

## Provisioning lifecycle

The dev provisioner:

1. selects Project, Server, Destination, and GitHub App by exact identity;
2. creates the Project and `dev` Environment when absent;
3. creates `<product>-dev` with `build_pack=dockercompose`, branch `dev`, and
   `/compose.yaml`;
4. reconciles domains and public/non-secret variables;
5. queues the first authorized deployment, waits for completion, and runs smoke
   checks;
6. leaves native GitHub App Auto Deploy enabled for subsequent pushes.

The operation is idempotent and never deletes, stops, restarts, or recreates an
ambiguous existing resource.

## Local development

Production-safe `compose.yaml` must not publish host ports. Repositories may add
`compose.local.yaml` containing only local port mappings and run:

```bash
docker compose -f compose.yaml -f compose.local.yaml up --build --wait
```
