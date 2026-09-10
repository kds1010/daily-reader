from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from daily_reader import conversations, life_assistant
from daily_reader.correction_context import reference_context


@pytest.fixture
def db(tmp_path):
    database = tmp_path / "synthetic.sqlite3"
    conversations.initialize_database(database)
    with life_assistant.connect(database):
        pass
    connection = sqlite3.connect(database)
    yield connection
    connection.close()


def recording(db, name, text="Pythonのデータベース設計を読みました。", *, date=None, label="話者1"):
    date = date or (
        "2026-09-09T10:00:00+09:00" if name == "target" else "2026-09-08T10:00:00+09:00"
    )
    db.execute(
        "INSERT INTO recordings(id,filename,audio_path,sha256,byte_size,status,created_at,"
        "recorded_at,recorded_at_verified,source_type) VALUES(?,?,?, ?,0,'completed',?,?,1,'text')",
        (name, "fixture.txt", "", name, date, date),
    )
    db.execute("INSERT INTO speakers VALUES(?,?,?,NULL)", (f"{name}-s", name, label))
    row = utterance(db, name, "u", text, label=label)
    item_id = reviewed_item(db, name, row)
    db.execute("INSERT INTO life_speaker_people VALUES(?,?,'self')", (name, label))
    profile(db, name, row, item_id)
    return row


def utterance(db, name, suffix, text, *, label="話者1", speaker_id=None):
    uid = f"{name}-{suffix}"
    db.execute(
        "INSERT INTO utterances(id,recording_id,speaker_id,start_seconds,end_seconds,text,"
        "context,topic) VALUES(?,?,?,0,1,?,'','')",
        (uid, name, speaker_id or f"{name}-s", text),
    )
    return {"id": uid, "text": text, "speaker": label}


def reviewed_item(db, name, row, *, status="kept", certainty="explicit", suffix=""):
    item_id = f"item-{row['id']}{suffix}"
    db.execute(
        "INSERT INTO conversation_items(id,recording_id,kind,title,certainty,status,source,"
        "extractor_version,fingerprint,created_at,updated_at) "
        "VALUES(?,?,'interest','PRIVATE PROFILE TITLE',?,?,'codex','v4',?,'2026','2026')",
        (item_id, name, certainty, status, item_id),
    )
    db.execute(
        "INSERT INTO conversation_item_evidence(item_id,position,utterance_id,quote,speaker) "
        "VALUES(?,0,?,?,?)",
        (item_id, row["id"], row["text"], row["speaker"]),
    )
    return item_id


def profile(db, name, row, item_id, *, suffix="", **data):
    evidence = {
        "type": "conversation",
        "recording_id": name,
        "subject": row["speaker"],
        "item_id": item_id,
        "confirmed_at": "2026-09-08T11:00:00+09:00",
        "quotes": [{"utterance_id": row["id"], "quote": row["text"], "speaker": row["speaker"]}],
    }
    db.execute(
        "INSERT INTO life_entries VALUES(?,'profile','active',?,NULL,?,'2026','2026',1)",
        (
            f"profile-{name}{suffix}",
            json.dumps(
                {
                    "person_id": "self",
                    "owner_confirmed": True,
                    "title": "PRIVATE INFERRED SENSITIVE PROFILE",
                    **data,
                }
            ),
            json.dumps(evidence),
        ),
    )


def mutate_json(db, name, column, **changes):
    current = json.loads(
        db.execute(
            f"SELECT {column} FROM life_entries WHERE id=?",
            (f"profile-{name}",),
        ).fetchone()[0]
    )
    current.update(changes)
    db.execute(
        f"UPDATE life_entries SET {column}=? WHERE id=?", (json.dumps(current), f"profile-{name}")
    )


def test_returns_only_bounded_provenance_and_matching_target_speakers(db):
    target = recording(db, "target", label="自分の話者")
    past = recording(db, "past", label="過去の話者")
    before = db.total_changes
    result = reference_context(db, "target", [target])
    assert result == [
        {
            "id": "c001",
            "source_type": "confirmed_self_utterance",
            "source_id": past["id"],
            "recording_id": "past",
            "recorded_at": "2026-09-08T10:00:00+09:00",
            "title": "過去の確認済み本人発言",
            "text": past["text"],
            "target_speakers": ["自分の話者"],
        }
    ]
    assert "PRIVATE" not in json.dumps(result)
    assert db.total_changes == before
    assert db.row_factory is None


@pytest.mark.parametrize("scope", ["target", "past"])
@pytest.mark.parametrize(
    "failure",
    [
        "no_mapping",
        "other_mapping",
        "no_profile",
        "unconfirmed",
        "automatic",
        "automatic_evidence",
        "expired",
        "invalid_expiry",
        "missing_confirmation",
        "other_subject",
        "wrong_evidence_speaker",
        "wrong_live_speaker",
        "old_quote",
        "missing_live_id",
        "renamed",
        "ambiguous_name",
        "conflict",
        "invalid_json",
        "inferred_profile",
        "archived",
        "unknown_date",
        "unverified",
        "analyzing",
    ],
)
def test_unverified_or_ambiguous_identity_fails_closed(db, scope, failure):
    target = recording(db, "target")
    recording(db, "past")
    if failure == "no_mapping":
        db.execute("DELETE FROM life_speaker_people WHERE recording_id=?", (scope,))
    elif failure == "other_mapping":
        db.execute(
            "UPDATE life_speaker_people SET person_id='other' WHERE recording_id=?", (scope,)
        )
    elif failure == "no_profile":
        db.execute("DELETE FROM life_entries WHERE id=?", (f"profile-{scope}",))
    elif failure == "unconfirmed":
        mutate_json(db, scope, "data", owner_confirmed=False)
    elif failure == "automatic":
        mutate_json(db, scope, "data", automatic=True)
    elif failure == "automatic_evidence":
        mutate_json(db, scope, "evidence", confirmation="automatic")
    elif failure in {"expired", "invalid_expiry"}:
        mutate_json(
            db,
            scope,
            "data",
            expires_at="2000-01-01T00:00:00Z" if failure == "expired" else "not a date",
        )
    elif failure == "missing_confirmation":
        mutate_json(db, scope, "evidence", confirmed_at=None)
    elif failure == "other_subject":
        mutate_json(db, scope, "evidence", subject="話題に出た第三者")
    elif failure in {"wrong_evidence_speaker", "missing_live_id", "old_quote"}:
        quote = {"utterance_id": f"{scope}-u", "quote": target["text"], "speaker": "話者1"}
        quote[
            {
                "wrong_evidence_speaker": "speaker",
                "missing_live_id": "utterance_id",
                "old_quote": "quote",
            }[failure]
        ] = "different"
        mutate_json(db, scope, "evidence", quotes=[quote])
    elif failure == "wrong_live_speaker":
        db.execute("INSERT INTO speakers VALUES(?,?,'他者',NULL)", (f"{scope}-other", scope))
        db.execute(
            "UPDATE utterances SET speaker_id=? WHERE recording_id=?", (f"{scope}-other", scope)
        )
    elif failure == "renamed":
        db.execute("UPDATE speakers SET display_name='別の名前' WHERE recording_id=?", (scope,))
    elif failure == "ambiguous_name":
        db.execute("INSERT INTO speakers VALUES(?,?,'別ラベル','話者1')", (f"{scope}-other", scope))
    elif failure == "conflict":
        row = {**target, "id": f"{scope}-u"}
        profile(db, scope, row, f"item-{scope}-u", suffix="-other", person_id="other")
    elif failure == "invalid_json":
        db.execute("UPDATE life_entries SET evidence='bad' WHERE id=?", (f"profile-{scope}",))
    elif failure == "inferred_profile":
        db.execute(
            "UPDATE conversation_items SET certainty='inferred' WHERE recording_id=?", (scope,)
        )
    elif failure == "archived":
        db.execute("UPDATE life_entries SET status='archived' WHERE id=?", (f"profile-{scope}",))
    elif failure == "unknown_date":
        db.execute("UPDATE recordings SET recorded_at=NULL WHERE id=?", (scope,))
    elif failure == "unverified":
        db.execute("UPDATE recordings SET recorded_at_verified=0 WHERE id=?", (scope,))
    elif failure == "analyzing":
        db.execute("UPDATE recordings SET status='analyzing' WHERE id=?", (scope,))
    assert reference_context(db, "target", [target]) == []


@pytest.mark.parametrize(
    "date",
    [
        "2026-09-09T10:00:00+09:00",
        "2026-09-10T00:00:00Z",
        "2026-09-08",
        "invalid",
    ],
)
def test_future_equal_naive_or_invalid_dates_are_excluded(db, date):
    target = recording(db, "target")
    recording(db, "past", date=date)
    assert reference_context(db, "target", [target]) == []


def test_compares_dates_as_instants_not_local_strings(db):
    target = recording(db, "target")
    recording(db, "past", date="2026-09-09T00:59:59Z")
    assert len(reference_context(db, "target", [target])) == 1
    db.execute("UPDATE recordings SET recorded_at='2026-09-09T01:00:01Z' WHERE id='past'")
    assert reference_context(db, "target", [target]) == []


@pytest.mark.parametrize(
    ("source", "target_text", "matches"),
    [
        ("Pythonで処理します。", "pythonを調べます。", True),
        ("I use Python.", "pythonを調べます。", True),
        ("Ｐｙｔｈｏｎで処理します。", "Pythonを調べます。", True),
        ("データベースを読みました。", "データベースについて話します。", True),
        ("京都で散歩しました。", "京都の資料を読みます。", True),
        ("Pythonで処理します。", "果物が好きです。", False),
        ("capitalについて話します。", "APIを調べます。", False),
        ("a" * 40 + "Python", "Pythonを調べます。", False),
        ("今日の予定です。", "今日の予定を確認します。", False),
        ("ありがとうございます。", "ありがとうございます。", False),
    ],
)
def test_requires_specific_lexical_overlap(db, source, target_text, matches):
    target = recording(db, "target", target_text)
    recording(db, "past", source)
    assert bool(reference_context(db, "target", [target])) is matches


@pytest.mark.parametrize("status", ["awaiting_review", "dismissed", "superseded"])
def test_unreviewed_or_discarded_past_candidates_are_excluded(db, status):
    target = recording(db, "target")
    recording(db, "past")
    db.execute("UPDATE conversation_items SET status=? WHERE recording_id='past'", (status,))
    assert reference_context(db, "target", [target]) == []


def test_approved_current_evidence_and_duplicate_items(db):
    target = recording(db, "target")
    past = recording(db, "past")
    reviewed_item(db, "past", past, suffix="-duplicate")
    db.execute("UPDATE conversation_items SET status='approved' WHERE recording_id='past'")
    assert len(reference_context(db, "target", [target])) == 1
    db.execute(
        "UPDATE conversation_item_evidence SET quote='stale' WHERE utterance_id=?", (past["id"],)
    )
    assert reference_context(db, "target", [target]) == []


def test_never_borrows_other_target_speaker_text_or_untrusted_input(db):
    target = recording(db, "target", "果物が好きです。")
    recording(db, "past", "Pythonで処理します。")
    db.execute("INSERT INTO speakers VALUES('other','target','話者2',NULL)")
    other = utterance(
        db, "target", "other", "Pythonについて話します。", label="話者2", speaker_id="other"
    )
    assert reference_context(db, "target", [target, other]) == []
    assert reference_context(db, "target", [{**other, "speaker": "話者1"}]) == []
    assert reference_context(db, "target", [{**target, "text": "Pythonを使う"}]) == []


def test_missing_tables_and_empty_input_are_read_only(db):
    with sqlite3.connect(":memory:") as empty:
        assert reference_context(empty, "target", []) == []
        assert empty.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
    recording(db, "target")
    assert reference_context(db, "target", []) == []
    assert reference_context(db, "unknown", []) == []


def test_text_and_result_limits_keep_matching_word_in_long_raw_snippet(db):
    target = recording(db, "target")
    past = recording(db, "past", "あ" * 900 + "Python" + "い" * 900)
    for index in range(12):
        row = utterance(db, "past", f"z{index:02d}", "あ" * 900 + "Python" + "う" * 900)
        reviewed_item(db, "past", row)
    result = reference_context(db, "target", [target])
    assert len(result) == 8
    assert sum(len(item["text"]) for item in result) == 1600
    assert all(len(item["text"]) == 200 and "Python" in item["text"] for item in result)
    assert result[0]["source_id"] == past["id"]
    assert [item["id"] for item in result] == [f"c{i:03d}" for i in range(1, 9)]
    assert result == reference_context(db, "target", [target])


def test_candidate_scan_is_bounded(db):
    target = recording(db, "target", "Pythonを使います。")
    recording(db, "past", "果物が好きです。")
    for index in range(200):
        row = utterance(db, "past", f"z{index:03d}", "果物が好きです。")
        reviewed_item(db, "past", row)
    row = utterance(db, "past", "zz-later", "Pythonを使います。")
    reviewed_item(db, "past", row)
    assert reference_context(db, "target", [target]) == []


def test_each_target_speaker_requires_its_own_word_match_within_sent_snippet(db):
    target = recording(db, "target", "Pythonを読みます。")
    recording(db, "past", "Python" + "あ" * 300 + "Ruby")
    db.execute("INSERT INTO speakers VALUES('second','target','話者2',NULL)")
    second = utterance(
        db, "target", "second", "Rubyを読みます。", label="話者2", speaker_id="second"
    )
    item = reviewed_item(db, "target", second)
    profile(db, "target", second, item, suffix="-second")
    db.execute("INSERT INTO life_speaker_people VALUES('target','話者2','self')")
    result = reference_context(db, "target", [target, second])
    assert len(result) == 1
    assert result[0]["target_speakers"] == ["話者1"]
    assert "Ruby" not in result[0]["text"]


def test_excessive_profile_proofs_fail_closed_without_hiding_later_conflict(db):
    target = recording(db, "target")
    recording(db, "past")
    for index in range(200):
        profile(db, "target", target, "item-target-u", suffix=f"-{index}")
    assert reference_context(db, "target", [target]) == []


def test_removed_current_evidence_cannot_be_replaced_by_profile_snapshot(db):
    target = recording(db, "target")
    recording(db, "past")
    db.execute("DELETE FROM conversation_item_evidence WHERE item_id='item-target-u'")
    assert reference_context(db, "target", [target]) == []


def test_existing_manual_profile_confirmation_produces_usable_provenance(db):
    target = recording(db, "target")
    recording(db, "past")
    db.execute("DELETE FROM life_entries")
    db.execute("DELETE FROM life_speaker_people")
    db.execute("UPDATE conversation_items SET assignee='話者1'")
    db.commit()
    database = Path(db.execute("PRAGMA database_list").fetchone()[2])
    for name in ("target", "past"):
        life_assistant.create_entry(
            database,
            {
                "kind": "profile",
                "title": "Python",
                "person_id": "self",
                "source_type": "conversation",
                "source_id": f"item-{name}-u",
            },
        )
    assert len(reference_context(db, "target", [target])) == 1


def mark_user_corrected(db, name, row):
    db.execute(
        "INSERT INTO conversation_user_corrections "
        "(id,utterance_id,recording_id,revision,original_text,corrected_text,active,"
        "created_at,updated_at) "
        "VALUES(?,?,?,1,?,'本人が訂正した内容',1,'2026','2026')",
        ("feedback-" + row["id"], row["id"], name, row["text"]),
    )


@pytest.mark.parametrize("name", ["target", "past"])
def test_user_corrected_identity_source_cannot_confirm_self_speech(db, name):
    target = recording(db, "target")
    past = recording(db, "past")
    assert reference_context(db, "target", [target])
    mark_user_corrected(db, name, target if name == "target" else past)
    assert reference_context(db, "target", [target]) == []


def test_user_corrected_past_quote_is_excluded_without_replacing_it(db):
    target = recording(db, "target")
    proof = recording(db, "past")
    changed = utterance(db, "past", "changed", "Pythonで書いた処理です。")
    reviewed_item(db, "past", changed)
    assert len(reference_context(db, "target", [target])) == 2
    mark_user_corrected(db, "past", changed)
    result = reference_context(db, "target", [target])
    assert [item["source_id"] for item in result] == [proof["id"]]
    assert result[0]["text"] == proof["text"]
