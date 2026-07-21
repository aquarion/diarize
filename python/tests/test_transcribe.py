import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import transcribe

from config import AppConfig


def _cfg(**overrides) -> AppConfig:
    base = dict(
        backend="whisperx",
        assemblyai_api_key="",
        hf_token="tok",
        whisperx_bin="whisperx",
        model="medium",
        language="en",
        mlx_model="mlx-community/whisper-large-v3-turbo",
        cuda_compute_type="float16",
        compute_type="int8",
        batch_size=4,
        output_dir="./out",
        speakers_file="speakers.json",
        transcript_title="Session Transcript",
        vault_path="~/Obsidian",
        vault_subdir="Transcripts",
        vault_filename_template="{audio_stem}.md",
        extra_path=[],
        extra_lib_path=[],
        num_speakers=2,
    )
    base.update(overrides)
    return AppConfig(**base)


# --- _resolve_whisperx_bin ---


def test_resolve_whisperx_bin_absolute_path_passthrough():
    abs_path = str(Path("/some/abs/whisperx").resolve())
    assert transcribe._resolve_whisperx_bin(abs_path) == abs_path


def test_resolve_whisperx_bin_prefers_sibling_of_interpreter(tmp_path, monkeypatch):
    suffix = ".exe" if sys.platform == "win32" else ""
    sibling = tmp_path / f"whisperx{suffix}"
    sibling.touch()
    monkeypatch.setattr(transcribe.sys, "executable", str(tmp_path / "python"))
    assert transcribe._resolve_whisperx_bin("whisperx") == str(sibling)


def test_resolve_whisperx_bin_falls_back_to_bare_name_when_sibling_missing(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(transcribe.sys, "executable", str(tmp_path / "python"))
    assert transcribe._resolve_whisperx_bin("whisperx") == "whisperx"


# --- _build_whisperx_cmd ---


def test_build_whisperx_cmd_includes_expected_flags(tmp_path):
    cfg = _cfg(num_speakers=3, model="large-v3", language="fr", batch_size=8)
    cmd = transcribe._build_whisperx_cmd(
        tmp_path / "audio.wav", cfg, tmp_path / "out", "int8", None
    )
    assert str(tmp_path / "audio.wav") in cmd
    assert "--model" in cmd and cmd[cmd.index("--model") + 1] == "large-v3"
    assert "--language" in cmd and cmd[cmd.index("--language") + 1] == "fr"
    assert "--diarize" in cmd
    assert "--hf_token" in cmd and cmd[cmd.index("--hf_token") + 1] == "tok"
    assert "--min_speakers" in cmd and cmd[cmd.index("--min_speakers") + 1] == "3"
    assert "--max_speakers" in cmd and cmd[cmd.index("--max_speakers") + 1] == "3"
    assert "--compute_type" in cmd and cmd[cmd.index("--compute_type") + 1] == "int8"
    assert "--batch_size" in cmd and cmd[cmd.index("--batch_size") + 1] == "8"
    assert "--device" not in cmd


def test_build_whisperx_cmd_includes_device_when_given(tmp_path):
    cfg = _cfg()
    cmd = transcribe._build_whisperx_cmd(
        tmp_path / "audio.wav", cfg, tmp_path / "out", "float16", "cuda"
    )
    assert "--device" in cmd and cmd[cmd.index("--device") + 1] == "cuda"


# --- _segment_overlap ---


@pytest.mark.parametrize(
    "a_start,a_end,b_start,b_end,expected",
    [
        (0.0, 10.0, 5.0, 15.0, 5.0),  # partial overlap
        (0.0, 10.0, 20.0, 30.0, 0.0),  # no overlap
        (0.0, 10.0, 2.0, 8.0, 6.0),  # full containment
        (0.0, 10.0, 10.0, 20.0, 0.0),  # touching edges, zero overlap
    ],
)
def test_segment_overlap(a_start, a_end, b_start, b_end, expected):
    assert transcribe._segment_overlap(a_start, a_end, b_start, b_end) == expected


# --- _fmt_srt_ts / _fmt_vtt_ts ---


def test_fmt_srt_ts_basic():
    assert transcribe._fmt_srt_ts(3661.5) == "01:01:01,500"


def test_fmt_srt_ts_zero():
    assert transcribe._fmt_srt_ts(0) == "00:00:00,000"


def test_fmt_srt_ts_negative_clamped_to_zero():
    assert transcribe._fmt_srt_ts(-5) == "00:00:00,000"


def test_fmt_vtt_ts_basic():
    assert transcribe._fmt_vtt_ts(3661.5) == "01:01:01.500"


def test_fmt_vtt_ts_negative_clamped_to_zero():
    assert transcribe._fmt_vtt_ts(-5) == "00:00:00.000"


# --- has_cuda_available ---


def test_has_cuda_available_true_via_torch(monkeypatch):
    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = True
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    assert transcribe.has_cuda_available() is True


def test_has_cuda_available_false_via_torch(monkeypatch):
    fake_torch = MagicMock()
    fake_torch.cuda.is_available.return_value = False
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    assert transcribe.has_cuda_available() is False


def test_has_cuda_available_falls_back_to_nvidia_smi_when_torch_missing(monkeypatch):
    # Setting sys.modules["torch"] = None forces `import torch` to raise ImportError.
    monkeypatch.setitem(sys.modules, "torch", None)
    with patch("transcribe.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        assert transcribe.has_cuda_available() is True
        assert mock_run.call_args[0][0] == ["nvidia-smi", "-L"]


def test_has_cuda_available_false_when_nvidia_smi_missing(monkeypatch):
    monkeypatch.setitem(sys.modules, "torch", None)
    with patch("transcribe.subprocess.run", side_effect=OSError()):
        assert transcribe.has_cuda_available() is False


def test_has_cuda_available_false_when_torch_cuda_check_raises_oserror(monkeypatch):
    fake_torch = MagicMock()
    fake_torch.cuda.is_available.side_effect = OSError()
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    with patch("transcribe.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=1)
        assert transcribe.has_cuda_available() is False


# --- resolve_whisperx_runtime ---


def test_resolve_whisperx_runtime_cuda(monkeypatch):
    monkeypatch.setattr(transcribe, "has_cuda_available", lambda: True)
    device, compute_type = transcribe.resolve_whisperx_runtime(
        _cfg(cuda_compute_type="float16")
    )
    assert device == "cuda"
    assert compute_type == "float16"


def test_resolve_whisperx_runtime_cpu(monkeypatch):
    monkeypatch.setattr(transcribe, "has_cuda_available", lambda: False)
    device, compute_type = transcribe.resolve_whisperx_runtime(
        _cfg(compute_type="int8")
    )
    assert device is None
    assert compute_type == "int8"


# --- run_whisperx ---


def test_run_whisperx_success_no_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(
        transcribe, "resolve_whisperx_runtime", lambda cfg: (None, "int8")
    )
    with patch("transcribe.subprocess.run") as mock_run:
        mock_run.return_value = MagicMock(returncode=0)
        transcribe.run_whisperx(tmp_path / "audio.wav", _cfg(), tmp_path / "out")
    assert mock_run.call_count == 1


def test_run_whisperx_retries_on_cpu_after_cuda_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(
        transcribe, "resolve_whisperx_runtime", lambda cfg: ("cuda", "float16")
    )
    fail = subprocess.CalledProcessError(returncode=1, cmd="whisperx")
    with patch("transcribe.subprocess.run") as mock_run:
        mock_run.side_effect = [fail, MagicMock(returncode=0)]
        transcribe.run_whisperx(tmp_path / "audio.wav", _cfg(), tmp_path / "out")
    assert mock_run.call_count == 2
    # second call should be the CPU fallback: no --device flag
    fallback_cmd = mock_run.call_args_list[1][0][0]
    assert "--device" not in fallback_cmd


def test_run_whisperx_non_cuda_failure_propagates(tmp_path, monkeypatch):
    monkeypatch.setattr(
        transcribe, "resolve_whisperx_runtime", lambda cfg: (None, "int8")
    )
    fail = subprocess.CalledProcessError(returncode=1, cmd="whisperx")
    with patch("transcribe.subprocess.run", side_effect=fail):
        with pytest.raises(subprocess.CalledProcessError):
            transcribe.run_whisperx(tmp_path / "audio.wav", _cfg(), tmp_path / "out")


def test_run_whisperx_creates_out_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(
        transcribe, "resolve_whisperx_runtime", lambda cfg: (None, "int8")
    )
    out_dir = tmp_path / "nested" / "out"
    with patch("transcribe.subprocess.run", return_value=MagicMock(returncode=0)):
        transcribe.run_whisperx(tmp_path / "audio.wav", _cfg(), out_dir)
    assert out_dir.exists()


# --- is_apple_silicon ---


def test_is_apple_silicon_true_on_darwin_arm64(monkeypatch):
    monkeypatch.setattr(transcribe.sys, "platform", "darwin")
    monkeypatch.setattr(transcribe.platform, "machine", lambda: "arm64")
    assert transcribe.is_apple_silicon() is True


def test_is_apple_silicon_false_on_darwin_intel(monkeypatch):
    monkeypatch.setattr(transcribe.sys, "platform", "darwin")
    monkeypatch.setattr(transcribe.platform, "machine", lambda: "x86_64")
    assert transcribe.is_apple_silicon() is False


def test_is_apple_silicon_false_on_non_darwin(monkeypatch):
    monkeypatch.setattr(transcribe.sys, "platform", "win32")
    monkeypatch.setattr(transcribe.platform, "machine", lambda: "arm64")
    assert transcribe.is_apple_silicon() is False


# --- load_segments ---


def test_load_segments_missing_file_exits(tmp_path):
    with pytest.raises(SystemExit) as exc_info:
        transcribe.load_segments(tmp_path / "missing.json")
    assert exc_info.value.code == 2


def test_load_segments_empty_segments_exits(tmp_path):
    p = tmp_path / "out.json"
    p.write_text('{"segments": []}')
    with pytest.raises(SystemExit):
        transcribe.load_segments(p)


def test_load_segments_returns_segments(tmp_path):
    p = tmp_path / "out.json"
    p.write_text('{"segments": [{"start": 0.0, "end": 1.0, "text": "hi"}]}')
    segments = transcribe.load_segments(p)
    assert segments == [{"start": 0.0, "end": 1.0, "text": "hi"}]


# --- run_transcription_and_diarization dispatch ---


def test_run_transcription_dispatches_to_assemblyai(tmp_path, monkeypatch):
    called = {}
    monkeypatch.setattr(
        transcribe,
        "run_assemblyai_pipeline",
        lambda w, c, o: called.setdefault("ran", True),
    )
    transcribe.run_transcription_and_diarization(
        tmp_path / "a.wav", _cfg(backend="assemblyai"), tmp_path / "out"
    )
    assert called.get("ran") is True


def test_run_transcription_dispatches_to_mlx_when_backend_is_mlx(tmp_path, monkeypatch):
    called = {}
    monkeypatch.setattr(
        transcribe,
        "run_mlx_whisper_pipeline",
        lambda w, c, o: called.setdefault("ran", True),
    )
    transcribe.run_transcription_and_diarization(
        tmp_path / "a.wav", _cfg(backend="mlx"), tmp_path / "out"
    )
    assert called.get("ran") is True


def test_run_transcription_mlx_failure_reraises_when_backend_forced(
    tmp_path, monkeypatch
):
    def _boom(w, c, o):
        raise RuntimeError("mlx broke")

    monkeypatch.setattr(transcribe, "run_mlx_whisper_pipeline", _boom)
    with pytest.raises(RuntimeError, match="mlx broke"):
        transcribe.run_transcription_and_diarization(
            tmp_path / "a.wav", _cfg(backend="mlx"), tmp_path / "out"
        )


def test_run_transcription_auto_falls_back_to_whisperx_on_mlx_failure(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(transcribe, "is_apple_silicon", lambda: True)

    def _boom(w, c, o):
        raise RuntimeError("mlx broke")

    called = {}
    monkeypatch.setattr(transcribe, "run_mlx_whisper_pipeline", _boom)
    monkeypatch.setattr(
        transcribe, "run_whisperx", lambda w, c, o: called.setdefault("ran", True)
    )
    transcribe.run_transcription_and_diarization(
        tmp_path / "a.wav", _cfg(backend="auto"), tmp_path / "out"
    )
    assert called.get("ran") is True


def test_run_transcription_auto_on_non_apple_silicon_goes_straight_to_whisperx(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(transcribe, "is_apple_silicon", lambda: False)
    called = {}
    monkeypatch.setattr(
        transcribe, "run_whisperx", lambda w, c, o: called.setdefault("ran", True)
    )
    transcribe.run_transcription_and_diarization(
        tmp_path / "a.wav", _cfg(backend="auto"), tmp_path / "out"
    )
    assert called.get("ran") is True


def test_run_transcription_fatal_pipeline_error_propagates_without_fallback(
    tmp_path, monkeypatch
):
    from config import FatalPipelineError

    monkeypatch.setattr(transcribe, "is_apple_silicon", lambda: True)

    def _boom(w, c, o):
        raise FatalPipelineError("no ffmpeg libs")

    monkeypatch.setattr(transcribe, "run_mlx_whisper_pipeline", _boom)
    with pytest.raises(FatalPipelineError):
        transcribe.run_transcription_and_diarization(
            tmp_path / "a.wav", _cfg(backend="auto"), tmp_path / "out"
        )
