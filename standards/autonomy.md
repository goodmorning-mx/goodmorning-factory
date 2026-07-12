# Autonomy Standard

This matrix defines what agents may do without approval and what requires Abel's approval. Project-level instructions may be stricter but must not be weaker.

## Autonomy levels

- **Automatic**: Agent may do this as part of assigned work.
- **Automatic after checks**: Agent may do this only after applicable checks pass and evidence is reported.
- **Requires Abel's approval**: Agent must receive explicit approval before proceeding.
- **Prohibited**: Agent must not do this.

## Matrix

| Action | Autonomy level | Notes |
| --- | --- | --- |
| Creating branches | Automatic | Branch from `dev` for normal work. |
| Editing code | Automatic | Stay within assigned scope and applicable standards. |
| Opening PRs | Automatic | Normal PRs target `dev`; release PRs target `main`. |
| Merging low-risk work into an integration branch | Automatic after checks | Applies to low-risk changes into `dev` when required checks pass and no blocking condition exists. |
| Deploying previews | Automatic after checks | Preview must be isolated and non-production. |
| Merging `dev` into `main` | Requires Abel's approval | Use a release pull request. |
| Production deployments | Requires Abel's approval | Production tracks `main`. |
| Non-destructive migrations | Automatic after checks | Must be documented and validated. |
| Destructive migrations | Requires Abel's approval | Requires backup and rollback plan. |
| Rotating credentials | Requires Abel's approval | Coordinate timing and affected services. |
| Adding paid services | Requires Abel's approval | Includes new billable infrastructure or subscriptions. |
| Deleting data | Requires Abel's approval | Requires scope, backup, and recovery plan. |
| Deleting repositories | Requires Abel's approval | Confirm ownership, backups, and downstream impact. |
| Disabling security controls | Prohibited | If a temporary exception is necessary, Abel must approve a documented alternative control. |
