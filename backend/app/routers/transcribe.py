"""
app/routers/transcribe.py — Transcription endpoints.

Responsibilities:
  - POST /transcribe: validate the request synchronously, register a job, and
    fire a background coroutine that runs the WhisperX pipeline in a thread pool.
  - WS /ws/progress/{job_id}: stream pipeline progress events to the client
    until a terminal "done" or "error" message is sent.

Architecture (job-based progress streaming):
  1. Client opens WS /ws/progress/{job_id}.
  2. Client calls POST /transcribe with the same job_id → 202 Accepted.
  3. Background coroutine runs the pipeline in run_in_executor; each stage is
     announced by putting {"stage": "..."} on an asyncio.Queue.
  4. WS handler drains the queue and forwards events as JSON text frames.
  5. Terminal messages ("done" or "error") cause the WS to close and the job
     entry to be removed from the store.

Constraints:
  - The in-memory job store (_jobs) is per-process. It is not shared across
    workers. Run with a single uvicorn worker in production (the SwiftUI layer
    launches exactly one backend process).
  - Progress events are put on the queue from a thread (run_in_executor), so
    asyncio.run_coroutine_threadsafe is used instead of queue.put_nowait.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect
from fastapi import status as http_status
from pydantic import BaseModel, Field

from app.services.transcription import (
    MODEL_SIZES,
    filter_words_by_segments,
    transcribe_video,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# In-memory job store
# ---------------------------------------------------------------------------

# Maps job_id → asyncio.Queue that carries progress event dicts.
# Entries are created on POST /transcribe and removed after the terminal
# message ("done" or "error") has been put on the queue.
_jobs: dict[str, asyncio.Queue] = {}


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class SegmentFilter(BaseModel):
    """A time range used to restrict which transcribed words are returned."""

    start: float
    end: float


class TranscribeRequest(BaseModel):
    """Request body for POST /transcribe."""

    file_path: str
    job_id: str
    model_size: str = Field(default="base")
    language: str | None = None
    segments: list[SegmentFilter] | None = None


# ---------------------------------------------------------------------------
# Thread-safe progress bridge
# ---------------------------------------------------------------------------


def make_progress_callback(
    loop: asyncio.AbstractEventLoop,
    queue: asyncio.Queue,
) -> callable:
    """Build a thread-safe callback that puts stage events on an asyncio queue.

    transcribe_video runs in a thread via run_in_executor. Regular queue.put()
    or queue.put_nowait() would race with the event loop, so we use
    asyncio.run_coroutine_threadsafe to schedule the put from any thread.

    Args:
        loop: The running event loop (captured before entering the executor).
        queue: The job's asyncio.Queue where events should land.

    Returns:
        A callable that accepts a stage string and schedules a queue put.
    """
    def callback(stage: str) -> None:
        # run_coroutine_threadsafe is the only safe way to interact with an
        # asyncio queue from a non-async thread.
        asyncio.run_coroutine_threadsafe(queue.put({"stage": stage}), loop)

    return callback


# ---------------------------------------------------------------------------
# Background job coroutine
# ---------------------------------------------------------------------------


async def _run_job(job_id: str, request: TranscribeRequest) -> None:
    """Run the transcription pipeline for a job and publish events to its queue.

    Called as an asyncio background task (asyncio.create_task). Runs the
    blocking WhisperX pipeline in a thread pool to avoid blocking the event
    loop. Puts a terminal "done" or "error" event when the pipeline finishes,
    then removes the job from the store.

    Args:
        job_id: Identifier used to look up the job's queue in _jobs.
        request: The validated POST /transcribe request body.
    """
    queue = _jobs[job_id]
    loop = asyncio.get_event_loop()
    callback = make_progress_callback(loop, queue)

    try:
        # Run the blocking pipeline in the default thread pool executor so the
        # event loop remains free to handle WebSocket messages concurrently.
        words = await loop.run_in_executor(
            None,
            lambda: transcribe_video(
                request.file_path,
                request.model_size,
                request.language,
                callback,
            ),
        )

        # Apply segment-range filter if the caller restricted scope.
        if request.segments:
            seg_dicts = [{"start": s.start, "end": s.end} for s in request.segments]
            words = filter_words_by_segments(words, seg_dicts)

        await queue.put({"stage": "done", "result": words})

    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        await queue.put({"stage": "error", "detail": str(exc)})

    finally:
        # Clean up regardless of outcome so the job_id can be reused.
        _jobs.pop(job_id, None)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/transcribe", status_code=http_status.HTTP_202_ACCEPTED)
async def post_transcribe(request: TranscribeRequest) -> dict:
    """Accept a transcription job and begin processing asynchronously.

    Validates the request synchronously (file existence, extension, model size,
    job_id uniqueness) before accepting. The actual transcription result is
    delivered via the WebSocket progress stream.

    Args:
        request: TranscribeRequest with file_path, job_id, model_size, etc.

    Returns:
        {"job_id": str} with HTTP 202 Accepted.

    Raises:
        HTTPException 404: file_path does not exist.
        HTTPException 422: unsupported file extension or unknown model_size.
        HTTPException 409: job_id is already registered.
    """
    path = Path(request.file_path)

    # --- Validation ---
    if not path.exists():
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"File not found: {request.file_path}",
        )

    if path.suffix.lower() not in {".mp4", ".mov"}:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unsupported file type: {path.suffix}",
        )

    if request.model_size not in MODEL_SIZES:
        raise HTTPException(
            status_code=http_status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown model size: {request.model_size}",
        )

    if request.job_id in _jobs:
        raise HTTPException(
            status_code=http_status.HTTP_409_CONFLICT,
            detail="Job ID already exists",
        )

    # --- Register job and start background task ---
    _jobs[request.job_id] = asyncio.Queue()
    asyncio.create_task(_run_job(request.job_id, request))

    return {"job_id": request.job_id}


@router.websocket("/ws/progress/{job_id}")
async def ws_progress(websocket: WebSocket, job_id: str) -> None:
    """Stream transcription progress events for a job over WebSocket.

    The client should open this connection before or simultaneously with the
    POST /transcribe call. Events are JSON text frames; the connection closes
    after a terminal "done" or "error" event is forwarded.

    Args:
        websocket: The FastAPI WebSocket connection.
        job_id: Must match the job_id sent in POST /transcribe.

    Protocol:
        Each frame is a JSON object with at least a "stage" field:
          {"stage": "loading_audio"}
          {"stage": "downloading_model"}
          {"stage": "transcribing"}
          {"stage": "aligning"}
          {"stage": "done", "result": [...]}   ← terminal
          {"stage": "error", "detail": "..."}  ← terminal
    """
    await websocket.accept()

    # If the job hasn't been registered yet (POST hasn't been called), reject.
    if job_id not in _jobs:
        await websocket.send_text(
            json.dumps({"stage": "error", "detail": "Job not found"})
        )
        await websocket.close()
        return

    queue = _jobs[job_id]

    try:
        while True:
            # Block until the next event is available. The queue is the only
            # communication channel between the executor thread and this handler.
            event = await queue.get()
            await websocket.send_text(json.dumps(event))

            # Terminal events signal the end of the pipeline.
            if event.get("stage") in {"done", "error"}:
                break

    except WebSocketDisconnect:
        # Client disconnected early — the background task will still complete
        # and clean up the job entry via its finally block.
        logger.info("WebSocket for job %s disconnected early", job_id)
