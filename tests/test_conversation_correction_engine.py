import copy
import json
import subprocess
from pathlib import Path

import pytest

from daily_reader import conversation_correction_engine as engine
from daily_reader import conversation_insights as insights
from daily_reader.conversation_insights import ConversationInsightError


def utterance(identifier="original", text="資料を確認します", **values):
    return {
        "id": identifier,
        "text": text,
        "speaker": "話者1",
        "start_seconds": 1.2,
        "end_seconds": 5.6,
        **values,
    }


def proposal(identifier="original", text="資料を確認します。", **values):
    return {
        "utterance_id": identifier,
        "corrected_text": text,
        "kind": "punctuation",
        "context_ids": [],
        **values,
    }


def run(monkeypatch, replies, utterances=None, contexts=None):
    calls = []
    replies = iter(replies)

    def request(**kwargs):
        calls.append(copy.deepcopy(kwargs))
        result = next(replies)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(engine, "_request", request)
    stages = []
    result = engine.run_correction_passes(
        utterances or [utterance()],
        contexts or [],
        schema_directory=Path("config"),
        codex_command="unused",
        model="unused",
        on_stage=stages.append,
    )
    return result, calls, stages


def test_two_independent_passes_preserve_originals_and_provenance(monkeypatch):
    original = [utterance()]
    snapshot = copy.deepcopy(original)
    result, calls, stages = run(
        monkeypatch,
        [
            {"corrections": [proposal()], "uncertain_utterance_ids": []},
            {"verdicts": [{"utterance_id": "original", "verdict": "verified"}]},
        ],
        original,
    )
    assert original == snapshot
    assert stages == ["correcting", "verifying"]
    assert calls[0]["developer_instructions"] != calls[1]["developer_instructions"]
    assert calls[1]["utterances"] == original
    assert calls[1]["context_payload"]["proposals"][0]["corrected_text"] == "資料を確認します。"
    assert result[0]["original_text"] == "資料を確認します"
    assert result[0]["corrected_text"] == "資料を確認します。"
    assert result[0]["status"] == "accepted"


@pytest.mark.parametrize(
    ("before", "after", "reason"),
    [
        ("会議は13時です", "会議は14時です", "protected_number"),
        ("予約は必要ありません", "予約は必要です", "protected_meaning"),
        ("まだ終わっていません", "もう終わりました", "protected_meaning"),
        ("田中さんに相談します", "中村さんに相談します", "protected_name"),
        ("それをお願いします", "資料をお願いします", "protected_meaning"),
        ("相談した", "旅行の予約と支払いをすべて済ませた", "large_change"),
    ],
)
def test_guard_holds_semantic_change_even_if_verifier_accepts(monkeypatch, before, after, reason):
    result, _, _ = run(
        monkeypatch,
        [
            {"corrections": [proposal(text=after)], "uncertain_utterance_ids": []},
            {"verdicts": [{"utterance_id": "original", "verdict": "verified"}]},
        ],
        [utterance(text=before)],
    )
    assert result[0]["reason"] == reason
    assert result[0]["corrected_text"] is None
    assert result[0]["original_text"] == before


def test_minimal_recognition_fix_can_use_nearby_vocabulary(monkeypatch):
    rows = [
        utterance("context", "プルリクエストのレビューの話です。"),
        utterance(text="プロリックレビューを確認します。"),
    ]
    result, _, _ = run(
        monkeypatch,
        [
            {
                "corrections": [
                    proposal(text="プルリクエストレビューを確認します。", kind="recognition")
                ],
                "uncertain_utterance_ids": [],
            },
            {"verdicts": [{"utterance_id": "original", "verdict": "verified"}]},
        ],
        rows,
    )
    assert result[0]["status"] == "accepted"


def test_historical_vocabulary_does_not_resolve_current_unknown_referent():
    assert (
        engine.change_guard("それをお願いします", "資料をお願いします", ["資料を読みました"])
        == "protected_meaning"
    )


def test_punctuation_before_name_does_not_change_identity():
    assert (
        engine.change_guard("明日田中さんに相談します", "明日、田中さんに相談します。", []) is None
    )


def test_guard_allows_local_compound_abbreviation_and_exact_adjacent_repetition():
    assert (
        engine.change_guard(
            "プロリックレビューの手順を確認します。",
            "プルリクレビューの手順を確認します。",
            ["プルリクエストのレビューについて話します。"],
        )
        is None
    )
    assert (
        engine.change_guard(
            "データ品質を確認しました確認しました。", "データ品質を確認しました。", []
        )
        is None
    )
    assert engine.change_guard("資料", "資料" + "。" * 100, []) == "large_change"


@pytest.mark.parametrize("prefix", ["できない", "必要ない"])
def test_repetition_cleanup_does_not_remove_nested_negation(prefix):
    assert (
        engine.change_guard(prefix + "わけではないわけではない", prefix + "わけではない", [])
        == "protected_meaning"
    )


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("明日送ります", "昨日送ります"),
        ("今週の月曜です", "来週の火曜です"),
        ("午後に確認します", "午前に確認します"),
        ("予約します？", "予約します。"),
        ("「削除して」と書いています", "削除してと書いています"),
    ],
)
def test_dates_and_quotation_are_protected_even_with_nearby_words(before, after):
    assert engine.change_guard(before, after, [after]) == "protected_meaning"


@pytest.mark.parametrize("verdict", ["rejected", "uncertain"])
def test_verifier_can_retain_source(monkeypatch, verdict):
    result, _, _ = run(
        monkeypatch,
        [
            {"corrections": [proposal()], "uncertain_utterance_ids": []},
            {"verdicts": [{"utterance_id": "original", "verdict": verdict}]},
        ],
    )
    assert result[0]["status"] == "retained"
    assert result[0]["verification"] == verdict
    assert result[0]["corrected_text"] is None


@pytest.mark.parametrize(
    "response",
    [
        {"corrections": [proposal("missing")], "uncertain_utterance_ids": []},
        {"corrections": [proposal(), proposal()], "uncertain_utterance_ids": []},
        {"corrections": [proposal()], "uncertain_utterance_ids": ["original"]},
        {"corrections": [], "uncertain_utterance_ids": ["original", "original"]},
        {"corrections": [proposal(context_ids=["missing"])], "uncertain_utterance_ids": []},
    ],
)
def test_invalid_or_ambiguous_ids_fail_the_batch(monkeypatch, response):
    with pytest.raises(ConversationInsightError):
        run(monkeypatch, [response])


@pytest.mark.parametrize(
    "verdicts",
    [
        [],
        [{"utterance_id": "unknown", "verdict": "verified"}],
        [{"utterance_id": "original", "verdict": "verified"}] * 2,
        [{"utterance_id": "original", "verdict": "probably"}],
    ],
)
def test_verification_must_cover_exactly_proposals(monkeypatch, verdicts):
    with pytest.raises(ConversationInsightError):
        run(
            monkeypatch,
            [{"corrections": [proposal()], "uncertain_utterance_ids": []}, {"verdicts": verdicts}],
        )


def test_late_failure_returns_no_partial_corrections(monkeypatch):
    monkeypatch.setattr(engine, "MAX_TARGETS", 1)
    with pytest.raises(ConversationInsightError):
        run(
            monkeypatch,
            [
                {"corrections": [proposal()], "uncertain_utterance_ids": []},
                {"verdicts": [{"utterance_id": "original", "verdict": "verified"}]},
                ConversationInsightError("検証に失敗しました"),
            ],
            [utterance(), utterance("next")],
        )


def test_long_single_utterance_is_held_whole_not_split(monkeypatch):
    text = "原文" * 3000
    result, calls, stages = run(monkeypatch, [], [utterance(text=text)])
    assert not calls and not stages
    assert result[0]["original_text"] == text
    assert result[0]["reason"] == "utterance_too_long"


def test_chunk_context_is_read_only_not_an_extra_target(monkeypatch):
    monkeypatch.setattr(engine, "MAX_TARGETS", 1)
    result, calls, _ = run(
        monkeypatch,
        [
            {"corrections": [], "uncertain_utterance_ids": []},
            {"corrections": [], "uncertain_utterance_ids": []},
        ],
        [utterance(), utterance("next")],
    )
    assert not result
    assert calls[0]["context_payload"]["target_ids"] == ["original"]
    assert len(calls[0]["utterances"]) == 2
    assert calls[1]["context_payload"]["target_ids"] == ["next"]


def test_historical_hint_requires_matching_target_speaker(monkeypatch):
    hint = {"id": "c001", "text": "資料のレビュー", "target_speakers": ["話者2"]}
    with pytest.raises(ConversationInsightError):
        run(
            monkeypatch,
            [{"corrections": [proposal(context_ids=["c001"])], "uncertain_utterance_ids": []}],
            contexts=[hint],
        )


@pytest.mark.parametrize(
    "history", [[], [{"id": "c001", "text": "reference", "target_speakers": ["speaker"]}]]
)
def test_output_schema_excludes_neighbor_ids_and_disallows_unavailable_history(
    monkeypatch, history
):
    seen = []

    def request(**kwargs):
        schema = json.loads(kwargs["schema_path"].read_text())
        seen.append(schema)
        return {}

    monkeypatch.setattr(engine, "_request", request)
    rows = [utterance("neighbor"), utterance("target")]
    payload = {"target_ids": ["target"], "reference_context": history}
    engine._scoped_request(
        schema_path=Path("config/conversation-correction-schema.json"),
        utterances=rows,
        context_payload=payload,
    )
    properties = seen[0]["properties"]
    item = properties["corrections"]["items"]["properties"]
    assert item["utterance_id"]["enum"] == ["u0002"]
    assert properties["uncertain_utterance_ids"]["items"]["enum"] == ["u0002"]
    if history:
        assert item["context_ids"]["items"]["enum"] == ["c001"]
    else:
        assert item["context_ids"]["maxItems"] == 0
    engine._scoped_request(
        schema_path=Path("config/conversation-correction-verification-schema.json"),
        utterances=rows,
        context_payload={**payload, "proposals": [proposal("target")]},
    )
    verdicts = seen[1]["properties"]["verdicts"]
    assert verdicts["items"]["properties"]["utterance_id"]["enum"] == ["u0002"]
    assert verdicts["minItems"] == verdicts["maxItems"] == 1


def test_scoped_schema_and_transport_use_same_aliases(monkeypatch):
    monkeypatch.setattr(insights, "codex_available", lambda _: True)

    def fake_run(command, **kwargs):
        body = json.loads(kwargs["input"])
        schema_path = Path(command[command.index("--output-schema") + 1])
        schema = json.loads(schema_path.read_text())
        assert [row["id"] for row in body["utterances"]] == ["u0001", "u0002"]
        assert body["target_utterance_ids"] == ["u0002"]
        allowed = schema["properties"]["corrections"]["items"]["properties"]
        assert allowed["utterance_id"]["enum"] == body["target_utterance_ids"]
        assert allowed["context_ids"]["maxItems"] == 0
        output = {"corrections": [proposal("u0002", text="資料。")], "uncertain_utterance_ids": []}
        Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps(output))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(insights.subprocess, "run", fake_run)
    result = engine._scoped_request(
        codex_command="unused",
        model="unused",
        schema_path=Path("config/conversation-correction-schema.json"),
        recorded_at=None,
        timezone="Asia/Tokyo",
        utterances=[utterance("neighbor"), utterance("target", text="資料")],
        context_payload={"reference_context": [], "target_ids": ["target"]},
    )
    assert result["corrections"][0]["utterance_id"] == "target"


def test_correction_transport_sends_only_allowed_fields_and_restores_ids(monkeypatch, tmp_path):
    (tmp_path / "schema").write_text('{"type":"object"}')
    monkeypatch.setattr(insights, "codex_available", lambda _: True)

    def fake_run(command, **kwargs):
        body = json.loads(kwargs["input"])
        assert body["utterances"] == [
            {
                "id": "u0001",
                "text": "資料",
                "speaker": "話者1",
                "start_seconds": 1.2,
                "end_seconds": 5.6,
            }
        ]
        assert body["reference_context"] == [
            {"id": "c001", "text": "資料", "target_speakers": ["話者1"]}
        ]
        assert body["target_utterance_ids"] == ["u0001"]
        assert body["proposals"] == [{"utterance_id": "u0001", "corrected_text": "資料。"}]
        assert body["approved_terms"] == [
            {"canonical": "Palo Alto", "reading": "パロアルト", "aliases": ["パロあると"]}
        ]
        assert "private" not in kwargs["input"]
        output = {
            "corrections": [{"utterance_id": "u0001"}],
            "verdicts": [{"utterance_id": "u0001"}],
            "uncertain_utterance_ids": ["u0001"],
        }
        Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps(output))
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(insights.subprocess, "run", fake_run)
    result = insights._request(
        codex_command="unused",
        model="unused",
        schema_path=tmp_path / "schema",
        recorded_at=None,
        timezone="Asia/Tokyo",
        utterances=[utterance(text="資料", gps="private", filename="private")],
        context_payload={
            "reference_context": [
                {
                    "id": "c001",
                    "text": "資料",
                    "target_speakers": ["話者1"],
                    "recording_id": "private",
                }
            ],
            "target_ids": ["original"],
            "proposals": [proposal(text="資料。")],
            "approved_terms": [{
                "canonical": "Palo Alto", "reading": "パロアルト", "aliases": ["パロあると"],
                "id": "private", "recording_id": "private", "created_at": "private",
            }],
        },
    )
    assert result["corrections"][0]["utterance_id"] == "original"
    assert result["verdicts"][0]["utterance_id"] == "original"
    assert result["uncertain_utterance_ids"] == ["original"]
