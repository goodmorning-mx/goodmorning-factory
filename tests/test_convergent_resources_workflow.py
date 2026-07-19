from pathlib import Path
import unittest


class ConvergentResourcesWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = Path(".github/workflows/provision-coolify-resources-development.yml").read_text(encoding="utf-8")

    def test_selects_changed_manifests_and_reuses_selection_across_steps(self):
        self.assertIn("fetch-depth: 2", self.text)
        self.assertIn("git diff --name-only", self.text)
        self.assertIn("RESOURCE_MANIFESTS_FILE", self.text)
        self.assertGreaterEqual(self.text.count('done < "${RESOURCE_MANIFESTS_FILE}"'), 3)

    def test_code_only_push_is_safe_no_op(self):
        self.assertIn("No changed resources-v2 manifests were selected; this push is a safe no-op.", self.text)
        self.assertIn('echo "selected=false" >> "${GITHUB_OUTPUT}"', self.text)
        self.assertGreaterEqual(self.text.count("steps.select.outputs.selected == 'true'"), 4)

    def test_retries_only_safe_partial_progress_with_a_hard_limit(self):
        self.assertIn("MAX_RECONCILE_ATTEMPTS: '6'", self.text)
        self.assertIn("RECONCILE_RETRY_BASE_SECONDS: '20'", self.text)
        self.assertIn('if [ "${status}" != "partial" ]; then', self.text)
        self.assertIn("Reconciliation did not converge", self.text)
        self.assertNotIn("continue-on-error", self.text)

    def test_backup_runs_only_after_resource_convergence(self):
        converge = self.text.index("Apply and deploy all resources-v2 products")
        backup = self.text.index("Reconcile external development backup schedules")
        self.assertLess(converge, backup)
        self.assertIn('if [ "${converged}" != "true" ]; then', self.text)


if __name__ == "__main__":
    unittest.main()
