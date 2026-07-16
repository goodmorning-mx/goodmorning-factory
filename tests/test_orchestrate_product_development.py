import argparse
import ast
import os
from pathlib import Path
import unittest
from unittest import mock

import scripts.orchestrate_product_development as op
import scripts.provision_product_development as pp


def oargs(**kw):
    base=dict(operation="plan", product="ledger", server="development", github_app="goodmorning-mx-coolify", destination_uuid=None, confirmation=None)
    base.update(kw)
    return argparse.Namespace(**base)


def env_for(operation):
    return {g:("true" if g in op.ALLOWED_TRUE_GATES[operation] else "false") for g in op.GATES}


class Stage9OrchestratorTests(unittest.TestCase):
    def test_inputs_derive_fixed_contract_and_reject_invalid_slug(self):
        d=op.validate_orchestration_inputs(oargs())
        self.assertEqual(d["repository"], "goodmorning-mx/ledger")
        self.assertEqual(d["branch"], "dev")
        self.assertEqual(d["environment"], "dev")
        self.assertEqual(d["api_domain"], "https://api.dev.ledger.goodmorning.mx")
        self.assertEqual(d["frontend_domain"], "https://dev.ledger.goodmorning.mx")
        with self.assertRaises(pp.ProvisioningError): op.validate_orchestration_inputs(oargs(product="Bad_Slug"))

    def test_confirmation_contract_exact(self):
        self.assertEqual(op.expected_confirmation("deploy-full","ledger"), "deploy-full:ledger:dev")
        for oper in ("provision","deploy-backend","deploy-full"):
            with mock.patch.dict(os.environ, env_for(oper), clear=True), mock.patch.object(op, "provision") as prov:
                prov.return_value={"status":"dry_run"}
                op.orchestrate(oargs(operation=oper, confirmation=op.expected_confirmation(oper,"ledger")))
        for bad in (None, "Deploy-full:ledger:dev", "deploy-full:other:dev", "deploy-full:ledger:prod"):
            with mock.patch.dict(os.environ, env_for("deploy-full"), clear=True), mock.patch.object(op, "provision") as prov:
                with self.assertRaises(pp.ProvisioningError): op.orchestrate(oargs(operation="deploy-full", confirmation=bad))
                prov.assert_not_called()

    def test_gate_matrix_fail_closed_before_requests(self):
        for oper in op.OPERATIONS:
            with mock.patch.dict(os.environ, env_for(oper), clear=True):
                op.validate_operation_gates(oper)
        with mock.patch.dict(os.environ, {"COOLIFY_ALLOW_RESOURCE_WRITES":"true"}, clear=True), mock.patch.object(op, "provision") as prov:
            with self.assertRaises(pp.ProvisioningError): op.orchestrate(oargs(operation="plan"))
            prov.assert_not_called()
        bad=env_for("provision"); bad["COOLIFY_ALLOW_FRONTEND_DEPLOYMENT"]="true"
        with mock.patch.dict(os.environ, bad, clear=True), mock.patch.object(op, "provision") as prov:
            with self.assertRaises(pp.ProvisioningError): op.orchestrate(oargs(operation="provision", confirmation="provision:ledger:dev"))
            prov.assert_not_called()

    def test_argv_operations_names_order_destination_and_no_secret(self):
        cases={"plan":[], "provision":[], "deploy-backend":["--deploy-backend"], "deploy-full":["--deploy-backend","--deploy-frontend"]}
        for oper, flags in cases.items():
            a=oargs(operation=oper, destination_uuid="dest-1", confirmation=op.expected_confirmation(oper,"ledger"))
            argv=op.build_provisioner_argv(a)
            self.assertIn("--destination-uuid", argv)
            self.assertIn("goodmorning-mx/ledger", argv)
            self.assertIn("dev", argv)
            for flag in flags: self.assertIn(flag, argv)
            joined=" ".join(argv)
            self.assertNotIn("COOLIFY_API_TOKEN", joined)
            if oper == "deploy-full":
                self.assertLess(argv.index("--deploy-backend"), argv.index("--deploy-frontend"))

    def test_plan_classification_safe_and_unsafe_nested_redacted(self):
        safe={"status":"dry_run","resources":[{"status":"absent"},{"action":"would_create"}],"blockers":[]}
        self.assertTrue(op.classify_plan_safety(safe)[0])
        for status in ["duplicate","incompatible","out_of_scope","scope_unverified","legacy_compose_migration_required","write_outcome_unknown"]:
            ok, blockers=op.classify_plan_safety({"status":"dry_run","nested":{"status":status}})
            self.assertFalse(ok); self.assertTrue(blockers)

    def test_execution_injects_client_factory_propagates_exit_and_partial_fails(self):
        calls=[]
        def fake(pargs, client_factory=None):
            calls.append((pargs, client_factory)); return {"status":"dry_run"}
        with mock.patch.dict(os.environ, env_for("plan"), clear=True), mock.patch.object(op,"provision",side_effect=fake):
            code,res=op.orchestrate(oargs(), client_factory=lambda: object())
        self.assertEqual(code,0); self.assertEqual(len(calls),1); self.assertEqual(res["orchestration"]["phase"],"plan")
        with mock.patch.dict(os.environ, env_for("deploy-backend"), clear=True), mock.patch.object(op,"provision",return_value={"status":"partial"}):
            code,_=op.orchestrate(oargs(operation="deploy-backend", confirmation="deploy-backend:ledger:dev"))
        self.assertEqual(code,2)

    def test_observed_variable_metadata_visible_values_redacted(self):
        observed={"observed_variable_names":["DATABASE_URL","VITE_API_URL"],"observed_variable_name_counts":{"DATABASE_URL":1},"DATABASE_URL":"postgres://u:p@h/db"}
        red=pp.redact(observed)
        self.assertEqual(red["observed_variable_names"], ["DATABASE_URL","VITE_API_URL"])
        self.assertEqual(red["observed_variable_name_counts"], {"DATABASE_URL":1})
        self.assertEqual(red["DATABASE_URL"], "[REDACTED]")

    def test_no_process_execution_apis(self):
        tree=ast.parse(Path("scripts/orchestrate_product_development.py").read_text())
        forbidden=[]
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in {"system","popen","run","Popen","call","check_call","check_output"}: forbidden.append(node.attr)
            if isinstance(node, ast.Name) and node.id in {"exec","eval"}: forbidden.append(node.id)
        self.assertEqual(forbidden, [])

    def test_workflow_contract(self):
        text=Path(".github/workflows/product-development.yml").read_text()
        self.assertIn("workflow_dispatch:", text)
        forbidden_triggers=["\npush:", "pull_request", "pull_request_target", "schedule:", "repository_dispatch"]
        for marker in forbidden_triggers:
            self.assertNotIn(marker, text)
        self.assertIn("permissions:" + "\n" + "  contents: read", text)
        self.assertIn("environment: dev", text)
        self.assertIn("timeout-minutes: 60", text)
        self.assertIn("group: goodmorning-factory-dev-${{ inputs.product }}", text)
        self.assertIn("cancel-in-progress: false", text)
        self.assertIn("github.ref_name != 'dev'", text)
        for choice in op.OPERATIONS:
            self.assertIn(f"          - {choice}", text)
        self.assertNotIn("COOLIFY_API_TOKEN:" + "\n" + "        required", text)
        self.assertNotIn("COOLIFY_BASE_URL:" + "\n" + "        required", text)
        self.assertLess(text.index("Run Stage 9 plan"), text.index("Run Stage 9 apply"))
        for bad in ["continue-on-error", "ssh ", "rsync", "scp ", "docker "]:
            self.assertNotIn(bad, text.lower())


if __name__ == "__main__":
    unittest.main()
