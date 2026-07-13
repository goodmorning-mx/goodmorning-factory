# Security Standard

GoodMorning products must be secure by default, auditable, and conservative with production access.

## Core principles

- Use least privilege for people, agents, services, tokens, and databases.
- Store secrets only in approved secret managers or environment configuration systems.
- Never commit credentials, tokens, private keys, customer data, or production secrets.
- Never print secrets in logs, screenshots, pull request comments, or agent reports.
- Validate and sanitize untrusted input at trust boundaries.
- Require authentication for private product areas and administrative tools.
- Enforce authorization on the server side for protected actions and data.
- Review dependencies for known vulnerabilities and unnecessary privileges.

## Database safety

- Prefer additive, reversible migrations.
- Back up affected data before destructive operations.
- Require Abel's explicit approval before destructive migrations or data deletion.
- Document migration commands, expected impact, and rollback behavior.
- Avoid giving development, preview, or cloud agent environments access to production databases.

## Auditability

Changes should be traceable through GitHub pull requests, commits, checks, deployment records, and agent reports. Avoid out-of-band production changes. If an emergency requires manual action, document the action, reason, risk, and follow-up repository change.

## Explicit approval required

Abel's approval is required before:

- Destructive migrations.
- Data deletion.
- Production changes or production deployments.
- Credential rotation.
- Adding paid services or billable infrastructure.
- Disabling or weakening security controls.
- Granting broad access to production systems or secrets.
