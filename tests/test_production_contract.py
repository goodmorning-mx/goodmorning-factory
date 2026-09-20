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
            "github_app_id": 1,
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

    def test_hydration_preserves_nonempty_listing_source_id(self):
        fallback = self.frontend_item()
        fallback.pop("github_app_uuid")
        fallback["source_id"] = 1
        detail = dict(fallback)
        detail["source_id"] = None

        class Client:
            def get(self, path):
                self.path = path
                return detail

        client = Client()
        hydrated = production.hydrate_application_by_uuid(client, "front-1", fallback)
        self.assertEqual(hydrated["source_id"], 1)
        classified = production.classify_frontend_application(
            [hydrated], self.args(), self.context()
        )
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

    def test_exact_source_contract_defers_only_unobservable_github_app(self):
        item = self.frontend_item()
        item.pop("github_app_uuid")
        client = FrontendConflictClient([[item]], details={"front-1": item})
        client._gm_frontend_source_verification_contract = {
            "uuid": "front-1",
            "confirmation": "verify-source-by-deployment:ledger-frontend-prod",
            "unverified_field": "github_app_uuid",
            "required_gates": ["deployment", "runtime", "smoke"],
        }

        result, actions, _sleeps = self.run_ensure(client)

        self.assertEqual(result["status"], "exists")
        self.assertFalse(result["source_api_verified"])
        self.assertEqual(
            result["source_verification"]["status"],
            "pending_required_gates",
        )
        self.assertEqual(result["source_verification"]["contract_uuid"], "front-1")
        self.assertEqual(client.bound_uuid, "front-1")
        self.assertEqual(client.posts, 0)
        self.assertEqual(
            [action["action"] for action in actions],
            ["reuse_with_deferred_source_verification"],
        )
        self.assertFalse(actions[0]["verified"])

    def test_source_contract_does_not_allow_uuid_field_or_source_drift(self):
        contract = {
            "uuid": "other-front",
            "confirmation": "verify-source-by-deployment:ledger-frontend-prod",
            "unverified_field": "github_app_uuid",
            "required_gates": ["deployment", "runtime", "smoke"],
        }
        source_missing = self.frontend_item()
        source_missing.pop("github_app_uuid")
        client = FrontendConflictClient(
            [[source_missing]],
            details={"front-1": source_missing},
        )
        client._gm_frontend_source_verification_contract = contract
        result, _actions, _sleeps = self.run_ensure(client)
        self.assertEqual(result["status"], "compatibility_unverified")
        self.assertIsNone(client.bound_uuid)

        contract["uuid"] = "front-1"
        another_field_missing = dict(source_missing, health_check_port=None)
        client = FrontendConflictClient(
            [[another_field_missing]],
            details={"front-1": another_field_missing},
        )
        client._gm_frontend_source_verification_contract = contract
        result, _actions, _sleeps = self.run_ensure(client)
        self.assertEqual(result["status"], "compatibility_unverified")
        self.assertEqual(
            {entry["field"] for entry in result["unverified_fields"]},
            {"github_app_uuid", "health_check_port"},
        )
        self.assertIsNone(client.bound_uuid)

        explicit_source_mismatch = self.frontend_item(github_app_uuid="wrong-source")
        client = FrontendConflictClient(
            [[explicit_source_mismatch]],
            details={"front-1": explicit_source_mismatch},
        )
        client._gm_frontend_source_verification_contract = contract
        result, _actions, _sleeps = self.run_ensure(client)
        self.assertEqual(result["status"], "recreate_required")
        self.assertIsNone(client.bound_uuid)

    def replacement_contract(self):
        return {
            "legacy_uuid": "front-1",
            "confirmation": "replace-source:ledger-frontend-prod",
            "candidate_name": "ledger-frontend-prod-candidate",
            "quarantine_name": "ledger-frontend-prod-legacy",
            "required_gates": ["deployment", "runtime", "domain_swap", "smoke"],
        }

    def test_replacement_reuses_isolated_source_verified_candidate(self):
        legacy = self.frontend_item()
        legacy.pop("github_app_uuid")
        candidate = self.frontend_item(
            "front-2",
            name="ledger-frontend-prod-candidate",
            domains="",
        )
        listing = [legacy, candidate]
        client = FrontendConflictClient(
            [listing, listing],
            details={"front-1": legacy, "front-2": candidate},
        )
        client._gm_frontend_source_replacement_contract = self.replacement_contract()

        result, actions, _sleeps = self.run_ensure(client)

        self.assertEqual(result["status"], "exists")
        self.assertEqual(result["name"], "ledger-frontend-prod")
        self.assertEqual(result["actual_name"], "ledger-frontend-prod-candidate")
        self.assertEqual(result["uuid"], "front-2")
        self.assertEqual(
            result["replacement"]["status"],
            "candidate_ready_for_deployment",
        )
        self.assertEqual(client.bound_uuid, "front-2")
        self.assertEqual([action["action"] for action in actions], ["reuse_candidate"])

    def test_replacement_creates_isolated_candidate_without_public_domain(self):
        legacy = self.frontend_item()
        legacy.pop("github_app_uuid")
        candidate = self.frontend_item(
            "front-2",
            name="ledger-frontend-prod-candidate",
            domains="",
        )

        class Client(FrontendConflictClient):
            def __init__(self):
                super().__init__(
                    [[legacy], [legacy]],
                    details={"front-1": legacy, "front-2": candidate},
                )
                self.replacement_payloads = []

            def create_frontend_replacement_application(self, payload):
                self.replacement_payloads.append(dict(payload))
                return production.Response(201, {"uuid": "front-2"}, {})

        client = Client()
        client._gm_frontend_source_replacement_contract = self.replacement_contract()

        result, actions, _sleeps = self.run_ensure(client)

        self.assertEqual(result["status"], "created")
        self.assertEqual(result["uuid"], "front-2")
        self.assertEqual(result["actual_name"], "ledger-frontend-prod-candidate")
        self.assertEqual(result["replacement"]["status"], "candidate_ready_for_deployment")
        self.assertEqual(len(client.replacement_payloads), 1)
        self.assertEqual(client.replacement_payloads[0]["domains"], "")
        self.assertEqual(
            client.replacement_payloads[0]["github_app_uuid"],
            "github-app-1",
        )
        self.assertEqual(client.bound_uuid, "front-2")
        self.assertEqual(
            [action["action"] for action in actions],
            ["attempt_create_candidate", "create_candidate"],
        )

    def test_replacement_recovers_candidate_promoted_before_legacy_quarantine(self):
        legacy = self.frontend_item()
        legacy.pop("github_app_uuid")
        promoted = self.frontend_item("front-2")
        client = FrontendConflictClient(
            [[legacy, promoted]],
            details={"front-1": legacy, "front-2": promoted},
        )
        client._gm_frontend_source_replacement_contract = self.replacement_contract()

        result, _actions, _sleeps = self.run_ensure(client)

        self.assertEqual(result["status"], "exists")
        self.assertEqual(result["uuid"], "front-2")
        self.assertEqual(result["actual_name"], "ledger-frontend-prod")
        self.assertEqual(
            result["replacement"]["status"],
            "candidate_promoted_legacy_pending",
        )

    def test_replacement_create_and_patch_contracts_are_exact(self):
        mode = production.ExecutionMode(
            name="apply",
            product="ledger",
            confirm_product="ledger",
            allow_resource_writes=True,
            allow_frontend_application_writes=True,
            confirm_frontend_application="ledger-frontend-prod",
            deploy_frontend=True,
            allow_deployments=True,
            allow_frontend_deployment=True,
            confirm_frontend_deployment="ledger-frontend-prod",
        )
        client = production.CoolifyClient(
            "https://coolify.example.test",
            "token",
            mode=mode,
        )
        client._gm_frontend_source_replacement_contract = self.replacement_contract()
        client.bind_frontend_application_bindings(self.context())
        candidate_payload = production.frontend_replacement_create_payload(
            self.args(),
            self.context(),
            "ledger-frontend-prod-candidate",
        )
        client._check_request(
            "POST",
            production.POST_FRONTEND_APPLICATION_CREATE,
            post_kind="frontend_replacement_create",
            json_body=candidate_payload,
        )
        client.bind_frontend_application_uuid("front-2", self.context())
        promote, quarantine = client.bind_frontend_replacement_promotion(
            "front-1",
            "front-2",
        )
        client._check_request(
            "PATCH",
            "/applications/front-2",
            post_kind="frontend_replacement_promote",
            json_body=promote,
        )
        client._check_request(
            "PATCH",
            "/applications/front-1",
            post_kind="frontend_replacement_quarantine",
            json_body=quarantine,
        )
        with self.assertRaises(production.ProvisioningError):
            client._check_request(
                "PATCH",
                "/applications/front-3",
                post_kind="frontend_replacement_promote",
                json_body=promote,
            )
        unsafe = dict(quarantine, domains="https://ledger.goodmorning.mx")
        with self.assertRaises(production.ProvisioningError):
            client._check_request(
                "PATCH",
                "/applications/front-1",
                post_kind="frontend_replacement_quarantine",
                json_body=unsafe,
            )

    def test_domain_swap_promotes_candidate_then_quarantines_legacy(self):
        legacy = self.frontend_item()
        legacy.pop("github_app_uuid")
        candidate = self.frontend_item(
            "front-2",
            name="ledger-frontend-prod-candidate",
            domains="",
        )

        class Client:
            frontend_application_bindings = self.context()

            def __init__(self):
                self.items = {
                    "front-1": dict(legacy),
                    "front-2": dict(candidate),
                }
                self.promotions = []

            def get(self, path):
                identifier = path.rsplit("/", 1)[-1]
                return dict(self.items[identifier])

            def promote_frontend_replacement(self, legacy_uuid, candidate_uuid):
                self.promotions.append((legacy_uuid, candidate_uuid))
                self.items[candidate_uuid].update(
                    name="ledger-frontend-prod",
                    domains="https://ledger.goodmorning.mx",
                )
                self.items[legacy_uuid].update(
                    name="ledger-frontend-prod-legacy",
                    domains="",
                    fqdn="",
                )

        client = Client()
        frontend = {
            "name": "ledger-frontend-prod",
            "actual_name": "ledger-frontend-prod-candidate",
            "uuid": "front-2",
            "status": "exists",
            "replacement": {
                "status": "candidate_ready_for_deployment",
                "legacy_uuid": "front-1",
                "candidate_uuid": "front-2",
                "candidate_name": "ledger-frontend-prod-candidate",
                "quarantine_name": "ledger-frontend-prod-legacy",
                "required_gates": ["deployment", "runtime", "domain_swap", "smoke"],
            },
        }
        actions = []

        verified, blockers = production.finalize_frontend_replacement(
            client,
            self.args(),
            frontend,
            actions,
        )

        self.assertTrue(verified)
        self.assertEqual(blockers, [])
        self.assertEqual(client.promotions, [("front-1", "front-2")])
        self.assertEqual(frontend["actual_name"], "ledger-frontend-prod")
        self.assertEqual(frontend["replacement"]["status"], "domain_swap_verified")
        self.assertTrue(actions[-1]["verified"])

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
