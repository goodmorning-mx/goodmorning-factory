from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CREATE_WORKFLOW = ROOT / ".github" / "workflows" / "create-product.yml"
RESOURCE_WORKFLOW = ROOT / ".github" / "workflows" / "reconcile-product-development.yml"


class RepositoryFirstFactoryWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.create = CREATE_WORKFLOW.read_text(encoding="utf-8")
        cls.resources = RESOURCE_WORKFLOW.read_text(encoding="utf-8")

    def test_gitops_request_resolves_exactly_one_changed_file(self):
        self.assertIn("'requests/*.json'", self.create)
        self.assertIn("Resolve product request", self.create)
        self.assertIn("git diff --name-only", self.create)
        self.assertIn('if [ "${#requests[@]}" -ne 1 ]', self.create)
        self.assertIn("${{ steps.request.outputs.product_name }}", self.create)

    def test_create_pipeline_registers_then_dispatches_then_waits(self):
        register = self.create.index("Register repository-first product manifest")
        sentinel = self.create.index("Create deployment verification sentinel")
        dispatch = self.create.index("Reconcile and verify targeted Coolify product")
        wait = self.create.index("Verify Oracle database backup")
        self.assertLess(register, sentinel)
        self.assertLess(sentinel, dispatch)
        self.assertLess(dispatch, wait)
        self.assertIn("scripts/register_product_manifest.py", self.create)
        self.assertIn("python -m scripts.reconcile_resources", self.create)
        self.assertIn("scripts/reconcile_database_backups.py", self.create)
        self.assertIn('manifest="${{ steps.catalog.outputs.manifest_path }}"', self.create)

    def test_sentinel_is_product_specific_and_parses_create_url_directly(self):
        self.assertIn('title="[automation] Verify repository-first product ${product}"', self.create)
        self.assertIn('issue_url="$(gh issue create', self.create)
        self.assertIn('issue_number="${issue_url##*/}"', self.create)
        self.assertIn('[[ "${issue_number}" =~ ^[0-9]+$ ]]', self.create)

    def test_create_pipeline_uses_factory_app_and_template(self):
        self.assertIn("actions/create-github-app-token@v2", self.create)
        self.assertIn("GOODMORNING_FACTORY_APP_ID", self.create)
        self.assertIn("GOODMORNING_FACTORY_PRIVATE_KEY", self.create)
        self.assertIn("--template-repo goodmorning-mvp-template", self.create)
        self.assertIn("actions: write", self.create)

    def test_targeted_resource_dispatch_is_fail_closed(self):
        self.assertIn("manifest_path:", self.resources)
        self.assertIn("manifest_path must identify an existing products/<slug>.json file.", self.resources)
        self.assertIn('requested_manifest="${{ inputs.manifest_path }}"', self.resources)
        self.assertIn('requested_manifest="${{ github.event.client_payload.manifest_path }}"', self.resources)
        self.assertIn("catalog(factory): register product ", self.resources)

    def test_catalog_push_is_skipped_and_dispatch_is_targeted(self):
        self.assertIn("github.event.head_commit.message", self.resources)
        self.assertIn("workflow_dispatch", self.resources)
        self.assertIn('printf \'%s\\n\' "${requested_manifest}"', self.resources)


if __name__ == "__main__":
    unittest.main()
