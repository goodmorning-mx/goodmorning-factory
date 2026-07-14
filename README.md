# GoodMorning Factory

## Coolify-safe Compose baseline

This repository includes a Coolify-safe `compose.yaml` baseline for frontend, backend, and PostgreSQL services. The frontend baseline uses the default `nginx:1.27-alpine` image without a repository Dockerfile or nginx.conf override, so the real nginx listen port is container port `80`. The deployment file does not publish frontend or backend host ports; Coolify owns public ingress and routes traffic to container-only `expose` ports. Services communicate through Docker DNS and internal networks.

The baseline follows the Cards Coolify pattern for factory smoke-test provisioning: create a Docker Compose application from the GoodMorning GitHub App source, use branch `dev`, compose path `/compose.yaml`, domain `dev.factory-smoke-test.goodmorning.mx`, and keep `instant_deploy=false` so the initial provisioning step does not deploy automatically.

See `docs/coolify-compose.md` and `docs/coolify-development-provisioning.md` for the deployment and review checklists.
