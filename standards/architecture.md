# Architecture Standard

This document defines the default GoodMorning MVP architecture. Projects may override these defaults only when the reason is documented in the repository README, architecture notes, or pull request.

## Default repository layout

GoodMorning products should start with:

```text
/frontend
/backend
/docs
```

- `/frontend` contains the user interface.
- `/backend` contains the API, workers, and server-side application code.
- `/docs` contains product, architecture, deployment, and rollback documentation.

## Default technology stack

- Frontend: React, TypeScript, and Vite.
- Backend: FastAPI and Python 3.12.
- Relational database: PostgreSQL.
- Cache, queues, realtime coordination, or worker coordination: Redis only when the product requirement justifies it.
- Runtime packaging: Dockerized services.

## Service boundaries

- Independent products should use separate services and separate databases.
- Avoid sharing databases across independent products.
- Keep internal service communication private when public exposure is unnecessary.
- Expose public APIs only when the product requires external access.

## Health checks

Every service must provide a health check that can be used by local development, CI, Coolify, and post-deployment smoke testing. A health check should verify the service is running and, when appropriate, that required dependencies are reachable.

## Overrides

A project may use a different framework, language, database, or hosting pattern when the decision is documented with:

- The reason for the override.
- Operational impact.
- Security impact.
- Testing and deployment impact.
- Rollback considerations.
