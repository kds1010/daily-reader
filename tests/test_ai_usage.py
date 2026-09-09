import json
import os
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from daily_reader.ai_usage import UsageCapture, run_codex, usage_context


def records():
    lines = Path(os.environ["TSUGITATE_USAGE_LOG"]).read_text().splitlines()
    return [json.loads(line) for line in lines]


def completed(**usage):
    return {"type": "turn.completed", "usage": usage}


def test_records_requested_and_observed_model_separately_without_content():
    with usage_context("daymeld-agent-planning", "job-1"), UsageCapture([
        "codex", "exec", "--model", "alias", "-c", 'model_reasoning_effort="low"',
        "secret prompt",
    ]) as usage:
        usage.observe({"type": "turn_context", "payload": {"model": "actual-model"}})
        usage.observe(completed(input_tokens=100, cached_input_tokens=70, output_tokens=10))
        usage.observe({"type": "item.completed", "item": {"text": "secret response"}})
        usage.return_code = 0
    row = records()[0]
    assert row["schema_version"] == 1
    assert row["task_type"] == "daymeld-agent-planning"
    assert row["task_id"] == "job-1"
    assert row["requested_model"] == "alias"
    assert row["requested_effort"] == "low"
    assert row["input_tokens"] == 100  # cached input is a subset, never added twice
    assert row["cached_input_tokens"] == 70
    assert row["output_tokens"] == 10
    assert row["models"][0]["model"] == "actual-model"
    assert row["cache_creation_input_tokens"] is None
    assert row["cost_usd"] is None
    assert row["cost_kind"] == "unavailable"
    assert row["duration_seconds"] >= 0
    assert row["usage_source"] == "codex_json"
    assert "secret" not in json.dumps(row)
    assert stat.S_IMODE(Path(os.environ["TSUGITATE_USAGE_LOG"]).stat().st_mode) == 0o600


def test_model_absence_never_becomes_requested_model_or_zero_tokens():
    with UsageCapture(["codex", "--model", "requested"]):
        pass
    row = records()[0]
    assert row["models"] == []
    assert row["input_tokens"] is None
    assert row["output_tokens"] is None
    assert row["usage_source"] == "unavailable"


def test_multiple_turns_preserve_actual_models_and_ignore_invalid_numbers():
    with UsageCapture(["codex"]) as usage:
        usage.observe({"type": "turn.started", "model": "first"})
        usage.observe(completed(input_tokens=10, output_tokens=3, cached_input_tokens=0))
        usage.observe({"type": "turn_context", "payload": {"model": "second"}})
        usage.observe(completed(input_tokens=20, output_tokens=5, cached_input_tokens=15))
        usage.observe(completed(input_tokens=-1, output_tokens=True))
        usage.observe({"type": "item.completed", "usage": {"input_tokens": 10000}})
    row = records()[0]
    assert row["input_tokens"] == 30
    assert row["output_tokens"] == 8
    assert row["cached_input_tokens"] == 15
    assert [entry["input_tokens"] for entry in row["models"]] == [10, 20]


@pytest.mark.parametrize("failure, expected_status", [
    (subprocess.CalledProcessError(2, ["codex"]), "error"),
    (subprocess.TimeoutExpired(["codex"], 1), "timeout"),
    (FileNotFoundError(), "error"),
])
def test_run_failures_record_partial_usage_and_propagate(monkeypatch, failure, expected_status):
    if isinstance(failure, subprocess.SubprocessError):
        failure.stdout = json.dumps(completed(input_tokens=7, output_tokens=2)).encode()

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(subprocess, "run", fail)
    with pytest.raises(type(failure)):
        run_codex(["codex"])
    row = records()[0]
    assert row["status"] == expected_status
    assert row["input_tokens"] == (7 if isinstance(failure, subprocess.SubprocessError) else None)


def test_successful_run_retains_process_result(monkeypatch):
    expected = subprocess.CompletedProcess(
        ["codex"], 0, json.dumps(completed(input_tokens=8, output_tokens=0)), "secret stderr"
    )
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: expected)
    assert run_codex(["codex"]) is expected
    row = records()[0]
    assert row["output_tokens"] == 0
    assert row["return_code"] == 0
    assert row["status"] == "success"
    assert "secret" not in json.dumps(row)


def test_cancelled_process_and_nested_task_context():
    with usage_context("daymeld-agent", "job-2"):
        with usage_context("daymeld-agent-deployment"), UsageCapture(["codex"]) as usage:
            usage.return_code = -15
        with UsageCapture(["codex"]):
            pass
    first, second = records()
    assert first["task_id"] == second["task_id"] == "job-2"
    assert first["task_type"] == "daymeld-agent-deployment"
    assert second["task_type"] == "daymeld-agent"
    assert first["status"] == "cancelled"


def test_concurrent_writers_keep_complete_records_and_thread_local_task_ids():
    def write(index):
        with usage_context("daymeld-test", str(index)), UsageCapture(["codex"]) as usage:
            usage.observe(completed(input_tokens=index, output_tokens=1))

    with ThreadPoolExecutor(max_workers=8) as workers:
        list(workers.map(write, range(40)))
    rows = records()
    assert len(rows) == 40
    assert len({row["event_id"] for row in rows}) == 40
    assert all(row["input_tokens"] == int(row["task_id"]) for row in rows)


def test_unwritable_ledger_does_not_change_success_or_reveal_path(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("TSUGITATE_USAGE_LOG", str(tmp_path))
    with UsageCapture(["codex"]):
        pass
    assert capsys.readouterr().err == "Daymeld: AI usage could not be recorded.\n"


def test_default_path_uses_state_home(monkeypatch, tmp_path):
    monkeypatch.delenv("TSUGITATE_USAGE_LOG")
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    with UsageCapture(["codex"]):
        pass
    assert (tmp_path / "tsugitate/ai-usage.jsonl").exists()


def test_conversation_request_records_only_its_request_not_login(monkeypatch, tmp_path):
    from daily_reader.conversation_insights import request_overview

    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}')

    def fake_run(command, **kwargs):
        if command[1:] == ["login", "status"]:
            return subprocess.CompletedProcess(command, 0, "Logged in using ChatGPT", "")
        assert "--json" in command
        Path(command[command.index("--output-last-message") + 1]).write_text(
            '{"overview":{"points":[]}}'
        )
        return subprocess.CompletedProcess(
            command, 0, json.dumps(completed(input_tokens=19, output_tokens=4)), ""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    assert request_overview(
        codex_command="codex", model="requested", schema_path=schema,
        recorded_at=None, timezone="Asia/Tokyo", utterances=[{"id": "u1", "text": "secret"}],
        usage_task_id="recording-1",
    ) == {"points": []}
    assert len(records()) == 1
    row = records()[0]
    assert row["task_type"] == "daymeld-conversation-overview"
    assert row["task_id"] == "recording-1"
    assert row["input_tokens"] == 19


def test_streamed_agent_events_record_usage_without_changing_result(monkeypatch, tmp_path):
    from daily_reader import agent_worker

    def command(_worktree, _schema, _prompt, _thread_id, output_path, *_args):
        script = (
            "import json,sys; from pathlib import Path; "
            "Path(sys.argv[1]).write_text('{\"state\":\"done\"}'); "
            "print(json.dumps({'type':'thread.started','thread_id':'thread-1'})); "
            "print(json.dumps({'type':'turn_context','payload':{'model':'runtime-model'}})); "
            "print(json.dumps({'type':'turn.completed',"
            "'usage':{'input_tokens':33,'output_tokens':6}}))"
        )
        return [sys.executable, "-c", script, str(output_path)]

    monkeypatch.setattr(agent_worker, "_codex_command", command)
    with usage_context("daymeld-agent-implementation", "job-3"):
        thread_id, result, _messages = agent_worker.run_codex_turn(
            tmp_path, tmp_path / "schema", "secret prompt", None
        )
    assert thread_id == "thread-1"
    assert result == {"state": "done"}
    row = records()[0]
    assert row["task_id"] == "job-3"
    assert row["task_type"] == "daymeld-agent-implementation"
    assert row["input_tokens"] == 33
    assert row["models"][0]["model"] == "runtime-model"
