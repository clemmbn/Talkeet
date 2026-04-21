"""
tests/test_export.py — Tests for export endpoints (M4).

Unit tests mock the service layer; no real video or ffmpeg needed.
Integration tests require TEST_VIDEO env var.
"""

import shutil

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch, MagicMock
from app.main import app

client = TestClient(app)


@pytest.fixture(autouse=True)
def set_app_state():
    """Pre-set app.state.ffmpeg_path so router tests don't depend on lifespan.

    The module-level TestClient may not trigger the lifespan context manager.
    Setting ffmpeg_path directly ensures all endpoints can access it without
    requiring a real ffmpeg binary in unit tests (mocks override the call).
    """
    app.state.ffmpeg_path = shutil.which("ffmpeg") or "ffmpeg"

SAMPLE_SEGMENTS = [
    {"start": 0.0,  "end": 1.2, "type": "silence"},
    {"start": 1.2,  "end": 4.5, "type": "speech"},
    {"start": 4.5,  "end": 5.0, "type": "silence"},
    {"start": 5.0,  "end": 8.3, "type": "speech"},
    {"start": 8.3,  "end": 9.0, "type": "silence"},
]

SAMPLE_WORDS = [
    {"word": "Hello", "start": 1.3, "end": 1.6},
    {"word": "world", "start": 1.7, "end": 2.0},
    {"word": "how",   "start": 5.1, "end": 5.4},
    {"word": "are",   "start": 5.5, "end": 5.7},
    {"word": "you",   "start": 5.8, "end": 6.0},
]


def test_export_edl_404_missing_file():
    resp = client.post("/export/edl", json={
        "file_path": "/nonexistent/video.mp4",
        "segments": SAMPLE_SEGMENTS,
        "words": [],
    })
    assert resp.status_code == 404


def test_export_srt_404_missing_file():
    resp = client.post("/export/srt", json={
        "file_path": "/nonexistent/video.mp4",
        "segments": SAMPLE_SEGMENTS,
        "words": SAMPLE_WORDS,
    })
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Service-level unit tests (imported directly — no HTTP)
# ---------------------------------------------------------------------------

from app.services.export import (
    generate_edl,
    generate_fcpxml,
    generate_premiere_xml,
    generate_srt,
    seconds_to_timecode_edl,
    seconds_to_timecode_srt,
)
import xml.etree.ElementTree as ET


# ---------------------------------------------------------------------------
# seconds_to_timecode_edl
# ---------------------------------------------------------------------------

def test_timecode_edl_zero():
    assert seconds_to_timecode_edl(0.0, 30) == "00:00:00:00"


def test_timecode_edl_one_hour():
    # 3600 seconds at 30fps = 108000 frames
    assert seconds_to_timecode_edl(3600.0, 30) == "01:00:00:00"


def test_timecode_edl_mixed():
    # 1m 2s 15f at 30fps → 62.5 seconds
    assert seconds_to_timecode_edl(62.5, 30) == "00:01:02:15"


# ---------------------------------------------------------------------------
# seconds_to_timecode_srt
# ---------------------------------------------------------------------------

def test_timecode_srt_zero():
    assert seconds_to_timecode_srt(0.0) == "00:00:00,000"


def test_timecode_srt_basic():
    # 1m 2.345s
    assert seconds_to_timecode_srt(62.345) == "00:01:02,345"


def test_timecode_srt_millisecond_truncation():
    # 1.2345s → 1234ms (truncated, not rounded, to avoid subtitle overlap)
    assert seconds_to_timecode_srt(1.2345) == "00:00:01,234"


# ---------------------------------------------------------------------------
# generate_edl
# ---------------------------------------------------------------------------

def test_edl_header():
    content = generate_edl([], fps=30, title="myvideo")
    assert "TITLE: myvideo" in content
    assert "FCM: NON-DROP FRAME" in content


def test_edl_single_event():
    # 1.0s → 4.0s at 30fps: src_in=00:00:01:00, src_out=00:00:04:00
    content = generate_edl([(1.0, 4.0)], fps=30, title="test")
    assert "001" in content
    assert "00:00:01:00" in content
    assert "00:00:04:00" in content


def test_edl_record_timecodes_cumulative():
    # Two clips: [1–4s] then [6–9s]
    # rec_in clip1 = 0s, rec_out = 3s; rec_in clip2 = 3s, rec_out = 6s
    content = generate_edl([(1.0, 4.0), (6.0, 9.0)], fps=30, title="test")
    # rec_in of second clip should be 00:00:03:00
    assert "00:00:03:00" in content


def test_edl_empty_segments():
    content = generate_edl([], fps=30, title="empty")
    # Only header, no event lines
    event_lines = [l for l in content.splitlines() if l.strip() and l[0].isdigit()]
    assert event_lines == []


# ---------------------------------------------------------------------------
# generate_srt
# ---------------------------------------------------------------------------

def test_srt_empty_words():
    content = generate_srt([(1.0, 4.0), (6.0, 9.0)], words=[])
    # No words → no subtitle blocks
    assert content.strip() == ""


def test_srt_basic_block():
    content = generate_srt(
        [(1.0, 4.0)],
        words=[
            {"word": "Hello", "start": 1.3, "end": 1.6},
            {"word": "world", "start": 1.7, "end": 2.0},
        ],
    )
    assert "1\n" in content
    assert "00:00:01,300 --> 00:00:02,000" in content
    assert "Hello world" in content


def test_srt_words_bucketed_by_segment():
    # Words at 1.3 and 5.1 fall in separate segments
    content = generate_srt(
        [(1.0, 4.0), (5.0, 8.0)],
        words=[
            {"word": "Hello", "start": 1.3, "end": 1.6},
            {"word": "how",   "start": 5.1, "end": 5.4},
        ],
    )
    # Should produce 2 subtitle blocks
    blocks = [b for b in content.strip().split("\n\n") if b.strip()]
    assert len(blocks) == 2


def test_srt_skips_words_with_none_timestamps():
    content = generate_srt(
        [(1.0, 4.0)],
        words=[
            {"word": "Hello", "start": None, "end": None},
            {"word": "world", "start": 1.7,  "end": 2.0},
        ],
    )
    # "Hello" is skipped (no timestamp); "world" forms the block
    assert "world" in content
    assert "Hello" not in content


def test_srt_segment_with_no_words_skipped():
    # Second segment [6–9s] has no matching words
    content = generate_srt(
        [(1.0, 4.0), (6.0, 9.0)],
        words=[{"word": "Hi", "start": 1.2, "end": 1.5}],
    )
    blocks = [b for b in content.strip().split("\n\n") if b.strip()]
    assert len(blocks) == 1


def test_srt_block_numbering_sequential():
    content = generate_srt(
        [(1.0, 4.0), (6.0, 9.0)],
        words=[
            {"word": "a", "start": 1.2, "end": 1.5},
            {"word": "b", "start": 6.2, "end": 6.5},
        ],
    )
    # Block numbers should be 1 and 2 (no gaps).
    # Block 1 is at the very start (no leading newline), block 2 is separated by a blank line.
    assert content.startswith("1\n")
    assert "\n2\n" in content


# ---------------------------------------------------------------------------
# generate_fcpxml
# ---------------------------------------------------------------------------

def test_fcpxml_is_valid_xml():
    content = generate_fcpxml([(1.0, 4.0)], file_path="/tmp/video.mp4", fps=30.0, duration=10.0)
    root = ET.fromstring(content)  # raises if invalid XML
    assert root.tag == "fcpxml"


def test_fcpxml_version():
    content = generate_fcpxml([], file_path="/tmp/video.mp4", fps=30.0, duration=10.0)
    root = ET.fromstring(content)
    assert root.attrib["version"] == "1.11"


def test_fcpxml_clip_count():
    # Two speech segments → two clips in the spine
    content = generate_fcpxml(
        [(1.0, 4.0), (6.0, 9.0)],
        file_path="/tmp/video.mp4",
        fps=30.0,
        duration=15.0,
    )
    root = ET.fromstring(content)
    spine = root.find(".//spine")
    assert spine is not None
    clips = spine.findall("clip")
    assert len(clips) == 2


def test_fcpxml_empty_segments():
    content = generate_fcpxml([], file_path="/tmp/video.mp4", fps=30.0, duration=10.0)
    root = ET.fromstring(content)
    spine = root.find(".//spine")
    assert spine is not None
    assert len(spine.findall("clip")) == 0


def test_fcpxml_asset_src_uri():
    content = generate_fcpxml([], file_path="/path/to/my video.mp4", fps=30.0, duration=5.0)
    root = ET.fromstring(content)
    asset = root.find(".//asset")
    assert asset is not None
    # src must be a valid file URI
    assert asset.attrib["src"].startswith("file:///")


# ---------------------------------------------------------------------------
# generate_premiere_xml
# ---------------------------------------------------------------------------

def test_premiere_xml_is_valid_xml():
    content = generate_premiere_xml(
        [(1.0, 4.0)], file_path="/tmp/video.mp4", fps=30.0, duration=10.0
    )
    root = ET.fromstring(content)
    assert root.tag == "xmeml"


def test_premiere_xml_version():
    content = generate_premiere_xml(
        [], file_path="/tmp/video.mp4", fps=30.0, duration=10.0
    )
    root = ET.fromstring(content)
    assert root.attrib["version"] == "4"


def test_premiere_xml_clip_count():
    content = generate_premiere_xml(
        [(1.0, 4.0), (6.0, 9.0)],
        file_path="/tmp/video.mp4",
        fps=30.0,
        duration=15.0,
    )
    root = ET.fromstring(content)
    clipitems = root.findall(".//clipitem")
    assert len(clipitems) == 2


def test_premiere_xml_empty_segments():
    content = generate_premiere_xml(
        [], file_path="/tmp/video.mp4", fps=30.0, duration=10.0
    )
    root = ET.fromstring(content)
    assert len(root.findall(".//clipitem")) == 0


def test_premiere_xml_file_uri():
    # Need at least one segment so the file element (with pathurl) is created
    content = generate_premiere_xml(
        [(1.0, 4.0)], file_path="/path/to/my video.mp4", fps=30.0, duration=5.0
    )
    root = ET.fromstring(content)
    pathurl = root.find(".//pathurl")
    assert pathurl is not None
    assert pathurl.text.startswith("file:///")


# ---------------------------------------------------------------------------
# Router-level tests (mock service + ffprobe)
# ---------------------------------------------------------------------------

# Minimal valid segments list for router tests
SEGMENTS_SPEECH_ONLY = [{"start": 1.0, "end": 4.0, "type": "speech"}]


@patch("app.routers.export.get_video_info", return_value=(30.0, 10.0))
def test_export_edl_streams_file(mock_info, tmp_path):
    video = tmp_path / "clip.mp4"
    video.touch()  # file must exist to pass validation
    resp = client.post("/export/edl", json={
        "file_path": str(video),
        "segments": SEGMENTS_SPEECH_ONLY,
        "words": [],
    })
    assert resp.status_code == 200
    assert "attachment" in resp.headers.get("content-disposition", "")
    assert "TITLE:" in resp.text


@patch("app.routers.export.get_video_info", return_value=(30.0, 10.0))
def test_export_edl_writes_to_path(mock_info, tmp_path):
    video = tmp_path / "clip.mp4"
    video.touch()
    out = tmp_path / "output.edl"
    resp = client.post("/export/edl", json={
        "file_path": str(video),
        "segments": SEGMENTS_SPEECH_ONLY,
        "words": [],
        "output_path": str(out),
    })
    assert resp.status_code == 200
    assert resp.json()["written_to"] == str(out)
    assert out.exists()
    assert "TITLE:" in out.read_text()


@patch("app.routers.export.get_video_info", return_value=(30.0, 10.0))
def test_export_fcpxml_streams_file(mock_info, tmp_path):
    video = tmp_path / "clip.mp4"
    video.touch()
    resp = client.post("/export/fcpxml", json={
        "file_path": str(video),
        "segments": SEGMENTS_SPEECH_ONLY,
        "words": [],
    })
    assert resp.status_code == 200
    assert "fcpxml" in resp.text


@patch("app.routers.export.get_video_info", return_value=(30.0, 10.0))
def test_export_premiere_streams_file(mock_info, tmp_path):
    video = tmp_path / "clip.mp4"
    video.touch()
    resp = client.post("/export/premiere", json={
        "file_path": str(video),
        "segments": SEGMENTS_SPEECH_ONLY,
        "words": [],
    })
    assert resp.status_code == 200
    assert "xmeml" in resp.text


def test_export_srt_streams_file(tmp_path):
    video = tmp_path / "clip.mp4"
    video.touch()
    resp = client.post("/export/srt", json={
        "file_path": str(video),
        "segments": [{"start": 1.0, "end": 4.0, "type": "speech"}],
        "words": [{"word": "Hello", "start": 1.3, "end": 1.6}],
    })
    assert resp.status_code == 200
    assert "Hello" in resp.text


def test_export_srt_422_unsupported_extension(tmp_path):
    video = tmp_path / "clip.avi"
    video.touch()
    resp = client.post("/export/srt", json={
        "file_path": str(video),
        "segments": [],
        "words": [],
    })
    assert resp.status_code == 422
