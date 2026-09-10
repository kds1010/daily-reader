from __future__ import annotations

import io
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

import pytest

from daily_reader import conversation_vocabulary as vocab
from daily_reader import conversations as conv
from daily_reader.local_server import make_handler


@pytest.fixture
def database(tmp_path):
    path = tmp_path / "conversations.sqlite3"
    vocab.list_terms(path)
    return path


def term_payload(**changes):
    return {
        "canonical": "Palo Alto",
        "reading": "パロアルト",
        "aliases": ["パロあると"],
        "enabled": True,
        **changes,
    }


def recording(database):
    raw = "パロあるとの資料を確認します。\n明日は行きません。".encode()
    record = conv.store_transcript(database, io.BytesIO(raw), len(raw), "example.txt")
    row = conv.get_recording(database, record["id"])["utterances"][0]
    return record["id"], row


def feedback_payload(row, **changes):
    return {
        "expected_original_text": row["text"],
        "expected_feedback_revision": 0,
        "corrected_text": "Palo Altoの資料を確認します。",
        **changes,
    }


def test_dictionary_versions_disabled_deleted_and_recreated(database):
    first = vocab.save_term(database, term_payload())
    assert first["revision"] == 1
    assert vocab.list_terms(database)["terms"] == [first]
    with closing(vocab._connect(database)) as connection:
        assert vocab.terms_snapshot(connection)["terms"] == [first]
        assert vocab.terms_snapshot(connection, [{"text": "パロあるとの話"}])["terms"] == [first]
        assert not vocab.terms_snapshot(connection, [{"text": "無関係な話"}])["terms"]
    disabled = vocab.save_term(database, term_payload(enabled=False, revision=1), first["id"])
    assert disabled["revision"] == 2
    with closing(vocab._connect(database)) as connection:
        assert vocab.terms_snapshot(connection) == {"revision": 2, "terms": []}
    with pytest.raises(vocab.VocabularyConflict):
        vocab.delete_term(database, first["id"], {"revision": 1})
    vocab.delete_term(database, first["id"], {"revision": 2})
    assert vocab.list_terms(database) == {"revision": 3, "terms": [], "max_terms": 100}
    recreated = vocab.save_term(database, term_payload())
    assert recreated["id"] != first["id"] and recreated["revision"] == 4
    with closing(vocab._connect(database)) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM conversation_vocabulary_history").fetchone()[0]
            == 4
        )


def test_relevance_uses_english_boundaries_and_unicode_normalization(database):
    saved = vocab.save_term(database, term_payload(canonical="PCPRegen", reading="", aliases=[]))
    with closing(vocab._connect(database)) as connection:
        assert not vocab.terms_snapshot(connection, [{"text": "XPCPRegenSuffix"}])["terms"]
        assert vocab.terms_snapshot(connection, [{"text": "ＰＣＰＲｅｇｅｎを確認"}])["terms"] == [
            saved
        ]
    with pytest.raises(vocab.VocabularyConflict):
        vocab.save_term(database, term_payload(canonical="pcpregen"))


@pytest.mark.parametrize(
    "changes",
    [
        {"canonical": ""},
        {"canonical": "x" * 81},
        {"canonical": "word\ninstruction"},
        {"reading": None},
        {"aliases": ["x"] * 6},
        {"aliases": [5]},
        {"aliases": ["x" * 81]},
        {"aliases": "wrong"},
        {"enabled": 1},
        {"unexpected": "value"},
    ],
)
def test_invalid_terms_do_not_change_dictionary(database, changes):
    with pytest.raises(ValueError):
        vocab.save_term(database, term_payload(**changes))
    assert vocab.list_terms(database)["revision"] == 0


def test_dictionary_limits_count_disabled_but_not_deleted(database, monkeypatch):
    monkeypatch.setattr(vocab, "MAX_TERMS", 2)
    first = vocab.save_term(database, term_payload(enabled=False))
    vocab.save_term(database, term_payload(canonical="PCPRegen"))
    with pytest.raises(ValueError):
        vocab.save_term(database, term_payload(canonical="Third"))
    vocab.delete_term(database, first["id"], {"revision": first["revision"]})
    vocab.save_term(database, term_payload(canonical="Third"))
    assert len(vocab.list_terms(database)["terms"]) == 2


def test_feedback_preserves_source_history_and_does_not_learn_without_opt_in(database):
    recording_id, row = recording(database)
    with closing(vocab._connect(database)) as connection:
        before = tuple(
            connection.execute("SELECT * FROM utterances WHERE id=?", (row["id"],)).fetchone()
        )
    result = vocab.save_feedback(database, recording_id, row["id"], feedback_payload(row))
    assert result["feedback"]["revision"] == 1 and result["vocabulary_term"] is None
    assert vocab.list_terms(database)["terms"] == []
    with closing(vocab._connect(database)) as connection:
        assert (
            tuple(
                connection.execute("SELECT * FROM utterances WHERE id=?", (row["id"],)).fetchone()
            )
            == before
        )
        assert (
            vocab.feedback_for_recording(connection, recording_id)[row["id"]] == result["feedback"]
        )
        rec = connection.execute("SELECT * FROM recordings WHERE id=?", (recording_id,)).fetchone()
        assert rec["correction_status"] == "stale"
        assert rec["correction_requires_review"] == rec["transcription_needs_review"] == 1
    reset = {"expected_original_text": row["text"], "expected_feedback_revision": 1, "reset": True}
    assert vocab.save_feedback(database, recording_id, row["id"], reset)["feedback"] is None
    with closing(vocab._connect(database)) as connection:
        assert vocab.feedback_for_recording(connection, recording_id) == {}
        assert vocab.feedback_revisions(connection, recording_id) == {row["id"]: 2}
    with pytest.raises(vocab.VocabularyConflict):
        vocab.save_feedback(database, recording_id, row["id"], feedback_payload(row))
    again = vocab.save_feedback(
        database, recording_id, row["id"], feedback_payload(row, expected_feedback_revision=2)
    )
    assert again["feedback"]["revision"] == 3
    with closing(vocab._connect(database)) as connection:
        history = connection.execute(
            "SELECT data FROM conversation_user_correction_history ORDER BY revision"
        ).fetchall()
        assert [json.loads(item[0])["active"] for item in history] == [True, False, True]


def test_explicit_term_and_feedback_save_atomically(database):
    recording_id, row = recording(database)
    with pytest.raises(ValueError):
        vocab.save_feedback(
            database, recording_id, row["id"], feedback_payload(row, term={"canonical": ""})
        )
    with closing(vocab._connect(database)) as connection:
        assert vocab.feedback_for_recording(connection, recording_id) == {}
    result = vocab.save_feedback(
        database, recording_id, row["id"], feedback_payload(row, term=term_payload())
    )
    assert result["vocabulary_term"]["canonical"] == "Palo Alto"
    assert vocab.list_terms(database)["terms"] == [result["vocabulary_term"]]


@pytest.mark.parametrize(
    "changes,exception",
    [
        ({"expected_original_text": "other"}, vocab.VocabularyConflict),
        ({"expected_feedback_revision": True}, ValueError),
        ({"expected_feedback_revision": 1}, vocab.VocabularyConflict),
        ({"corrected_text": ""}, ValueError),
        ({"corrected_text": "x" * 8001}, ValueError),
        ({"reset": True}, ValueError),
    ],
)
def test_invalid_feedback_does_not_save_or_register_terms(database, changes, exception):
    recording_id, row = recording(database)
    with pytest.raises(exception):
        vocab.save_feedback(
            database, recording_id, row["id"], feedback_payload(row, term=term_payload(), **changes)
        )
    assert vocab.list_terms(database)["revision"] == 0
    with closing(vocab._connect(database)) as connection:
        assert vocab.feedback_for_recording(connection, recording_id) == {}


@pytest.mark.parametrize(
    "column,status",
    [
        ("status", "queued"),
        ("status", "analyzing"),
        ("insight_status", "extracting"),
        ("overview_status", "extracting"),
        ("correction_status", "verifying"),
    ],
)
def test_feedback_rejects_inflight_processing(database, column, status):
    recording_id, row = recording(database)
    with closing(vocab._connect(database)) as connection, connection:
        connection.execute(f"UPDATE recordings SET {column}=? WHERE id=?", (status, recording_id))
    with pytest.raises(vocab.VocabularyConflict):
        vocab.save_feedback(
            database, recording_id, row["id"], feedback_payload(row, term=term_payload())
        )
    assert vocab.list_terms(database)["terms"] == []


def test_feedback_never_follows_replacement_utterances(database):
    recording_id, row = recording(database)
    vocab.save_feedback(database, recording_id, row["id"], feedback_payload(row))
    with closing(vocab._connect(database)) as connection, connection:
        connection.execute("UPDATE utterances SET text='新しい認識結果' WHERE id=?", (row["id"],))
        assert vocab.feedback_for_recording(connection, recording_id) == {}
        assert vocab.feedback_revisions(connection, recording_id) == {}
        assert (
            connection.execute(
                "SELECT COUNT(*) FROM conversation_user_correction_history"
            ).fetchone()[0]
            == 1
        )
    with pytest.raises(KeyError):
        vocab.save_feedback(database, "other-recording", row["id"], feedback_payload(row))


def test_competing_edits_have_one_winner(database):
    first = vocab.save_term(database, term_payload())

    def update(reading):
        try:
            return vocab.save_term(database, term_payload(reading=reading, revision=1), first["id"])
        except vocab.VocabularyConflict:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(update, ["読みに訂正", "別の訂正"]))
    assert sum(result is not None for result in results) == 1
    assert vocab.list_terms(database)["revision"] == 2


def handler(tmp_path: Path, database):
    factory = make_handler(
        tmp_path,
        tmp_path / "articles",
        tmp_path / "reads",
        tmp_path / "feedback",
        tmp_path / "gmail-db",
        tmp_path / "client",
        tmp_path / "token",
        conversations_db=database,
    )
    instance = factory.func.__new__(factory.func)
    responses = []
    instance._send_json = lambda code, body: responses.append((code, body))
    instance.headers = {"Host": "127.0.0.1:8787", "Content-Type": "application/json"}
    return instance, responses


def request(handler, path, payload):
    handler.path = path
    raw = json.dumps(payload, ensure_ascii=False).encode()
    handler.headers["Content-Length"] = str(len(raw))
    handler.rfile = io.BytesIO(raw)
    handler.do_POST()


def test_http_roundtrip_and_feedback_conflict(tmp_path, database):
    h, responses = handler(tmp_path, database)
    base = "/api/conversation-vocabulary"
    request(h, base, term_payload())
    code, body = responses[-1]
    assert code == 200
    term = body["term"]
    h.path = base
    h.do_GET()
    assert responses[-1][1]["terms"] == [term]
    request(h, base + "/" + term["id"], term_payload(revision=0))
    assert responses[-1][0] == 409
    request(h, base + "/" + term["id"] + "/delete", {"revision": term["revision"]})
    assert responses[-1] == (200, {"deleted": True})
    recording_id, row = recording(database)
    route = f"/api/conversations/{recording_id}/utterances/{row['id']}/feedback"
    request(h, route, feedback_payload(row))
    assert responses[-1][0] == 200
    request(h, route, feedback_payload(row))
    assert responses[-1][0] == 409


@pytest.mark.parametrize(
    "headers,code",
    [
        ({"Host": "attacker.example"}, 403),
        ({"Origin": "https://attacker.example"}, 403),
        ({"Sec-Fetch-Site": "cross-site"}, 403),
        ({"Content-Type": "text/plain"}, 415),
        ({"Transfer-Encoding": "chunked"}, 400),
    ],
)
def test_http_boundary_rejects_before_mutation(tmp_path, database, headers, code):
    h, responses = handler(tmp_path, database)
    h.headers.update(headers)
    request(h, "/api/conversation-vocabulary", term_payload())
    assert responses[-1][0] == code
    assert vocab.list_terms(database)["revision"] == 0


def test_http_read_boundary_and_malformed_routes(tmp_path, database):
    h, responses = handler(tmp_path, database)
    h.headers["Host"] = "attacker.example"
    h.path = "/api/conversation-vocabulary"
    h.do_GET()
    assert responses[-1][0] == 403
    h.headers["Host"] = "localhost:8787"
    for route in [
        "/api/conversation-vocabulary//delete",
        "/api/conversation-vocabulary/id/other",
        "/api/conversations/id/feedback",
    ]:
        request(h, route, {})
        assert responses[-1][0] == 404


def test_feedback_invalidates_dependent_correction_context_without_resubmitting(database):
    recording_id, row = recording(database)
    raw = "後の会話です。".encode()
    dependent = conv.store_transcript(database, io.BytesIO(raw), len(raw), "later.txt")["id"]
    with closing(vocab._connect(database)) as connection, connection:
        connection.execute(
            """INSERT INTO conversation_correction_runs
            (id,recording_id,input_hash,version,model,status,token,contexts,created_at)
            VALUES('dependent-run',?,'hash','version','model','completed','token',?,'now')""",
            (dependent, json.dumps([{"recording_id": recording_id}])),
        )
        connection.execute(
            "UPDATE recordings SET correction_status='completed',"
            "correction_revision_id='dependent-run' WHERE id=?", (dependent,),
        )
    vocab.save_feedback(database, recording_id, row["id"], feedback_payload(row))
    with closing(vocab._connect(database)) as connection:
        state = connection.execute(
            "SELECT correction_status,insight_status,overview_status FROM recordings WHERE id=?",
            (dependent,),
        ).fetchone()
        assert tuple(state) == ("stale", "not_requested", "not_requested")


def test_feedback_preserves_already_created_task_evidence(database):
    recording_id, row = recording(database)
    with closing(vocab._connect(database)) as connection:
        before = {
            table: [tuple(item) for item in connection.execute(f"SELECT * FROM {table}")]
            for table in ("task_proposals", "conversation_items", "conversation_item_evidence")
        }
    vocab.save_feedback(database, recording_id, row["id"], feedback_payload(row))
    with closing(vocab._connect(database)) as connection:
        for table, values in before.items():
            assert [tuple(item) for item in connection.execute(f"SELECT * FROM {table}")] == values


def test_http_conflicts_distinguish_duplicate_busy_and_revision(tmp_path, database):
    h, responses = handler(tmp_path, database)
    base = "/api/conversation-vocabulary"
    request(h, base, term_payload())
    request(h, base, term_payload())
    assert responses[-1][0] == 409
    assert responses[-1][1]["code"] == "duplicate_term"
    recording_id, row = recording(database)
    route = f"/api/conversations/{recording_id}/utterances/{row['id']}/feedback"
    with closing(vocab._connect(database)) as connection, connection:
        connection.execute("UPDATE recordings SET overview_status='extracting' WHERE id=?",
                           (recording_id,))
    request(h, route, feedback_payload(row))
    assert responses[-1][1]["code"] == "recording_busy"
    with closing(vocab._connect(database)) as connection, connection:
        connection.execute("UPDATE recordings SET overview_status='not_requested' WHERE id=?",
                           (recording_id,))
    request(h, route, feedback_payload(row, expected_feedback_revision=9))
    assert responses[-1][1]["code"] == "revision_conflict"
    assert vocab.list_terms(database)["revision"] == 1
