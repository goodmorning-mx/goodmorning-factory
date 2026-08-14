import argparse
import ast
import os
from pathlib import Path
import re
import unittest
from unittest import mock

import scripts.orchestrate_product_development as op
import scripts.provision_product_development as pp


def oargs(**kw):
    base=dict(operation="plan", phase="plan", product="ledger", server="localhost", github_app="goodmorning-mx-coolify", destination_uuid='bk8cw0gkswgo04o0s88sk0kg', confirmation=None)
    base.update(kw)
    return argparse.Namespace(**base)


def env_for(operation, phase="apply"):
    allowed = frozenset() if phase == "plan" else op.APPLY_TRUE_GATES[operation]
    return {g:("true" if g in allowed else "false") for g in op.GATES}


class Stage9OrchestratorTests(unittest.TestCase):
    def test_inputs_derive_fixed_contract_and_reject_invalid_slug(self):
        d=op.validate_orchestration_inputs(oargs(operation="deploy-full", phase="plan"))
        self.assertEqual(d["repository"], "goodmorning-mx/ledger")
        self.assertEqual(d["branch"], "dev")
        self.assertEqual(d["environment"], "dev")
        self.assertEqual(d["api_domain"], "https://api.dev.ledger.goodmorning.mx")
        self.assertEqual(d["frontend_domain"], "https://dev.ledger.goodmorning.mx")
        with self.assertRaises(pp.ProvisioningError): op.validate_orchestration_inputs(oargs(product="Bad_Slug"))

    def test_operation_phase_valid_and_invalid_combinations(self):
        valid=[("plan","plan"),("provision","plan"),("provision","apply"),("deploy-backend","plan"),("deploy-backend","apply"),("deploy-full","plan"),("deploy-full","apply")]
        for operation, phase in valid:
            op.validate_orchestration_inputs(oargs(operation=operation, phase=phase))
        for operation, phase in [("plan","apply"),("bogus","plan"),("deploy-full","bogus")]:
            with self.assertRaises(pp.ProvisioningError): op.validate_orchestration_inputs(oargs(operation=operation, phase=phase))

    def test_confirmation_contract_only_required_in_apply(self):
        self.assertEqual(op.expected_confirmation("deploy-full","ledger"), "deploy-full:ledger:dev")
        for oper in ("provision","deploy-backend","deploy-full"):
            with mock.patch.dict(os.environ, env_for(oper,"plan"), clear=True), mock.patch.object(op, "provision", return_value={"status":"dry_run", "selected_destination":{"status":"found"}}):
                op.orchestrate(oargs(operation=oper, phase="plan"))
            with mock.patch.dict(os.environ, env_for(oper,"apply"), clear=True), mock.patch.object(op, "provision", return_value={"status":"dry_run"}):
                op.orchestrate(oargs(operation=oper, phase="apply", confirmation=op.expected_confirmation(oper,"ledger")))
        for bad in (None, "Deploy-full:ledger:dev", "deploy-full:other:dev", "deploy-full:ledger:prod"):
            with mock.patch.dict(os.environ, env_for("deploy-full","apply"), clear=True), mock.patch.object(op, "provision") as prov:
                with self.assertRaises(pp.ProvisioningError): op.orchestrate(oargs(operation="deploy-full", phase="apply", confirmation=bad))
                prov.assert_not_called()

    def test_gate_matrix_plan_and_apply_fail_before_requests(self):
        for oper in op.OPERATIONS:
            with mock.patch.dict(os.environ, env_for(oper,"plan"), clear=True):
                op.validate_operation_gates(oper,"plan")
        for oper in op.MUTATING_OPERATIONS:
            with mock.patch.dict(os.environ, env_for(oper,"apply"), clear=True):
                op.validate_operation_gates(oper,"apply")
        with mock.patch.dict(os.environ, {"COOLIFY_ALLOW_RESOURCE_WRITES":"true"}, clear=True), mock.patch.object(op, "provision") as prov:
            with self.assertRaises(pp.ProvisioningError): op.orchestrate(oargs(operation="deploy-backend", phase="plan"))
            prov.assert_not_called()
        bad=env_for("provision","apply"); bad["COOLIFY_ALLOW_FRONTEND_DEPLOYMENT"]="true"
        with mock.patch.dict(os.environ, bad, clear=True), mock.patch.object(op, "provision") as prov:
            with self.assertRaises(pp.ProvisioningError): op.orchestrate(oargs(operation="provision", phase="apply", confirmation="provision:ledger:dev"))
            prov.assert_not_called()
        bad=env_for("deploy-full","apply"); bad["COOLIFY_ALLOW_BACKEND_DEPLOYMENT"]="false"
        with mock.patch.dict(os.environ, bad, clear=True), mock.patch.object(op, "provision") as prov:
            with self.assertRaises(pp.ProvisioningError): op.orchestrate(oargs(operation="deploy-full", phase="apply", confirmation="deploy-full:ledger:dev"))
            prov.assert_not_called()

    def test_argv_by_operation_phase_destination_and_no_secret(self):
        plan_cases={
            "plan": [],
            "provision": [],
            "deploy-backend": ["--deploy-backend"],
            "deploy-full": ["--deploy-backend", "--deploy-frontend"],
        }
        for oper, flags in plan_cases.items():
            argv=op.build_provisioner_argv(oargs(operation=oper, phase="plan", destination_uuid="bk8cw0gkswgo04o0s88sk0kg"))
            self.assertIn("--dry-run", argv); self.assertNotIn("--apply", argv)
            for flag in flags: self.assertIn(flag, argv)
            if oper != "deploy-full": self.assertNotIn("--deploy-frontend", argv)
            self.assertIn("goodmorning-mx/ledger", argv); self.assertIn("--destination-uuid", argv)
            self.assertNotIn("COOLIFY_API_TOKEN", " ".join(argv))
        for oper in ("provision","deploy-backend","deploy-full"):
            argv=op.build_provisioner_argv(oargs(operation=oper, phase="apply", confirmation=op.expected_confirmation(oper,"ledger")))
            self.assertIn("--apply", argv); self.assertNotIn("--dry-run", argv)
            self.assertIn("--confirm-product", argv)
            if oper == "deploy-full": self.assertLess(argv.index("--deploy-backend"), argv.index("--deploy-frontend"))

    def test_plan_classification_safe_and_fail_closed_fixtures(self):
        safe={"status":"dry_run","selected_destination":{"status":"found"},"postgresql":{"status":"would_create","uuid":"${postgres.uuid}"},"postgresql_readiness":{"status":"would_start","reason":"postgresql_creation_planned"},"backend_application":{"status":"would_create","reason":"postgresql_dependency_planned"},"frontend_application":{"status":"planned_after_backend","reason":"backend_dependency_planned"},"resources":[{"status":"absent"},{"status":"would_create"}],"blockers":[]}
        self.assertTrue(op.classify_plan_safety(safe)[0])
        safe_matched={"status":"dry_run","selected_destination":{"status":"found"},"project":{"status":"found","scope_details":[{"status":"matched"}]},"blockers":[]}
        self.assertTrue(op.classify_plan_safety(safe_matched)[0])
        safe_backend={"status":"dry_run","selected_destination":{"status":"found"},"backend_deployment":{"status":"would_deploy"},"blockers":[]}
        self.assertTrue(op.classify_plan_safety(safe_backend)[0])
        safe_full={"status":"dry_run","selected_destination":{"status":"found"},"backend_deployment":{"status":"would_deploy"},"frontend_deployment":{"status":"would_verify_backend_then_deploy"},"blockers":[]}
        self.assertTrue(op.classify_plan_safety(safe_full)[0])
        bad_fixtures=[
            {"status":"dry_run","selected_destination":{"status":"found"},"blockers":[{"reason":"new_reason"}]},
            {"status":"dry_run","selected_destination":{"status":"found"},"nested":{"blockers":[{"reason":"new_nested"}]}},
            {"status":"blocked","selected_destination":{"status":"found"}},
            {"status":"partial","selected_destination":{"status":"found"}},
            {"status":"error","selected_destination":{"status":"found"}},
            {"status":"dry_run","selected_destination":None},
            {"status":"dry_run","selected_destination":{"status":"duplicate"}},
            {"status":"dry_run","selected_destination":{"status":"found"},"github_app":{"status":"ambiguous"}},
            {"status":"dry_run","selected_destination":{"status":"found"},"legacy_compose":{"status":"legacy_compose_migration_required"}},
            {"status":"dry_run","selected_destination":{"status":"found"},"actions":[{"status":"write_outcome_unknown","write_outcome_unknown":True}]},
            {"status":"dry_run","selected_destination":{"status":"found"},"frontend_deployment":{"status":"blocked","blockers":[{"reason":"preflight_new"}]}},
            {"status":"dry_run","selected_destination":{"status":"found"},"resources":[{"status":"mystery"}]},
            {"status":"dry_run","selected_destination":{"status":"found"},"postgresql":{"status":"would_create","uuid":"${postgres.uuid}"},"postgresql_readiness":{"status":"would_start","reason":"postgresql_creation_planned"},"backend_application":{"status":"incompatible"},"frontend_application":{"status":"planned_after_backend","reason":"backend_dependency_planned"}},
            {"status":"dry_run","selected_destination":{"status":"found"},"project":{"status":"found","scope_details":[{"status":"matched"},{"status":"mismatch"}]}},
            {"status":"exists","selected_destination":{"status":"found"}},
            {"status":"created","selected_destination":{"status":"found"}},
            {"status":"future_status","selected_destination":{"status":"found"}},
        ]
        for fixture in bad_fixtures:
            ok, blockers=op.classify_plan_safety(fixture)
            self.assertFalse(ok, fixture); self.assertTrue(blockers)
            self.assertNotIn("postgres://", repr(blockers)); self.assertNotIn("token", repr(blockers).lower())


    def test_planned_after_backend_requires_explicit_safe_backend_evidence(self):
        positive={"status":"dry_run","selected_destination":{"status":"found"},"postgresql":{"status":"would_create","uuid":"${postgres.uuid}"},"postgresql_readiness":{"status":"would_start","reason":"postgresql_creation_planned"},"backend_application":{"status":"would_create","reason":"postgresql_dependency_planned"},"frontend_application":{"status":"planned_after_backend","reason":"backend_dependency_planned"},"blockers":[]}
        self.assertTrue(op.classify_plan_safety(positive)[0])
        negative_cases=[
            {"status":"dry_run","selected_destination":{"status":"found"},"frontend_application":{"status":"planned_after_backend","reason":"backend_dependency_planned"}},
            {"status":"dry_run","selected_destination":{"status":"found"},"backend_application":{"status":"would_create"},"frontend_application":{"status":"planned_after_backend"}},
            {"status":"dry_run","selected_destination":{"status":"found"},"backend_application":{"status":"would_create"},"frontend_application":{"status":"planned_after_backend","reason":""}},
            {"status":"dry_run","selected_destination":{"status":"found"},"backend_application":{"status":"would_create"},"frontend_application":{"status":"planned_after_backend","reason":"application_dependency_planned"}},
            {"status":"dry_run","selected_destination":{"status":"found"},"backend_application":{"status":"future_backend_status"},"frontend_application":{"status":"planned_after_backend","reason":"backend_dependency_planned"}},
            {"status":"dry_run","selected_destination":{"status":"found"},"backend_application":{"status":"would_create","blockers":[{"reason":"backend_blocked"}]},"frontend_application":{"status":"planned_after_backend","reason":"backend_dependency_planned"}},
        ]
        for fixture in negative_cases:
            ok, blockers=op.classify_plan_safety(fixture)
            self.assertFalse(ok, fixture)
            self.assertTrue(blockers)
            self.assertNotIn("postgres://", repr(blockers))
            self.assertNotIn("secret-value", repr(blockers).lower())

    def test_stage9_real_plan_fixture_preserves_context_and_completes(self):
        fixture={"status":"dry_run","selected_destination":{"status":"found"},"postgresql":{"status":"would_create","uuid":"${postgres.uuid}"},"postgresql_readiness":{"status":"would_start","reason":"postgresql_creation_planned"},"backend_application":{"status":"would_create","reason":"postgresql_dependency_planned"},"frontend_application":{"status":"planned_after_backend","reason":"backend_dependency_planned"},"resources":[{"name":"ledger-postgres-dev","status":"would_create","provisioning":{"status":"would_create"}},{"name":"ledger-api-dev","status":"would_create","reason":"postgresql_dependency_planned","provisioning":{"status":"would_create","reason":"postgresql_dependency_planned"}},{"name":"ledger-frontend-dev","status":"planned_after_backend","reason":"backend_dependency_planned","provisioning":{"status":"planned_after_backend","reason":"backend_dependency_planned"}}],"backend_deployment":{"status":"not_requested","user_requested":False,"request_attempted":False,"write_performed":False,"write_outcome_unknown":False,"verified":False},"frontend_deployment":{"status":"not_requested","user_requested":False,"request_attempted":False,"write_performed":False,"write_outcome_unknown":False,"verified":False},"actions":[{"resource":"backend_deployment","action":"plan","status":"not_requested","request_attempted":False,"write_performed":False,"write_outcome_unknown":False,"verified":False},{"resource":"frontend_deployment","action":"plan","status":"not_requested","request_attempted":False,"write_performed":False,"write_outcome_unknown":False,"verified":False}],"blockers":[]}
        ok, blockers=op.classify_plan_safety(fixture)
        self.assertTrue(ok, blockers)
        self.assertEqual(fixture["resources"][2]["reason"], "backend_dependency_planned")
        with mock.patch.dict(os.environ, env_for("plan","plan"), clear=True), mock.patch.object(op,"provision",return_value=fixture):
            code,result=op.orchestrate(oargs(operation="plan", phase="plan"), client_factory=lambda: object())
        self.assertEqual(code,0)
        self.assertEqual(result["plan_blockers"], [])
        self.assertTrue(result["orchestration"]["plan_safe"])
        self.assertEqual(result["orchestration"]["next_action"], "complete")


    def test_stage9_scope_details_api_omitted_is_audit_metadata_only(self):
        fixture={"status":"dry_run","selected_destination":{"status":"found"},"postgresql":{"name":"ledger-postgres-dev","uuid":"db123","status":"exists","scope_api_verified":False,"project_scope_api_present":False,"environment_scope_api_present":False,"server_scope_api_matched":True,"scope_details":[{"field":"project_uuid","status":"api_omitted"},{"field":"environment_uuid","status":"api_omitted"},{"field":"server_uuid","status":"matched"}]},"postgresql_readiness":{"status":"would_start","reason":"verified_factory_postgresql_requires_start"},"backend_application":{"status":"would_create","reason":"postgresql_dependency_planned"},"frontend_application":{"status":"planned_after_backend","reason":"backend_dependency_planned"},"plan_blockers":[],"blockers":[]}
        ok, blockers=op.classify_plan_safety(fixture)
        self.assertTrue(ok, blockers)
        self.assertEqual(fixture["postgresql"]["status"], "exists")
        self.assertFalse(fixture["postgresql"]["scope_api_verified"])
        self.assertEqual(fixture["postgresql"]["scope_details"][0]["status"], "api_omitted")
        with mock.patch.dict(os.environ, env_for("provision","plan"), clear=True), mock.patch.object(op,"provision",return_value=fixture):
            code,result=op.orchestrate(oargs(operation="provision", phase="plan"), client_factory=lambda: object())
        self.assertEqual(code,0)
        self.assertEqual(result["plan_blockers"], [])
        self.assertTrue(result["orchestration"]["plan_safe"])
        self.assertEqual(result["orchestration"]["next_action"], "apply")
        self.assertNotIn("api_omitted", op.SAFE_STATUSES)

    def test_stage9_api_omitted_blocks_operational_statuses(self):
        base={"status":"dry_run","selected_destination":{"status":"found"},"blockers":[]}
        cases=(
            {"resources":[{"name":"ledger-postgres-dev","status":"api_omitted"}]},
            {"actions":[{"resource":"postgresql","action":"create","status":"api_omitted"}]},
            {"postgresql_readiness":{"status":"api_omitted"}},
            {"backend_deployment":{"status":"api_omitted"}},
            {"resources":[{"name":"ledger-postgres-dev","status":"exists","provisioning":{"status":"api_omitted"}}]},
        )
        for update in cases:
            fixture={**base, **update}
            ok, blockers=op.classify_plan_safety(fixture)
            self.assertFalse(ok, fixture)
            self.assertTrue(any(b.get("reason") == "unknown_status:api_omitted" for b in blockers), blockers)

    def test_stage9_frontend_resource_reason_must_be_exact_and_noncontradictory(self):
        base={"status":"dry_run","selected_destination":{"status":"found"},"postgresql":{"status":"would_create","uuid":"${postgres.uuid}"},"postgresql_readiness":{"status":"would_start","reason":"postgresql_creation_planned"},"backend_application":{"status":"would_create","reason":"postgresql_dependency_planned"},"resources":[{"name":"ledger-frontend-dev","status":"planned_after_backend","provisioning":{"status":"planned_after_backend","reason":"backend_dependency_planned"}}],"blockers":[]}
        for reason in (None, "", "application_dependency_planned"):
            fixture={**base, "resources":[{**base["resources"][0]}]}
            if reason is not None:
                fixture["resources"][0]["reason"] = reason
            ok, blockers=op.classify_plan_safety(fixture)
            self.assertFalse(ok)
            self.assertTrue(any(b.get("reason") == "planned_after_backend_reason_unverified" for b in blockers))
        contradictory={**base, "resources":[{**base["resources"][0], "reason":"application_dependency_planned"}]}
        ok, blockers=op.classify_plan_safety(contradictory)
        self.assertFalse(ok)
        self.assertTrue(blockers)

    def test_stage9_deployment_action_not_requested_context_is_fail_closed(self):
        def fixture(resource="backend_deployment", **action_updates):
            top={"status":"not_requested","user_requested":False,"request_attempted":False,"write_performed":False,"write_outcome_unknown":False,"verified":False}
            action={"resource":resource,"action":"plan","status":"not_requested","request_attempted":False,"write_performed":False,"write_outcome_unknown":False,"verified":False}
            action.update(action_updates)
            return {"status":"dry_run","selected_destination":{"status":"found"},resource:dict(top),"actions":[action],"blockers":[]}
        self.assertTrue(op.classify_plan_safety(fixture("backend_deployment"))[0])
        self.assertTrue(op.classify_plan_safety(fixture("frontend_deployment"))[0])
        for bad in (
            {"resource":"arbitrary_deployment"},
            {"request_attempted":True},
            {"write_performed":True},
            {"write_outcome_unknown":True},
        ):
            ok, blockers=op.classify_plan_safety(fixture(**bad))
            self.assertFalse(ok)
            self.assertTrue(any(b.get("reason") == "not_requested_outside_deployment_context" for b in blockers))
        requested=fixture()
        requested["backend_deployment"]["user_requested"] = True
        self.assertFalse(op.classify_plan_safety(requested)[0])
        missing=fixture()
        del missing["backend_deployment"]
        self.assertFalse(op.classify_plan_safety(missing)[0])
        outside={"status":"dry_run","selected_destination":{"status":"found"},"some_node":{"resource":"backend_deployment","status":"not_requested","request_attempted":False,"write_performed":False,"write_outcome_unknown":False,"verified":False},"backend_deployment":{"status":"not_requested","user_requested":False},"blockers":[]}
        self.assertFalse(op.classify_plan_safety(outside)[0])

    def test_destination_identifier_validation_and_workflow_required(self):
        for valid in ("xs8e9kfps9c7dmes414gyt05", "bk8cw0gkswgo04o0s88sk0kg", "rowwocok08sgg800ogkogkc4"):
            self.assertEqual(op.validate_coolify_destination_identifier(valid), valid)
        bad_values = (
            None,
            "",
            "   ",
            " bk8cw0gkswgo04o0s88sk0kg",
            "bk8cw0gkswgo04o0s88sk0kg ",
            "bk8cw0gkswgo04o0s88sk0kg rowwocok08sgg800ogkogkc4",
            "bk8cw0gkswgo04o0s88sk0kg,rowwocok08sgg800ogkogkc4",
            '["bk8cw0gkswgo04o0s88sk0kg"]',
            '{"destination_uuid":"bk8cw0gkswgo04o0s88sk0kg"}',
            "https://coolify.example/destinations/bk8cw0gkswgo04o0s88sk0kg",
            "/destinations/bk8cw0gkswgo04o0s88sk0kg",
            "bk8cw0gkswgo04o0s88sk0kg?foo=bar",
            "bk8cw0gkswgo04o0s88sk0kg#fragment",
            "user:pass@bk8cw0gkswgo04o0s88sk0kg",
            "bk8cw0gkswgo04o0s88sk0kg\n",
            "bk8cw0gkswgo04o0s88sk0kg\t",
            "bk8cw0gkswgo04o0s88sk0kg" + chr(1),
            "a" * (op.COOLIFY_IDENTIFIER_MAX_LENGTH + 1),
        )
        for bad in bad_values:
            with self.subTest(bad=bad):
                with self.assertRaises(pp.ProvisioningError):
                    op.validate_coolify_destination_identifier(bad)
        text=Path(".github/workflows/product-development.yml").read_text()
        self.assertRegex(text, r"destination_uuid:\n\s+required: true")
        self.assertIn("default: localhost", text)
        self.assertNotIn("default: development", text)

    def test_not_checked_is_contextual_for_unrequested_deployments(self):
        safe={"status":"dry_run","selected_destination":{"status":"found"},"backend_deployment":{"status":"not_requested","user_requested":False},"backend_runtime_readiness":{"status":"not_checked"},"backend_smoke_check":{"status":"not_checked"},"frontend_deployment":{"status":"not_requested","user_requested":False},"frontend_runtime_readiness":{"status":"not_checked"},"frontend_smoke_check":{"status":"not_checked"},"blockers":[]}
        self.assertTrue(op.classify_plan_safety(safe)[0])
        unsafe=dict(safe)
        unsafe["backend_deployment"]={"status":"would_deploy","user_requested":True}
        ok, blockers=op.classify_plan_safety(unsafe)
        self.assertFalse(ok)
        self.assertTrue(any(b.get("reason") == "not_checked_without_unrequested_deployment" for b in blockers))
        self.assertNotIn("not_checked", op.SAFE_STATUSES)

    def test_dry_run_deploy_plan_allows_runtime_checks_deferred_to_apply(self):
        plan = {"status": "dry_run", "mode": "dry_run", "selected_destination": {"status": "found"}, "backend_deployment": {"status": "not_requested", "user_requested": True}, "backend_runtime_readiness": {"status": "not_checked"}, "backend_smoke_check": {"status": "not_checked"}, "frontend_deployment": {"status": "not_requested", "user_requested": True}, "frontend_runtime_readiness": {"status": "not_checked"}, "frontend_smoke_check": {"status": "not_checked"}, "blockers": []}
        self.assertTrue(op.classify_plan_safety(plan)[0])

    def test_execution_metadata_and_exit_codes(self):
        with mock.patch.dict(os.environ, env_for("deploy-backend","plan"), clear=True), mock.patch.object(op,"provision",return_value={"status":"dry_run","selected_destination":{"status":"found"},"backend_deployment":{"status":"would_deploy"}}):
            code,res=op.orchestrate(oargs(operation="deploy-backend", phase="plan"), client_factory=lambda: object())
        self.assertEqual(code,0); self.assertTrue(res["orchestration"]["plan_safe"]); self.assertEqual(res["orchestration"]["next_action"],"apply")
        for status in ("exists", "created", "ready"):
            with mock.patch.dict(os.environ, env_for("deploy-backend","apply"), clear=True), mock.patch.object(op,"provision",return_value={"status":status}):
                code,res=op.orchestrate(oargs(operation="deploy-backend", phase="apply", confirmation="deploy-backend:ledger:dev"))
            self.assertEqual(code,0); self.assertEqual(res["orchestration"]["next_action"],"complete")
        for status in ("partial", "blocked", "write_outcome_unknown", "created_unverified", "future_status"):
            with mock.patch.dict(os.environ, env_for("deploy-backend","apply"), clear=True), mock.patch.object(op,"provision",return_value={"status":status}):
                code,res=op.orchestrate(oargs(operation="deploy-backend", phase="apply", confirmation="deploy-backend:ledger:dev"))
            self.assertNotEqual(code,0); self.assertEqual(res["orchestration"]["next_action"],"inspect_failure"); self.assertNotEqual(res["orchestration"]["next_action"],"complete")

    def test_observed_variable_metadata_visible_values_redacted(self):
        observed={"observed_variable_names":["DATABASE_URL","VITE_API_URL"],"observed_variable_name_counts":{"DATABASE_URL":1},"DATABASE_URL":"postgres://u:p@h/db"}
        red=pp.redact(observed)
        self.assertEqual(red["observed_variable_names"], ["DATABASE_URL","VITE_API_URL"])
        self.assertEqual(red["observed_variable_name_counts"], {"DATABASE_URL":1})
        self.assertEqual(red["DATABASE_URL"], "[REDACTED]")

    def test_no_process_execution_apis_and_no_duplicate_top_level_functions(self):
        tree=ast.parse(Path("scripts/orchestrate_product_development.py").read_text())
        forbidden=[]; names=[]
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"system","popen","run","Popen","call","check_call","check_output"}: forbidden.append(node.attr)
            if isinstance(node, ast.Name) and node.id in {"exec","eval"}: forbidden.append(node.id)
        for node in tree.body:
            if isinstance(node, ast.FunctionDef): names.append(node.name)
        self.assertEqual(forbidden, [])
        self.assertEqual(len(names), len(set(names)))

    def test_workflow_contract_operation_phase_branch_checkout_summary_and_secrets(self):
        text=Path(".github/workflows/product-development.yml").read_text()
        self.assertIn("workflow_dispatch:", text)
        for marker in ["\npush:", "pull_request", "pull_request_target", "schedule:", "repository_dispatch", "continue-on-error", "ssh ", "rsync", "scp ", "docker "]:
            self.assertNotIn(marker, text.lower())
        self.assertIn("permissions:" + "\n" + "  contents: read", text)
        self.assertIn("environment: dev", text); self.assertIn("timeout-minutes: 60", text)
        self.assertIn("group: goodmorning-factory-dev-${{ inputs.product }}", text); self.assertIn("cancel-in-progress: false", text)
        self.assertIn("github.ref != 'refs/heads/dev'", text); self.assertNotIn("github.ref_name != 'dev'", text); self.assertNotIn("refs/tags/dev", text)
        self.assertIn("ref: ${{ github.sha }}", text); self.assertNotIn("ref: dev", text)
        self.assertIn('"--operation", os.environ["INPUT_OPERATION"]', text)
        self.assertIn('"--phase", "plan"', text); self.assertIn('"--phase", "apply"', text)
        self.assertLess(text.index('"--phase", "plan"'), text.index('"--phase", "apply"'))
        self.assertIn('COOLIFY_API_TOKEN: ${{ secrets.COOLIFY_API_TOKEN }}', text)
        self.assertIn('COOLIFY_BASE_URL: ${{ secrets.COOLIFY_BASE_URL }}', text)
        self.assertNotIn("COOLIFY_API_TOKEN:" + "\n" + "        required", text)
        self.assertIn('product = product_raw if re.fullmatch', text)
        self.assertIn('else "<invalid>"', text)
        self.assertNotIn("INPUT_CONFIRMATION", text[text.index("Write safe operation summary"):])

        ci = Path(".github/workflows/factory-ci.yml").read_text()
        stage9_block = ci[ci.index("Verify Stage 9 orchestration semantic contract"):]
        self.assertNotIn("${{", stage9_block)
        self.assertIn("dollar = '$'", stage9_block)
        self.assertIn("github_sha_expression = dollar + '{{ github.sha }}'", stage9_block)

if __name__ == "__main__":
    unittest.main()
