from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


class SoftwareProductChangeWorkflowTests(unittest.TestCase):
    def test_queue_dispatches_only_operation_id_for_code_changes(self):
        workflow = (ROOT / ".github/workflows/software-factory-queue.yml").read_text()
        self.assertIn("'code.change': {", workflow)
        self.assertIn("workflow_id: 'product-change.yml'", workflow)
        self.assertIn("operation_id: operation.id", workflow)
        self.assertNotIn("prompt: operation.prompt", workflow)

    def test_prompt_is_authorized_and_scoped_to_a_private_product_repository(self):
        workflow = (ROOT / ".github/workflows/product-change.yml").read_text()
        self.assertIn("/internal/factory/operations/authorize", workflow)
        self.assertIn("permissions:\n      contents: read\n      id-token: write", workflow)
        self.assertIn("repositories: ${{ steps.authorize.outputs.product_name }}", workflow)
        self.assertIn("permission-contents: read", workflow)
        self.assertIn("permission-pull-requests: write", workflow)
        self.assertIn("openai/codex-action@a8d8be6ee028bea4525bc722d16f36687b408c81", workflow)
        self.assertIn("permission-profile: ':workspace'", workflow)
        self.assertIn("Do not push or merge.", workflow)
        self.assertNotIn("      prompt:\n", workflow)

        agent = workflow.index("Implement request in the isolated workspace")
        validation = workflow.index("Validate changes and run product checks")
        write_token = workflow.index("Create product-scoped pull request token")
        open_pr = workflow.index("Push isolated branch and open pull request")
        self.assertLess(agent, validation)
        self.assertLess(validation, write_token)
        self.assertLess(write_token, open_pr)

    def test_only_reviewed_factory_workflows_can_use_portal_oidc(self):
        manifest = (ROOT / "products/software.json").read_text()
        self.assertIn(".github/workflows/software-factory-queue.yml@refs/heads/dev", manifest)
        self.assertIn(".github/workflows/create-product.yml@refs/heads/dev", manifest)
        self.assertIn(".github/workflows/product-change.yml@refs/heads/dev", manifest)


if __name__ == "__main__":
    unittest.main()
