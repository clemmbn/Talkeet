import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import TypedDict


class Segment(TypedDict):
    start: float
    end: float
    type: str


def extract_audio(input_path: str, ffmpeg_path: str) -> str:
    """Extract 16 kHz mono WAV to a temp file. Caller is responsible for cleanup."""
    wav_path = tempfile.mktemp(suffix=".wav", prefix="talkeet_")
    result = subprocess.run(
        [ffmpeg_path, "-y", "-i", input_path, "-ar", "16000", "-ac", "1", "-f", "wav", wav_path],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg error: {result.stderr[-500:]}")
    return wav_path


def get_audio_duration(wav_path: str, ffmpeg_path: str) -> float:
    """Return audio duration in seconds using ffprobe."""
    ffprobe_path = str(Path(ffmpeg_path).parent / "ffprobe")
    result = subprocess.run(
        [
            ffprobe_path, "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            wav_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffprobe error getting duration")
    return float(result.stdout.strip())


def get_video_fps(input_path: str, ffmpeg_path: str) -> float:
    """Return the video frame rate. Falls back to 30.0 if not parseable."""
    ffprobe_path = str(Path(ffmpeg_path).parent / "ffprobe")
    result = subprocess.run(
        [
            ffprobe_path, "-v", "quiet",
            "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate",
            "-of", "default=noprint_wrappers=1:nokey=1",
            input_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    try:
        raw = result.stdout.strip()
        if "/" in raw:
            num, den = raw.split("/")
            return float(num) / float(den)
        return float(raw)
    except (ValueError, ZeroDivisionError):
        return 30.0


def detect_silences(
    wav_path: str,
    threshold_db: float,
    min_duration: float,
    ffmpeg_path: str,
) -> list[tuple[float, float]]:
    """Run ffmpeg silencedetect and return (silence_start, silence_end) tuples."""
    result = subprocess.run(
        [
            ffmpeg_path, "-i", wav_path,
            "-af", f"silencedetect=noise={threshold_db}dB:duration={min_duration}",
            "-f", "null", "-",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    silences: list[tuple[float, float]] = []
    current_start: float | None = None

    for line in result.stderr.splitlines():
        if "silence_start" in line:
            m = re.search(r"silence_start:\s*([\d.eE+-]+)", line)
            if m:
                current_start = float(m.group(1))
        elif "silence_end" in line:
            m = re.search(r"silence_end:\s*([\d.eE+-]+)", line)
            if m and current_start is not None:
                silences.append((current_start, float(m.group(1))))
                current_start = None

    if current_start is not None:
        silences.append((current_start, float("inf")))

    return silences


def build_segments(
    silences: list[tuple[float, float]],
    audio_duration: float,
    pre_padding: float,
    post_padding: float,
    fps: float,
) -> list[Segment]:
    """Build contiguous speech/silence segments covering the full duration."""
    # 1. Invert silences → raw speech intervals
    speech_raw: list[tuple[float, float]] = []
    cursor = 0.0
    for sil_start, sil_end in silences:
        effective_end = min(sil_end, audio_duration) if sil_end != float("inf") else audio_duration
        if sil_start > cursor:
            speech_raw.append((cursor, sil_start))
        cursor = effective_end

    if cursor < audio_duration:
        speech_raw.append((cursor, audio_duration))

    # 2. Apply asymmetric padding
    min_dur = 5.0 / fps
    padded_speech: list[tuple[float, float]] = []
    prev_padded_end = 0.0

    for i, (raw_start, raw_end) in enumerate(speech_raw):
        pad_start = max(raw_start - pre_padding, 0.0, prev_padded_end)
        pad_end = raw_end + post_padding

        if i < len(speech_raw) - 1:
            next_raw_start = speech_raw[i + 1][0]
            midpoint = (raw_end + next_raw_start) / 2.0
            pad_end = min(pad_end, midpoint)

        pad_end = max(pad_end, raw_end)
        pad_end = min(pad_end, audio_duration)
        prev_padded_end = pad_end

        # 3. Drop sub-5-frame segments
        if (pad_end - pad_start) >= min_dur:
            padded_speech.append((pad_start, pad_end))

    # 4. Fill gaps with silence segments
    segments: list[Segment] = []
    cursor = 0.0

    for seg_start, seg_end in padded_speech:
        if seg_start > cursor:
            segments.append({"start": cursor, "end": seg_start, "type": "silence"})
        segments.append({"start": seg_start, "end": seg_end, "type": "speech"})
        cursor = seg_end

    if cursor < audio_duration:
        segments.append({"start": cursor, "end": audio_duration, "type": "silence"})

    return segments
