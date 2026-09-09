import pytest


@pytest.fixture(autouse=True)
def isolated_ai_usage_log(monkeypatch, tmp_path):
    """Mocked Codex requests must never enter the user's production usage ledger."""
    monkeypatch.setenv("TSUGITATE_USAGE_LOG", str(tmp_path / "ai-usage.jsonl"))
