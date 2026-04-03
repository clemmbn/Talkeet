"""
app/services/transcription.py — WhisperX transcription pipeline.

Responsibilities:
  - Run the full WhisperX pipeline (load audio, load model, transcribe, align).
  - Flatten word-level alignment results into a uniform list of dicts.
  - Filter words by provided segment ranges (optional).

Constraints:
  - Always uses device="cpu" — MPS is not supported by CTranslate2 on Apple Silicon.
  - This module is synchronous (blocking). Callers must invoke it via
    asyncio.get_event_loop().run_in_executor() to avoid blocking the event loop.
  - whisperx is an optional dependency (transcription group). Import errors are
    surfaced at call time, not at module load, so the base server can start without
    the transcription group installed.
  - Pinned to torch==2.8.0 / torchaudio==2.8.0 / whisperx==3.8.4 / pyannote-audio==4.0.4.
    These are the only versions known to work together without compatibility shims.
    Do not upgrade these without re-validating the full pipeline end-to-end.
"""

from pathlib import Path
from typing import Callable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# All model sizes supported by WhisperX / faster-whisper.
MODEL_SIZES: set[str] = {
    "tiny",
    "base",
    "small",
    "medium",
    "large",
    "large-v2",
    "large-v3",
    "large-v3-turbo",
}

# Models are cached here so they survive across server restarts and don't
# require a re-download on every run.
MODEL_CACHE_DIR: str = str(
    Path.home() / "Library" / "Application Support" / "Talkeet" / "models"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def transcribe_video(
    file_path: str,
    model_size: str,
    language: str | None,
    progress_callback: Callable[[str], None],
) -> list[dict]:
    """Run the full WhisperX transcription pipeline on a video file.

    Stages (each announced via progress_callback before execution):
      1. "loading_audio"      — decode audio to a float32 numpy array
      2. "downloading_model"  — load (and download if missing) the Whisper model
      3. "transcribing"       — run Whisper inference to get segments + language
      4. "aligning"           — align segments to word-level timestamps

    Args:
        file_path: Absolute path to the video file (.mp4 or .mov).
        model_size: Whisper model variant (e.g. "base", "large-v3").
        language: ISO 639-1 language code, or None for auto-detection from the
                  first 30 seconds of audio.
        progress_callback: Called with a stage name string before each stage.
                           Must be thread-safe (see make_progress_callback in
                           the router for the asyncio bridge).

    Returns:
        List of word dicts: [{"word": str, "start": float|None,
                               "end": float|None, "speaker": str|None}].
        "start" and "end" are None when alignment could not place a word.

    Raises:
        ImportError: If the transcription dependency group is not installed.
        RuntimeError: If WhisperX encounters a pipeline error (e.g. unsupported
                      alignment language). Caller should surface this as a
                      WebSocket error event.
    """
    # Import here so the base server starts even if transcription deps are missing.
    import whisperx  # type: ignore[import]

    # Stage 1 — load audio as a 16 kHz mono float32 array.
    progress_callback("loading_audio")
    audio = whisperx.load_audio(file_path)

    # Stage 2 — load (or reuse cached) Whisper model.
    # device="cpu" is mandatory on Apple Silicon — CTranslate2 does not support MPS.
    # compute_type="int8" gives the best CPU throughput with minimal accuracy loss.
    progress_callback("downloading_model")
    model = whisperx.load_model(
        model_size,
        device="cpu",
        compute_type="int8",
        language=language,           # None → auto-detect from first 30s
        download_root=MODEL_CACHE_DIR,
    )

    # Stage 3 — Whisper inference; produces coarse segment-level timestamps.
    # batch_size=1 avoids memory pressure on CPU.
    progress_callback("transcribing")
    result = model.transcribe(audio, batch_size=1)
    detected_language = result["language"]
    # Note: if the file starts with silence, auto-detection may produce a wrong
    # language. Callers should pass language= explicitly when this is a concern.

    # Stage 4 — word-level alignment via wav2vec2.
    progress_callback("aligning")
    align_model, metadata = whisperx.load_align_model(
        language_code=detected_language,
        device="cpu",
        model_dir=MODEL_CACHE_DIR,
    )
    result = whisperx.align(
        result["segments"],
        align_model,
        metadata,
        audio,
        "cpu",
        return_char_alignments=False,
    )

    # Flatten word_segments into a uniform list.
    # Use .get() for timestamps — alignment may fail to place individual words,
    # leaving "start"/"end" absent from the dict rather than set to None.
    # Cast to plain float — whisperx returns np.float64, which FastAPI's JSON
    # encoder cannot serialize.
    def _to_float(v):
        return float(v) if v is not None else None

    words = [
        {
            "word": w["word"],
            "start": _to_float(w.get("start")),
            "end": _to_float(w.get("end")),
            "speaker": w.get("speaker"),  # None — diarization is out of scope for M2
        }
        for w in result["word_segments"]
    ]

    return words


def filter_words_by_segments(
    words: list[dict],
    segments: list[dict],
) -> list[dict]:
    """Discard words that fall outside all provided segment ranges.

    Words are kept when their "start" timestamp falls within at least one
    segment's [start, end] interval (inclusive). Words with start=None are
    always kept because alignment could not place them — discarding them would
    silently drop valid transcript content.

    Args:
        words: List of word dicts with optional "start" key (float or None).
        segments: List of segment dicts with "start" and "end" float keys.

    Returns:
        Filtered list of word dicts.
    """
    def _in_any_segment(start: float) -> bool:
        # Linear scan is fine — segment lists are short (tens of items).
        return any(seg["start"] <= start <= seg["end"] for seg in segments)

    return [
        w for w in words
        if w.get("start") is None or _in_any_segment(w["start"])
    ]
