import json
from pathlib import Path

import pytest

from scripts.validate_module_install_refs import ModuleInstallPinError, validate_repository


SHA = "a" * 40
REGISTRY = {
    "schema": 1,
    "modules": [{
        "name": "gm-payments",
        "backend_package": "gm-payments",
        "frontend_package": None,
        "release_candidates": {
            "0.1.1": {
                "version": "0.1.1", "commit_sha": SHA, "formal_release": True,
                "checks_passed": True, "installable": True, "blockers": [],
            }
        },
    }],
}


def scan(tmp_path: Path, files: dict[str, str], base: dict[str, str] | None = None) -> list[str]:
    root = tmp_path / "product"
    root.mkdir()
    for name, contents in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding="utf-8")
    return validate_repository(root, REGISTRY, read_base=lambda path: (base or {}).get(path.relative_to(root).as_posix(), ""))


def test_accepts_new_backend_dependency_only_when_it_matches_approved_sha(tmp_path):
    checked = scan(tmp_path, {
        "backend/requirements.txt": f"gm-payments @ git+https://github.com/goodmorning-mx/gm-payments.git@{SHA}\n"
    })
    assert checked == ["backend/requirements.txt:gm-payments"]


def test_accepts_new_frontend_dependency_pinned_to_approved_sha(tmp_path):
    value = {"dependencies": {"@goodmorning/gm-payments": f"git+https://github.com/goodmorning-mx/gm-payments.git#{SHA}"}}
    checked = scan(tmp_path, {"frontend/package.json": json.dumps(value)})
    assert checked == ["frontend/package.json:gm-payments"]


@pytest.mark.parametrize("requirement", [
    "gm-payments>=0.1.1",
    "gm-payments @ git+https://github.com/goodmorning-mx/gm-payments.git@v0.1.1",
    "gm-payments @ git+https://github.com/goodmorning-mx/gm-payments.git@" + "b" * 40,
])
def test_rejects_unpinned_or_unapproved_backend_module_dependency(tmp_path, requirement):
    with pytest.raises(ModuleInstallPinError, match="approved full GitHub commit SHA"):
        scan(tmp_path, {"backend/requirements.txt": requirement + "\n"})


def test_existing_unpinned_module_dependencies_remain_unchanged(tmp_path):
    old = "gm-payments>=0.1.1\n"
    checked = scan(tmp_path, {"backend/requirements.txt": old}, {"backend/requirements.txt": old})
    assert checked == []


def test_blocks_new_module_dependencies_unknown_to_factory(tmp_path):
    with pytest.raises(ModuleInstallPinError, match="not registered"):
        scan(tmp_path, {"backend/requirements.txt": "gm-unknown @ git+https://github.com/goodmorning-mx/gm-unknown.git@" + SHA + "\n"})
