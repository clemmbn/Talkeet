"""
app/services/waveform.py — Audio waveform extraction for visual display.

Responsibilities:
  - Pipe raw PCM audio from a video file through ffmpeg (no temp file).
  - Bucket the samples into a fixed number of bins and compute RMS per bin.
  - Normalize the result to [0.0, 1.0] for frontend rendering.

Constraints:
  - ffmpeg is invoked via subprocess; the path must be pre-resolved by the
    caller (never use shutil.which in production).
  - numpy is used for efficient array operations on potentially large PCM
    buffers (10-minute file ≈ 9.6 M samples at 16 kHz mono).
  - Audio is extracted at 16 kHz mono to match the silence-detection pipeline
    and keep throughput predictable.
"""

import subprocess

import numpy as np


# Sample rate used when extracting audio — matches the silence-detection pipeline.
_SAMPLE_RATE = 16_000


def extract_waveform(file_path: str, ffmpeg_path: str, num_samples: int) -> list[float]:
    """Extract a downsampled amplitude envelope from a video file.

    Uses RMS (root mean square) per bucket rather than peak amplitude because
    RMS better matches perceived loudness and produces the smooth, editor-style
    waveform that the frontend expects.

    The pipeline:
      1. ffmpeg decodes the audio track to raw signed 16-bit little-endian PCM
         (mono, 16 kHz) and writes it to stdout — no temp file is created.
      2. The raw bytes are interpreted as a numpy int16 array and reshaped into
         `num_samples` equal-length buckets (the last bucket is padded with zeros
         to make it divisible).
      3. RMS is computed per bucket: sqrt(mean(samples**2)).
      4. The result is normalized to [0.0, 1.0] by dividing by the global max
         RMS. If all samples are silent the function returns all zeros.

    Args:
        file_path: Absolute path to the source video (.mp4 or .mov).
        ffmpeg_path: Absolute path to the ffmpeg binary.
        num_samples: Number of output amplitude values (waveform resolution).

    Returns:
        List of `num_samples` floats in [0.0, 1.0].

    Raises:
        RuntimeError: If ffmpeg exits with a non-zero status (includes the
            last 500 characters of stderr for diagnostics).
    """
    # Pipe raw PCM directly to stdout — avoids writing a temp WAV file and
    # keeps peak memory proportional to the audio duration × sample rate.
    cmd = [
        ffmpeg_path,
        "-i", file_path,
        "-vn",                  # drop video track
        "-ac", "1",             # mono
        "-ar", str(_SAMPLE_RATE),
        "-f", "s16le",          # raw signed 16-bit little-endian
        "-acodec", "pcm_s16le",
        "pipe:1",               # write to stdout
    ]

    result = subprocess.run(cmd, capture_output=True)

    if result.returncode != 0:
        # Include up to 500 chars of stderr for diagnostics without flooding logs.
        stderr_excerpt = result.stderr.decode(errors="replace")[-500:]
        raise RuntimeError(f"ffmpeg error: {stderr_excerpt}")

    raw_bytes = result.stdout

    # Empty audio (e.g. video with no audio track) — return silence.
    if not raw_bytes:
        return [0.0] * num_samples

    # Interpret raw bytes as signed 16-bit integers.
    samples = np.frombuffer(raw_bytes, dtype=np.int16).astype(np.float32)

    # Pad to make len(samples) divisible by num_samples before reshaping.
    # Zero-padding is harmless: it contributes 0 to the RMS of the last bucket.
    remainder = len(samples) % num_samples
    if remainder:
        samples = np.concatenate([samples, np.zeros(num_samples - remainder, dtype=np.float32)])

    # Reshape into (num_samples, bucket_size) and compute RMS per row.
    buckets = samples.reshape(num_samples, -1)
    rms = np.sqrt(np.mean(buckets ** 2, axis=1))

    # Normalize to [0.0, 1.0]. Guard against all-silent audio.
    max_rms = rms.max()
    if max_rms > 0:
        rms = rms / max_rms

    return rms.tolist()
