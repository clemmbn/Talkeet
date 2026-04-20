"""
app/services/export.py — Pure export format generators for Talkeet.

Responsibilities:
  - get_video_info: query fps and duration from a video file via ffprobe.
  - seconds_to_timecode_edl: convert seconds to HH:MM:SS:FF for CMX 3600 EDL.
  - seconds_to_timecode_srt: convert seconds to HH:MM:SS,mmm for SRT.
  - generate_edl: produce a CMX 3600 EDL string from speech intervals.
  - generate_srt: produce a SubRip subtitle string from words + speech segments.
  - generate_fcpxml: produce FCPXML 1.11 for Final Cut Pro.
  - generate_premiere_xml: produce xmeml v4 XML for Adobe Premiere Pro.

All functions are pure (no I/O except get_video_info which shells to ffprobe).
The router layer handles HTTP concerns; these functions only produce strings.
"""

import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import quote


# ---------------------------------------------------------------------------
# Video metadata
# ---------------------------------------------------------------------------

def get_video_info(file_path: str, ffmpeg_path: str) -> tuple[float, float]:
    """Query fps and duration from a video file using ffprobe.

    Uses a single ffprobe call to extract both values efficiently.

    Args:
        file_path:   Absolute path to the video file.
        ffmpeg_path: Path to the ffmpeg binary (ffprobe lives in the same dir).

    Returns:
        Tuple of (fps, duration_in_seconds).

    Raises:
        RuntimeError: ffprobe subprocess fails or output cannot be parsed.
    """
    # ffprobe lives alongside ffmpeg — replace the binary name in the path
    ffprobe_path = str(Path(ffmpeg_path).parent / "ffprobe")

    result = subprocess.run(
        [
            ffprobe_path,
            "-v", "quiet",
            "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate:format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            file_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    if result.returncode != 0:
        raise RuntimeError(f"ffprobe error: {result.stderr[:300]}")

    lines = [l.strip() for l in result.stdout.splitlines() if l.strip()]
    if len(lines) < 2:
        raise RuntimeError(f"ffprobe returned unexpected output: {result.stdout!r}")

    # First line: r_frame_rate as a fraction like "30/1" or "30000/1001"
    fps_raw = lines[0]
    if "/" in fps_raw:
        num, den = fps_raw.split("/")
        fps = float(num) / float(den)
    else:
        fps = float(fps_raw)

    # Second line: duration in seconds (from format section)
    duration = float(lines[1])
    return fps, duration


# ---------------------------------------------------------------------------
# Timecode utilities
# ---------------------------------------------------------------------------

def seconds_to_timecode_edl(seconds: float, fps: float) -> str:
    """Convert seconds to a CMX 3600 timecode string HH:MM:SS:FF.

    Uses frame-accurate rounding: total frames are computed first, then
    decomposed into HH/MM/SS/FF. This avoids floating-point drift when
    adding fractional frame times.

    Args:
        seconds: Time in seconds (non-negative).
        fps:     Frame rate (frames per second).

    Returns:
        Timecode string in "HH:MM:SS:FF" format.
    """
    total_frames = round(seconds * fps)
    fps_int = round(fps)  # EDL timecodes use integer frame counts
    ff = total_frames % fps_int
    total_seconds = total_frames // fps_int
    ss = total_seconds % 60
    mm = (total_seconds // 60) % 60
    hh = total_seconds // 3600
    return f"{hh:02d}:{mm:02d}:{ss:02d}:{ff:02d}"


def seconds_to_timecode_srt(seconds: float) -> str:
    """Convert seconds to SRT timecode format HH:MM:SS,mmm.

    Milliseconds are truncated (not rounded) to avoid end-time creeping
    into the next subtitle block.

    Args:
        seconds: Time in seconds (non-negative).

    Returns:
        Timecode string in "HH:MM:SS,mmm" format.
    """
    total_ms = int(seconds * 1000)  # truncate, not round
    ms = total_ms % 1000
    total_secs = total_ms // 1000
    ss = total_secs % 60
    mm = (total_secs // 60) % 60
    hh = total_secs // 3600
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


# ---------------------------------------------------------------------------
# EDL (CMX 3600)
# ---------------------------------------------------------------------------

def generate_edl(
    speech_segments: list[tuple[float, float]],
    fps: float,
    title: str,
) -> str:
    """Generate a CMX 3600 EDL string from speech intervals.

    Each speech segment becomes one EDL event. Record timecodes are
    cumulative: the record out-point of event N is the record in-point
    of event N+1. This produces a gapless edit of only the kept segments.

    Args:
        speech_segments: List of (start, end) tuples in seconds. Must only
                         contain speech intervals (silence already filtered).
        fps:             Video frame rate (used for timecode calculation).
        title:           Project title written into the EDL header.

    Returns:
        CMX 3600 EDL as a newline-terminated string.
    """
    lines = [f"TITLE: {title}", "FCM: NON-DROP FRAME", ""]
    record_cursor = 0.0

    for event_num, (src_start, src_end) in enumerate(speech_segments, start=1):
        duration = src_end - src_start
        rec_in = record_cursor
        rec_out = record_cursor + duration
        record_cursor = rec_out

        src_in_tc  = seconds_to_timecode_edl(src_start, fps)
        src_out_tc = seconds_to_timecode_edl(src_end,   fps)
        rec_in_tc  = seconds_to_timecode_edl(rec_in,    fps)
        rec_out_tc = seconds_to_timecode_edl(rec_out,   fps)

        lines.append(
            f"{event_num:03d}  AX       V     C        "
            f"{src_in_tc} {src_out_tc} {rec_in_tc} {rec_out_tc}"
        )

    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# SRT
# ---------------------------------------------------------------------------

def generate_srt(
    speech_segments: list[tuple[float, float]],
    words: list[dict],
) -> str:
    """Generate a SubRip (.srt) subtitle string from words grouped by segment.

    Strategy: for each speech segment, collect all words whose `start` falls
    within [segment_start, segment_end). Words with None timestamps are
    skipped. Segments with no matched words are skipped entirely. The
    subtitle block's time span stretches from the first to the last matched
    word in the segment.

    Args:
        speech_segments: List of (start, end) tuples in seconds (speech only).
        words:           List of word dicts with keys: word, start, end.
                         `start` and `end` may be None if alignment failed.

    Returns:
        SRT file content as a string (empty string if no subtitles produced).
    """
    # Pre-filter: keep only words with valid timestamps
    timed_words = [w for w in words if w.get("start") is not None and w.get("end") is not None]

    blocks: list[str] = []
    block_num = 1

    for seg_start, seg_end in speech_segments:
        # Collect words whose start falls within this segment
        seg_words = [w for w in timed_words if seg_start <= w["start"] < seg_end]
        if not seg_words:
            continue

        start_tc = seconds_to_timecode_srt(seg_words[0]["start"])
        end_tc   = seconds_to_timecode_srt(seg_words[-1]["end"])
        text     = " ".join(w["word"] for w in seg_words)

        blocks.append(f"{block_num}\n{start_tc} --> {end_tc}\n{text}")
        block_num += 1

    return "\n\n".join(blocks) + ("\n" if blocks else "")


# ---------------------------------------------------------------------------
# FCPXML (Final Cut Pro)
# ---------------------------------------------------------------------------

def _rational_time(seconds: float, fps: float) -> str:
    """Express seconds as a rational FCPXML time value "{frames}/{fps_int}s".

    FCPXML requires time values as rational fractions. Using frame-count
    over integer fps avoids floating-point imprecision in the XML.

    Args:
        seconds: Time in seconds.
        fps:     Frame rate (will be rounded to nearest integer).

    Returns:
        Rational time string, e.g. "90/30s" for 3.0s at 30fps.
        Returns "0s" for zero seconds to satisfy FCPXML parsers.
    """
    fps_int = round(fps)
    frames = round(seconds * fps_int)
    if frames == 0:
        return "0s"
    return f"{frames}/{fps_int}s"


def generate_fcpxml(
    speech_segments: list[tuple[float, float]],
    file_path: str,
    fps: float,
    duration: float,
) -> str:
    """Generate FCPXML 1.11 for Final Cut Pro.

    Structure:
      <fcpxml>
        <resources>
          <format/>   — frame rate + name
          <asset/>    — source file reference
        </resources>
        <library>
          <event>
            <project>
              <sequence>
                <spine>
                  <clip/> × N  — one per speech segment
                </spine>
              </sequence>
            </project>
          </event>
        </library>
      </fcpxml>

    Each clip carries:
      - offset: cumulative position in the output timeline
      - start:  source in-point
      - duration: clip length

    Args:
        speech_segments: List of (start, end) tuples in seconds (speech only).
        file_path:       Absolute path to source video (used for asset src URI).
        fps:             Video frame rate.
        duration:        Total video duration in seconds (for the asset element).

    Returns:
        FCPXML 1.11 document as an indented XML string.
    """
    fps_int = round(fps)
    stem = Path(file_path).stem
    # File URIs must percent-encode spaces but not slashes
    file_uri = "file://" + quote(file_path, safe="/:")

    root = ET.Element("fcpxml", version="1.11")

    # --- resources ---
    resources = ET.SubElement(root, "resources")
    ET.SubElement(
        resources, "format",
        id="r1",
        name=f"FFVideoFormat{fps_int}",
        frameDuration=f"1/{fps_int}s",
    )
    asset = ET.SubElement(
        resources, "asset",
        id="r2",
        name=stem,
        src=file_uri,
        start="0s",
        duration=_rational_time(duration, fps),
        hasVideo="1",
        hasAudio="1",
    )
    ET.SubElement(asset, "media-rep", kind="original-media", src=file_uri)

    # --- library / event / project / sequence ---
    library = ET.SubElement(root, "library")
    event = ET.SubElement(library, "event", name="Talkeet Export")
    project = ET.SubElement(event, "project", name=stem)

    # Total output duration = sum of kept segment durations
    output_duration = sum(end - start for start, end in speech_segments)
    sequence = ET.SubElement(
        project, "sequence",
        duration=_rational_time(output_duration, fps),
        format="r1",
        tcStart="0s",
        tcFormat="NDF",
    )
    spine = ET.SubElement(sequence, "spine")

    # --- clips ---
    record_cursor = 0.0
    for src_start, src_end in speech_segments:
        seg_duration = src_end - src_start
        ET.SubElement(
            spine, "clip",
            name=stem,
            ref="r2",
            offset=_rational_time(record_cursor, fps),
            start=_rational_time(src_start, fps),
            duration=_rational_time(seg_duration, fps),
        )
        record_cursor += seg_duration

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")


# ---------------------------------------------------------------------------
# Premiere Pro XML (xmeml v4)
# ---------------------------------------------------------------------------

def generate_premiere_xml(
    speech_segments: list[tuple[float, float]],
    file_path: str,
    fps: float,
    duration: float,
) -> str:
    """Generate an xmeml v4 XML sequence for Adobe Premiere Pro.

    xmeml (Extensible Media Exchange Markup Language) v4 is the format
    Premiere Pro imports via File > Import. Times are expressed in frames
    (integer), not seconds. One <clipitem> is created per speech segment.

    The file element is shared (same id="file1") across all clipitems so
    Premiere links them all to the same source clip.

    Args:
        speech_segments: List of (start, end) tuples in seconds (speech only).
        file_path:       Absolute path to source video (used for pathurl).
        fps:             Video frame rate (rounded to nearest integer for timebase).
        duration:        Total video duration in seconds.

    Returns:
        xmeml v4 XML document as an indented string.
    """
    fps_int = round(fps)
    stem = Path(file_path).stem
    file_uri = "file://" + quote(file_path, safe="/:")
    total_frames = round(duration * fps_int)

    def _frames(seconds: float) -> str:
        return str(round(seconds * fps_int))

    def _rate_elem(parent: ET.Element) -> None:
        """Append a <rate> child with <timebase> and <ntsc>FALSE</ntsc>."""
        rate = ET.SubElement(parent, "rate")
        ET.SubElement(rate, "timebase").text = str(fps_int)
        ET.SubElement(rate, "ntsc").text = "FALSE"

    root = ET.Element("xmeml", version="4")
    sequence = ET.SubElement(root, "sequence")
    ET.SubElement(sequence, "name").text = stem
    _rate_elem(sequence)
    ET.SubElement(sequence, "duration").text = str(total_frames)

    media = ET.SubElement(sequence, "media")
    video = ET.SubElement(media, "video")
    track = ET.SubElement(video, "track")

    # The file element is declared inline in the first clipitem and referenced
    # by id in subsequent ones to avoid duplication (Premiere convention).
    record_cursor_frames = 0
    for idx, (src_start, src_end) in enumerate(speech_segments):
        clip_frames = round((src_end - src_start) * fps_int)
        clipitem = ET.SubElement(track, "clipitem", id=f"clipitem-{idx + 1}")
        ET.SubElement(clipitem, "name").text = stem
        ET.SubElement(clipitem, "duration").text = str(clip_frames)
        _rate_elem(clipitem)
        ET.SubElement(clipitem, "start").text = str(record_cursor_frames)
        ET.SubElement(clipitem, "end").text = str(record_cursor_frames + clip_frames)
        ET.SubElement(clipitem, "in").text = _frames(src_start)
        ET.SubElement(clipitem, "out").text = _frames(src_end)

        if idx == 0:
            # First clipitem: declare the full file element
            file_elem = ET.SubElement(clipitem, "file", id="file1")
            ET.SubElement(file_elem, "name").text = Path(file_path).name
            ET.SubElement(file_elem, "pathurl").text = file_uri
            _rate_elem(file_elem)
            ET.SubElement(file_elem, "duration").text = str(total_frames)
        else:
            # Subsequent clipitems: reference by id only
            ET.SubElement(clipitem, "file", id="file1")

        record_cursor_frames += clip_frames

    ET.indent(root, space="  ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")
