#!/usr/bin/env python3
"""Fail closed unless CV uses credentials matching its explicit payment mode."""

from __future__ import annotations

import os
import argparse


def validate_cv_production_secrets(
    secret_key: str | None,
    webhook_secret: str | None,
    payment_mode: str = "live",
) -> None:
    mode = payment_mode.lower()
    if mode not in {"demo", "live"}:
        raise ValueError("CV production PAYMENT_MODE must be either 'demo' or 'live'.")
    if not secret_key or not webhook_secret:
        raise ValueError(
            f"CV production requires Stripe {mode} secrets; deployment was not started."
        )
    expected_prefixes = ("sk_test_", "rk_test_") if mode == "demo" else ("sk_live_", "rk_live_")
    if not secret_key.startswith(expected_prefixes):
        raise ValueError(f"CV production Stripe key must use {mode} mode; deployment was not started.")
    if not webhook_secret.startswith("whsec_"):
        raise ValueError("CV production webhook secret is invalid; deployment was not started.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("demo", "live"), default="live")
    args = parser.parse_args()
    if args.mode == "demo":
        secret_key_name = "GOODMORNING_MANAGED_STRIPE_SECRET_KEY"
        webhook_secret_name = "GOODMORNING_MANAGED_STRIPE_WEBHOOK_SECRET"
    else:
        secret_key_name = "GOODMORNING_MANAGED_CV_PRODUCTION_STRIPE_SECRET_KEY"
        webhook_secret_name = "GOODMORNING_MANAGED_CV_PRODUCTION_STRIPE_WEBHOOK_SECRET"
    validate_cv_production_secrets(
        os.environ.get(secret_key_name),
        os.environ.get(webhook_secret_name),
        args.mode,
    )
    print(f"CV production Stripe secrets are present and use {args.mode}-mode prefixes.")


if __name__ == "__main__":
    main()
