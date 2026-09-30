import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest
from googleapiclient.errors import HttpError

from daily_reader import email_assistant as gmail


def quota_error(status=403, reason="rateLimitExceeded"):
    return HttpError(
        SimpleNamespace(status=status, reason="failure"),
        json.dumps({"error": {"errors": [{"reason": reason}]}}).encode(),
    )


@pytest.mark.parametrize("status,reason", [
    (403, "rateLimitExceeded"), (403, "userRateLimitExceeded"), (429, "quota"),
])
def test_transient_quota_retries_same_request_with_pacing(monkeypatch, status, reason):
    sleeps = []
    monkeypatch.setattr(gmail.time, "sleep", sleeps.append)
    monkeypatch.setattr(gmail.random, "random", lambda: 0.25)
    calls = []

    def execute():
        calls.append(True)
        if len(calls) < 3:
            raise quota_error(status, reason)
        return {"threads": [{"id": "saved"}]}

    result = gmail._execute_gmail_sync_request(SimpleNamespace(execute=execute))
    assert result == {"threads": [{"id": "saved"}]}
    assert sleeps == [0.5, 1.25, 0.5, 2.25, 0.5]


@pytest.mark.parametrize("error", [
    quota_error(403, "forbidden"), quota_error(401, "authError"),
    HttpError(SimpleNamespace(status=403, reason="failure"), b"not JSON"),
])
def test_non_quota_errors_are_not_retried(monkeypatch, error):
    sleeps = []
    monkeypatch.setattr(gmail.time, "sleep", sleeps.append)

    def execute():
        raise error

    with pytest.raises(HttpError) as raised:
        gmail._execute_gmail_sync_request(SimpleNamespace(execute=execute))
    assert raised.value is error
    assert sleeps == [0.5]


def test_quota_retries_are_bounded(monkeypatch):
    sleeps = []
    calls = []
    monkeypatch.setattr(gmail.time, "sleep", sleeps.append)
    monkeypatch.setattr(gmail.random, "random", lambda: 0)

    def execute():
        calls.append(True)
        raise quota_error()

    with pytest.raises(HttpError):
        gmail._execute_gmail_sync_request(SimpleNamespace(execute=execute))
    assert len(calls) == 8
    assert sleeps == [0.5, 1, 0.5, 2, 0.5, 4, 0.5, 8, 0.5, 16, 0.5, 32, 0.5, 32, 0.5]


def test_concurrent_syncs_do_not_overlap_and_release_lock_on_error(tmp_path, monkeypatch):
    first_entered = Event()
    second_started = Event()
    second_entered = Event()
    release_first = Event()
    token = tmp_path / "token.json"
    calls = []

    def sync(*args):
        calls.append(True)
        if len(calls) == 1:
            first_entered.set()
            assert release_first.wait(5)
            raise RuntimeError("failed sync")
        second_entered.set()
        return 3

    monkeypatch.setattr(gmail, "_sync_gmail_locked", sync)

    def run(second=False):
        if second:
            second_started.set()
        return gmail.sync_gmail(Path("unused"), Path("unused"), token)

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run)
        assert first_entered.wait(5)
        second = pool.submit(run, True)
        assert second_started.wait(5)
        try:
            assert not second_entered.wait(0.1)
            # Sync lock must not block OAuth token replacement.
            with gmail._token_lock(token):
                pass
        finally:
            release_first.set()
        with pytest.raises(RuntimeError, match="failed sync"):
            first.result(timeout=5)
        assert second.result(timeout=5) == 3
    assert (tmp_path / "token.json.sync.lock").stat().st_mode & 0o777 == 0o600
