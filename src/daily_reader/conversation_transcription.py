"""Local speech recognition. Audio and recognition output never leave this Mac."""

from __future__ import annotations

import math
import os
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MODEL = "large-v3-turbo"
SETTINGS_VERSION = "local-ja-v3-vocabulary"


class TranscriptionError(ValueError):
    MESSAGES = {
        "model": "音声認識モデルを読み込めませんでした。モデルの取得状況を確認してください。",
        "decode": "音声ファイルを読み込めませんでした。原音の形式・状態を確認してください。",
        "empty": "発話を認識できませんでした。原音を確認して再試行してください。",
        "invalid": "音声認識の時刻・音声長・スコアが不正です。再試行してください。",
    }

    def __init__(self, code: str):
        super().__init__(self.MESSAGES[code])


@dataclass
class Transcription:
    segments: list[tuple[float, float, str, float | None, str]]
    metadata: dict


def vocabulary_hotwords(model, snapshot: dict | None) -> tuple[str, dict]:
    """Keep complete canonical terms within the installed decoder's token budget.

    Readings and observed mistakes are useful for text correction, but including
    those spellings as ASR hints would also encourage the unwanted spelling.
    No model weights or utterances are changed by dictionary updates.
    """
    snapshot = snapshot or {"revision": 0, "terms": []}
    terms = snapshot["terms"]
    limit = max(0, min(223, model.max_length // 2 - 1)) if terms else 223
    selected, texts, tokens = [], [], 0
    for term in terms:
        candidate = ", ".join([*texts, term["canonical"]])
        count = len(model.hf_tokenizer.encode(" " + candidate, add_special_tokens=False).ids)
        if count > limit:
            continue
        selected.append(term)
        texts.append(term["canonical"])
        tokens = count
    return ", ".join(texts), {
        "vocabulary_revision": snapshot["revision"],
        "vocabulary_term_ids": [term["id"] for term in selected],
        "vocabulary_term_revisions": {term["id"]: term["revision"] for term in selected},
        "vocabulary_omitted_count": len(terms) - len(selected),
        "hotword_tokens": tokens,
        "hotword_token_limit": limit,
    }


def recognize(
    wav: Path,
    *,
    model_name: str | None = None,
    download_root: str | None = None,
    condition_on_previous_text: bool = False,
    vad_filter: bool = True,
    vocabulary_snapshot: dict | None = None,
) -> Transcription:
    from faster_whisper import WhisperModel

    started = time.monotonic()
    name = model_name or os.environ.get("DAYMELD_WHISPER_MODEL", DEFAULT_MODEL)
    try:
        model = WhisperModel(name, device="cpu", compute_type="int8", download_root=download_root)
    except Exception:
        raise TranscriptionError("model") from None
    hotwords, vocabulary_metadata = vocabulary_hotwords(model, vocabulary_snapshot)
    segments, info = model.transcribe(
        str(wav),
        language="ja",
        vad_filter=vad_filter,
        beam_size=5,
        condition_on_previous_text=condition_on_previous_text,
        **({"hotwords": hotwords} if hotwords else {}),
    )
    duration = float(info.duration)
    if not math.isfinite(duration) or duration <= 0:
        raise TranscriptionError("invalid")
    transcript = []
    low_probability = 0
    repetitions = 0
    previous = None
    for segment in segments:
        text = segment.text.strip()
        if not text:
            continue
        start, end, logprob = float(segment.start), float(segment.end), float(segment.avg_logprob)
        if not all(math.isfinite(value) for value in (start, end, logprob)) or end <= start:
            raise TranscriptionError("invalid")
        start, end = max(0.0, start), min(duration, end)
        if end <= start:
            continue
        transcript.append((start, end, text, logprob, "話者未判定"))
        low_probability += logprob < -1.0
        repetitions += text == previous
        previous = text
    speech_duration = float(info.duration_after_vad)
    warnings = []
    if not transcript:
        warnings.append("発話を認識できませんでした。原音を確認してください。")
    elif duration > 0 and sum(end - start for start, end, *_ in transcript) / duration < 0.1:
        warnings.append(
            "認識された区間が録音の10%未満です。無音か認識漏れか原音で確認してください。"
        )
    if repetitions:
        warnings.append("連続する同じ発話があります。原音で確認してください。")
    return Transcription(
        transcript,
        {
            "model": name,
            "settings_version": SETTINGS_VERSION,
            "device": "cpu",
            "compute_type": "int8",
            "language": "ja",
            "beam_size": 5,
            "condition_on_previous_text": condition_on_previous_text,
            "vad_filter": vad_filter,
            "duration_seconds": duration,
            "speech_seconds": speech_duration,
            "recognition_seconds": round(time.monotonic() - started, 3),
            "segment_count": len(transcript),
            "low_logprob_segments": low_probability,
            "consecutive_repetitions": repetitions,
            "warnings": warnings,
            **vocabulary_metadata,
        },
    )


def diarize(wav: Path, token_file: Path) -> list[tuple[float, float, str]]:
    token = token_file.read_text(encoding="utf-8").strip()
    if not token:
        raise ValueError("missing diarization token")
    import torch
    from pyannote.audio import Pipeline
    from scipy.io import wavfile

    pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-community-1", token=token)
    sample_rate, waveform = wavfile.read(wav)
    waveform = waveform.astype("float32") / 32768.0
    result = pipeline(
        {"waveform": torch.from_numpy(waveform).unsqueeze(0), "sample_rate": sample_rate}
    )
    annotation = getattr(result, "speaker_diarization", result)
    raw_turns = list(annotation.itertracks(yield_label=True))
    labels = list(dict.fromkeys(label for _, _, label in raw_turns))
    names = {label: f"話者{index + 1}" for index, label in enumerate(labels)}
    return [(float(turn.start), float(turn.end), names[label]) for turn, _, label in raw_turns]


def speaker_for(start: float, end: float, turns: list[tuple[float, float, str]]) -> str:
    overlap, label = max(
        ((max(0.0, min(end, b) - max(start, a)), label) for a, b, label in turns),
        default=(0.0, "話者未判定"),
    )
    return label if overlap > 0 else "話者未判定"


def transcribe_audio(
    audio_path: Path, token_file: Path, *, vocabulary_snapshot: dict | None = None
) -> Transcription:
    import numpy as np
    from scipy.io import wavfile

    with tempfile.TemporaryDirectory(prefix="daymeld-audio-") as temporary:
        wav = Path(temporary) / "analysis.wav"
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-i",
                    str(audio_path),
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    "-c:a",
                    "pcm_s16le",
                    str(wav),
                ],
                check=True,
                capture_output=True,
            )
        except (OSError, subprocess.CalledProcessError):
            raise TranscriptionError("decode") from None
        result = (
            recognize(wav, vocabulary_snapshot=vocabulary_snapshot)
            if vocabulary_snapshot is not None
            else recognize(wav)
        )
        if not result.segments:
            raise TranscriptionError("empty")
        _, samples = wavfile.read(wav)
        clipped = float(np.mean(np.abs(samples.astype("int32")) >= 32760))
        result.metadata["clipped_sample_fraction"] = clipped
        if clipped >= 0.01:
            result.metadata["warnings"].append(
                "変換後の音声の1%以上が最大振幅付近です。音割れや録音状態を確認してください。"
            )
        del samples
        try:
            turns = diarize(wav, token_file)
            result.metadata["diarization_status"] = "completed" if turns else "unavailable"
        except Exception:
            # Dependency/token errors must not discard successful speech recognition,
            # and exception strings can contain tokens, paths, or download URLs.
            turns = []
            result.metadata["diarization_status"] = "failed"
        if not turns:
            result.metadata["warnings"].append(
                "話者分離ができなかったため、話者未判定で保存しました。"
            )
        result.segments = [
            (start, end, text, confidence, speaker_for(start, end, turns))
            for start, end, text, confidence, _ in result.segments
        ]
        return result
