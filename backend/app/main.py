import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import resolve_ffmpeg
from app.routers import analyze

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        ffmpeg_path = resolve_ffmpeg()
        app.state.ffmpeg_path = ffmpeg_path
        logger.info("ffmpeg resolved at: %s", ffmpeg_path)
    except RuntimeError as exc:
        logger.error("Startup failed: %s", exc)
        raise

    yield


app = FastAPI(title="Talkeet Backend", lifespan=lifespan)
app.include_router(analyze.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
