"""Compare local ASR without opening the application DB or printing private text.

Run each model in a separate process for comparable peak RSS. A reference must be
manually verified (or a known synthetic script), never another model's hypothesis.
"""

from __future__ import annotations

import argparse
import json
import resource
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

from daily_reader.conversation_transcription import recognize


def normalized(text: str) -> str:
    return "".join(
        c.lower()
        for c in unicodedata.normalize("NFKC", text)
        if not c.isspace() and not unicodedata.category(c).startswith("P")
    )


def character_errors(reference: str, hypothesis: str) -> dict:
    reference, hypothesis = normalized(reference), normalized(hypothesis)
    row = list(range(len(hypothesis) + 1))
    for i, a in enumerate(reference, 1):
        following = [i]
        for j, b in enumerate(hypothesis, 1):
            following.append(min(following[-1] + 1, row[j] + 1, row[j - 1] + (a != b)))
        row = following
    return {
        "reference_characters": len(reference),
        "character_errors": row[-1],
        "cer": row[-1] / len(reference) if reference else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio", type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--model-cache")
    parser.add_argument("--start", type=float, default=0)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--previous-text", action="store_true")
    parser.add_argument("--no-vad", action="store_true")
    args = parser.parse_args()
    if args.start < 0 or not 0 < args.seconds <= 600:
        parser.error("start must be nonnegative; seconds must be in (0,600]")
    try:
        with tempfile.TemporaryDirectory(prefix="daymeld-asr-evaluation-") as temporary:
            wav = Path(temporary) / "clip.wav"
            subprocess.run(
                [
                    "ffmpeg",
                    "-nostdin",
                    "-v",
                    "error",
                    "-ss",
                    str(args.start),
                    "-i",
                    str(args.audio),
                    "-t",
                    str(args.seconds),
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
            result = recognize(
                wav,
                model_name=args.model,
                download_root=args.model_cache,
                condition_on_previous_text=args.previous_text,
                vad_filter=not args.no_vad,
            )
        metrics = result.metadata
        text = "".join(segment[2] for segment in result.segments)
        metrics["recognized_characters"] = len(normalized(text))
        metrics["recognized_seconds"] = sum(b - a for a, b, *_ in result.segments)
        metrics["peak_rss_mib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (
            1024**2 if sys.platform == "darwin" else 1024
        )
        if args.reference:
            metrics.update(character_errors(args.reference.read_text(encoding="utf-8"), text))
        print(json.dumps(metrics, ensure_ascii=False))
    except Exception:
        # Library exceptions can contain local filenames or credential-bearing URLs.
        print("Local ASR evaluation failed; no audio or transcript was printed.", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
