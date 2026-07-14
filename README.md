# goodmorning-factory
Operating system and software factory standards for GoodMorning.

## Coolify-safe Compose baseline

This repository includes a Coolify-safe `compose.yaml` baseline for frontend, backend, and PostgreSQL services. The frontend baseline uses the default `nginx:1.27-alpine` image without a repository Dockerfile or nginx.conf override, so the real nginx listen port is container port `80`. The deployment file does not publish frontend or backend host ports; it keeps the current service images and application wiring while documenting container-only access with `expose` on frontend container port `80` and backend port `8000`. Services communicate through Docker DNS and internal networks.

Use `compose.local.yaml` only for local development when workstation port access is required:

```bash
docker compose -f compose.yaml -f compose.local.yaml up
```

The local override publishes `localhost:8080` to frontend container port `80` and `localhost:8000` for backend. Do not use the local override in Coolify. See `docs/coolify-compose.md` for the deployment and review checklist.
