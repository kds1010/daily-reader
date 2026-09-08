from __future__ import annotations

import json
import runpy
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from daily_reader import conversation_transcription as asr

character_errors = runpy.run_path(
    Path(__file__).resolve().parents[1] / "scripts/evaluate_conversation_transcription.py"
)["character_errors"]


def fake_model(monkeypatch, segments, *, duration=30):
    calls = {}

    class Model:
        def __init__(self, name, **kwargs):
            calls.update(model=name, **kwargs)

        def transcribe(self, path, **kwargs):
            calls.update(kwargs)
            return iter(segments), SimpleNamespace(duration=duration, duration_after_vad=2)

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Model))
    return calls


def segment(start, end, text, logprob=-0.5):
    return SimpleNamespace(start=start, end=end, text=text, avg_logprob=logprob)


def test_recognition_preserves_text_and_clamps_timestamps(monkeypatch, tmp_path):
    calls = fake_model(
        monkeypatch,
        [segment(-0.1, 1, " 確認します "), segment(1, 2.2, "確認します", -1.5)],
        duration=2,
    )
    monkeypatch.setenv("DAYMELD_WHISPER_MODEL", "custom-local-model")
    result = asr.recognize(tmp_path / "audio.wav")
    assert calls["model"] == "custom-local-model"
    assert calls["condition_on_previous_text"] is False
    assert calls["language"] == "ja"
    assert result.segments == [
        (0, 1, "確認します", -0.5, "話者未判定"),
        (1, 2, "確認します", -1.5, "話者未判定"),
    ]
    assert result.metadata["consecutive_repetitions"] == 1
    assert result.metadata["low_logprob_segments"] == 1


@pytest.mark.parametrize("rows", [[segment(0, float("nan"), "不正")], [segment(1, 0, "不正")]])
def test_invalid_output_rejected(monkeypatch, tmp_path, rows):
    fake_model(monkeypatch, rows)
    with pytest.raises(ValueError):
        asr.recognize(tmp_path / "audio.wav")


def test_empty_and_short_recognition_are_reported(monkeypatch, tmp_path):
    fake_model(monkeypatch, [])
    assert asr.recognize(tmp_path / "audio.wav").metadata["warnings"]
    fake_model(monkeypatch, [segment(0, 1, "短い発話")])
    result = asr.recognize(tmp_path / "audio.wav")
    assert "10%未満" in result.metadata["warnings"][0]


def test_diarization_failure_preserves_transcript_without_leaking_token(monkeypatch, tmp_path):
    def convert(command, **kwargs):
        with wave.open(command[-1], "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(b"\xff\x7f" * 16000)

    monkeypatch.setattr(asr.subprocess, "run", convert)
    monkeypatch.setattr(
        asr,
        "recognize",
        lambda _: asr.Transcription([(0, 1, "確認します", -0.5, "話者未判定")], {"warnings": []}),
    )

    def failed(*_):
        raise ValueError("secret-token private-filename")

    monkeypatch.setattr(asr, "diarize", failed)
    result = asr.transcribe_audio(tmp_path / "original.mp3", tmp_path / "missing-token")
    assert result.segments[0][2] == "確認します"
    assert result.segments[0][4] == "話者未判定"
    assert result.metadata["diarization_status"] == "failed"
    assert result.metadata["clipped_sample_fraction"] == 1
    assert "secret-token" not in json.dumps(result.metadata)


def test_speaker_assignment_requires_overlap():
    turns = [(0, 1, "話者1"), (1, 3, "話者2")]
    assert asr.speaker_for(0.9, 2, turns) == "話者2"
    assert asr.speaker_for(4, 5, turns) == "話者未判定"


def test_character_error_measurement_does_not_treat_hypothesis_as_reference():
    assert character_errors("Ａ、B。", "ab")["cer"] == 0
    assert character_errors("資料を確認", "資料確認")["character_errors"] == 1
    assert character_errors("", "誤生成")["character_errors"] == 3
    assert character_errors("", "誤生成")["cer"] is None


def test_model_failure_reports_safe_actionable_reason(monkeypatch, tmp_path):
    class Unavailable:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("private-path?token=secret")

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=Unavailable))
    with pytest.raises(asr.TranscriptionError, match="モデルを読み込めません") as failure:
        asr.recognize(tmp_path / "audio.wav")
    assert "secret" not in str(failure.value)


def test_decoder_failure_does_not_expose_private_filename(monkeypatch, tmp_path):
    def failed(*args, **kwargs):
        raise FileNotFoundError("private-filename")

    monkeypatch.setattr(asr.subprocess, "run", failed)
    with pytest.raises(asr.TranscriptionError, match="音声ファイル") as failure:
        asr.transcribe_audio(tmp_path / "private.mp3", tmp_path / "token")
    assert "private" not in str(failure.value)
