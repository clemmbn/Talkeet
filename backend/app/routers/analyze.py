"""
app/routers/analyze.py — REST endpoints for audio/video analysis.

Responsibilities:
  - POST /analyze/silence: accepts a video path and silence-detection
    parameters, runs the full pipeline (extract → detect → build), and returns
    a contiguous list of speech/silence segments covering the file duration.
  - POST /analyze/waveform: accepts a video path and a sample count, extracts
    RMS amplitude buckets normalized to [0.0, 1.0] for waveform rendering.

Constraints:
  - ffmpeg path is read from app.state (set during lifespan) rather than
    resolved per-request to avoid redundant lookups.
  - The temporary WAV file for silence detection is always cleaned up in a
    finally block, even if the pipeline raises an exception mid-flight.
"""

import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.services.silence import (
    build_segments,
    detect_silences,
    extract_audio,
    get_audio_duration,
    get_video_fps,
)
from app.services.waveform import extract_waveform

router = APIRouter()


class SilenceRequest(BaseModel):
    """Request body for POST /analyze/silence.

    Attributes:
        file_path: Absolute path to the source video file (.mp4 or .mov).
        threshold_db: Silence detection threshold in dB. Audio below this
            level is considered silent (default -25.0).
        min_silence_duration: Minimum continuous duration in seconds for a
            quiet region to count as silence (default 0.3).
        pre_padding: Seconds of audio to include before each detected speech
            interval (default 0.05).
        post_padding: Seconds of audio to include after each detected speech
            interval (default 0.05).
    """

    file_path: str
    threshold_db: float = -25.0
    min_silence_duration: float = 0.3
    pre_padding: float = 0.05
    post_padding: float = 0.05


@router.post("/analyze/silence")
async def analyze_silence(req: SilenceRequest, request: Request):
    """Detect speech and silence segments in a video file.

    Pipeline:
      1. Validate the file path and extension.
      2. Extract a 16 kHz mono WAV via ffmpeg.
      3. Probe the WAV duration and video frame rate.
      4. Detect silence intervals via ffmpeg silencedetect.
      5. Build contiguous speech/silence segments with asymmetric padding.

    Args:
        req: Silence detection parameters (file path, thresholds, padding).
        request: FastAPI request used to access app.state.ffmpeg_path.

    Returns:
        JSON array of segment objects: [{start, end, type}, ...] covering the
        full file duration without gaps.

    Raises:
        HTTPException 404: file_path does not exist on disk.
        HTTPException 422: file extension is not .mp4 or .mov.
        HTTPException 500: ffmpeg/ffprobe subprocess failure.
    """
    path = Path(req.file_path)

    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {req.file_path}")

    if path.suffix.lower() not in (".mp4", ".mov"):
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file type: {path.suffix}",
        )

    ffmpeg_path: str = request.app.state.ffmpeg_path
    wav_path: str | None = None

    try:
        wav_path = extract_audio(str(path), ffmpeg_path)
        duration = get_audio_duration(wav_path, ffmpeg_path)
        # FPS is queried from the original video (not the WAV) for frame-accurate
        # segment dropping (segments shorter than 5 frames are discarded).
        fps = get_video_fps(str(path), ffmpeg_path)
        silences = detect_silences(wav_path, req.threshold_db, req.min_silence_duration, ffmpeg_path)
        segments = build_segments(silences, duration, req.pre_padding, req.post_padding, fps)
    except RuntimeError as exc:
        msg = str(exc)
        raise HTTPException(status_code=500, detail=msg)
    finally:
        # Always remove the temp WAV; it can be several hundred MB for long videos.
        if wav_path and os.path.exists(wav_path):
            os.remove(wav_path)

    return segments


class WaveformRequest(BaseModel):
    """Request body for POST /analyze/waveform.

    Attributes:
        file_path: Absolute path to the source video file (.mp4 or .mov).
        num_samples: Number of amplitude buckets to return (waveform resolution).
            Higher values give more detail; lower values are faster to render.
            Default 1000 is suitable for a full-width timeline view.
    """

    file_path: str
    num_samples: int = 1000


@router.post("/analyze/waveform")
async def analyze_waveform(req: WaveformRequest, request: Request):
    """Extract a normalized amplitude envelope from a video file.

    Pipes raw PCM through ffmpeg, computes RMS per bucket, and returns a
    list of floats in [0.0, 1.0] suitable for drawing a waveform in the UI.

    Args:
        req: Waveform request parameters (file path, desired sample count).
        request: FastAPI request used to access app.state.ffmpeg_path.

    Returns:
        JSON array of `num_samples` floats, each in [0.0, 1.0].

    Raises:
        HTTPException 404: file_path does not exist on disk.
        HTTPException 422: file extension is not .mp4 or .mov.
        HTTPException 500: ffmpeg subprocess failure.
    """
    path = Path(req.file_path)

    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {req.file_path}")

    if path.suffix.lower() not in (".mp4", ".mov"):
        raise HTTPException(
            status_code=422,
            detail=f"Unsupported file type: {path.suffix}",
        )

    ffmpeg_path: str = request.app.state.ffmpeg_path

    try:
        samples = extract_waveform(str(path), ffmpeg_path, req.num_samples)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    return samples
