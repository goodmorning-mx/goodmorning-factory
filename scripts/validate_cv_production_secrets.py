#!/usr/bin/env python3
"""Fail closed unless CV production has distinct live Stripe credentials."""

from __future__ import annotations

import os


def validate_cv_production_secrets(secret_key: str | None, webhook_secret: str | None) -> None:
    if not secret_key or not webhook_secret:
        raise ValueError(
            "CV production requires dedicated Stripe live secrets; development credentials are not a fallback."
        )
    if not secret_key.startswith(("sk_live_", "rk_live_")):
        raise ValueError("CV production Stripe key must use live mode; deployment was not started.")
    if not webhook_secret.startswith("whsec_"):
        raise ValueError("CV production webhook secret is invalid; deployment was not started.")


def main() -> None:
    validate_cv_production_secrets(
        os.environ.get("GOODMORNING_MANAGED_CV_PRODUCTION_STRIPE_SECRET_KEY"),
        os.environ.get("GOODMORNING_MANAGED_CV_PRODUCTION_STRIPE_WEBHOOK_SECRET"),
    )
    print("CV production Stripe secrets are present and use live-mode prefixes.")


if __name__ == "__main__":
    main()
