"""
app/services/silence.py — Core silence detection and segment-building logic.

Responsibilities:
  - Extract audio from a video file to a temporary 16 kHz mono WAV.
  - Probe audio duration and video frame rate via ffprobe.
  - Run ffmpeg's silencedetect filter and parse its stderr output.
  - Invert silence intervals into speech intervals, apply asymmetric padding,
    drop sub-5-frame segments, and produce a contiguous segment list.

Constraints:
  - All subprocess calls use the explicit ffmpeg_path argument; PATH is never
    searched so the bundled binary is always used in production.
  - Callers are responsible for deleting the temp WAV returned by extract_audio.
  - ffprobe is expected to live in the same directory as ffmpeg.
"""

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import TypedDict


class Segment(TypedDict):
    """A single contiguous time range in the video.

    Attributes:
        start: Segment start time in seconds (inclusive).
        end: Segment end time in seconds (exclusive).
        type: Either "speech" (keep) or "silence" (cut candidate).
    """

    start: float
    end: float
    type: str


def extract_audio(input_path: str, ffmpeg_path: str) -> str:
    """Extract audio from a video file to a temporary 16 kHz mono WAV.

    Uses a temp path from tempfile.mktemp so the caller controls the file
    lifetime and must delete it when done.

    Args:
        input_path: Absolute path to the source video file.
        ffmpeg_path: Absolute path to the ffmpeg binary.

    Returns:
        Path to the extracted WAV file (caller must delete).

    Raises:
        RuntimeError: If ffmpeg exits with a non-zero return code, with the
            last 500 characters of stderr included in the message.
    """
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
    """Return the duration of a WAV file in seconds using ffprobe.

    Args:
        wav_path: Path to the WAV file to probe.
        ffmpeg_path: Absolute path to the ffmpeg binary; ffprobe is resolved
            from the same directory.

    Returns:
        Duration in seconds as a float.

    Raises:
        RuntimeError: If ffprobe exits with a non-zero return code.
    """
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
    """Return the frame rate of the first video stream in a file.

    ffprobe reports the frame rate as a rational number string (e.g.
    "30000/1001" for 29.97 fps). This function parses that fraction. Any
    parse failure (missing stream, malformed output) falls back to 30.0 so
    that segment-dropping still works for audio-only or exotic inputs.

    Args:
        input_path: Absolute path to the source video file.
        ffmpeg_path: Absolute path to the ffmpeg binary; ffprobe is resolved
            from the same directory.

    Returns:
        Frame rate as a float. Falls back to 30.0 on parse error.
    """
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
        # Non-fatal: segment dropping uses 5/fps, so 30.0 is a safe fallback.
        return 30.0


def detect_silences(
    wav_path: str,
    threshold_db: float,
    min_duration: float,
    ffmpeg_path: str,
) -> list[tuple[float, float]]:
    """Run ffmpeg silencedetect and return (silence_start, silence_end) pairs.

    ffmpeg writes silence markers to stderr (not stdout). Each silence region
    produces a "silence_start" line followed by a "silence_end" line. If the
    file ends while still in silence there is no closing "silence_end", so
    a trailing open interval is represented as (start, float("inf")).

    Args:
        wav_path: Path to the WAV file to analyse.
        threshold_db: dB level below which audio is considered silent (e.g. -25.0).
        min_duration: Minimum duration in seconds for a region to count as silence.
        ffmpeg_path: Absolute path to the ffmpeg binary.

    Returns:
        List of (silence_start, silence_end) tuples in chronological order.
        silence_end is float("inf") for a trailing open silence.
    """
    result = subprocess.run(
        [
            ffmpeg_path, "-i", wav_path,
            "-af", f"silencedetect=noise={threshold_db}dB:duration={min_duration}",
            # Discard the decoded audio; we only need the filter's log output.
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
            # Guard against a silence_end with no preceding silence_start
            if m and current_start is not None:
                silences.append((current_start, float(m.group(1))))
                current_start = None

    # File ended while still silent — record open interval so build_segments
    # can clamp it to audio_duration rather than silently dropping it.
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
    """Build a contiguous list of speech and silence segments for the full file.

    Algorithm:
      1. Invert silence intervals to derive raw speech intervals.
      2. Apply asymmetric padding: expand each speech interval backward by
         pre_padding and forward by post_padding, clamping so adjacent
         intervals never overlap (uses the midpoint between adjacent speech
         ends/starts as the hard ceiling for post_padding).
      3. Drop any speech segment shorter than 5 frames (5 / fps seconds).
      4. Fill the remaining gaps (start-of-file, between kept speech segments,
         end-of-file) with silence segments.

    Args:
        silences: Chronological list of (silence_start, silence_end) tuples as
            returned by detect_silences. silence_end may be float("inf").
        audio_duration: Total duration of the audio in seconds.
        pre_padding: Seconds to expand each speech interval toward the start.
        post_padding: Seconds to expand each speech interval toward the end.
        fps: Video frame rate used to compute the minimum segment duration.

    Returns:
        Flat list of Segment dicts covering [0, audio_duration] without gaps
        or overlaps, alternating speech and silence types.
    """
    # --- Step 1: Invert silences → raw speech intervals ---
    speech_raw: list[tuple[float, float]] = []
    cursor = 0.0
    for sil_start, sil_end in silences:
        # Clamp inf to the known audio duration so arithmetic below is safe.
        effective_end = min(sil_end, audio_duration) if sil_end != float("inf") else audio_duration
        if sil_start > cursor:
            speech_raw.append((cursor, sil_start))
        cursor = effective_end

    # Trailing speech after the last silence (or the entire file if no silence).
    if cursor < audio_duration:
        speech_raw.append((cursor, audio_duration))

    # --- Step 2: Apply asymmetric padding ---
    min_dur = 5.0 / fps  # Minimum speech duration in seconds (5-frame rule)
    padded_speech: list[tuple[float, float]] = []
    prev_padded_end = 0.0  # Tracks where the previous padded segment ended

    for i, (raw_start, raw_end) in enumerate(speech_raw):
        # pre_padding: expand start backward, but never before 0 or the
        # previous segment's padded end (avoids overlap between adjacent speech).
        pad_start = max(raw_start - pre_padding, 0.0, prev_padded_end)
        pad_end = raw_end + post_padding

        if i < len(speech_raw) - 1:
            next_raw_start = speech_raw[i + 1][0]
            # Clamp post_padding to the midpoint between this speech end and
            # the next speech start so the padding never bleeds into silence
            # that belongs to the next interval.
            midpoint = (raw_end + next_raw_start) / 2.0
            pad_end = min(pad_end, midpoint)

        # Guarantee pad_end is never before the raw speech end (padding can only grow).
        pad_end = max(pad_end, raw_end)
        # Clamp to file boundary.
        pad_end = min(pad_end, audio_duration)
        prev_padded_end = pad_end

        # --- Step 3: Drop sub-5-frame speech segments ---
        if (pad_end - pad_start) >= min_dur:
            padded_speech.append((pad_start, pad_end))

    # --- Step 4: Fill gaps with silence segments ---
    segments: list[Segment] = []
    cursor = 0.0

    for seg_start, seg_end in padded_speech:
        # Any gap before this speech segment becomes a silence segment.
        if seg_start > cursor:
            segments.append({"start": cursor, "end": seg_start, "type": "silence"})
        segments.append({"start": seg_start, "end": seg_end, "type": "speech"})
        cursor = seg_end

    # Trailing silence from the last speech segment to the end of the file.
    if cursor < audio_duration:
        segments.append({"start": cursor, "end": audio_duration, "type": "silence"})

    return segments
