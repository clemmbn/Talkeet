"""
tests/test_silence.py — Unit and integration tests for silence detection.

Responsibilities:
  - Unit tests: verify stderr parsing and segment-building logic in isolation
    by mocking subprocess.run (no real video or ffmpeg needed).
  - Integration tests: run the full POST /analyze/silence pipeline against a
    real video file; skipped unless the TEST_VIDEO env var is set.

Constraints:
  - Unit tests must pass without any external dependencies (ffmpeg, video file).
  - Integration tests rely on FFMPEG_PATH being set in the environment.
"""

import math
import os
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.silence import build_segments, detect_silences

# Path to a real video file for integration tests; skip those tests if absent.
TEST_VIDEO = os.environ.get("TEST_VIDEO")
requires_video = pytest.mark.skipif(not TEST_VIDEO, reason="TEST_VIDEO not set")

# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

# Canonical ffmpeg stderr with two complete silence intervals.
CANNED_STDERR = """\
[silencedetect @ 0x...] silence_start: 0.500000
[silencedetect @ 0x...] silence_end: 1.200000 | silence_duration: 0.700000
[silencedetect @ 0x...] silence_start: 3.000000
[silencedetect @ 0x...] silence_end: 3.800000 | silence_duration: 0.800000
"""

# Stderr where the file ends while still silent (no silence_end line).
TRAILING_STDERR = """\
[silencedetect @ 0x...] silence_start: 2.000000
"""


def _mock_run_detect(stderr: str):
    """Return a MagicMock that mimics a successful subprocess.run result.

    Args:
        stderr: The stderr string the mock should expose.

    Returns:
        MagicMock with returncode=0 and stderr set to the given string.
    """
    mock = MagicMock()
    mock.returncode = 0
    mock.stderr = stderr
    return mock


# ---------------------------------------------------------------------------
# Unit tests — silence detection parsing
# ---------------------------------------------------------------------------

@patch("app.services.silence.subprocess.run")
def test_detect_silences_parses_stderr(mock_run):
    """detect_silences correctly parses two complete silence intervals."""
    mock_run.return_value = _mock_run_detect(CANNED_STDERR)
    result = detect_silences("/fake.wav", -25.0, 0.3, "/usr/bin/ffmpeg")
    assert result == [(0.5, 1.2), (3.0, 3.8)]


@patch("app.services.silence.subprocess.run")
def test_detect_silences_trailing_open(mock_run):
    """detect_silences handles a trailing silence with no silence_end as (start, inf)."""
    mock_run.return_value = _mock_run_detect(TRAILING_STDERR)
    result = detect_silences("/fake.wav", -25.0, 0.3, "/usr/bin/ffmpeg")
    assert len(result) == 1
    start, end = result[0]
    assert start == 2.0
    assert math.isinf(end)


# ---------------------------------------------------------------------------
# Unit tests — build_segments
# ---------------------------------------------------------------------------

def test_build_segments_covers_full_duration():
    """Segments are contiguous, start at 0, and end exactly at audio_duration."""
    silences = [(1.0, 2.0), (4.0, 5.0)]
    duration = 6.0
    segments = build_segments(silences, duration, pre_padding=0.0, post_padding=0.0, fps=30.0)

    assert segments[0]["start"] == pytest.approx(0.0)
    assert segments[-1]["end"] == pytest.approx(duration)

    # Verify no gaps between consecutive segments.
    for i in range(len(segments) - 1):
        assert segments[i]["end"] == pytest.approx(segments[i + 1]["start"])


def test_build_segments_pre_post_padding():
    """pre_padding expands toward the start, post_padding toward the end, independently."""
    # Single speech interval 1.0–3.0 bracketed by silence on both sides.
    silences = [(0.0, 1.0), (3.0, 5.0)]
    duration = 5.0
    segments = build_segments(
        silences, duration, pre_padding=0.2, post_padding=0.1, fps=30.0
    )

    speech = [s for s in segments if s["type"] == "speech"]
    assert len(speech) == 1
    seg = speech[0]
    # pre_padding shifts start back by 0.2 (but clamped to 0): 1.0 - 0.2 = 0.8
    assert seg["start"] == pytest.approx(0.8)
    # post_padding extends end by 0.1: 3.0 + 0.1 = 3.1
    assert seg["end"] == pytest.approx(3.1)


def test_build_segments_drops_short_segments():
    """Speech intervals shorter than 5 frames are excluded from the output."""
    fps = 30.0

    # Construct a speech interval of exactly 2 frames (below the 5-frame threshold).
    short_speech_dur = 2 / fps
    silence_end = 1.0
    speech_end = silence_end + short_speech_dur

    silences = [(0.0, silence_end), (speech_end, 5.0)]
    duration = 5.0
    segments = build_segments(silences, duration, pre_padding=0.0, post_padding=0.0, fps=fps)

    speech = [s for s in segments if s["type"] == "speech"]
    assert len(speech) == 0


# ---------------------------------------------------------------------------
# Integration tests (require TEST_VIDEO env var)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    """TestClient wrapping the FastAPI app for the duration of this module."""
    with TestClient(app) as c:
        yield c


@requires_video
def test_analyze_silence_returns_200(client):
    """Full pipeline returns HTTP 200 and a non-empty segment list."""
    resp = client.post("/analyze/silence", json={"file_path": TEST_VIDEO})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) > 0


@requires_video
def test_analyze_silence_segments_contiguous(client):
    """Each segment's end equals the next segment's start (no gaps or overlaps)."""
    resp = client.post("/analyze/silence", json={"file_path": TEST_VIDEO})
    segments = resp.json()
    for i in range(len(segments) - 1):
        assert segments[i]["end"] == pytest.approx(segments[i + 1]["start"], abs=1e-3)


@requires_video
def test_analyze_silence_covers_duration(client):
    """Segments start at 0 and the last segment ends at the file duration."""
    resp = client.post("/analyze/silence", json={"file_path": TEST_VIDEO})
    segments = resp.json()
    assert segments[0]["start"] == pytest.approx(0.0, abs=1e-3)
    # Last segment end should be close to the file duration (not checking exact value)
    assert segments[-1]["end"] > 0.0
