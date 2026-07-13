# GoodMorning Agent Operating Standard

This repository, `goodmorning-mx/goodmorning-factory`, is the central operating standard for GoodMorning. It defines shared engineering standards, agent instructions, product templates, deployment conventions, and future factory automation for every GoodMorning product.

## Mandatory workflow

- Inspect the applicable files in `standards/` before changing any product or factory repository.
- Treat `main` as production and `dev` as integrated development.
- Create normal work branches from `dev` using the naming rules in `standards/branching.md`.
- Open normal pull requests toward `dev`.
- Open release pull requests from `dev` to `main`.
- Do not push directly to `main`.
- Keep changes small, reviewable, and documented.
- Do not claim automation, integrations, tests, or deployments exist unless they have been verified.

## Required validation

When applicable to the repository and change, run and report:

- Formatting checks.
- Linting.
- Type checking.
- Unit tests.
- Integration tests.
- Production builds.
- Docker builds.
- Service health checks.
- Post-deployment smoke checks.

If a check cannot be run, explain why and describe the risk.

## Secrets and configuration

- Maintain a `.env.example` with safe placeholder names only.
- Never commit real secrets, credentials, tokens, private keys, customer data, or production environment values.
- Do not print secrets in logs, pull request comments, or agent reports.

## Approval required

Get Abel's explicit approval before performing or requesting:

- Destructive database migrations.
- Data deletion.
- Production changes or production deployments.
- Credential rotation.
- New paid services or billable infrastructure.
- Disabling or weakening security controls.

## Agent report requirements

Every completed change must report:

- Modified files.
- Tests and checks run, including failures or skipped checks.
- Known risks.
- Database migrations or schema impact.
- Secret or configuration impact.
- Deployment impact.
- Rollback instructions.

## Project-level instructions

Product repositories may include their own `AGENTS.md` files. Project-level instructions may add stricter rules or product-specific procedures, but they must not weaken GoodMorning security, branching, review, approval, or production controls defined here and in `standards/`.
