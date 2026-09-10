import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from daily_reader import conversation_correction_engine as engine
from daily_reader import conversation_corrections as correction
from daily_reader import conversation_transcription as asr
from daily_reader import conversation_vocabulary as vocabulary
from daily_reader import conversations as conv
from daily_reader.conversation_overviews import grounded_points

SCHEMA = Path(__file__).resolve().parents[1] / "config/conversation-insight-schema.json"


def term(canonical="Palo Alto", reading="パロアルト", aliases=None, **extra):
    return {
        "id": "term",
        "revision": 1,
        "canonical": canonical,
        "reading": reading,
        "aliases": ["パロアウト"] if aliases is None else aliases,
        **extra,
    }


def row(text="パロアウトの設定を確認します", **extra):
    return {
        "id": "u",
        "text": text,
        "speaker": "話者1",
        "start_seconds": 0,
        "end_seconds": 2,
        **extra,
    }


def ai_result(row):
    return {
        "utterance_id": row["id"],
        "original_text": row["text"],
        "proposed_text": row["text"] + "。",
        "corrected_text": row["text"] + "。",
        "status": "accepted",
        "reason": "punctuation",
        "verification": "verified",
        "context_ids": [],
    }


@pytest.fixture
def record(tmp_path):
    database = tmp_path / "conversation.sqlite3"
    content = "パロアウトの設定を確認します\n次の資料を読みます".encode()
    recording = conv.store_transcript(database, io.BytesIO(content), len(content), "fixture.txt")
    return database, recording["id"]


def save_term(database):
    return vocabulary.save_term(
        database,
        {key: value for key, value in term().items() if key in {"canonical", "reading", "aliases"}},
    )


def feedback(record, utterance, *, revision=0, text="Palo Altoの設定を確認します", reset=False):
    payload = {"expected_original_text": utterance["text"], "expected_feedback_revision": revision}
    payload.update({"reset": True} if reset else {"corrected_text": text})
    return vocabulary.save_feedback(*record, utterance["id"], payload)


def test_hotwords_use_decoder_tokens_whole_canonicals_and_only_metadata():
    calls = []

    def encode(text, *, add_special_tokens):
        assert not add_special_tokens
        calls.append(text)
        # Deliberately count UTF-8 units so character count cannot pass this test.
        return SimpleNamespace(ids=list(text.encode()))

    model = SimpleNamespace(max_length=42, hf_tokenizer=SimpleNamespace(encode=encode))
    snapshot = {
        "revision": 7,
        "terms": [
            term(canonical="とても長い用語" * 8, id="oversize"),
            term(id="palo", revision=3),
            term(canonical="PCPRegen", id="pcp", revision=6),
            term(canonical="追加", id="omitted"),
        ],
    }
    hints, metadata = asr.vocabulary_hotwords(model, snapshot)
    assert hints == "Palo Alto, PCPRegen"
    assert metadata["hotword_tokens"] == metadata["hotword_token_limit"] == 20
    assert metadata["vocabulary_term_ids"] == ["palo", "pcp"]
    assert metadata["vocabulary_term_revisions"] == {"palo": 3, "pcp": 6}
    assert metadata["vocabulary_revision"] == 7
    assert metadata["vocabulary_omitted_count"] == 2
    assert "パロアウト" not in hints and "パロアルト" not in hints
    assert "Palo Alto" not in json.dumps(metadata)
    assert calls


def test_empty_dictionary_does_not_require_tokenizer():
    assert asr.vocabulary_hotwords(object(), {"revision": 3, "terms": []}) == (
        "",
        {
            "vocabulary_revision": 3,
            "vocabulary_term_ids": [],
            "vocabulary_term_revisions": {},
            "vocabulary_omitted_count": 0,
            "hotword_tokens": 0,
            "hotword_token_limit": 223,
        },
    )


def test_recognize_passes_hotwords_without_restoring_previous_text(monkeypatch):
    calls = []

    class Model:
        max_length = 448
        hf_tokenizer = SimpleNamespace(
            encode=lambda text, **kw: SimpleNamespace(ids=list(text.encode()))
        )

        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, path, **kwargs):
            calls.append(kwargs)
            return iter(
                [SimpleNamespace(text="Palo Altoです", start=0, end=1, avg_logprob=-0.2)]
            ), SimpleNamespace(duration=1, duration_after_vad=1)

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model))
    result = asr.recognize(
        Path("unused.wav"), vocabulary_snapshot={"revision": 1, "terms": [term()]}
    )
    assert calls[0]["hotwords"] == "Palo Alto"
    assert calls[0]["condition_on_previous_text"] is False
    assert "prefix" not in calls[0] and "initial_prompt" not in calls[0]
    assert result.metadata["vocabulary_term_ids"] == ["term"]
    assert result.metadata["vocabulary_revision"] == 1


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        ("パロアウトの設定を確認します", "Palo Altoの設定を確認します", "accepted"),
        ("パロアウトの設定は不要です", "Palo Altoの設定は必要です", "retained"),
        ("パロアウトを13時に確認します", "Palo Altoを14時に確認します", "retained"),
        ("田中さんに確認します", "中村さんに確認します", "retained"),
    ],
)
def test_dictionary_can_ground_notation_without_weakening_protected_meaning(
    monkeypatch, before, after, expected
):
    calls = []

    def request(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return {
                "corrections": [
                    {
                        "utterance_id": "u",
                        "corrected_text": after,
                        "kind": "notation",
                        "context_ids": [],
                    }
                ],
                "uncertain_utterance_ids": [],
            }
        return {"verdicts": [{"utterance_id": "u", "verdict": "verified"}]}

    monkeypatch.setattr(engine, "_request", request)
    result = engine.run_correction_passes(
        [row(before)],
        [],
        schema_directory=SCHEMA.parent,
        codex_command="unused",
        model="unused",
        approved_terms=[term(), term(canonical="中村さん", reading="", aliases=["田中さん"])],
    )
    assert result[0]["status"] == expected
    assert (
        calls[0]["context_payload"]["approved_terms"]
        == calls[1]["context_payload"]["approved_terms"]
    )
    assert all(
        set(t) == {"canonical", "reading", "aliases"}
        for t in calls[0]["context_payload"]["approved_terms"]
    )


def test_dictionary_scope_is_bounded_and_does_not_match_inside_english_words():
    assert (
        engine._terms_for_window(
            [term(canonical="API", reading="", aliases=[])], [row("capitalについて話します")]
        )
        == []
    )
    terms = [
        term(canonical=str(index) + "用語" * 20, reading="共通", aliases=[]) for index in range(100)
    ]
    scoped = engine._terms_for_window(engine._approved_terms(terms), [row("共通の話")])
    assert 0 < len(scoped) < 100
    assert (
        sum(len(json.dumps(t, ensure_ascii=False)) for t in scoped)
        <= engine.MAX_APPROVED_TERMS_CHARACTERS
    )


@pytest.mark.parametrize(
    ("heard", "canonical"),
    [
        ("パロあると", "Palo Alto"),
        ("PCPリゲン", "PCPRegen"),
    ],
)
@pytest.mark.parametrize("use_dictionary", [False, True])
def test_confirmed_spelling_examples_work_only_with_dictionary(
    monkeypatch, heard, canonical, use_dictionary
):
    requests = iter(
        [
            {
                "corrections": [
                    {
                        "utterance_id": "u",
                        "corrected_text": canonical + "の資料を確認します",
                        "kind": "notation",
                        "context_ids": [],
                    }
                ],
                "uncertain_utterance_ids": [],
            },
            {"verdicts": [{"utterance_id": "u", "verdict": "verified"}]},
        ]
    )
    monkeypatch.setattr(engine, "_request", lambda **kw: next(requests))
    result = engine.run_correction_passes(
        [row(heard + "の資料を確認します")],
        [],
        schema_directory=SCHEMA.parent,
        codex_command="unused",
        model="unused",
        approved_terms=[term(canonical=canonical, reading="", aliases=[heard])]
        if use_dictionary
        else [],
    )
    assert result[0]["status"] == ("accepted" if use_dictionary else "retained")


def test_user_corrected_rows_are_context_only_even_at_chunk_boundaries(monkeypatch):
    calls = []
    monkeypatch.setattr(engine, "MAX_TARGETS", 1)

    def request(**kwargs):
        calls.append(kwargs)
        return {"corrections": [], "uncertain_utterance_ids": []}

    monkeypatch.setattr(engine, "_request", request)
    rows = [row("Palo Alto", user_corrected=True), row("次の資料", id="other")]
    assert (
        engine.run_correction_passes(
            rows, [], schema_directory=SCHEMA.parent, codex_command="unused", model="unused"
        )
        == []
    )
    assert len(calls) == 1
    assert calls[0]["context_payload"]["target_ids"] == ["other"]
    assert calls[0]["utterances"][0]["text"] == "Palo Alto"


@pytest.mark.parametrize("change", ["edit", "disable", "delete"])
def test_dictionary_change_invalidates_overlay_cache_and_automatic_adoption(
    record, monkeypatch, change
):
    original_term = save_term(record[0])
    calls = []

    def run(rows, contexts, **kwargs):
        calls.append(kwargs["approved_terms"])
        return [ai_result(rows[0])]

    monkeypatch.setattr(engine, "run_correction_passes", run)
    assert correction.ensure_corrected(*record, SCHEMA, "unused", "unused")
    initial = conv.get_recording(*record)
    with conv._connect(record[0]) as connection:
        evidence = {
            "type": "conversation",
            "recording_id": record[1],
            "quotes": [{"correction_revision_id": initial["correction"]["revision_id"]}],
        }
        assert correction.automatic_allowed(connection, evidence)
    if change == "delete":
        vocabulary.delete_term(
            record[0], original_term["id"], {"revision": original_term["revision"]}
        )
    else:
        vocabulary.save_term(
            record[0],
            {
                "canonical": "Palo Alto",
                "reading": "パロアルト",
                "aliases": ["パロアウト", "パロあると"],
                "enabled": change != "disable",
                "revision": original_term["revision"],
            },
            original_term["id"],
        )
    assert conv.get_recording(*record)["correction"]["status"] == "stale"
    assert conv.list_recordings(record[0])[0]["correction"]["status"] == "stale"
    with conv._connect(record[0]) as connection:
        assert not correction.automatic_allowed(connection, evidence)
        _, rows = conv._insight_input(connection, record[1])
        assert rows[0]["text"] == initial["utterances"][0]["text"]
    assert len(calls) == 1  # Reading stale state did not send any request.
    assert correction.ensure_corrected(*record, SCHEMA, "unused", "unused")
    assert correction.ensure_corrected(*record, SCHEMA, "unused", "unused")
    assert len(calls) == 2 and calls[0][0]["canonical"] == "Palo Alto"
    assert bool(calls[1]) == (change == "edit")


@pytest.mark.parametrize("with_previous_dictionary", [False, True])
def test_new_and_unrelated_terms_keep_existing_results_and_evidence_valid(
    record, monkeypatch, with_previous_dictionary
):
    if with_previous_dictionary:
        save_term(record[0])
    monkeypatch.setattr(
        engine, "run_correction_passes", lambda rows, *a, **kw: [ai_result(rows[0])]
    )
    assert correction.ensure_corrected(*record, SCHEMA, "unused", "unused")
    initial = conv.get_recording(*record)
    other = vocabulary.save_term(record[0], {"canonical": "Unrelated", "enabled": False})
    for _ in range(2):
        current = conv.get_recording(*record)
        assert current["correction"]["status"] == "completed"
        assert current["correction"]["revision_id"] == initial["correction"]["revision_id"]
        with conv._connect(record[0]) as connection:
            assert correction.automatic_allowed(
                connection,
                {
                    "type": "conversation",
                    "recording_id": record[1],
                    "quotes": [{"correction_revision_id": initial["correction"]["revision_id"]}],
                },
            )
            assert conv._insight_input(connection, record[1])[1][0]["text"].endswith("。")
        other = vocabulary.save_term(
            record[0],
            {
                "canonical": "Unrelated",
                "reading": "アンリレイテッド",
                "enabled": True,
                "revision": other["revision"],
            },
            other["id"],
        )


def test_dictionary_change_during_generation_discards_result(record, monkeypatch):
    def run(rows, contexts, **kwargs):
        save_term(record[0])
        return [ai_result(rows[0])]

    monkeypatch.setattr(engine, "run_correction_passes", run)
    assert not correction.ensure_corrected(*record, SCHEMA, "unused", "unused")
    assert conv.get_recording(*record)["correction"]["items"] == []


def test_manual_feedback_preserves_raw_timing_gps_and_quote_lineage_and_reset_revision(record):
    before = conv.get_recording(*record)
    source = before["utterances"][0]
    saved = feedback(record, source)["feedback"]
    after = conv.get_recording(*record)
    current = after["utterances"][0]
    assert {key: current[key] for key in source if not key.startswith("user_correction")} == {
        key: source[key] for key in source if not key.startswith("user_correction")
    }
    assert after["location_contexts"] == before["location_contexts"]
    assert current["user_correction"] == saved and current["user_correction_revision"] == 1
    with conv._connect(record[0]) as connection:
        _, rows = conv._insight_input(connection, record[1])
        first_hash = correction.inputs(connection, record[1], engine.VERSION)[2]
    proof = grounded_points(
        [{"text": "設定を確認する話", "evidence_utterance_ids": [source["id"]]}],
        {r["id"]: r for r in rows},
        1,
    )[0]["evidence"][0]
    assert proof["quote"] == source["text"]
    assert proof["corrected_quote"] == saved["corrected_text"]
    assert proof["correction_revision_id"] == f"user:{saved['id']}:1"
    feedback(record, source, revision=1, reset=True)
    reset = conv.get_recording(*record)["utterances"][0]
    assert reset["user_correction"] is None and reset["user_correction_revision"] == 2
    with conv._connect(record[0]) as connection:
        assert correction.inputs(connection, record[1], engine.VERSION)[2] != first_hash
        assert conv._insight_input(connection, record[1])[1][0]["text"] == source["text"]


def test_ai_result_cannot_overwrite_manual_feedback_even_if_model_adapter_misbehaves(
    record, monkeypatch
):
    source = conv.get_recording(*record)["utterances"][0]
    saved = feedback(record, source)["feedback"]
    monkeypatch.setattr(
        engine, "run_correction_passes", lambda rows, *a, **kw: [ai_result(rows[0])]
    )
    assert not correction.ensure_corrected(*record, SCHEMA, "unused", "unused")
    current = conv.get_recording(*record)["utterances"][0]
    assert current["user_correction"] == saved and current["text"] == source["text"]


def test_stale_dictionary_recap_is_hidden_but_explicit_manual_recap_can_be_current(
    record, monkeypatch
):
    original_term = save_term(record[0])
    monkeypatch.setattr(
        engine, "run_correction_passes", lambda rows, *a, **kw: [ai_result(rows[0])]
    )
    assert correction.ensure_corrected(*record, SCHEMA, "unused", "unused")
    monkeypatch.setattr(
        conv,
        "request_overview",
        lambda **kw: {
            "points": [
                {"text": "設定を確認する話", "evidence_utterance_ids": [kw["utterances"][0]["id"]]}
            ]
        },
    )
    conv.extract_recording_overview(*record, SCHEMA)
    assert conv.get_recording(*record)["overview"]["status"] == "ready"
    with conv._connect(record[0]) as connection:
        original_summary = connection.execute("SELECT data FROM conversation_overviews").fetchone()[
            0
        ]
    vocabulary.save_term(
        record[0],
        {
            "canonical": "Palo Alto",
            "aliases": ["パロアウト", "パロあると"],
            "revision": original_term["revision"],
        },
        original_term["id"],
    )
    assert conv.get_recording(*record)["overview"]["status"] == "stale"
    assert conv.list_recordings(record[0])[0]["digest"]["summary"]["status"] == "stale"
    with conv._connect(record[0]) as connection:
        assert (
            connection.execute("SELECT data FROM conversation_overviews").fetchone()[0]
            == original_summary
        )
    source = conv.get_recording(*record)["utterances"][0]
    feedback(record, source)
    conv.extract_recording_overview(*record, SCHEMA)
    detail = conv.get_recording(*record)
    assert detail["correction"]["status"] == "stale"
    assert detail["overview"]["status"] == "ready"
    assert detail["overview"]["points"][0]["evidence"][0]["correction_revision_id"].startswith(
        "user:"
    )


def test_audio_analysis_receives_current_dictionary_snapshot_without_touching_audio(
    record, monkeypatch
):
    saved = save_term(record[0])
    with conv._connect(record[0]) as connection:
        connection.execute("UPDATE recordings SET source_type='audio'")
    calls = []

    def transcribe(*args, vocabulary_snapshot):
        calls.append(vocabulary_snapshot)
        return asr.Transcription(
            [(0, 2, "Palo Altoの設定", -0.2, "話者未判定")],
            {
                "model": "fixture",
                "warnings": [],
                "vocabulary_revision": vocabulary_snapshot["revision"],
            },
        )

    monkeypatch.setattr(conv, "transcribe_audio", transcribe)
    conv.analyze_recording(*record, Path("unused"))
    detail = conv.get_recording(*record)
    assert calls[0]["terms"][0]["id"] == saved["id"]
    assert detail["transcription_metadata"]["vocabulary_revision"] == saved["revision"]
    assert detail["status"] == "completed"
