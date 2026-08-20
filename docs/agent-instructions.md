# Agent instructions contract

GoodMorning uses two instruction levels so an agent can enter either context
without receiving an undocumented operational protocol from the user.

## Working on Factory

[`AGENTS.md`](../AGENTS.md) is the repository-level contract. It explains the
Factory mental model, Resources V3, DEV/PROD, provisioning, reconciliation,
promotion, modules, bindings, ownership, auto-deploy, healthchecks, backups,
security, approvals, validation, and reporting expectations. The standards in
`standards/` and the contracts in `docs/` remain authoritative for details.

## Working on a product

Factory generates `instructions.md` in the product repository from
`templates/product-instructions.md` and the product's catalog manifest. The
renderer is `scripts/product_instructions.py`; it is versioned with Factory.
The generated file describes the product's repository, topology, DEV/PROD
branches and domains, applications, databases, modules, deployment modes,
bindings, ownership, secrets policy, healthchecks, backups, validation and
Definition of Done.

The generated file has two explicit regions:

- `GOODMORNING FACTORY: MANAGED START/END` is replaced by Factory whenever the
  contract or manifest changes.
- `GOODMORNING PRODUCT: CUSTOM START/END` belongs to the product and is kept
  verbatim during reconciliation.

The renderer intentionally omits literal environment values and all sensitive
values. Binding names, variable names and resource names are operational
metadata, not secret values. A product must never add credentials, tokens,
private keys, database URLs, Coolify UUIDs or internal hostnames to the file.

## Lifecycle

- New repository registration reconciles `instructions.md` before the catalog
  registration result is returned.
- Existing products can be adopted with
  `scripts/reconcile_product_instructions.py --manifest products/<slug>.json`.
- `.github/workflows/reconcile-product-instructions.yml` reconciles the catalog
  on Factory `dev` changes and supports a targeted manual run.
- Updates are idempotent. A missing or legacy file is preserved inside the
  product-owned section rather than overwritten.

Instruction changes do not provision Coolify resources or deploy production.
They are a repository-contract update and remain subject to the normal GitHub
App permissions, CI checks, branch protections, and Factory approval rules.
