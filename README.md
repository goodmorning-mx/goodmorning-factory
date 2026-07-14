# goodmorning-factory
Operating system and software factory standards for GoodMorning.

## Coolify-safe Compose baseline

This repository includes a Coolify-safe `compose.yaml` baseline for frontend, backend, and PostgreSQL services. The deployment file does not publish host ports; services communicate through Docker DNS and internal networks. Use `compose.local.yaml` only for local development when workstation port access is required. See `docs/coolify-compose.md` for the deployment and review checklist.
