import unittest
from types import SimpleNamespace

from scripts import production_application_conflict_recovery as recovery
from scripts import provision_product_production as production
from scripts import reconcile_resource_product_production as adapter


class LegacyFrontendMigrationTests(unittest.TestCase):
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
            "legacy_frontend_migration": {
                "uuid": "legacy-front-1",
                "confirmation": "migrate:ledger-frontend-prod",
                "expected": {
                    "name": "ledger-old",
                    "git_repository": "old-owner/ledger",
                    "git_branch": "main",
                    "build_pack": "dockerfile",
                    "base_directory": "/",
                    "dockerfile_location": "/Dockerfile",
                    "ports_exposes": "80",
                    "domains": "https://ledger.goodmorning.mx",
                },
            },
        }

    def context(self):
        return {
            "project_uuid": "project-1",
            "environment_uuid": "environment-1",
            "server_uuid": "server-1",
            "destination_uuid": "destination-1",
            "github_app_uuid": "github-app-1",
            "git_repository": "goodmorning-mx/ledger",
        }

    def target_payload(self):
        args = SimpleNamespace(product="ledger", repository="goodmorning-mx/ledger")
        return production.frontend_create_payload(args, self.context())

    def legacy_item(self, **overrides):
        item = {
            "uuid": "legacy-front-1",
            "name": "ledger-old",
            "git_repository": "git@github.com:old-owner/ledger.git",
            "git_branch": "main",
            "build_pack": "dockerfile",
            "base_directory": "/",
            "dockerfile_location": "/Dockerfile",
            "ports_exposes": "80",
            "fqdn": "https://ledger.goodmorning.mx",
        }
        item.update(overrides)
        return item

    def source_verification_contract(self):
        return {
            "uuid": "legacy-front-1",
            "confirmation": "verify-source-by-deployment:ledger-frontend-prod",
            "unverified_field": "github_app_uuid",
            "required_gates": ["deployment", "runtime", "smoke"],
        }

    def test_manifest_accepts_exact_legacy_contract(self):
        validated = adapter.validate_manifest(self.manifest())
        self.assertEqual(
            validated["legacy_frontend_migration"]["uuid"],
            "legacy-front-1",
        )

    def test_manifest_rejects_implicit_or_drifting_authorization(self):
        manifest = self.manifest()
        manifest["legacy_frontend_migration"]["confirmation"] = "yes"
        with self.assertRaises(adapter.ManifestError):
            adapter.validate_manifest(manifest)

    def test_manifest_accepts_only_exact_source_verification_contract(self):
        manifest = self.manifest()
        manifest["frontend_source_verification"] = self.source_verification_contract()
        validated = adapter.validate_manifest(manifest)
        self.assertEqual(
            validated["frontend_source_verification"]["required_gates"],
            ["deployment", "runtime", "smoke"],
        )

        for field, replacement in (
            ("uuid", "other-front"),
            ("confirmation", "yes"),
            ("unverified_field", "destination_uuid"),
            ("required_gates", ["deployment", "smoke"]),
        ):
            manifest = self.manifest()
            manifest["frontend_source_verification"] = self.source_verification_contract()
            manifest["frontend_source_verification"][field] = replacement
            with self.assertRaises(adapter.ManifestError, msg=field):
                adapter.validate_manifest(manifest)

        manifest = self.manifest()
        manifest["frontend_source_verification"] = self.source_verification_contract()
        manifest["frontend_source_verification"]["extra"] = True
        with self.assertRaises(adapter.ManifestError):
            adapter.validate_manifest(manifest)

        manifest = self.manifest()
        manifest["legacy_frontend_migration"]["expected"]["domains"] = (
            "https://other.goodmorning.mx"
        )
        with self.assertRaises(adapter.ManifestError):
            adapter.validate_manifest(manifest)

        manifest = self.manifest()
        manifest["legacy_frontend_migration"]["extra"] = True
        with self.assertRaises(adapter.ManifestError):
            adapter.validate_manifest(manifest)

    def test_repository_normalization_supports_github_ssh_forms(self):
        expected = "goodmorning-mx/ledger"
        self.assertEqual(
            production.normalize_repository("git@github.com:goodmorning-mx/ledger.git"),
            expected,
        )
        self.assertEqual(
            production.normalize_repository("ssh://git@github.com/goodmorning-mx/ledger.git"),
            expected,
        )

    def test_migration_patch_is_bound_to_exact_uuid_and_target(self):
        mode = production.ExecutionMode(
            name="apply",
            product="ledger",
            confirm_product="ledger",
            allow_resource_writes=True,
            allow_frontend_application_writes=True,
            confirm_frontend_application="ledger-frontend-prod",
        )
        client = production.CoolifyClient(
            "https://coolify.example.test",
            "token",
            mode=mode,
        )
        client.bind_frontend_application_bindings(self.context())
        patch_payload = client.bind_frontend_application_migration(
            "legacy-front-1",
            self.target_payload(),
        )
        self.assertIs(patch_payload["force_domain_override"], True)
        for immutable in (
            "project_uuid",
            "environment_uuid",
            "environment_name",
            "server_uuid",
            "destination_uuid",
            "github_app_uuid",
            "autogenerate_domain",
        ):
            self.assertNotIn(immutable, patch_payload)

        client._check_request(
            "PATCH",
            "/applications/legacy-front-1",
            post_kind="frontend_application_migrate",
            json_body=patch_payload,
        )
        with self.assertRaises(production.ProvisioningError):
            client._check_request(
                "PATCH",
                "/applications/another-app",
                post_kind="frontend_application_migrate",
                json_body=patch_payload,
            )
        changed = dict(patch_payload)
        changed["git_repository"] = "attacker/other"
        with self.assertRaises(production.ProvisioningError):
            client._check_request(
                "PATCH",
                "/applications/legacy-front-1",
                post_kind="frontend_application_migrate",
                json_body=changed,
            )
        changed = dict(patch_payload)
        changed["force_domain_override"] = False
        with self.assertRaises(production.ProvisioningError):
            client._check_request(
                "PATCH",
                "/applications/legacy-front-1",
                post_kind="frontend_application_migrate",
                json_body=changed,
            )

    def test_verified_legacy_frontend_is_migrated_once(self):
        class Client:
            def __init__(self, item):
                self.item = item
                self.migrations = []

            def get(self, path):
                self.requested_path = path
                return {"data": dict(self.item)}

            def migrate_frontend_application(self, uuid, payload):
                self.migrations.append((uuid, dict(payload)))
                return production.Response(200, {"uuid": uuid}, {})

        client = Client(self.legacy_item())
        recovery.migrate_legacy_frontend(
            production,
            client,
            "legacy-front-1",
            self.target_payload(),
            self.manifest()["legacy_frontend_migration"],
        )
        self.assertEqual(client.requested_path, "/applications/legacy-front-1")
        self.assertEqual(len(client.migrations), 1)
        self.assertEqual(client.migrations[0][0], "legacy-front-1")

    def test_legacy_drift_blocks_before_patch(self):
        class Client:
            def __init__(self, item):
                self.item = item
                self.migrations = []

            def get(self, path):
                return {"data": dict(self.item)}

            def migrate_frontend_application(self, uuid, payload):
                self.migrations.append((uuid, payload))

        client = Client(self.legacy_item(git_repository="git@github.com:other/ledger.git"))
        with self.assertRaises(production.ProvisioningError):
            recovery.migrate_legacy_frontend(
                production,
                client,
                "legacy-front-1",
                self.target_payload(),
                self.manifest()["legacy_frontend_migration"],
            )
        self.assertEqual(client.migrations, [])

    def test_conflict_wrapper_uses_only_authorized_uuid_and_marks_migration(self):
        legacy = self.legacy_item()

        class Client:
            def __init__(self):
                self.migrations = []

            def create_backend_application(self, payload):
                raise AssertionError("not used")

            def create_frontend_application(self, payload):
                raise production.ProvisioningError(
                    'Domain conflict {"resource_uuid":"legacy-front-1"}',
                    error_type="coolify_api",
                    code=2,
                    http_status=409,
                    request_attempted=True,
                )

            def get(self, path):
                return {"data": dict(legacy)}

            def migrate_frontend_application(self, uuid, payload):
                self.migrations.append((uuid, dict(payload)))
                return production.Response(200, {"uuid": uuid}, {})

        module = SimpleNamespace(
            CoolifyClient=Client,
            Response=production.Response,
            ProvisioningError=production.ProvisioningError,
            normalized_equal=production.normalized_equal,
        )
        recovery.install_application_conflict_recovery(
            module,
            legacy_frontend_migration=self.manifest()["legacy_frontend_migration"],
        )
        client = Client()
        response = client.create_frontend_application(self.target_payload())
        self.assertEqual(response.data["data"]["uuid"], "legacy-front-1")
        self.assertTrue(response.data["data"]["_factory_legacy_migrated"])
        self.assertEqual(len(client.migrations), 1)

    def test_uuid_less_conflict_hydrates_manifest_authorized_legacy_frontend(self):
        legacy = self.legacy_item()

        class Client:
            def __init__(self):
                self.migrations = []

            def create_backend_application(self, payload):
                raise AssertionError("not used")

            def create_frontend_application(self, payload):
                raise production.ProvisioningError(
                    "Coolify API 422: the FQDN is already in use.",
                    error_type="coolify_api",
                    code=2,
                    http_status=422,
                    request_attempted=True,
                )

            def get(self, path):
                return {"data": dict(legacy)}

            def migrate_frontend_application(self, uuid, payload):
                self.migrations.append((uuid, dict(payload)))
                return production.Response(200, {"uuid": uuid}, {})

        module = SimpleNamespace(
            CoolifyClient=Client,
            Response=production.Response,
            ProvisioningError=production.ProvisioningError,
            normalized_equal=production.normalized_equal,
        )
        recovery.install_application_conflict_recovery(
            module,
            legacy_frontend_migration=self.manifest()["legacy_frontend_migration"],
        )
        client = Client()
        response = client.create_frontend_application(self.target_payload())
        self.assertEqual(response.data["data"]["uuid"], "legacy-front-1")
        self.assertTrue(response.data["data"]["_factory_legacy_migrated"])
        self.assertEqual(len(client.migrations), 1)

    def test_conflict_with_different_uuid_still_fails_closed(self):
        legacy = self.legacy_item()

        class Client:
            def create_backend_application(self, payload):
                raise AssertionError("not used")

            def create_frontend_application(self, payload):
                raise production.ProvisioningError(
                    'Domain conflict {"resource_uuid":"different-front"}',
                    error_type="coolify_api",
                    code=2,
                    http_status=409,
                    request_attempted=True,
                )

            def get(self, path):
                return {"data": dict(legacy)}

            def migrate_frontend_application(self, uuid, payload):
                raise AssertionError("migration must not be attempted")

        module = SimpleNamespace(
            CoolifyClient=Client,
            Response=production.Response,
            ProvisioningError=production.ProvisioningError,
            normalized_equal=production.normalized_equal,
        )
        recovery.install_application_conflict_recovery(
            module,
            legacy_frontend_migration=self.manifest()["legacy_frontend_migration"],
        )
        with self.assertRaises(production.ProvisioningError):
            Client().create_frontend_application(self.target_payload())

    def test_nonzero_provisioner_exit_codes_remain_fail_closed(self):
        class ReturnTwo:
            @staticmethod
            def main(argv):
                return 2

        class ExitTwo:
            @staticmethod
            def main(argv):
                raise SystemExit(2)

        self.assertEqual(adapter.run_provisioner(ReturnTwo, []), 2)
        self.assertEqual(adapter.run_provisioner(ExitTwo, []), 2)


if __name__ == "__main__":
    unittest.main()
