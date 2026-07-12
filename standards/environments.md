# Environment Standard

GoodMorning products should separate development, preview, and production environments. No production secret or production database should be used in development or preview environments.

## Environment types

### Local development

Used by Abel and local agents for implementation and debugging. Local development should use local or disposable services and safe `.env` values based on `.env.example`.

### Cloud agent environments

Used by Codex Cloud, Claude Code on the web, and future cloud workers. These environments should receive only the minimum secrets required for the assigned task and should not receive production credentials unless Abel explicitly approves a production operation.

### `dev`

The integrated development environment tracks the `dev` branch. It is used to validate combined work before release.

### Preview environments

Preview environments may be created for pull requests or short-lived validation. They should be isolated, disposable, and clearly non-production.

### Production

Production tracks the `main` branch. Production changes require the release pull request flow and Abel's approval when required by the autonomy and security standards.

## Configuration and secrets

- Separate secrets by environment.
- Separate databases by environment.
- Use environment-specific configuration rather than hard-coded values.
- Do not copy production secrets into local, cloud agent, `dev`, or preview environments.
- Keep `.env.example` current without real credentials.

## Promotion behavior

When practical, promote a validated image or artifact between environments rather than rebuilding different artifacts for each environment. If promotion is not practical, document the build and release process so differences between environments are visible.
