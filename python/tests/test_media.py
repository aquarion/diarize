import json
import shutil
import subprocess
from unittest.mock import MagicMock, patch

import media
import pytest

_has_ffmpeg = shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None
requires_ffmpeg = pytest.mark.skipif(
    not _has_ffmpeg, reason="ffmpeg/ffprobe not on PATH"
)


def _ffprobe_result(streams: list[dict]) -> MagicMock:
    proc = MagicMock()
    proc.stdout = json.dumps({"streams": streams})
    return proc


# --- has_video_stream ---


def test_has_video_stream_true_for_real_video_track(tmp_path):
    streams = [{"codec_type": "audio"}, {"codec_type": "video", "disposition": {}}]
    with patch("media.subprocess.run", return_value=_ffprobe_result(streams)):
        assert media.has_video_stream(tmp_path / "clip.mp4") is True


def test_has_video_stream_false_for_pure_audio(tmp_path):
    streams = [{"codec_type": "audio"}]
    with patch("media.subprocess.run", return_value=_ffprobe_result(streams)):
        assert media.has_video_stream(tmp_path / "clip.wav") is False


def test_has_video_stream_false_for_audio_with_cover_art(tmp_path):
    streams = [
        {"codec_type": "audio"},
        {"codec_type": "video", "disposition": {"attached_pic": 1}},
    ]
    with patch("media.subprocess.run", return_value=_ffprobe_result(streams)):
        assert media.has_video_stream(tmp_path / "song.m4a") is False


def test_has_video_stream_no_streams_at_all(tmp_path):
    with patch("media.subprocess.run", return_value=_ffprobe_result([])):
        assert media.has_video_stream(tmp_path / "empty.dat") is False


def test_has_video_stream_ffprobe_not_found_raises_media_probe_error(tmp_path):
    with patch("media.subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(media.MediaProbeError, match="ffprobe not found"):
            media.has_video_stream(tmp_path / "clip.mp4")


def test_has_video_stream_ffprobe_failure_raises_media_probe_error(tmp_path):
    err = subprocess.CalledProcessError(returncode=1, cmd="ffprobe", stderr="bad file")
    with patch("media.subprocess.run", side_effect=err):
        with pytest.raises(media.MediaProbeError, match="ffprobe failed"):
            media.has_video_stream(tmp_path / "clip.mp4")


def test_has_video_stream_invalid_json_raises_media_probe_error(tmp_path):
    proc = MagicMock()
    proc.stdout = "not json"
    with patch("media.subprocess.run", return_value=proc):
        with pytest.raises(
            media.MediaProbeError, match="could not parse ffprobe output"
        ):
            media.has_video_stream(tmp_path / "clip.mp4")


# --- extract_audio ---


def test_extract_audio_returns_wav_path_in_out_dir(tmp_path):
    src = tmp_path / "input" / "recording.mp4"
    src.parent.mkdir()
    src.touch()
    out_dir = tmp_path / "out"

    with patch("media.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock()
        dest = media.extract_audio(src, out_dir)

    assert dest == out_dir / "recording.wav"
    assert out_dir.exists()
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "ffmpeg"
    assert str(src) in cmd
    assert str(dest) in cmd


def test_extract_audio_ffmpeg_not_found_raises_media_probe_error(tmp_path):
    src = tmp_path / "recording.mp4"
    with patch("media.subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(media.MediaProbeError, match="ffmpeg not found"):
            media.extract_audio(src, tmp_path / "out")


def test_extract_audio_ffmpeg_failure_raises_media_probe_error(tmp_path):
    src = tmp_path / "recording.mp4"
    err = subprocess.CalledProcessError(
        returncode=1, cmd="ffmpeg", stderr="broken input"
    )
    with patch("media.subprocess.run", side_effect=err):
        with pytest.raises(media.MediaProbeError, match="audio extraction failed"):
            media.extract_audio(src, tmp_path / "out")


# --- integration tests against real ffmpeg/ffprobe (skipped if unavailable) ---


def _run_ffmpeg(*args: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


@pytest.fixture
def video_with_audio(tmp_path):
    path = tmp_path / "fake_video.mp4"
    _run_ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=1:size=32x32:rate=5",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=1",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        str(path),
    )
    return path


@pytest.fixture
def pure_audio(tmp_path):
    path = tmp_path / "pure_audio.wav"
    _run_ffmpeg(
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1", "-ar", "44100", str(path)
    )
    return path


@pytest.fixture
def audio_with_cover_art(tmp_path, pure_audio):
    cover = tmp_path / "cover.png"
    _run_ffmpeg(
        "-f", "lavfi", "-i", "color=c=red:s=32x32", "-frames:v", "1", str(cover)
    )
    path = tmp_path / "audio_with_cover.m4a"
    _run_ffmpeg(
        "-i",
        str(pure_audio),
        "-i",
        str(cover),
        "-map",
        "0:a",
        "-map",
        "1:v",
        "-c:v",
        "mjpeg",
        "-c:a",
        "aac",
        "-disposition:v:0",
        "attached_pic",
        str(path),
    )
    return path


@requires_ffmpeg
def test_real_has_video_stream_true_for_actual_video(video_with_audio):
    assert media.has_video_stream(video_with_audio) is True


@requires_ffmpeg
def test_real_has_video_stream_false_for_actual_pure_audio(pure_audio):
    assert media.has_video_stream(pure_audio) is False


@requires_ffmpeg
def test_real_has_video_stream_false_for_actual_cover_art(audio_with_cover_art):
    assert media.has_video_stream(audio_with_cover_art) is False


@requires_ffmpeg
def test_real_extract_audio_produces_playable_audio_only_file(
    tmp_path, video_with_audio
):
    out_dir = tmp_path / "out"
    dest = media.extract_audio(video_with_audio, out_dir)
    assert dest.exists()
    assert dest.stat().st_size > 0
    assert media.has_video_stream(dest) is False
