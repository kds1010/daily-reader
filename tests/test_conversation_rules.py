import pytest

from daily_reader.conversation_rules import rule_task_title, visible_rule_item


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



@pytest.mark.parametrize("source,status,title,visible", [
    ("rule", "awaiting_review", "ボタンを押す必要があるから", False),
    ("rule", "awaiting_review", "資料を確認してください", True),
    ("rule", "kept", "それを確認して", True),
    ("rule", "approved", "それを確認して", True),
    ("rule", "dismissed", "それを確認して", True),
    ("rule", "superseded", "それを確認して", True),
    ("codex", "awaiting_review", "それを確認して", True),
])
def test_visibility_only_filters_unreviewed_rule_fragments(source, status, title, visible):
    assert visible_rule_item(source, status, title) is visible
