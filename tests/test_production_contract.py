import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts import provision_product_production as production
from scripts import reconcile_database_backups as backups
from scripts import reconcile_resource_product_production as adapter


class ProductionContractTests(unittest.TestCase):
    def manifest(self):
        return {
            "topology": "resources-v2-production",
            "product": "ledger",
            "repository": "goodmorning-mx/ledger",
            "branch": "main",
            "production_domain": "ledger.goodmorning.mx",
            "backend_base_directory": "/backend",
            "frontend_base_directory": "/frontend",
            "confirm_production": True,
            "backup": {
                "enabled": True,
                "storage_uuid": "c1rrntk8wv4cf3tl3wqviwp3",
                "frequency": "0 2 * * *",
                "local_retention_count": 2,
                "local_retention_days": 2,
                "s3_retention_count": 30,
                "s3_retention_days": 30,
            },
        }

    def test_production_application_contract(self):
        args = SimpleNamespace(product="ledger", repository="goodmorning-mx/ledger")
        context = {
            "project_uuid": "project-1",
            "environment_uuid": "environment-1",
            "server_uuid": "server-1",
            "destination_uuid": "destination-1",
            "github_app_uuid": "github-app-1",
            "git_repository": "goodmorning-mx/ledger",
        }
        backend = production.backend_create_payload(args, context)
        frontend = production.frontend_create_payload(args, context)
        self.assertEqual(backend["name"], "ledger-api-prod")
        self.assertEqual(backend["git_branch"], "main")
        self.assertEqual(backend["environment_name"], "production")
        self.assertEqual(backend["domains"], "https://api.ledger.goodmorning.mx")
        self.assertFalse(backend["is_auto_deploy_enabled"])
        self.assertEqual(frontend["name"], "ledger-frontend-prod")
        self.assertEqual(frontend["domains"], "https://ledger.goodmorning.mx")
        self.assertFalse(frontend["is_auto_deploy_enabled"])
        self.assertNotIn(":8000", backend["domains"])
        self.assertNotIn(":80", frontend["domains"])

    def test_production_manifest_builds_exact_confirmations(self):
        argv = adapter.build_provisioner_argv(
            self.manifest(),
            phase="apply",
            operation="deploy-full",
            server="localhost",
            github_app="goodmorning-mx-coolify",
            destination_uuid="destination-1",
        )
        joined = " ".join(argv)
        self.assertIn("--branch main", joined)
        self.assertIn("--environment production", joined)
        self.assertIn("--confirm-database-start ledger-postgres-prod", joined)
        self.assertIn("--confirm-backend-deployment ledger-api-prod", joined)
        self.assertIn("--confirm-frontend-deployment ledger-frontend-prod", joined)

    def test_production_backup_selects_isolated_database(self):
        manifest = self.manifest()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.json"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            loaded = backups.load_manifest(path)
        self.assertEqual(loaded["_backup_environment"], "production")
        self.assertEqual(loaded["_backup_database_name"], "ledger-postgres-prod")

    def test_production_contract_rejects_development_inputs(self):
        manifest = self.manifest()
        manifest["branch"] = "dev"
        with self.assertRaises(adapter.ManifestError):
            adapter.validate_manifest(manifest)
        manifest = self.manifest()
        manifest["production_domain"] = "dev.ledger.goodmorning.mx"
        with self.assertRaises(adapter.ManifestError):
            adapter.validate_manifest(manifest)


class FrontendConflictClient:
    """Minimal Coolify double for the Frontend Application conflict path.

    ``listings`` is one ``GET /applications`` payload per call; the last entry
    is reused once the script keeps polling. ``create_frontend_application``
    always raises the configured conflict so a test can assert the POST is
    never repeated inside one execution.
    """

    def __init__(self, listings, *, http_status=409, details=None, create_response=None):
        self.listings = [list(entry) for entry in listings] or [[]]
        self.details = details or {}
        self.http_status = http_status
        self.create_response = create_response
        self.list_calls = 0
        self.detail_calls = []
        self.posts = 0
        self.bound_uuid = None
        self.bound_bindings = None

    def bind_frontend_application_bindings(self, bindings):
        self.bound_bindings = bindings

    def bind_frontend_application_uuid(self, uuid, bindings=None):
        self.bound_uuid = uuid

    def get(self, path):
        if path == "/applications":
            index = min(self.list_calls, len(self.listings) - 1)
            self.list_calls += 1
            return list(self.listings[index])
        if path.startswith("/applications/"):
            uuid = path.rsplit("/", 1)[-1]
            self.detail_calls.append(uuid)
            if uuid in self.details:
                return self.details[uuid]
            raise production.ProvisioningError("detail unavailable", error_type="coolify_api")
        raise production.ProvisioningError(f"unexpected path {path}", error_type="coolify_api")

    def create_frontend_application(self, payload):
        self.posts += 1
        if self.create_response is not None:
            return self.create_response
        raise production.ProvisioningError(
            "conflict",
            error_type="coolify_api",
            http_status=self.http_status,
            request_attempted=True,
        )


class FrontendConflictRediscoveryTests(unittest.TestCase):
    """Stage 6 production Frontend recovery after a Coolify 409/422 conflict."""

    def args(self):
        return SimpleNamespace(product="ledger", repository="goodmorning-mx/ledger")

    def context(self):
        return {
            "project_uuid": "project-1",
            "environment_uuid": "environment-1",
            "server_uuid": "server-1",
            "destination_uuid": "destination-1",
            "github_app_uuid": "github-app-1",
            "git_repository": "goodmorning-mx/ledger",
        }

    def mode(self):
        return production.ExecutionMode(
            name="apply",
            product="ledger",
            confirm_product="ledger",
            allow_resource_writes=True,
            allow_frontend_application_writes=True,
            confirm_frontend_application="ledger-frontend-prod",
        )

    def backend(self):
        return {"name": "ledger-api-prod", "status": "exists", "uuid": "api-1"}

    def frontend_item(self, uuid="front-1", **overrides):
        item = {
            "uuid": uuid,
            "name": "ledger-frontend-prod",
            "project_uuid": "project-1",
            "environment_uuid": "environment-1",
            "server_uuid": "server-1",
            "destination_uuid": "destination-1",
            "github_app_uuid": "github-app-1",
            "git_repository": "goodmorning-mx/ledger",
            "git_branch": "main",
            "build_pack": "dockerfile",
            "base_directory": "/frontend",
            "dockerfile_location": "/Dockerfile",
            "ports_exposes": "80",
            "domains": "https://ledger.goodmorning.mx",
            "instant_deploy": False,
            "is_auto_deploy_enabled": False,
            "autogenerate_domain": False,
            "health_check_enabled": True,
            "health_check_path": "/",
            "health_check_port": "80",
            "health_check_method": "GET",
            "health_check_scheme": "http",
            "health_check_return_code": 200,
        }
        item.update(overrides)
        return item

    def run_ensure(self, client):
        actions = []
        sleeps = []
        result = production.ensure_frontend_application(
            client,
            self.args(),
            self.context(),
            self.mode(),
            actions,
            self.backend(),
            sleeper=sleeps.append,
        )
        return result, actions, sleeps

    def test_compatible_frontend_fixture_classifies_as_exists(self):
        classified = production.classify_frontend_application([self.frontend_item()], self.args(), self.context())
        self.assertEqual(classified["status"], "exists")

    def test_conflict_recovers_when_resource_appears_on_second_attempt(self):
        item = self.frontend_item()
        client = FrontendConflictClient([[], [], [item]], details={"front-1": item})
        result, actions, sleeps = self.run_ensure(client)
        self.assertEqual(result["status"], "exists")
        self.assertTrue(result["recovered_after_conflict"])
        self.assertEqual(result["uuid"], "front-1")
        self.assertEqual(result["conflict_http_status"], 409)
        self.assertEqual(result["rediscovery_attempts"], 2)
        self.assertEqual([entry["status"] for entry in result["rediscovery_observations"]], ["absent", "exists"])
        self.assertEqual(client.bound_uuid, "front-1")
        self.assertEqual(client.posts, 1)
        self.assertEqual(sleeps, [2.0])
        recovery = [action for action in actions if action["action"] == "recover_after_conflict"]
        self.assertEqual(len(recovery), 1)
        self.assertTrue(recovery[0]["recovered_after_conflict"])
        self.assertTrue(recovery[0]["verified"])
        self.assertTrue(recovery[0]["request_attempted"])
        self.assertFalse(recovery[0]["write_performed"])

    def test_conflict_without_visible_resource_blocks_after_all_attempts(self):
        client = FrontendConflictClient([[]], http_status=422)
        result, actions, sleeps = self.run_ensure(client)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "conflict_without_verified_resource")
        self.assertEqual(result["conflict_http_status"], 422)
        self.assertEqual(result["rediscovery_attempts"], production.FRONTEND_CONFLICT_REDISCOVERY_ATTEMPTS)
        self.assertEqual(result["rediscovery_max_attempts"], production.FRONTEND_CONFLICT_REDISCOVERY_ATTEMPTS)
        self.assertEqual([entry["status"] for entry in result["rediscovery_observations"]], ["absent", "absent", "absent"])
        # The rejected POST is never repeated blindly inside one execution.
        self.assertEqual(client.posts, 1)
        self.assertEqual(client.list_calls, 1 + production.FRONTEND_CONFLICT_REDISCOVERY_ATTEMPTS)
        self.assertEqual(sleeps, [2.0, 4.0])
        self.assertIsNone(client.bound_uuid)
        self.assertEqual([action["status"] for action in actions if action["action"] == "recover_after_conflict"], ["conflict_without_verified_resource"])

    def test_nested_patch_conflict_is_not_misclassified_as_create_conflict(self):
        client = FrontendConflictClient([[]])

        def reject_patch(_payload):
            raise production.ProvisioningError(
                "migration patch conflict",
                error_type="coolify_api",
                http_status=409,
                method="PATCH",
                path="/applications/legacy-front-1",
                request_attempted=True,
            )

        client.create_frontend_application = reject_patch
        with self.assertRaises(production.ProvisioningError) as raised:
            self.run_ensure(client)
        self.assertEqual(raised.exception.method, "PATCH")
        self.assertEqual(client.list_calls, 1)

    def test_conflict_with_incompatible_resource_is_not_adopted(self):
        item = self.frontend_item(git_branch="feature/x", domains="https://other.goodmorning.mx")
        client = FrontendConflictClient([[], [item]], details={"front-1": item})
        result, actions, _sleeps = self.run_ensure(client)
        self.assertEqual(result["status"], "recreate_required")
        self.assertEqual(result["reason"], "conflict_with_unadoptable_resource")
        self.assertEqual(result["rediscovery_attempts"], 1)
        changed = {difference["field"] for difference in result["differences"]}
        self.assertEqual(changed, {"git_branch", "domains"})
        self.assertIsNone(client.bound_uuid)
        self.assertEqual(client.posts, 1)
        self.assertEqual([action["status"] for action in actions if action["action"] == "recover_after_conflict"], ["incompatible"])

    def test_conflict_with_out_of_scope_resource_is_not_adopted(self):
        item = self.frontend_item(project_uuid="other-project", server_uuid="other-server")
        client = FrontendConflictClient([[], [item]], details={"front-1": item})
        result, _actions, _sleeps = self.run_ensure(client)
        self.assertEqual(result["status"], "out_of_scope")
        self.assertEqual(result["reason"], "conflict_with_unadoptable_resource")
        self.assertIsNone(client.bound_uuid)

    def test_conflict_with_duplicates_is_not_adopted(self):
        first = self.frontend_item("front-1")
        second = self.frontend_item("front-2")
        client = FrontendConflictClient([[], [first, second]], details={"front-1": first, "front-2": second})
        result, actions, _sleeps = self.run_ensure(client)
        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(result["reason"], "conflict_with_unadoptable_resource")
        self.assertEqual(len(result["matches"]), 2)
        self.assertIsNone(client.bound_uuid)
        self.assertEqual(client.posts, 1)
        self.assertEqual([action["status"] for action in actions if action["action"] == "recover_after_conflict"], ["duplicate"])

    def test_successful_creation_without_conflict_skips_rediscovery(self):
        item = self.frontend_item()
        client = FrontendConflictClient(
            [[]],
            details={"front-1": item},
            create_response=production.Response(201, {"uuid": "front-1"}, {}),
        )
        result, actions, sleeps = self.run_ensure(client)
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["uuid"], "front-1")
        self.assertNotIn("recovered_after_conflict", result)
        self.assertNotIn("rediscovery_attempts", result)
        self.assertEqual(client.bound_uuid, "front-1")
        self.assertEqual(client.posts, 1)
        self.assertEqual(sleeps, [])
        self.assertEqual(client.list_calls, 1)
        self.assertEqual([action["status"] for action in actions if action["resource"] == "frontend_application"], ["attempted", "created"])

    def test_existing_compatible_frontend_is_reused_without_any_post(self):
        item = self.frontend_item()
        client = FrontendConflictClient([[item]], details={"front-1": item})
        result, actions, _sleeps = self.run_ensure(client)
        self.assertEqual(result["status"], "exists")
        self.assertEqual(client.posts, 0)
        self.assertEqual(client.bound_uuid, "front-1")
        self.assertEqual([action["action"] for action in actions], ["reuse"])

    def test_every_unadoptable_status_fails_the_run_closed(self):
        self.assertTrue(
            production.FRONTEND_UNADOPTABLE_STATUSES - {"incompatible"} <= production.FRONTEND_BLOCKING_STATUSES,
            "an unadoptable Frontend status must never be able to read as ready",
        )
        # ``incompatible`` is remapped to ``recreate_required`` before it reaches the plan.
        self.assertIn("recreate_required", production.FRONTEND_BLOCKING_STATUSES)
        self.assertIn("blocked", production.FRONTEND_BLOCKING_STATUSES)

    def test_rediscovery_backoff_is_short_and_exponential(self):
        delays = [production.frontend_conflict_backoff_delay(attempt) for attempt in (1, 2, 3)]
        self.assertEqual(delays, [2.0, 4.0, 8.0])

    def test_rediscovery_failure_does_not_adopt_anything(self):
        class FailingListClient(FrontendConflictClient):
            """Initial discovery succeeds; every post-conflict rediscovery fails."""

            def get(self, path):
                if path == "/applications" and self.list_calls >= 1:
                    self.list_calls += 1
                    raise production.ProvisioningError("list unavailable", error_type="coolify_api")
                return super().get(path)

        client = FailingListClient([[]])
        result, _actions, _sleeps = self.run_ensure(client)
        self.assertEqual(result["status"], "blocked")
        self.assertEqual(result["reason"], "conflict_without_verified_resource")
        self.assertEqual(
            [entry["status"] for entry in result["rediscovery_observations"]],
            ["rediscovery_failed"] * production.FRONTEND_CONFLICT_REDISCOVERY_ATTEMPTS,
        )
        self.assertIsNone(client.bound_uuid)


if __name__ == "__main__":
    unittest.main()
