"""
app/routers/export.py — Export endpoints for NLE-compatible file formats.

Responsibilities:
  - POST /export/edl:      CMX 3600 EDL for DaVinci Resolve
  - POST /export/fcpxml:   FCPXML for Final Cut Pro
  - POST /export/premiere: xmeml XML for Premiere Pro
  - POST /export/srt:      SubRip subtitle file

All endpoints share the same request body. If output_path is omitted the
generated file is streamed as a download attachment; otherwise it is written
to disk and {"written_to": path} is returned.

Constraints:
  - ffmpeg_path is read from app.state (set at startup).
  - File validation (exists + extension) happens in the router before any
    service call so that errors are uniform across all four endpoints.
"""

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response
from pydantic import BaseModel

from app.services.export import (
    generate_edl,
    generate_fcpxml,
    generate_premiere_xml,
    generate_srt,
    get_video_info,
)

router = APIRouter()

SUPPORTED_EXTENSIONS = {".mp4", ".mov"}


class SegmentItem(BaseModel):
    """A single speech/silence segment from the silence detection pipeline.

    Attributes:
        start: Segment start time in seconds.
        end:   Segment end time in seconds.
        type:  "speech" or "silence".
    """

    start: float
    end: float
    type: str


class WordItem(BaseModel):
    """A single word from the WhisperX transcription pipeline.

    Attributes:
        word:  The transcribed word string.
        start: Word start time in seconds, or None if alignment failed.
        end:   Word end time in seconds, or None if alignment failed.
    """

    word: str
    start: float | None = None
    end: float | None = None


class ExportRequest(BaseModel):
    """Shared request body for all export endpoints.

    Attributes:
        file_path:   Absolute path to the source video file (.mp4 or .mov).
        segments:    Full segment list (speech + silence) from /analyze/silence.
        words:       Word list from /transcribe (empty list if not transcribed).
        output_path: If provided, write the file here and return a path response.
                     If omitted, stream the file as a download attachment.
    """

    file_path: str
    segments: list[SegmentItem]
    words: list[WordItem] = []
    output_path: str | None = None


def _validate_file(file_path: str) -> Path:
    """Validate that file_path exists and has a supported extension.

    Args:
        file_path: Absolute path string to validate.

    Returns:
        Resolved Path object.

    Raises:
        HTTPException 404: File does not exist.
        HTTPException 422: Extension is not .mp4 or .mov.
    """
    path = Path(file_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"File not found: {file_path}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=422, detail=f"Unsupported file type: {path.suffix}"
        )
    return path


def _respond(content: str, filename: str, media_type: str, output_path: str | None):
    """Return content as a download or write it to disk.

    Args:
        content:     Generated file content as a UTF-8 string.
        filename:    Default download filename (e.g. "video.edl").
        media_type:  MIME type for the response.
        output_path: If set, write to this path and return a JSON response.

    Returns:
        FastAPI Response (file download) or dict (written-to-disk confirmation).
    """
    if output_path:
        Path(output_path).write_text(content, encoding="utf-8")
        return {"written_to": output_path}
    return Response(
        content=content.encode("utf-8"),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/export/edl")
async def export_edl(req: ExportRequest, request: Request):
    """Export a CMX 3600 EDL from speech segments.

    Each speech segment becomes one EDL event. Timecodes are derived from
    the video's native frame rate (queried via ffprobe).

    Args:
        req:     Export request with file path, segments, and optional output path.
        request: FastAPI request providing app.state.ffmpeg_path.

    Returns:
        EDL file download, or {"written_to": path} if output_path is set.

    Raises:
        HTTPException 404: file_path does not exist.
        HTTPException 422: Unsupported file extension.
        HTTPException 500: ffprobe failure.
    """
    path = _validate_file(req.file_path)
    ffmpeg_path: str = request.app.state.ffmpeg_path

    try:
        fps, _duration = get_video_info(str(path), ffmpeg_path)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    speech = [s for s in req.segments if s.type == "speech"]
    content = generate_edl(
        [(s.start, s.end) for s in speech],
        fps=fps,
        title=path.stem,
    )
    return _respond(content, f"{path.stem}.edl", "text/plain", req.output_path)


@router.post("/export/fcpxml")
async def export_fcpxml(req: ExportRequest, request: Request):
    """Export an FCPXML sequence for Final Cut Pro.

    Produces FCPXML 1.11 with one clip per speech segment in the spine.
    The asset references the original source file by absolute path.

    Args:
        req:     Export request with file path, segments, and optional output path.
        request: FastAPI request providing app.state.ffmpeg_path.

    Returns:
        FCPXML file download, or {"written_to": path} if output_path is set.

    Raises:
        HTTPException 404: file_path does not exist.
        HTTPException 422: Unsupported file extension.
        HTTPException 500: ffprobe failure.
    """
    path = _validate_file(req.file_path)
    ffmpeg_path: str = request.app.state.ffmpeg_path

    try:
        fps, duration = get_video_info(str(path), ffmpeg_path)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    speech = [s for s in req.segments if s.type == "speech"]
    content = generate_fcpxml(
        [(s.start, s.end) for s in speech],
        file_path=str(path),
        fps=fps,
        duration=duration,
    )
    return _respond(
        content, f"{path.stem}.fcpxml", "application/xml", req.output_path
    )


@router.post("/export/premiere")
async def export_premiere(req: ExportRequest, request: Request):
    """Export an xmeml XML sequence for Adobe Premiere Pro.

    Produces xmeml v4 format with one clipitem per speech segment.
    The file reference uses an absolute file:// URI.

    Args:
        req:     Export request with file path, segments, and optional output path.
        request: FastAPI request providing app.state.ffmpeg_path.

    Returns:
        XML file download, or {"written_to": path} if output_path is set.

    Raises:
        HTTPException 404: file_path does not exist.
        HTTPException 422: Unsupported file extension.
        HTTPException 500: ffprobe failure.
    """
    path = _validate_file(req.file_path)
    ffmpeg_path: str = request.app.state.ffmpeg_path

    try:
        fps, duration = get_video_info(str(path), ffmpeg_path)
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    speech = [s for s in req.segments if s.type == "speech"]
    content = generate_premiere_xml(
        [(s.start, s.end) for s in speech],
        file_path=str(path),
        fps=fps,
        duration=duration,
    )
    return _respond(
        content, f"{path.stem}.xml", "application/xml", req.output_path
    )


@router.post("/export/srt")
async def export_srt(req: ExportRequest, request: Request):
    """Export a SubRip (.srt) subtitle file from transcribed words.

    Groups words by speech segment: one subtitle block per segment that
    contains at least one word with a valid timestamp. Segments with no
    matching words are skipped.

    Args:
        req:     Export request with file path, segments, words, and optional output path.
        request: FastAPI request (not used for SRT but kept for API consistency).

    Returns:
        SRT file download, or {"written_to": path} if output_path is set.

    Raises:
        HTTPException 404: file_path does not exist.
        HTTPException 422: Unsupported file extension.
    """
    path = _validate_file(req.file_path)

    speech = [s for s in req.segments if s.type == "speech"]
    words_dicts = [
        {"word": w.word, "start": w.start, "end": w.end}
        for w in req.words
    ]
    content = generate_srt(
        [(s.start, s.end) for s in speech],
        words=words_dicts,
    )
    return _respond(content, f"{path.stem}.srt", "text/plain", req.output_path)
