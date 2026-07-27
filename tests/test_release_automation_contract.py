from pathlib import Path
import unittest

import scripts.register_production_manifest as production_catalog


class ReleaseAutomationContractTests(unittest.TestCase):
    def test_production_promotion_accepts_only_verified_release_events(self):
        workflow = Path(
            ".github/workflows/promote-product-production.yml"
        ).read_text(encoding="utf-8")

        required = (
            "repository_dispatch:",
            "- promote-product",
            "actions/create-github-app-token@v2",
            "EVENT_SOURCE_RUN_ID:",
            "promotion_policy // empty",
            'test "$(jq -r \'.name\' <<<"${run_json}")" = "CI"',
            'test "$(jq -r \'.event\' <<<"${run_json}")" = "push"',
            'test "$(jq -r \'.conclusion\' <<<"${run_json}")" = "success"',
            'test "$(jq -r \'.head_branch\' <<<"${run_json}")" = "main"',
            'test "$(jq -r \'.head_sha\' <<<"${run_json}")" = "${source_sha}"',
            'test "${current_sha}" = "${source_sha}"',
            "Verify pre-deployment production backup",
            "Verify post-deployment production backup",
        )
        for marker in required:
            self.assertIn(marker, workflow)

        self.assertNotIn("on:\n  push:", workflow)
        self.assertNotIn("schedule:", workflow)

    def test_new_products_receive_auto_on_main_manifest(self):
        manifest = production_catalog.build_manifest("ledger", "storageuuid123")
        self.assertEqual(manifest["repository"], "goodmorning-mx/ledger")
        self.assertEqual(manifest["branch"], "main")
        self.assertEqual(manifest["promotion_policy"], "auto-on-main")
        self.assertTrue(manifest["confirm_production"])


if __name__ == "__main__":
    unittest.main()
