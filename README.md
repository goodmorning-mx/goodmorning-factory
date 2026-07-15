# GoodMorning Factory

## Coolify-safe Compose baseline

This repository includes a Coolify-safe `compose.yaml` baseline for frontend, backend, and PostgreSQL services. The baseline deploys as one Docker Compose application from GitHub through the private GoodMorning Coolify GitHub App. The deployment file never publishes host `ports`; Coolify/Traefik owns public ingress and reaches services through container-only `expose` ports and explicit Traefik service-port labels.

The factory smoke-test pattern uses branch `dev`, compose path `/compose.yaml`, public domains `https://dev.factory-smoke-test.goodmorning.mx` and `https://api.dev.factory-smoke-test.goodmorning.mx`, `instant_deploy=false`, API-based reconciliation, and an explicit authorized Coolify API deployment step.

See `docs/coolify-compose.md` and `docs/coolify-development-provisioning.md` for the deployment and review checklists.
