# DEV product cloning

Factory's **Clone GoodMorning product in DEV** workflow is the single entry
point for cloning an existing product. It accepts `source_product`, a unique
`destination_slug`, a description, and `confirm_dev_clone=true`.

The operation is source-code-only. It creates a new private GitHub repository,
copies the `main` and `dev` Git branches, rewrites product identity/domain
references, and creates a derived `resources-v3` manifest in a Factory branch.
The workflow opens a PR against `dev`; it does not change `main` or production.

The derived manifest preserves the source frontend profile, modules, versions,
dependencies, and managed-secret binding names. It replaces the product slug,
repository, DEV domains, and backup storage setting. It refuses source
manifests with legacy/migration descriptors, missing managed secrets, or known
data paths (`*.sql`, dumps, SQLite files, `uploads/`, or `data/`).

No PostgreSQL database, volume, user, order, upload, infrastructure UUID,
production domain, or secret value is copied. After the manifest PR is merged,
the existing resources-v3 DEV workflow provisions a new project and an empty
PostgreSQL database. The normal CI and health checks must pass before using the
new DEV URL.

Required Factory configuration is the existing GitHub App ID/private key,
`COOLIFY_BACKUP_STORAGE_UUID`, and the managed secret names used by the source
manifest. Values are supplied through GitHub Actions secrets/variables only.

For safe cleanup, close the clone PR if it is no longer wanted, then remove
only the clone's DEV Coolify project/resources and archive the clone repository
through the normal review process. Never delete the source product or reuse its
database/volumes. Re-running with an existing destination intentionally stops.
