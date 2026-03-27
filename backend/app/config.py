import os
import sys
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    port: int = 8742
    ffmpeg_path: str = ""
    model_cache_dir: str = ""

    model_config = {"env_prefix": "TALKEET_"}


def resolve_ffmpeg() -> str:
    if path := os.environ.get("FFMPEG_PATH"):
        return path
    bundled = Path(sys.executable).parent.parent / "Resources" / "ffmpeg"
    if bundled.exists():
        return str(bundled)
    raise RuntimeError(
        "ffmpeg not found. Set the FFMPEG_PATH environment variable "
        "to the ffmpeg binary (e.g. FFMPEG_PATH=$(which ffmpeg))."
    )


settings = Settings()
