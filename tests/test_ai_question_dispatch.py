import ast
from pathlib import Path

from services.ai import views as ai_views


def _function(name):
    tree = ast.parse(Path(ai_views.__file__.replace(".pyc", ".py")).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return ast.unparse(node)
    raise AssertionError(f"{name} not found")


def test_ai_question_uses_builtin_ai_ops_assistant():
    source = _function("ai_question")
    assert "role_key='ai-ops-assistant'" in source
    assert "CommandAgent" in source


def test_ai_question_creates_command_task_with_context():
    source = _function("ai_question")
    assert "CommandTask.create" in source
    assert "user_query" in source
    assert "ai_type" in source
    assert "room" in source


def test_ai_question_keeps_playbook_fallback():
    source = _function("ai_question")
    assert "PlaybookVersion" in source
    assert "TASK_TOPIC" in source


def test_ai_question_scopes_agent_lookup_to_tenant_and_active_records():
    source = _function("ai_question")
    assert "deleted=False" in source
    assert "tenant_id=tenant_id" in source
