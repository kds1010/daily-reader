import sqlite3

import pytest

from daily_reader.conversation_rules import retire_unreviewed_rule_fragments, rule_task_title


@pytest.mark.parametrize("text", [
    "資料を確認してください", "金曜日までに資料を確認してください",
    "会議の準備をお願いします。", "見積書を送付しておいて", "資料を確認して",
])
def test_fallback_keeps_self_contained_requests(text):
    assert rule_task_title(text) == text


@pytest.mark.parametrize("text", [
    "そこへ行く必要なかった。", "資料を確認する必要はありません。",
    "あれを担当者が見る必要は", "ボタンを押す必要があるから",
    "必要です", "お願いします", "対応してください", "それを確認して",
    "資料を確認しなくていい", "資料を確認しないでください",
    "資料を確認してくださいと言われた", "「資料を確認してください」",
    "もし必要なら資料を確認してください", "資料を確認したら連絡をお願いします",
    "資料の確認は完了しています", "あ" * 201 + "を確認してください",
])
def test_fallback_omits_negations_fragments_quoted_and_unclear_actions(text):
    assert rule_task_title(text) is None


def test_migration_preserves_reviewed_items_and_evidence_and_is_idempotent():
    with sqlite3.connect(":memory:") as connection:
        connection.executescript("""
            CREATE TABLE conversation_items(
                id TEXT PRIMARY KEY,title TEXT,source TEXT,status TEXT,
                certainty TEXT,updated_at TEXT);
            CREATE TABLE task_proposals(id TEXT PRIMARY KEY,status TEXT);
            CREATE TABLE conversation_item_evidence(item_id TEXT,quote TEXT);
        """)
        cases = [
            ("bad", "ボタンを押す必要があるから", "rule", "awaiting_review"),
            ("good", "資料を確認してください", "rule", "awaiting_review"),
            ("saved", "それを確認して", "rule", "kept"),
            ("approved", "それを確認して", "rule", "approved"),
            ("codex", "それを確認して", "codex", "awaiting_review"),
        ]
        for item_id, title, source, status in cases:
            connection.execute(
                "INSERT INTO conversation_items VALUES(?,?,?,?,?,?)",
                (item_id, title, source, status, "explicit", "old"),
            )
            connection.execute("INSERT INTO task_proposals VALUES(?,?)", (item_id, status))
            connection.execute(
                "INSERT INTO conversation_item_evidence VALUES(?,?)", (item_id, title),
            )
        assert retire_unreviewed_rule_fragments(connection) == 1
        assert retire_unreviewed_rule_fragments(connection) == 0
        results = {r[0]: r[1:] for r in connection.execute(
            "SELECT id,status,certainty FROM conversation_items"
        )}
        assert results["bad"][0] == "superseded"
        assert results["good"] == ("awaiting_review", "ambiguous")
        assert results["saved"] == ("kept", "explicit")
        assert results["approved"] == ("approved", "explicit")
        assert results["codex"] == ("awaiting_review", "explicit")
        assert connection.execute(
            "SELECT status FROM task_proposals WHERE id=?", ("bad",)
        ).fetchone()[0] == "superseded"
        assert connection.execute(
            "SELECT count(*) FROM conversation_item_evidence"
        ).fetchone()[0] == 5
