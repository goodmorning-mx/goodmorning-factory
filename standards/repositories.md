# Repository Standard

GoodMorning repositories should be private by default unless Abel explicitly decides the project should be public.

## Naming conventions

- Use lowercase, hyphen-separated repository names.
- Prefer product or service names that are clear outside the implementation team.
- Avoid names that expose private customer, credential, or infrastructure details.

## Expected files and folders

Product repositories should include, when applicable:

```text
README.md
AGENTS.md
CLAUDE.md
frontend/
backend/
docs/
.github/workflows/
.env.example
Dockerfile or service-specific Dockerfiles
compose.yaml or docker-compose.yaml
deployment and rollback documentation
```

## Required documentation

- `README.md` should explain the product purpose, local setup, test commands, deployment overview, and operational owner.
- `AGENTS.md` should contain project-specific agent instructions and must not weaken GoodMorning controls.
- `CLAUDE.md` should contain project-specific Claude guidance when interface, product, or UX conventions matter.
- `docs/` should contain architecture notes, deployment instructions, rollback instructions, and product decisions that affect future agents.
- `.env.example` should list required configuration keys with safe placeholder values only.

## Docker and compose files

- Each deployable service should have a Dockerfile or documented equivalent build path.
- Include a compose file when it improves local development, integration testing, or multi-service operation.
- Compose files must not contain real secrets.
