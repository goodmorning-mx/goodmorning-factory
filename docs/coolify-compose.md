# Coolify-safe Docker Compose

`compose.yaml` is the deployment default for Coolify development environments. It is a single Docker Compose application with exactly these services: `frontend`, `backend`, and `postgres`. The file is consumed from GitHub by Coolify; no SSH, rsync, SCP, local server services, or manual file copy is part of the supported flow.

## Routing mechanism

Coolify's current Docker Compose documentation says the compose file is the source of truth and that domain assignment normally routes to container port `80`; for non-80 services, Coolify's domain field can include a port to tell the proxy which container port to target. The current OpenAPI schema exposes `docker_compose_domains[].name` and `docker_compose_domains[].domain`, but no separate per-service port field for Docker Compose domain routing.

Because GoodMorning public URLs must never contain `:80` or `:8000`, this repository keeps `docker_compose_domains` as clean public URLs and uses explicit Traefik service-port labels in `compose.yaml` for internal routing:

- `frontend` public domain: `https://dev.<product>.goodmorning.mx`.
- `frontend` internal target: service `frontend`, port `80`, label `traefik.http.services.frontend.loadbalancer.server.port=80`.
- `backend` public domain: `https://api.dev.<product>.goodmorning.mx`.
- `backend` internal target: service `backend`, port `8000`, label `traefik.http.services.backend.loadbalancer.server.port=8000`.

The `domain:port` form is therefore treated only as a Coolify internal routing hint documented by Coolify, not as a public URL format that GoodMorning exposes in logs, smoke checks, docs, or result JSON.

## Services

### frontend

- Exact service name: `frontend`.
- Image: `nginx:1.27-alpine` by default.
- Real Nginx config: `frontend/nginx.conf`.
- Listens on `0.0.0.0:80` / `[::]:80` through Nginx.
- `expose: ["80"]`.
- No `ports:`.
- Healthcheck uses `http://127.0.0.1/`.
- No unused `BACKEND_URL`; stock Nginx does not consume it.
- Network: `app` only.

### backend

- Exact service name: `backend`.
- Listens on `0.0.0.0:8000`.
- `expose: ["8000"]`.
- No `ports:`.
- Real `GET /health` endpoint returns HTTP 200.
- Healthcheck uses `http://127.0.0.1:8000/health`.
- Connects to PostgreSQL through Compose DNS at `postgres:5432`.
- Networks: `app` and `data`.
- Depends on PostgreSQL with `condition: service_healthy`.

### postgres

- Exact service name: `postgres`.
- Image: `postgres:16-alpine`.
- Persistent volume mounted at `/var/lib/postgresql/data`.
- `expose: ["5432"]` for internal Compose connectivity only.
- No `ports:` and no public domain.
- Network: `data` only.
- `data` is `internal: true`.
- Healthcheck uses `pg_isready`.
- Credentials are read from Coolify-provided environment variables; no real password is committed.

## expose vs ports

- `expose` documents and enables container-network access only. It does not publish the container port on the Docker host.
- `ports` publishes a host port and bypasses the intended Coolify/Traefik ingress path. Host `ports` are forbidden in this deployment baseline.

## Review checklist

1. `compose.yaml` has no `ports` key for any service.
2. Public domains are configured through Coolify domains and contain no ports.
3. Traefik service labels identify `frontend:80` and `backend:8000` for internal routing.
4. PostgreSQL remains reachable only from backend containers over the internal `data` network.
5. Frontend, backend, and PostgreSQL health checks are present before provisioning.
6. Provisioning keeps `instant_deploy=false`; deployment is queued only by the explicit API deployment step after reconciliation and authorization.
