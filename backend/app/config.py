"""
app/config.py — Application configuration and ffmpeg path resolution.

Responsibilities:
  - Expose typed settings via pydantic-settings (env prefix: TALKEET_).
  - Resolve the ffmpeg binary path at startup using a deterministic priority
    order: FFMPEG_PATH env var → bundled binary inside the .app bundle.

Constraints:
  - Never call shutil.which("ffmpeg"); ffmpeg must be explicitly located so
    the bundled binary is used in production and a dev override is possible.
  - resolve_ffmpeg() is called once during the FastAPI lifespan (not at import
    time) so a missing binary fails fast with a clear log message.
"""

import os
import sys
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Runtime settings populated from environment variables (prefix: TALKEET_).

    Attributes:
        port: TCP port the uvicorn server listens on (default 8742).
        ffmpeg_path: Override path to the ffmpeg binary. Resolved at startup
            via resolve_ffmpeg() if left empty.
        model_cache_dir: Directory for WhisperX model downloads. Defaults to
            ~/Library/Application Support/Talkeet/models/ (set in Milestone 2).
    """

    port: int = 8742
    ffmpeg_path: str = ""
    model_cache_dir: str = ""

    model_config = {"env_prefix": "TALKEET_"}


def resolve_ffmpeg() -> str:
    """Locate the ffmpeg binary using the documented resolution order.

    Resolution order:
      1. FFMPEG_PATH environment variable — used for local development.
      2. ../Resources/ffmpeg relative to sys.executable — the .app bundle layout.

    Returns:
        Absolute path string to the ffmpeg binary.

    Raises:
        RuntimeError: If ffmpeg cannot be found at either location.
    """
    # 1. Developer override: FFMPEG_PATH=$(which ffmpeg) uv run uvicorn ...
    if path := os.environ.get("FFMPEG_PATH"):
        return path

    # 2. Production: binary is bundled two levels up from the Python executable
    #    inside the macOS .app bundle (Contents/MacOS/python → Contents/Resources/ffmpeg).
    bundled = Path(sys.executable).parent.parent / "Resources" / "ffmpeg"
    if bundled.exists():
        return str(bundled)

    raise RuntimeError(
        "ffmpeg not found. Set the FFMPEG_PATH environment variable "
        "to the ffmpeg binary (e.g. FFMPEG_PATH=$(which ffmpeg))."
    )


settings = Settings()
