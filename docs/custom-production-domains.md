# Custom production domains

Production manifests may declare a custom apex and aliases for an existing
public application. For example, `production/inbrainwebpage.json` binds
`inbrainmedical.com` and `www.inbrainmedical.com` to the `frontend` application
in `production` with desired state `active`.

This MVP assumes DNS is managed by the customer and already points to the
Coolify resource server. It does not call GoDaddy, create or delete DNS records,
or store GoDaddy credentials. Live DNS validation confirms that public
resolvers return an address; without a reviewed server IP in the manifest, it
does not claim that the returned address matches the selected Coolify server.
DNS checks run only when live validation is explicitly requested.

## Coolify behavior

`scripts/reconcile_domains.py` updates only the existing application's
`domains` field through the Factory's existing Coolify client and documented
`PATCH /api/v1/applications/{uuid}` contract. Domain values are sent as the
comma-separated HTTPS URLs accepted by Coolify. It does not create an
application, force a domain conflict, change application source or environment,
or start a deployment. Coolify configures HTTPS for HTTPS domains; certificate
issuance and routing become healthy after Coolify applies the changed domain,
typically on the next deployment. See the [Coolify domain guide](https://coolify.io/docs/knowledge-base/domains)
and [application update API](https://coolify.io/docs/api/endpoints/applications/update-application-by-uuid).

The command defaults to `--phase plan`, performs only Coolify GET requests, and
does not contact the public host unless `--validate-live` is supplied. Use the
manual `Reconcile production custom domains` workflow on Factory branch `dev`:

```text
product: inbrainwebpage
phase: plan
validate_live: false
```

Review the structured report. For a later apply, select `phase: apply`, enter
`domain:inbrainwebpage:production`, and ensure the production environment has
`COOLIFY_ALLOW_ADVANCED_PRODUCTION_WRITES=true`. Apply patches only the declared
domains for an already discovered, production-scoped application. It never
deploys. `--validate-live` performs bounded, read-only DNS and HTTPS checks for
the custom domains and checks `/health` on the product's declared API. Reports
include hostnames and status only; response bodies and credentials are omitted.

## Runtime configuration and access

The workflow reads configuration from the existing GitHub Environment
`production`:

- `COOLIFY_BASE_URL` variable;
- `COOLIFY_DESTINATION_UUID` variable;
- `COOLIFY_SERVER_NAME` variable (defaults to `localhost`);
- `COOLIFY_API_TOKEN` secret, with read access to servers, projects,
  applications and GitHub Apps, plus application update access for apply;
- `COOLIFY_ALLOW_ADVANCED_PRODUCTION_WRITES=true` variable to authorize apply.

Do not put tokens in workflow inputs or manifests. The workflow requires the
exact product confirmation and `domain:<product>:production` confirmation for
apply. Configure the `production` environment's required reviewers according
to the existing production release controls.

## Validation, deployment, and rollback

`plan` checks identity and scope and reports `would_update` or `unchanged`.
After apply, the report says `ssl_pending` until live validation succeeds. If
DNS has not propagated, HTTPS is not ready, or any endpoint is unhealthy, the
report keeps that state visible and does not retry a Coolify write. DNS
propagation checks use capped exponential backoff only when `--validate-live`
is requested.

The regular reviewed production deployment remains responsible for deploying
the application and allowing Coolify to apply proxy and certificate changes.
No deployment is triggered by the domain workflow.

For rollback, restore the previous `domains` value in the Coolify application's
General settings and let the next normal deployment apply it. Revert the
manifest change as well so the next Factory reconciliation does not re-add the
custom domain. DNS records remain customer-managed and are never removed by
this tool.

This change has no database schema or binding/secret contract impact. Do not
run the production workflow as part of code review; validate behavior with the
mocked unit tests and workflow syntax checks.
