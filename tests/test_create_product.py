import argparse
import io
import json
import os
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

import scripts.create_product as cp


def args(**kw):
    base = dict(name="test-product", description="A test product", owner=cp.ORG, template_owner=cp.ORG, template_repo=cp.TEMPLATE_REPO)
    base.update(kw)
    return argparse.Namespace(**base)


class CreateProductTests(unittest.TestCase):
    def test_valid_product_name(self):
        cp.validate_name("abc-123")

    def test_invalid_uppercase_name(self):
        with self.assertRaises(cp.ProvisioningError):
            cp.validate_name("Bad")

    def test_invalid_characters(self):
        with self.assertRaises(cp.ProvisioningError):
            cp.validate_name("bad_name")

    def test_leading_or_trailing_hyphen(self):
        for name in ("-bad", "bad-"):
            with self.assertRaises(cp.ProvisioningError):
                cp.validate_name(name)

    def test_excessive_length(self):
        with self.assertRaises(cp.ProvisioningError):
            cp.validate_name("a" * 101)

    def test_reserved_names(self):
        for name in cp.RESERVED:
            with self.assertRaises(cp.ProvisioningError):
                cp.validate_name(name)

    def test_owner_override_rejection(self):
        with self.assertRaises(cp.ProvisioningError):
            cp.validate_inputs(args(owner="other-org"))

    def test_existing_repository_rejection(self):
        with mock.patch.object(cp, "get_repo", return_value={"name": "test-product"}):
            with self.assertRaises(cp.ProvisioningError) as ctx:
                cp.provision(args())
        self.assertEqual(ctx.exception.status, "already_exists")

    def test_successful_template_generation_and_final_json_result(self):
        calls = []
        def fake_request(method, path, **kwargs):
            calls.append((method, path, kwargs.get("body")))
            if path == "/repos/goodmorning-mx/test-product/topics" and method == "GET":
                return cp.Response(200, {"names": cp.TOPICS}, {})
            if method == "POST" and path.endswith("/generate"):
                self.assertTrue(kwargs["body"]["private"])
                self.assertFalse(kwargs["body"]["include_all_branches"])
                return cp.Response(201, {"html_url": "https://github.com/goodmorning-mx/test-product"}, {})
            if method == "POST" and path.endswith("/git/refs"):
                self.assertEqual(kwargs["body"], {"ref": "refs/heads/dev", "sha": "abc123"})
                return cp.Response(201, {}, {})
            if method == "PATCH":
                self.assertEqual(kwargs["body"], {"default_branch": "dev"})
                return cp.Response(200, {}, {})
            if method == "PUT" and path.endswith("/topics"):
                self.assertEqual(kwargs["body"], {"names": cp.TOPICS})
                return cp.Response(200, {"names": cp.TOPICS}, {})
            raise AssertionError((method, path))
        with mock.patch.object(cp, "get_repo", side_effect=[None, {"is_template": True}, {"private": True, "default_branch": "dev", "html_url": "https://github.com/goodmorning-mx/test-product"}, {"private": True, "default_branch": "dev", "html_url": "https://github.com/goodmorning-mx/test-product"}]), \
             mock.patch.object(cp, "request", side_effect=fake_request), \
             mock.patch.object(cp, "wait_for_branch", return_value={"commit": {"sha": "abc123"}}):
            result = cp.provision(args())
        self.assertEqual(result["status"], "created")
        self.assertEqual(result["main_branch"], "main")
        self.assertEqual(result["development_branch"], "dev")
        self.assertEqual(result["default_branch"], "dev")
        self.assertTrue(result["private"])
        self.assertTrue(set(cp.TOPICS).issubset(result["topics"]))
        self.assertIn(("PATCH", "/repos/goodmorning-mx/test-product", {"default_branch": "dev"}), calls)

    def test_main_sha_retrieval(self):
        with mock.patch.object(cp, "get_branch", return_value={"commit": {"sha": "sha1"}}), mock.patch.object(cp.time, "sleep"):
            self.assertEqual(cp.wait_for_branch("o", "r", "main")["commit"]["sha"], "sha1")

    def test_safe_partial_failure_reporting(self):
        def fake_request(method, path, **kwargs):
            if method == "POST" and path.endswith("/generate"):
                return cp.Response(201, {"html_url": "url"}, {})
            raise cp.ProvisioningError("boom")
        with mock.patch.object(cp, "get_repo", side_effect=[None, {"is_template": True}, {"private": True}]), mock.patch.object(cp, "request", side_effect=fake_request):
            with self.assertRaises(cp.ProvisioningError) as ctx:
                cp.provision(args())
        self.assertEqual(ctx.exception.status, "partial")
        self.assertEqual(ctx.exception.partial["repository"], "test-product")

    def test_authentication_failures(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(cp.ProvisioningError):
                cp.token()

    def test_token_redaction_from_errors_and_logs(self):
        with mock.patch.dict(os.environ, {"GH_TOKEN": "secret-token"}):
            self.assertNotIn("secret-token", cp.redact("bad secret-token value"))

    def test_main_prints_json(self):
        result = {"status": "created", "repository": "test-product"}
        out = io.StringIO()
        with mock.patch.object(cp, "provision", return_value=result), redirect_stdout(out):
            self.assertEqual(cp.main(["--name", "test-product", "--description", "desc"]), 0)
        self.assertEqual(json.loads(out.getvalue()), result)

    def test_main_redacts_error_json(self):
        err = io.StringIO()
        with mock.patch.dict(os.environ, {"GH_TOKEN": "secret-token"}), redirect_stderr(err):
            code = cp.main(["--name", "Bad", "--description", "secret-token"])
        self.assertNotEqual(code, 0)
        self.assertNotIn("secret-token", err.getvalue())


if __name__ == "__main__":
    unittest.main()
