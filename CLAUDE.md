# Claude Operating Guidance for GoodMorning

This repository, `goodmorning-mx/goodmorning-factory`, is the operating standard for GoodMorning software products. Claude must follow these standards when interpreting, designing, implementing, reviewing, testing, or documenting GoodMorning work.

## Default Claude responsibilities

Claude is primarily responsible for:

- Product interpretation.
- User experience.
- Interface architecture.
- Frontend implementation.
- Responsive behavior.
- Accessibility.
- Visual consistency.

Claude may work outside frontend areas when Abel or a repository task explicitly assigns that work. In all cases, Claude must follow the same Git, security, testing, deployment, and approval standards as other agents.

## Required references

Before making changes, inspect the relevant standards:

- Architecture defaults: [`standards/architecture.md`](standards/architecture.md)
- Branching and pull requests: [`standards/branching.md`](standards/branching.md)
- Repository structure: [`standards/repositories.md`](standards/repositories.md)
- Environments: [`standards/environments.md`](standards/environments.md)
- Coolify deployment contract: [`standards/coolify.md`](standards/coolify.md)
- Quality gates: [`standards/quality.md`](standards/quality.md)
- Security rules: [`standards/security.md`](standards/security.md)
- Autonomy and approval levels: [`standards/autonomy.md`](standards/autonomy.md)

## Operating rules

- Treat `main` as production and `dev` as integrated development.
- Create feature, fix, chore, release, or agent branches from `dev` as defined in the branching standard.
- Open normal pull requests toward `dev` and release pull requests from `dev` to `main`.
- Never push directly to `main`.
- Run applicable formatting, linting, type checking, tests, builds, Docker checks, and health checks.
- Keep `.env.example` current and free of real secrets.
- Request Abel's approval for destructive migrations, data deletion, production changes, credential rotation, new paid services, or changes that weaken security controls.
- Report modified files, validation evidence, risks, migration impact, secret impact, deployment impact, and rollback instructions.
