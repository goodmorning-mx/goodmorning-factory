# Coolify-safe Docker Compose

`compose.yaml` is the deployment default for Coolify. It must not publish frontend or backend ports to the Docker host. Coolify owns public ingress through its proxy, and service-to-service traffic should use Docker DNS names on internal Compose networks.

## Deployment default

- `frontend` listens on container port `80` and exposes only that port to the Compose network.
- `backend` listens on container port `8000` and exposes only that port to the Compose network.
- `postgres` stays on the private `data` network and is not exposed publicly.
- Host `ports` entries are intentionally forbidden in `compose.yaml`.
- `frontend` and `backend` define container health checks so Coolify can evaluate service readiness without host port publishing.

## Coolify application pattern

Factory smoke-test development provisioning should create a Docker Compose application from GitHub, not from local services or rsync:

- Source: GoodMorning Coolify GitHub App.
- Repository: `goodmorning-mx/factory-smoke-test`.
- Branch: `dev`.
- Compose path: `/compose.yaml`.
- Coolify environment name: `dev`.
- Frontend domain: `https://dev.factory-smoke-test.goodmorning.mx` routed to service `frontend`, port `80`.
- Backend domain: `https://api.dev.factory-smoke-test.goodmorning.mx` routed to service `backend`, port `8000`.
- Initial application payload: `instant_deploy=false`.

## Review checklist

1. `compose.yaml` has no `ports` key for `frontend` or `backend`.
2. Public HTTP routing is configured in Coolify domains, not host port mappings.
3. PostgreSQL remains reachable only from backend containers.
4. Frontend and backend health checks are present before provisioning.
5. Initial provisioning creates or reconciles resources only; deployment is queued later after manual review.
