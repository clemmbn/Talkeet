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

router = APIRouter()


class SilenceRequest(BaseModel):
    file_path: str
    threshold_db: float = -25.0
    min_silence_duration: float = 0.3
    pre_padding: float = 0.05
    post_padding: float = 0.05


@router.post("/analyze/silence")
async def analyze_silence(req: SilenceRequest, request: Request):
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
        fps = get_video_fps(str(path), ffmpeg_path)
        silences = detect_silences(wav_path, req.threshold_db, req.min_silence_duration, ffmpeg_path)
        segments = build_segments(silences, duration, req.pre_padding, req.post_padding, fps)
    except RuntimeError as exc:
        msg = str(exc)
        raise HTTPException(status_code=500, detail=msg)
    finally:
        if wav_path and os.path.exists(wav_path):
            os.remove(wav_path)

    return segments
