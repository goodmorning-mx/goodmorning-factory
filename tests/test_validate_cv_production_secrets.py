import unittest

from scripts.validate_cv_production_secrets import validate_cv_production_secrets


class ValidateCvProductionSecretsTests(unittest.TestCase):
    def test_missing_values_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "dedicated Stripe live secrets"):
            validate_cv_production_secrets(None, "whsec_placeholder")
        with self.assertRaisesRegex(ValueError, "dedicated Stripe live secrets"):
            validate_cv_production_secrets("sk_live_placeholder", None)

    def test_development_key_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must use live mode"):
            validate_cv_production_secrets("sk_test_placeholder", "whsec_placeholder")

    def test_non_stripe_webhook_secret_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "webhook secret is invalid"):
            validate_cv_production_secrets("sk_live_placeholder", "test_webhook_placeholder")

    def test_live_standard_and_restricted_keys_are_accepted(self):
        validate_cv_production_secrets("sk_live_placeholder", "whsec_placeholder")
        validate_cv_production_secrets("rk_live_placeholder", "whsec_placeholder")


if __name__ == "__main__":
    unittest.main()
