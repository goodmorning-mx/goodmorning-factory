# GoodMorning product repository provisioning

This document describes the first safe execution primitive for creating GoodMorning product repositories from the standard MVP template.

## What the provisioner does

The manual GitHub Actions workflow `.github/workflows/create-product.yml` calls `scripts/create_product.py` with a temporary GitHub App installation token. The CLI:

1. Validates the requested repository slug and allowed owner/template values locally.
2. Refuses to continue if the target repository already exists.
3. Verifies that `goodmorning-mx/goodmorning-mvp-template` is marked as a GitHub template repository.
4. Generates a new private repository with `include_all_branches=false`.
5. Waits for the generated repository and `main` branch to become available.
6. Creates `dev` from the `main` commit.
7. Sets `dev` as the default branch.
8. Applies the standard topics `goodmorning`, `goodmorning-product`, and `managed-by-goodmorning-factory`.
9. Verifies privacy, branches, default branch, and topics.
10. Prints a structured JSON result.

## What it intentionally does not do

The provisioner never deletes repositories, transfers repositories, archives repositories, changes visibility to public, overwrites existing repositories, modifies the MVP template repository, deploys services, provisions Coolify resources, or registers the product in a dashboard database. If repository creation succeeds but a later step fails, it reports partial provisioning and does not attempt destructive rollback.

## Required GitHub App permissions

The GoodMorning Software Factory GitHub App must be installed on `goodmorning-mx` with access to current and future repositories. It needs the minimum repository and organization permissions required to:

- Read `goodmorning-mx/goodmorning-mvp-template` and its template metadata.
- Generate repositories from the template in `goodmorning-mx`.
- Read repository metadata and branches.
- Write repository metadata, including default branch and topics.
- Write Git refs to create `refs/heads/dev`.

Do not broaden permissions beyond what these operations require.

## Required variable and secret

Configure these values on the factory repository before running the workflow:

- Repository variable `GOODMORNING_FACTORY_APP_ID`: the GitHub App ID.
- Repository secret `GOODMORNING_FACTORY_PRIVATE_KEY`: the GitHub App private key.

Never commit App IDs, installation IDs, tokens, private keys, or generated credentials to this repository.

## Manual trigger

Run **Create GoodMorning product repository** from GitHub Actions with:

```text
product_name: customer-portal
description: Customer portal MVP for GoodMorning
confirm_provision: true
```

`product_name` must use lowercase letters, numbers, and hyphens, start and end with a letter or number, be no longer than 100 characters, and not be `goodmorning-factory` or `goodmorning-mvp-template`.

## Expected JSON output

Successful runs print JSON similar to:

```json
{
  "status": "created",
  "owner": "goodmorning-mx",
  "repository": "customer-portal",
  "repository_url": "https://github.com/goodmorning-mx/customer-portal",
  "template": "goodmorning-mx/goodmorning-mvp-template",
  "main_branch": "main",
  "development_branch": "dev",
  "default_branch": "dev",
  "private": true,
  "topics": [
    "goodmorning",
    "goodmorning-product",
    "managed-by-goodmorning-factory"
  ]
}
```

If the repository already exists, the CLI exits non-zero with `status: already_exists` and refuses to modify it.

## Failure and recovery behavior

Validation failures happen before the workflow requests a GitHub App token. GitHub API failures for authentication, authorization, missing resources, conflicts, validation errors, timeouts, and malformed responses are reported as structured errors. If generation succeeds but branch creation, default branch update, topic update, or verification fails, the result status is `partial` and includes the known repository details.

Recovery should be manual and non-destructive: inspect the created private repository, correct missing branches/topics/default branch as needed, or rerun only after confirming the repository does not already exist. A second run for the same name intentionally stops without changing the existing repository.

## Future dashboard usage

The future GoodMorning dashboard should call this workflow through the GitHub Actions workflow dispatch API on `goodmorning-mx/goodmorning-factory`, passing `product_name`, `description`, and `confirm_provision: true`. The dashboard should store and display the workflow run ID, retrieve the run logs or summary for the JSON result, and then continue with the next product-registration step only after `status` is `created`.

## Security and credential rotation

The workflow uses `actions/create-github-app-token` to mint a short-lived installation token and passes it to the CLI only through `GH_TOKEN`. The CLI never accepts tokens as arguments and redacts the token from structured errors. Rotate the GitHub App private key through GitHub and update `GOODMORNING_FACTORY_PRIVATE_KEY` if the key is exposed or according to GoodMorning credential-rotation policy. Credential rotation requires Abel's approval under the GoodMorning security standard.

## Next step after repository creation

After the private repository exists with `dev` as the default branch, the factory should register the product in the future product registry and dispatch follow-up work, such as repository bootstrap checks, product-specific configuration, and implementation tasks. This provisioner intentionally stops before those steps so repository creation remains small, auditable, and reusable.
