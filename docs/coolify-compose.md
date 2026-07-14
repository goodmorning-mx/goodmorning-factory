# Coolify-safe Docker Compose

`compose.yaml` is the deployment default for Coolify. It must not publish frontend or backend ports to the Docker host. Coolify owns public ingress through its proxy, and service-to-service traffic should use Docker DNS names on internal Compose networks.

## Deployment default

- `frontend` reaches the API through `http://backend:8000` on the internal `app` network.
- `backend` reaches PostgreSQL through `postgres:5432` on the internal `data` network.
- `postgres` is attached only to the internal `data` network.
- `frontend` uses `expose: ["80"]` and `backend` uses `expose: ["8000"]`; these entries document container reachability without publishing host ports.
- Host `ports` entries are intentionally forbidden in `compose.yaml`.

## Local development

Use `compose.local.yaml` with the base file when a developer needs browser or API access from the workstation:

```bash
docker compose -f compose.yaml -f compose.local.yaml up
```

The local override publishes:

- `frontend` on `localhost:8080`, mapped to container port `80`
- `backend` on `localhost:8000`

Do not use `compose.local.yaml` in Coolify. If a product needs different local ports, change only the local override and keep `compose.yaml` private-by-default.

## Review checklist

Before merging Compose changes, verify that:

1. `compose.yaml` has no `ports` key for `frontend` or `backend`.
2. Public HTTP routing is configured in Coolify domains, not host port mappings.
3. PostgreSQL remains reachable only from backend containers and is not published by the local frontend/backend override.
4. `.env.example` uses safe placeholders only; no real database passwords are committed.
