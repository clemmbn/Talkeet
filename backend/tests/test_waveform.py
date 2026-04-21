"""
tests/test_waveform.py — Tests for the waveform extraction endpoint and service.

Unit tests mock subprocess.run so no real video or ffmpeg binary is needed.
Integration tests require TEST_VIDEO env var and are skipped otherwise.

Test categories:
  - Service-level: extract_waveform() correctness (length, normalization, silence).
  - Router-level: HTTP status codes and response shape via TestClient.
  - Integration: real ffmpeg + real video, including performance target.
"""

import os
import struct
import time
from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.waveform import extract_waveform

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEST_VIDEO = os.environ.get("TEST_VIDEO")
requires_video = pytest.mark.skipif(not TEST_VIDEO, reason="TEST_VIDEO not set")


def _make_pcm_bytes(values: list[int]) -> bytes:
    """Pack a list of int16 values into raw little-endian PCM bytes."""
    return struct.pack(f"<{len(values)}h", *values)


def _make_mock_run(pcm_bytes: bytes) -> MagicMock:
    """Return a mock for subprocess.run that yields the given PCM bytes as stdout."""
    mock = MagicMock()
    mock.returncode = 0
    mock.stdout = pcm_bytes
    mock.stderr = b""
    return mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    """TestClient with ffmpeg_path injected into app state."""
    with TestClient(app) as c:
        app.state.ffmpeg_path = "/fake/ffmpeg"
        yield c


# ---------------------------------------------------------------------------
# Unit tests — extract_waveform()
# ---------------------------------------------------------------------------


class TestExtractWaveform:
    """Service-level unit tests for extract_waveform()."""

    def test_correct_length(self):
        """Returned list must have exactly num_samples elements."""
        # 4000 samples = 0.25 s at 16 kHz; any size works for length check.
        pcm = _make_pcm_bytes(list(range(4000)))
        with patch("app.services.waveform.subprocess.run", return_value=_make_mock_run(pcm)):
            result = extract_waveform("/fake/video.mp4", "/fake/ffmpeg", num_samples=100)
        assert len(result) == 100

    def test_normalized_values(self):
        """All returned values must be in [0.0, 1.0] and max must be 1.0."""
        # Vary amplitudes so normalization is non-trivial.
        rng = np.random.default_rng(42)
        samples = (rng.integers(-10000, 10000, size=8000)).tolist()
        pcm = _make_pcm_bytes(samples)
        with patch("app.services.waveform.subprocess.run", return_value=_make_mock_run(pcm)):
            result = extract_waveform("/fake/video.mp4", "/fake/ffmpeg", num_samples=200)
        assert all(0.0 <= v <= 1.0 for v in result)
        assert max(result) == pytest.approx(1.0)

    def test_silent_audio_returns_zeros(self):
        """All-zero PCM must produce all-zero output (no division-by-zero)."""
        pcm = _make_pcm_bytes([0] * 3200)
        with patch("app.services.waveform.subprocess.run", return_value=_make_mock_run(pcm)):
            result = extract_waveform("/fake/video.mp4", "/fake/ffmpeg", num_samples=50)
        assert result == [0.0] * 50

    def test_empty_audio_returns_zeros(self):
        """Empty stdout (video with no audio track) must return all zeros without error."""
        with patch("app.services.waveform.subprocess.run", return_value=_make_mock_run(b"")):
            result = extract_waveform("/fake/video.mp4", "/fake/ffmpeg", num_samples=50)
        assert result == [0.0] * 50

    def test_ffmpeg_failure_raises_runtime_error(self):
        """Non-zero ffmpeg returncode must raise RuntimeError with 'ffmpeg error' prefix."""
        mock = MagicMock()
        mock.returncode = 1
        mock.stdout = b""
        mock.stderr = b"some ffmpeg error"
        with patch("app.services.waveform.subprocess.run", return_value=mock):
            with pytest.raises(RuntimeError, match="ffmpeg error"):
                extract_waveform("/fake/video.mp4", "/fake/ffmpeg", num_samples=10)

    def test_indivisible_sample_count_handled(self):
        """num_samples that doesn't evenly divide the PCM length must still return num_samples items."""
        # 101 samples does not divide evenly into 10 buckets — padding must handle this.
        pcm = _make_pcm_bytes(list(range(101)))
        with patch("app.services.waveform.subprocess.run", return_value=_make_mock_run(pcm)):
            result = extract_waveform("/fake/video.mp4", "/fake/ffmpeg", num_samples=10)
        assert len(result) == 10


# ---------------------------------------------------------------------------
# Unit tests — POST /analyze/waveform router
# ---------------------------------------------------------------------------


class TestWaveformEndpoint:
    """HTTP-level unit tests for POST /analyze/waveform."""

    def _fake_waveform(self, *args, **kwargs):
        """Replacement for extract_waveform that returns a fixed list."""
        return [0.5] * 100

    def test_returns_200_and_correct_length(self, client, tmp_path):
        """Endpoint returns 200 and a list of the requested length."""
        video = tmp_path / "sample.mp4"
        video.touch()
        with patch("app.routers.analyze.extract_waveform", side_effect=self._fake_waveform):
            resp = client.post(
                "/analyze/waveform",
                json={"file_path": str(video), "num_samples": 100},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) == 100

    def test_404_missing_file(self, client):
        """Endpoint returns 404 when the file does not exist."""
        resp = client.post(
            "/analyze/waveform",
            json={"file_path": "/does/not/exist.mp4"},
        )
        assert resp.status_code == 404
        assert "File not found" in resp.json()["detail"]

    def test_422_unsupported_extension(self, client, tmp_path):
        """Endpoint returns 422 for unsupported file extensions."""
        video = tmp_path / "clip.avi"
        video.touch()
        resp = client.post(
            "/analyze/waveform",
            json={"file_path": str(video)},
        )
        assert resp.status_code == 422
        assert "Unsupported file type" in resp.json()["detail"]

    def test_500_on_ffmpeg_failure(self, client, tmp_path):
        """Endpoint returns 500 when the waveform service raises RuntimeError."""
        video = tmp_path / "bad.mp4"
        video.touch()
        with patch(
            "app.routers.analyze.extract_waveform",
            side_effect=RuntimeError("ffmpeg error: broken pipe"),
        ):
            resp = client.post(
                "/analyze/waveform",
                json={"file_path": str(video)},
            )
        assert resp.status_code == 500
        assert "ffmpeg error" in resp.json()["detail"]

    def test_default_num_samples(self, client, tmp_path):
        """Omitting num_samples uses the default of 1000."""
        video = tmp_path / "default.mp4"
        video.touch()
        with patch(
            "app.routers.analyze.extract_waveform",
            side_effect=lambda path, ffmpeg, n: [0.0] * n,
        ):
            resp = client.post(
                "/analyze/waveform",
                json={"file_path": str(video)},
            )
        assert resp.status_code == 200
        assert len(resp.json()) == 1000


# ---------------------------------------------------------------------------
# Integration tests — real ffmpeg + real video
# ---------------------------------------------------------------------------


@requires_video
class TestWaveformIntegration:
    """Integration tests that run against a real video file.

    Set TEST_VIDEO=/path/to/video.mp4 to enable.
    """

    @pytest.fixture(scope="class")
    def int_client(self):
        """TestClient that uses the actual ffmpeg binary from the environment."""
        with TestClient(app) as c:
            ffmpeg = os.environ.get("FFMPEG_PATH", "ffmpeg")
            app.state.ffmpeg_path = ffmpeg
            yield c

    def test_length_matches_request(self, int_client):
        """Response list length must equal num_samples."""
        resp = int_client.post(
            "/analyze/waveform",
            json={"file_path": TEST_VIDEO, "num_samples": 500},
        )
        assert resp.status_code == 200
        assert len(resp.json()) == 500

    def test_values_in_range(self, int_client):
        """All values must be floats in [0.0, 1.0] and max must be 1.0."""
        resp = int_client.post(
            "/analyze/waveform",
            json={"file_path": TEST_VIDEO, "num_samples": 500},
        )
        data = resp.json()
        assert all(0.0 <= v <= 1.0 for v in data)
        assert max(data) == pytest.approx(1.0, abs=1e-6)

    def test_performance_under_five_seconds(self, int_client):
        """Full extraction must complete in under 5 seconds (milestone target)."""
        start = time.time()
        resp = int_client.post(
            "/analyze/waveform",
            json={"file_path": TEST_VIDEO, "num_samples": 1000},
        )
        elapsed = time.time() - start
        assert resp.status_code == 200
        assert elapsed < 5.0, f"Waveform extraction took {elapsed:.1f}s (limit: 5s)"
