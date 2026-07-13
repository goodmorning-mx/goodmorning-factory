# Branching Standard

## Branch roles

- `main` represents production.
- `dev` represents integrated development.
- Direct changes to production are not allowed.
- Direct pushes to `main` are prohibited.

## Branch naming

Use descriptive branch names:

- `feature/*` for new product or platform capabilities.
- `fix/*` for bug fixes.
- `chore/*` for maintenance, documentation, tooling, or dependency work.
- `release/*` for release preparation from `dev` to `main`.
- `codex/*` for Codex-authored work when a more specific prefix is not useful.

## Pull request flow

- Start normal work from `dev`.
- Open normal pull requests toward `dev`.
- Release production by opening a release pull request from `dev` to `main`.
- Do not bypass pull requests for production changes.

## Commit expectations

- Prefer small, reviewable commits.
- Keep commits focused on one logical change.
- Use clear commit messages that describe the user or operational impact.
- Do not mix unrelated refactors, features, and deployment changes in one commit.

## Automated merge eligibility

Low-risk changes may be eligible for automated merge into an integration branch after required checks pass. Examples include documentation-only changes, isolated test updates, formatting-only changes, or small non-production fixes.

Automated merge eligibility does not imply production release approval. Production releases still follow the `dev` to `main` release pull request flow.

## Merge blocking conditions

Do not merge when any of the following apply:

- Required checks fail or were skipped without explanation.
- The change contains real secrets or unsafe configuration.
- The change includes unapproved destructive migrations, data deletion, credential rotation, new paid services, or production changes.
- Security controls are weakened without explicit approval.
- Rollback instructions are missing for deployment-impacting changes.
- The pull request has unresolved review comments or unclear ownership.
