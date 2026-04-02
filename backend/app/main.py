"""
app/main.py — FastAPI application entry point.

Responsibilities:
  - Define the FastAPI app instance with a lifespan context that resolves and
    stores the ffmpeg path on startup.
  - Mount the analyze router (POST /analyze/silence).
  - Mount the transcribe router (POST /transcribe, WS /ws/progress/{job_id}).
  - Expose GET /health for the SwiftUI layer to poll readiness on startup.

Constraints:
  - ffmpeg resolution happens in the lifespan, not at import time, so a
    missing binary causes an immediate, logged startup failure rather than a
    silent error on the first request.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import resolve_ffmpeg
from app.routers import analyze, transcribe

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """FastAPI lifespan: run startup logic before yield, teardown after.

    Startup:
      - Resolves the ffmpeg binary path and stores it on app.state so every
        request handler can access it without re-resolving.
      - Re-raises RuntimeError so uvicorn exits immediately with a clear log
        message if ffmpeg is missing.

    Args:
        app: The FastAPI application instance.
    """
    try:
        ffmpeg_path = resolve_ffmpeg()
        # Store on app.state so request handlers retrieve it via request.app.state.ffmpeg_path
        app.state.ffmpeg_path = ffmpeg_path
        logger.info("ffmpeg resolved at: %s", ffmpeg_path)
    except RuntimeError as exc:
        logger.error("Startup failed: %s", exc)
        raise

    yield  # Application runs here; add teardown logic below if needed


app = FastAPI(title="Talkeet Backend", lifespan=lifespan)
app.include_router(analyze.router)
app.include_router(transcribe.router)


@app.get("/health")
async def health():
    """Health check endpoint polled by the SwiftUI layer on app startup.

    Returns:
        JSON object {"status": "ok"} once the server is ready.
    """
    return {"status": "ok"}
