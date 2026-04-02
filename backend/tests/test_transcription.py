"""
tests/test_transcription.py — Tests for the transcription service and router.

Unit tests mock the whisperx module entirely so no GPU, model download, or
real video file is needed. Integration tests require TEST_VIDEO env var and
the transcription dependency group to be installed.
"""

import asyncio
import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

client = TestClient(app)

# Integration tests are skipped unless a real video is provided.
requires_video = pytest.mark.skipif(
    not os.environ.get("TEST_VIDEO"),
    reason="TEST_VIDEO not set",
)


def _make_whisperx_mock(word_segments: list[dict]) -> MagicMock:
    """Build a minimal whisperx module mock for unit tests.

    Args:
        word_segments: List of word dicts returned by the mock align step.

    Returns:
        MagicMock configured to satisfy the transcription pipeline calls.
    """
    mock_wx = MagicMock()

    # load_audio returns a numpy-like array (opaque to our code).
    mock_wx.load_audio.return_value = MagicMock()

    # load_model returns a model whose .transcribe() produces segments.
    mock_model = MagicMock()
    mock_model.transcribe.return_value = {
        "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}],
        "language": "en",
    }
    mock_wx.load_model.return_value = mock_model

    # load_align_model returns (align_model, metadata).
    mock_wx.load_align_model.return_value = (MagicMock(), MagicMock())

    # align returns the final result with word_segments.
    mock_wx.align.return_value = {"word_segments": word_segments}

    return mock_wx


# ---------------------------------------------------------------------------
# Unit tests — app/services/transcription.py
# ---------------------------------------------------------------------------


def test_transcribe_sends_progress_stages(tmp_path):
    """Progress callback must be called with all 4 stages in order."""
    # Create a dummy file so the path check in the router passes if needed.
    dummy = tmp_path / "video.mp4"
    dummy.write_bytes(b"")

    word_segments = [{"word": "hello", "start": 0.1, "end": 0.5}]
    mock_wx = _make_whisperx_mock(word_segments)

    stages: list[str] = []

    with patch.dict("sys.modules", {"whisperx": mock_wx}):
        from app.services.transcription import transcribe_video

        transcribe_video(str(dummy), "base", None, stages.append)

    assert stages == ["loading_audio", "downloading_model", "transcribing", "aligning"]


def test_word_timestamps_use_get(tmp_path):
    """Words missing start/end keys must produce None, not raise KeyError."""
    dummy = tmp_path / "video.mp4"
    dummy.write_bytes(b"")

    # Simulate alignment failure: word present but no start/end keys.
    word_segments = [{"word": "hello"}, {"word": "world", "start": 0.5, "end": 1.0}]
    mock_wx = _make_whisperx_mock(word_segments)

    with patch.dict("sys.modules", {"whisperx": mock_wx}):
        from app.services.transcription import transcribe_video

        words = transcribe_video(str(dummy), "base", None, lambda _: None)

    assert words[0]["start"] is None
    assert words[0]["end"] is None
    assert words[1]["start"] == 0.5


def test_segment_filter_excludes_out_of_range_words():
    """Words outside all segment ranges are removed; words with start=None are kept."""
    from app.services.transcription import filter_words_by_segments

    words = [
        {"word": "inside", "start": 1.5, "end": 2.0},
        {"word": "outside", "start": 5.0, "end": 5.5},
        {"word": "unaligned", "start": None, "end": None},
    ]
    segments = [{"start": 1.0, "end": 3.0}]

    result = filter_words_by_segments(words, segments)

    assert len(result) == 2
    words_text = [w["word"] for w in result]
    assert "inside" in words_text
    assert "unaligned" in words_text
    assert "outside" not in words_text


# ---------------------------------------------------------------------------
# Unit tests — app/routers/transcribe.py
# ---------------------------------------------------------------------------


def test_post_transcribe_404_missing_file():
    """POST /transcribe with a nonexistent file_path must return 404."""
    response = client.post(
        "/transcribe",
        json={
            "file_path": "/nonexistent/path/video.mp4",
            "job_id": "test-404",
        },
    )
    assert response.status_code == 404
    assert "File not found" in response.json()["detail"]


def test_post_transcribe_422_bad_extension(tmp_path):
    """POST /transcribe with an unsupported extension must return 422."""
    bad_file = tmp_path / "video.avi"
    bad_file.write_bytes(b"")

    response = client.post(
        "/transcribe",
        json={
            "file_path": str(bad_file),
            "job_id": "test-422-ext",
        },
    )
    assert response.status_code == 422
    assert "Unsupported file type" in response.json()["detail"]


def test_post_transcribe_422_bad_model_size(tmp_path):
    """POST /transcribe with an unknown model_size must return 422."""
    video = tmp_path / "video.mp4"
    video.write_bytes(b"")

    response = client.post(
        "/transcribe",
        json={
            "file_path": str(video),
            "job_id": "test-422-model",
            "model_size": "xlarge-ultra",
        },
    )
    assert response.status_code == 422
    assert "Unknown model size" in response.json()["detail"]


def test_post_transcribe_returns_202(tmp_path):
    """POST /transcribe with a valid request must return 202 with the job_id."""
    video = tmp_path / "video.mp4"
    video.write_bytes(b"")

    # Patch _run_job so the background task never actually tries to transcribe.
    async def _noop(*_args, **_kwargs):
        pass

    with patch("app.routers.transcribe._run_job", side_effect=_noop):
        response = client.post(
            "/transcribe",
            json={
                "file_path": str(video),
                "job_id": "test-202",
                "model_size": "base",
            },
        )

    assert response.status_code == 202
    assert response.json()["job_id"] == "test-202"


def test_post_transcribe_409_duplicate_job(tmp_path):
    """POST /transcribe with a job_id already in _jobs must return 409."""
    from app.routers import transcribe as tr

    video = tmp_path / "video.mp4"
    video.write_bytes(b"")

    job_id = "test-409"
    # Manually insert the job_id into the store to simulate a running job.
    tr._jobs[job_id] = asyncio.Queue()

    try:
        response = client.post(
            "/transcribe",
            json={
                "file_path": str(video),
                "job_id": job_id,
                "model_size": "base",
            },
        )
        assert response.status_code == 409
        assert "already exists" in response.json()["detail"]
    finally:
        # Clean up so other tests are not affected.
        tr._jobs.pop(job_id, None)


def test_ws_receives_done_with_result():
    """WebSocket must forward the 'done' event placed on the job queue."""
    from app.routers import transcribe as tr

    job_id = "test-ws-done"
    queue: asyncio.Queue = asyncio.Queue()
    tr._jobs[job_id] = queue

    # Pre-load the terminal event so the WS handler exits immediately.
    queue.put_nowait({"stage": "done", "result": []})

    with client.websocket_connect(f"/ws/progress/{job_id}") as ws:
        data = json.loads(ws.receive_text())

    assert data["stage"] == "done"
    assert data["result"] == []


def test_ws_job_not_found():
    """WebSocket with an unknown job_id must receive an error and close."""
    with client.websocket_connect("/ws/progress/no-such-job") as ws:
        data = json.loads(ws.receive_text())

    assert data["stage"] == "error"
    assert "Job not found" in data["detail"]


# ---------------------------------------------------------------------------
# Integration tests — require TEST_VIDEO env var
# ---------------------------------------------------------------------------


@requires_video
def test_transcribe_full_pipeline():
    """Full POST + WS round-trip on a real video; result must contain words."""
    video_path = os.environ["TEST_VIDEO"]
    job_id = "integration-test-full"

    with client.websocket_connect(f"/ws/progress/{job_id}") as ws:
        resp = client.post(
            "/transcribe",
            json={
                "file_path": video_path,
                "job_id": job_id,
                "model_size": "tiny",
            },
        )
        assert resp.status_code == 202

        # Drain events until we hit "done" or "error".
        terminal = None
        for _ in range(20):  # Guard against infinite loop
            msg = json.loads(ws.receive_text())
            if msg["stage"] in {"done", "error"}:
                terminal = msg
                break

    assert terminal is not None, "No terminal event received"
    assert terminal["stage"] == "done", f"Pipeline error: {terminal.get('detail')}"
    assert len(terminal["result"]) > 0, "Expected non-empty word list"


@requires_video
def test_transcribe_words_are_within_video_duration():
    """All word start/end timestamps must fall within [0, file_duration]."""
    import subprocess

    video_path = os.environ["TEST_VIDEO"]
    job_id = "integration-test-timestamps"

    # Get file duration via ffprobe.
    ffmpeg_path = os.environ.get("FFMPEG_PATH", "ffmpeg")
    ffprobe_path = str(Path(ffmpeg_path).parent / "ffprobe")
    result = subprocess.run(
        [
            ffprobe_path,
            "-v", "quiet",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            video_path,
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    duration = float(result.stdout.strip())

    with client.websocket_connect(f"/ws/progress/{job_id}") as ws:
        client.post(
            "/transcribe",
            json={
                "file_path": video_path,
                "job_id": job_id,
                "model_size": "tiny",
            },
        )
        terminal = None
        for _ in range(20):
            msg = json.loads(ws.receive_text())
            if msg["stage"] in {"done", "error"}:
                terminal = msg
                break

    assert terminal and terminal["stage"] == "done"

    for word in terminal["result"]:
        if word["start"] is not None:
            assert 0 <= word["start"] <= duration, f"start out of range: {word}"
        if word["end"] is not None:
            assert 0 <= word["end"] <= duration, f"end out of range: {word}"
