#!/usr/bin/env python3
"""Provision a private GoodMorning product repository from the MVP template."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

API = "https://api.github.com"
ORG = "goodmorning-mx"
TEMPLATE_REPO = "goodmorning-mvp-template"
MAX_REPO_NAME_LENGTH = 100
TOPICS = ["goodmorning", "goodmorning-product", "managed-by-goodmorning-factory"]
RESERVED = {"goodmorning-factory", "goodmorning-mvp-template"}
NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$")
BRANCH_RETRIES = 12
RETRY_DELAY_SECONDS = 2.0
TIMEOUT_SECONDS = 20


class ProvisioningError(Exception):
    def __init__(self, message: str, *, status: str = "error", partial: dict[str, Any] | None = None, code: int = 1):
        super().__init__(message)
        self.message = redact(message)
        self.status = status
        self.partial = partial or {}
        self.code = code


@dataclass
class Response:
    status: int
    data: Any
    headers: dict[str, str]


def redact(text: str) -> str:
    token = os.environ.get("GH_TOKEN")
    if token:
        text = text.replace(token, "[REDACTED]")
    return text


def validate_name(name: str) -> None:
    if not name:
        raise ProvisioningError("Repository name is required.")
    if len(name) > MAX_REPO_NAME_LENGTH:
        raise ProvisioningError(f"Repository name exceeds GitHub's {MAX_REPO_NAME_LENGTH} character limit.")
    if name in RESERVED:
        raise ProvisioningError(f"Repository name {name!r} is reserved for factory infrastructure.")
    if not NAME_RE.fullmatch(name):
        raise ProvisioningError("Repository name must use lowercase letters, numbers, and hyphens, and start/end with a letter or number.")


def validate_inputs(args: argparse.Namespace) -> None:
    validate_name(args.name)
    if args.owner != ORG or args.template_owner != ORG:
        raise ProvisioningError("Owner and template owner overrides outside goodmorning-mx are not allowed.")
    if args.template_repo != TEMPLATE_REPO:
        raise ProvisioningError("Template repository override outside goodmorning-mvp-template is not allowed.")
    if not args.description.strip():
        raise ProvisioningError("Description is required.")


def token() -> str:
    value = os.environ.get("GH_TOKEN")
    if not value:
        raise ProvisioningError("GH_TOKEN environment variable is required.")
    return value


def request(method: str, path: str, *, body: dict[str, Any] | None = None, accept: str = "application/vnd.github+json") -> Response:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={
            "Accept": accept,
            "Authorization": f"Bearer {token()}",
            "Content-Type": "application/json",
            "User-Agent": "goodmorning-factory-provisioner",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            raw = resp.read().decode("utf-8")
            parsed = json.loads(raw) if raw else None
            return Response(resp.status, parsed, dict(resp.headers))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"message": raw}
        message = parsed.get("message") if isinstance(parsed, dict) else raw
        raise ProvisioningError(f"GitHub API {exc.code} for {method} {path}: {message}", code=2)
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ProvisioningError(f"Network timeout or connection failure for {method} {path}: {exc}", code=2)
    except json.JSONDecodeError as exc:
        raise ProvisioningError(f"Malformed JSON response for {method} {path}: {exc}", code=2)


def get_repo(owner: str, name: str) -> dict[str, Any] | None:
    try:
        return request("GET", f"/repos/{owner}/{name}").data
    except ProvisioningError as exc:
        if "GitHub API 404" in exc.message:
            return None
        raise


def require_repo(owner: str, name: str) -> dict[str, Any]:
    repo = get_repo(owner, name)
    if not isinstance(repo, dict):
        raise ProvisioningError(f"Repository {owner}/{name} is not available yet.")
    return repo


def get_branch(owner: str, repo: str, branch: str) -> dict[str, Any] | None:
    try:
        data = request("GET", f"/repos/{owner}/{repo}/branches/{branch}").data
        return data if isinstance(data, dict) else None
    except ProvisioningError as exc:
        if "GitHub API 404" in exc.message:
            return None
        raise


def wait_for_branch(owner: str, repo: str, branch: str) -> dict[str, Any]:
    for _ in range(BRANCH_RETRIES):
        data = get_branch(owner, repo, branch)
        if data:
            return data
        time.sleep(RETRY_DELAY_SECONDS)
    raise ProvisioningError(f"Timed out waiting for {owner}/{repo} branch {branch}.", code=3)


def update_topics(owner: str, repo: str) -> list[str]:
    data = request("PUT", f"/repos/{owner}/{repo}/topics", body={"names": TOPICS}, accept="application/vnd.github.mercy-preview+json").data
    if not isinstance(data, dict) or not isinstance(data.get("names"), list):
        raise ProvisioningError("Malformed topics update response from GitHub.")
    return data["names"]


def inspect_existing_repository(args: argparse.Namespace, repo: dict[str, Any]) -> dict[str, Any]:
    main = get_branch(args.owner, args.name, "main")
    dev = get_branch(args.owner, args.name, "dev")
    topics_response = request(
        "GET",
        f"/repos/{args.owner}/{args.name}/topics",
        accept="application/vnd.github.mercy-preview+json",
    ).data
    topics = topics_response.get("names", []) if isinstance(topics_response, dict) else []
    checks = {
        "private": repo.get("private") is True,
        "default_branch_dev": repo.get("default_branch") == "dev",
        "main_branch": isinstance(main, dict),
        "development_branch": isinstance(dev, dict),
        "factory_topics": set(TOPICS).issubset(topics),
    }
    if not all(checks.values()):
        failed = sorted(name for name, passed in checks.items() if not passed)
        raise ProvisioningError(
            f"Repository {args.owner}/{args.name} already exists but is incompatible with the GoodMorning factory contract.",
            status="incompatible_existing",
            partial={"owner": args.owner, "repository": args.name, "failed_checks": failed},
            code=4,
        )
    return {
        "status": "existing",
        "owner": args.owner,
        "repository": args.name,
        "repository_url": repo.get("html_url"),
        "template": f"{args.template_owner}/{args.template_repo}",
        "main_branch": "main",
        "development_branch": "dev",
        "default_branch": "dev",
        "private": True,
        "topics": topics,
    }


def provision(args: argparse.Namespace) -> dict[str, Any]:
    validate_inputs(args)
    existing = get_repo(args.owner, args.name)
    if isinstance(existing, dict):
        return inspect_existing_repository(args, existing)

    template = require_repo(args.template_owner, args.template_repo)
    if not template.get("is_template"):
        raise ProvisioningError(f"Source repository {args.template_owner}/{args.template_repo} is not marked as a template.")

    partial: dict[str, Any] = {"owner": args.owner, "repository": args.name, "template": f"{args.template_owner}/{args.template_repo}"}
    generated = False
    try:
        created = request("POST", f"/repos/{args.template_owner}/{args.template_repo}/generate", body={
            "owner": args.owner,
            "name": args.name,
            "description": args.description,
            "private": True,
            "include_all_branches": False,
        }).data
        if not isinstance(created, dict):
            raise ProvisioningError("Malformed repository generation response from GitHub.")
        generated = True
        partial["repository_url"] = created.get("html_url")
        repo = require_repo(args.owner, args.name)
        main = wait_for_branch(args.owner, args.name, "main")
        sha = main.get("commit", {}).get("sha")
        if not sha:
            raise ProvisioningError("Malformed main branch response: missing commit SHA.")
        partial["main_branch"] = "main"
        request("POST", f"/repos/{args.owner}/{args.name}/git/refs", body={"ref": "refs/heads/dev", "sha": sha})
        partial["development_branch"] = "dev"
        request("PATCH", f"/repos/{args.owner}/{args.name}", body={"default_branch": "dev"})
        partial["default_branch"] = "dev"
        topics = update_topics(args.owner, args.name)
        partial["topics"] = topics

        repo = require_repo(args.owner, args.name)
        main = wait_for_branch(args.owner, args.name, "main")
        dev = wait_for_branch(args.owner, args.name, "dev")
        got_topics = request("GET", f"/repos/{args.owner}/{args.name}/topics", accept="application/vnd.github.mercy-preview+json").data
        names = got_topics.get("names", []) if isinstance(got_topics, dict) else []
        if repo.get("private") is not True or not main or not dev or repo.get("default_branch") != "dev" or not set(TOPICS).issubset(names):
            raise ProvisioningError("Provisioning verification failed.", partial=partial, status="partial")
        return {"status": "created", "owner": args.owner, "repository": args.name, "repository_url": repo.get("html_url"), "template": partial["template"], "main_branch": "main", "development_branch": "dev", "default_branch": "dev", "private": True, "topics": names}
    except ProvisioningError as exc:
        exc.partial = {**partial, **exc.partial}
        if generated and exc.status == "error":
            exc.status = "partial"
        raise


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Create a private GoodMorning product repository from the MVP template.")
    p.add_argument("--name", required=True)
    p.add_argument("--description", required=True)
    p.add_argument("--owner", default=ORG)
    p.add_argument("--template-owner", default=ORG)
    p.add_argument("--template-repo", default=TEMPLATE_REPO)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        result = provision(args)
        print(json.dumps(result, sort_keys=True))
        return 0
    except ProvisioningError as exc:
        result = {"status": exc.status, "error": exc.message, **exc.partial}
        print(json.dumps(result, sort_keys=True), file=sys.stderr)
        return exc.code


if __name__ == "__main__":
    raise SystemExit(main())
