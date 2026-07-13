from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "verify-coolify-connection.yml"
DOCS = ROOT / "docs" / "coolify-connection-verification.md"


def workflow_text():
    return WORKFLOW.read_text(encoding="utf-8")


def test_workflow_is_manual_and_named():
    text = workflow_text()
    assert "name: Verify Coolify connection" in text
    assert "workflow_dispatch:" in text


def test_workflow_uses_required_coolify_configuration():
    text = workflow_text()
    assert "${{ vars.COOLIFY_BASE_URL }}" in text
    assert "${{ secrets.COOLIFY_API_TOKEN }}" in text
    assert "/api/v1/teams" in text
    assert "--request GET" in text
    assert "Authorization: Bearer ${COOLIFY_API_TOKEN}" in text


def test_workflow_does_not_print_sensitive_response_data():
    text = workflow_text()
    assert "::add-mask::${COOLIFY_API_TOKEN}" in text
    assert "--output \"${response_file}\"" in text
    assert "cat ${response_file}" not in text
    assert "cat \"${response_file}\"" not in text


def test_workflow_has_clear_failure_paths():
    text = workflow_text()
    assert "401)" in text
    assert "403)" in text
    assert "000)" in text
    assert "unable to connect" in text


def test_documentation_mentions_scope_and_configuration():
    text = DOCS.read_text(encoding="utf-8")
    assert "Verify Coolify connection" in text
    assert "vars.COOLIFY_BASE_URL" in text
    assert "secrets.COOLIFY_API_TOKEN" in text
    assert "without creating, changing, or deploying" in text
