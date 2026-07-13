# Quality Standard

Every GoodMorning pull request should provide evidence for the checks that apply to the changed repository. If a check does not apply or cannot be run, explain why.

## Minimum quality gates

- Formatting.
- Linting.
- Type checking.
- Unit tests.
- Integration tests when applicable.
- Production build.
- Docker build for deployable services.
- Health check for running services.
- Smoke test after deployment.
- Dependency scanning.
- Secret scanning.

## Pull request evidence

Pull requests should include:

- Commands run and results.
- Checks not run and the reason.
- Manual testing notes when user experience changed.
- Screenshots or recordings for visible interface changes when practical.
- Database migration impact.
- Configuration and secret impact.
- Deployment impact.
- Rollback instructions for deployment-impacting changes.

## Failure handling

Do not merge when required checks fail unless Abel explicitly accepts the risk. If a failure is caused by an environment limitation, document the limitation, expected command, and remaining risk.
