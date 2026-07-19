from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CREATE_WORKFLOW = ROOT / ".github" / "workflows" / "create-product.yml"
RESOURCE_WORKFLOW = ROOT / ".github" / "workflows" / "provision-coolify-resources-development.yml"


class RepositoryFirstFactoryWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.create = CREATE_WORKFLOW.read_text(encoding="utf-8")
        cls.resources = RESOURCE_WORKFLOW.read_text(encoding="utf-8")

    def test_create_pipeline_registers_then_dispatches_then_waits(self):
        register = self.create.index("Register repository-first product manifest")
        sentinel = self.create.index("Create deployment verification sentinel")
        dispatch = self.create.index("Dispatch targeted Coolify reconciliation")
        wait = self.create.index("Wait for verified development deployment")
        self.assertLess(register, sentinel)
        self.assertLess(sentinel, dispatch)
        self.assertLess(dispatch, wait)
        self.assertIn("scripts/register_product_manifest.py", self.create)
        self.assertIn('manifest_path="${{ steps.catalog.outputs.manifest_path }}"', self.create)

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
        self.assertIn("catalog(factory): register product ", self.resources)

    def test_catalog_push_is_skipped_and_dispatch_is_targeted(self):
        self.assertIn("github.event.head_commit.message", self.resources)
        self.assertIn("workflow_dispatch", self.resources)
        self.assertIn('printf \'%s\\n\' "${requested_manifest}"', self.resources)


if __name__ == "__main__":
    unittest.main()
