# media.py
from __future__ import annotations

import json
import subprocess
from pathlib import Path


class MediaProbeError(Exception):
    """Raised when ffprobe can't inspect the input file."""


def has_video_stream(path: Path) -> bool:
    """Return True if the file has a real video stream (as opposed to a pure
    audio file, or an audio file with an embedded cover-art image), meaning
    its audio track needs to be extracted before transcription."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as err:
        raise MediaProbeError("ffprobe not found on PATH") from err
    except subprocess.CalledProcessError as err:
        raise MediaProbeError(
            f"ffprobe failed on {path}: {err.stderr.strip()}"
        ) from err

    try:
        streams = json.loads(result.stdout).get("streams", [])
    except json.JSONDecodeError as err:
        raise MediaProbeError(f"could not parse ffprobe output for {path}") from err

    for stream in streams:
        if stream.get("codec_type") != "video":
            continue
        if stream.get("disposition", {}).get("attached_pic"):
            continue  # cover art on an audio file, not a real video track
        return True
    return False


def extract_audio(src: Path, out_dir: Path) -> Path:
    """Extract the audio track from a video file into a standalone WAV
    alongside the rest of this run's output."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"{src.stem}.wav"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(src),
        "-vn",
        "-ar",
        "44100",
        "-ac",
        "2",
        str(dest),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except FileNotFoundError as err:
        raise MediaProbeError("ffmpeg not found on PATH") from err
    except subprocess.CalledProcessError as err:
        raise MediaProbeError(
            f"ffmpeg audio extraction failed for {src}: {err.stderr.strip()}"
        ) from err
    return dest
