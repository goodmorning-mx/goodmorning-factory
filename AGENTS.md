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

## Factory mental model

GoodMorning Factory is the control plane for reviewed GoodMorning products. It
does not replace the product application: it validates declarative manifests,
provisions and reconciles Resources V3, and coordinates safe DEV/PROD
promotion through GitHub and Coolify.

- Resources V3 models one isolated Coolify Project per product. DEV tracks
  `dev`; PROD tracks `main` and is promoted only through the release contract.
- Provisioning and reconciliation are convergent and ownership-aware. Factory
  discovers existing resources, verifies identity and compatibility, and fails
  closed on ambiguity instead of creating duplicates or deleting data.
- Applications, native PostgreSQL, optional Redis, workers and gateways are
  described by product manifests. Bindings connect runtime configuration to
  databases, services, generated secrets, or managed secrets without placing
  secret values in Git.
- Modules can run as `embedded`, `service`, `worker`, or `gateway`. Their
  dependencies, resource requirements, selected deployment mode, ownership,
  migrations, healthchecks, and auto-deploy behavior are part of the reviewed
  module contract.
- Factory owns resource discovery, Project/Environment creation, application
  and database reconciliation, health/readiness verification, auto-deploy
  configuration, external backups, and promotion gates. Product repositories
  own application code, migrations, runtime health endpoints, and product
  behavior.

Important contracts live in `standards/`, `docs/coolify-resources-v3.md`,
`docs/production-release-contract.md`, `docs/modular-modules.md`,
`scripts/advanced_product_contract.py`, `scripts/module_contract.py`, and the
reconciliation/provisioning scripts. Read the relevant contract and tests
before changing them. Changes to ownership, bindings, managed secrets,
migrations, healthchecks, auto-deploy, backup policy, or compatibility adapters
can affect every provisioned product and both environments.

## Agent instructions contract

This repository is the source of truth for two instruction levels:

1. This `AGENTS.md` explains how to work on Factory itself.
2. `templates/product-instructions.md` plus
   `scripts/product_instructions.py` define the Factory-managed section of
   `instructions.md` in each product repository.

Product instructions are generated from the catalog manifest, never maintained
manually repo by repo. The managed section is delimited by explicit markers;
the product-owned custom section is preserved during updates. The generator
must render only non-sensitive metadata: never add secret values, credentials,
tokens, private keys, database URLs, Coolify UUIDs, or internal hostnames.

New product registration reconciles `instructions.md` automatically. Existing
products can be adopted with `scripts/reconcile_product_instructions.py` or
the corresponding reconciliation workflow. Updates are idempotent and replace
only the managed section. A product-specific extension belongs between the
custom markers and must not weaken Factory controls.

## End-to-end goal rule

For every assigned goal, inspect the real repository and catalog state first,
trace the change through provisioning/reconciliation and CI, implement the
smallest compatible change, run the relevant tests, and validate the real
DEV/PROD contract as far as authorization permits. Do not stop at generated
code when the repository owns the integration needed to make the behavior
operable. Production changes, destructive operations, credential rotation,
and other actions listed in `standards/autonomy.md` still require approval.
