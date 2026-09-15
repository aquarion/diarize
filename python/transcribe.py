# transcribe.py
from __future__ import annotations

import importlib
import json
import platform
import re
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

from config import AppConfig, FatalPipelineError

# Matches a whisper-style verbose segment line: "[00:12.340 --> 00:15.670] text"
# (hours are included only for recordings over an hour: "01:02:03.456 --> ...").
_SEGMENT_TIMESTAMP_RE = re.compile(r"^\[[\d:.]+\s*-->\s*([\d:.]+)\]")


def _parse_clock(timestamp: str) -> float:
    """Parse a "MM:SS.mmm" or "HH:MM:SS.mmm" clock timestamp into seconds."""
    parts = timestamp.split(":")
    seconds = float(parts[-1])
    if len(parts) >= 2:
        seconds += int(parts[-2]) * 60
    if len(parts) == 3:
        seconds += int(parts[-3]) * 3600
    return seconds


class _ProgressTee:
    """Forwards everything written to `target`, translating each whisper-style
    "[start --> end] text" verbose segment line into a "progress:<fraction>:
    <stage>" line (parsed by the MCP server) instead of passing it through -
    the segment text itself is redundant with the output files, and passing
    thousands of them through would otherwise bloat the MCP server's
    in-memory copy of this job's stdout for no benefit. Everything else
    (diagnostics, warnings, ...) is forwarded unchanged.

    This lets us surface fine-grained transcription progress without a
    callback hook into the underlying transcription library, mirroring the
    granularity WhisperKit's progress callback gives the Swift backend.
    """

    def __init__(self, target: Any, total_duration: float, stage: str) -> None:
        self._target = target
        self._total_duration = max(total_duration, 1e-6)
        self._stage = stage
        self._buffer = ""

    def write(self, s: str) -> int:
        self._buffer += s
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            match = _SEGMENT_TIMESTAMP_RE.match(line)
            if match:
                fraction = min(1.0, _parse_clock(match.group(1)) / self._total_duration)
                self._target.write(f"progress:{fraction:.4f}:{self._stage}\n")
            else:
                self._target.write(line + "\n")
        return len(s)

    def flush(self) -> None:
        if self._buffer:
            self._target.write(self._buffer)
            self._buffer = ""
        self._target.flush()


@contextmanager
def _tee_progress(stage: str, total_duration: float):
    """Temporarily replaces sys.stdout with a `_ProgressTee` for the duration
    of the `with` block."""
    old_stdout = sys.stdout
    sys.stdout = _ProgressTee(old_stdout, total_duration, stage)
    try:
        yield
    finally:
        sys.stdout = old_stdout


def has_cuda_available() -> bool:
    torch_mod: Any | None = None
    try:
        import torch  # type: ignore

        torch_mod = torch
    except ImportError:
        pass

    if torch_mod is not None:
        cuda_ns = getattr(torch_mod, "cuda", None)
        is_available = getattr(cuda_ns, "is_available", None)
        if callable(is_available):
            try:
                return bool(is_available())
            except OSError:
                pass

    try:
        probe = subprocess.run(
            ["nvidia-smi", "-L"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return probe.returncode == 0
    except OSError:
        return False


def resolve_whisperx_runtime(cfg: AppConfig) -> tuple[str | None, str]:
    if has_cuda_available():
        return "cuda", cfg.cuda_compute_type
    return None, cfg.compute_type


def _resolve_whisperx_bin(whisperx_bin: str) -> str:
    """Resolve the whisperx console script, preferring the one next to this
    interpreter (its venv's Scripts/bin dir isn't necessarily on PATH)."""
    if Path(whisperx_bin).is_absolute():
        return whisperx_bin
    exe_suffix = ".exe" if sys.platform == "win32" else ""
    sibling = Path(sys.executable).parent / f"{whisperx_bin}{exe_suffix}"
    if sibling.exists():
        return str(sibling)
    return whisperx_bin


def _build_whisperx_cmd(
    wav_path: Path,
    cfg: AppConfig,
    out_dir: Path,
    compute_type: str,
    device: str | None,
) -> list[str]:
    cmd = [
        _resolve_whisperx_bin(cfg.whisperx_bin),
        str(wav_path),
        "--model",
        cfg.model,
        "--language",
        cfg.language,
        "--diarize",
        "--hf_token",
        cfg.hf_token,
        "--min_speakers",
        str(cfg.num_speakers),
        "--max_speakers",
        str(cfg.num_speakers),
        "--compute_type",
        compute_type,
        "--batch_size",
        str(cfg.batch_size),
        "--output_dir",
        str(out_dir),
        "--output_format",
        "all",
        "--print_progress",
        "True",
    ]
    if device:
        cmd.extend(["--device", device])
    return cmd


# whisperx's own --print_progress output looks like "Progress: 42.00%..."
_WHISPERX_PROGRESS_RE = re.compile(r"^Progress:\s*([\d.]+)%")


def _run_whisperx_subprocess(cmd: list[str]) -> None:
    """Runs a whisperx command, relaying its stdout live while translating its
    own "Progress: NN.NN%..." lines (from --print_progress) into the
    "progress:<fraction>:<stage>" lines the MCP server parses - the same
    format the mlx-whisper and Swift/WhisperKit backends emit."""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, text=True, bufsize=1, errors="replace"
    )
    assert proc.stdout is not None
    # whisperx (when run with --diarize, as we always do) runs transcription
    # then a separate word-alignment pass, and each prints its own
    # independent 0-100% "Progress:" lines rather than a single combined
    # sweep - so translating both verbatim would make the reported fraction
    # jump backwards once alignment starts. Only forwarding strictly
    # increasing fractions keeps what we report monotonic: alignment then
    # just reads as holding at "almost done" rather than restarting.
    last_fraction = 0.0
    for line in proc.stdout:
        sys.stdout.write(line)
        match = _WHISPERX_PROGRESS_RE.match(line.strip())
        if match:
            fraction = min(1.0, float(match.group(1)) / 100.0)
            if fraction > last_fraction:
                last_fraction = fraction
                print(f"progress:{fraction:.4f}:transcribing")
    proc.stdout.close()
    returncode = proc.wait()
    if returncode != 0:
        raise subprocess.CalledProcessError(returncode, cmd)


def run_whisperx(wav_path: Path, cfg: AppConfig, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    device, compute_type = resolve_whisperx_runtime(cfg)
    cmd = _build_whisperx_cmd(wav_path, cfg, out_dir, compute_type, device)

    print("==> Running WhisperX")
    print(f"    runtime: device={device or 'cpu'} compute_type={compute_type}")
    print("    command:", " ".join(cmd))
    try:
        _run_whisperx_subprocess(cmd)
    except subprocess.CalledProcessError:
        if device == "cuda":
            print("!! CUDA WhisperX run failed; retrying on CPU.")
            fallback_cmd = _build_whisperx_cmd(
                wav_path,
                cfg,
                out_dir,
                cfg.compute_type,
                None,
            )
            print("    fallback command:", " ".join(fallback_cmd))
            _run_whisperx_subprocess(fallback_cmd)
            return
        raise


def is_apple_silicon() -> bool:
    return sys.platform == "darwin" and platform.machine().lower() in {
        "arm64",
        "aarch64",
    }


def _segment_overlap(
    a_start: float, a_end: float, b_start: float, b_end: float
) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def _fmt_srt_ts(seconds: float) -> str:
    total_ms = max(0, int(seconds * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_vtt_ts(seconds: float) -> str:
    total_ms = max(0, int(seconds * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


def run_mlx_whisper_pipeline(wav_path: Path, cfg: AppConfig, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        import mlx_whisper  # type: ignore
    except ImportError as err:
        raise RuntimeError(
            "mlx-whisper is not installed. "
            "Install it or set backend to 'whisperx' in config."
        ) from err

    if not cfg.hf_token.strip():
        raise RuntimeError(
            "hf_token is required for diarization when using mlx-whisper."
        )

    try:
        pyannote_audio = importlib.import_module("pyannote.audio")
        pipeline_cls = getattr(pyannote_audio, "Pipeline")
        pyannote_hook_mod = importlib.import_module(
            "pyannote.audio.pipelines.utils.hook"
        )
        ProgressHook = getattr(pyannote_hook_mod, "ProgressHook")
        pyannote_io = importlib.import_module("pyannote.audio.core.io")
        if not hasattr(pyannote_io, "AudioDecoder"):
            raise FatalPipelineError(
                "pyannote.audio audio decoder failed to load — torchcodec "
                "could not find ffmpeg shared libraries.\n"
                "Fix: add the ffmpeg lib directory to extra_lib_path in "
                "config, e.g.:\n"
                '  "extra_lib_path": '
                '["/opt/homebrew/opt/ffmpeg@7/lib"]'
            )
    except (ImportError, AttributeError) as err:
        raise RuntimeError(
            "pyannote.audio is missing; install whisperx dependencies first."
        ) from err

    print("==> Loading pyannote diarization model")
    pretrained_kwargs: dict[str, Any] = {"token": cfg.hf_token}
    pipeline = cast(Any, pipeline_cls).from_pretrained(
        "pyannote/speaker-diarization-3.1", **pretrained_kwargs
    )
    if pipeline is None:
        raise RuntimeError("Failed to initialize pyannote diarization pipeline.")

    base_name = wav_path.stem
    transcription_checkpoint = out_dir / f"{base_name}_transcription.json"
    diarization_checkpoint = out_dir / f"{base_name}_diarization.json"

    if transcription_checkpoint.exists():
        print(f"==> Resuming from transcription checkpoint: {transcription_checkpoint}")
        checkpoint_data = json.loads(transcription_checkpoint.read_text())
        normalized = checkpoint_data["segments"]
        language = checkpoint_data.get("language", cfg.language)
    else:
        print("==> Running mlx-whisper (Apple Silicon)")
        print(f"    model: {cfg.mlx_model}")
        from mlx_whisper.audio import SAMPLE_RATE, load_audio

        # Load audio ourselves (rather than handing transcribe() the path) so we
        # know its duration up front, letting us translate whisper's per-segment
        # verbose timestamps into a 0-1 progress fraction as they're printed.
        audio_array = load_audio(str(wav_path))
        duration = len(audio_array) / SAMPLE_RATE
        with _tee_progress(stage="transcribing", total_duration=duration):
            raw = mlx_whisper.transcribe(
                audio_array, path_or_hf_repo=cfg.mlx_model, verbose=True
            )
        # If the last segment ends before the file's actual duration (common
        # with trailing silence), the tee above never sees a timestamp that
        # reaches 1.0 - report completion explicitly rather than leaving a
        # caller reading a stale, less-than-100% fraction until the next
        # "==>" line (which won't be printed until diarization starts).
        print("progress:1.0000:transcribing")

        raw_segments = raw.get("segments", []) if isinstance(raw, dict) else []
        if not isinstance(raw_segments, list) or not raw_segments:
            raise RuntimeError("mlx-whisper returned no segments.")

        normalized = []
        for idx, seg in enumerate(raw_segments):
            if not isinstance(seg, dict):
                continue
            text = str(seg.get("text") or "").strip()
            if not text:
                continue
            start = float(seg.get("start", 0.0))
            end = float(seg.get("end", start))
            if end <= start:
                if idx + 1 < len(raw_segments) and isinstance(
                    raw_segments[idx + 1], dict
                ):
                    end = float(raw_segments[idx + 1].get("start", start))
                if end <= start:
                    end = start + 0.01
            normalized.append({"start": start, "end": end, "text": text})

        if not normalized:
            raise RuntimeError("mlx-whisper produced no usable segments.")

        language = raw.get("language") if isinstance(raw, dict) else cfg.language
        transcription_checkpoint.write_text(
            json.dumps({"segments": normalized, "language": language}, indent=2) + "\n"
        )
        print(f"    checkpoint saved: {transcription_checkpoint}")

    if diarization_checkpoint.exists():
        print(f"==> Resuming from diarization checkpoint: {diarization_checkpoint}")
        diar_turns: list[dict[str, Any]] = json.loads(
            diarization_checkpoint.read_text()
        )
    else:
        print("==> Running pyannote diarization")

        with ProgressHook() as hook:
            diarization = cast(Any, pipeline)(
                str(wav_path),
                min_speakers=cfg.num_speakers,
                max_speakers=cfg.num_speakers,
                hook=hook,
            )

        if hasattr(diarization, "itertracks"):
            annotation = diarization
        elif hasattr(diarization, "speaker_diarization"):
            annotation = diarization.speaker_diarization
        else:
            attrs = [a for a in dir(diarization) if not a.startswith("_")]
            raise RuntimeError(
                f"Cannot extract annotation from {type(diarization).__name__}. "
                f"Available attributes: {attrs}"
            )
        diar_turns = []
        for turn, _, speaker in annotation.itertracks(yield_label=True):
            diar_turns.append(
                {
                    "start": float(turn.start),
                    "end": float(turn.end),
                    "speaker": str(speaker),
                }
            )
        diarization_checkpoint.write_text(json.dumps(diar_turns, indent=2) + "\n")
        print(f"    checkpoint saved: {diarization_checkpoint}")

    for seg in normalized:
        best_speaker = "UNKNOWN"
        best_overlap = 0.0
        for turn in diar_turns:
            overlap = _segment_overlap(
                float(seg["start"]),
                float(seg["end"]),
                float(turn["start"]),
                float(turn["end"]),
            )
            if overlap > best_overlap:
                best_overlap = overlap
                best_speaker = str(turn["speaker"])
        seg["speaker"] = best_speaker

    json_path = out_dir / f"{base_name}.json"
    txt_path = out_dir / f"{base_name}.txt"
    srt_path = out_dir / f"{base_name}.srt"
    vtt_path = out_dir / f"{base_name}.vtt"

    payload = {
        "segments": normalized,
        "language": language,
    }
    json_path.write_text(json.dumps(payload, indent=2) + "\n")
    txt_path.write_text("\n".join(seg["text"] for seg in normalized) + "\n")

    srt_lines: list[str] = []
    vtt_lines: list[str] = ["WEBVTT", ""]
    for i, seg in enumerate(normalized, start=1):
        start = float(seg["start"])
        end = float(seg["end"])
        speaker = str(seg.get("speaker") or "UNKNOWN")
        text = str(seg["text"])

        srt_lines.extend(
            [
                str(i),
                f"{_fmt_srt_ts(start)} --> {_fmt_srt_ts(end)}",
                f"[{speaker}] {text}",
                "",
            ]
        )
        vtt_lines.extend(
            [
                str(i),
                f"{_fmt_vtt_ts(start)} --> {_fmt_vtt_ts(end)}",
                f"[{speaker}] {text}",
                "",
            ]
        )

    srt_path.write_text("\n".join(srt_lines).rstrip() + "\n")
    vtt_path.write_text("\n".join(vtt_lines).rstrip() + "\n")


def run_assemblyai_pipeline(wav_path: Path, cfg: AppConfig, out_dir: Path) -> None:
    try:
        import assemblyai as aai  # type: ignore
    except ImportError as err:
        raise RuntimeError(
            "assemblyai package is not installed. Run: pip install assemblyai"
        ) from err

    aai.settings.api_key = cfg.assemblyai_api_key

    transcription_config = aai.TranscriptionConfig(
        speaker_labels=True,
        speakers_expected=cfg.num_speakers,
        language_code=cfg.language,
    )

    print("==> Running AssemblyAI transcription + diarization")
    print(f"    file: {wav_path}")

    transcriber = aai.Transcriber()
    transcript = transcriber.transcribe(str(wav_path), config=transcription_config)

    if transcript.status == aai.TranscriptStatus.error:
        raise RuntimeError(f"AssemblyAI transcription failed: {transcript.error}")

    if not transcript.utterances:
        raise RuntimeError("AssemblyAI returned no utterances.")

    speaker_index: dict[str, int] = {}
    segments: list[dict[str, Any]] = []
    for utt in transcript.utterances:
        if utt.speaker not in speaker_index:
            speaker_index[utt.speaker] = len(speaker_index)
        speaker_label = f"SPEAKER_{speaker_index[utt.speaker]:02d}"
        segments.append(
            {
                "start": utt.start / 1000.0,
                "end": utt.end / 1000.0,
                "text": utt.text,
                "speaker": speaker_label,
            }
        )

    base_name = wav_path.stem
    json_path = out_dir / f"{base_name}.json"
    txt_path = out_dir / f"{base_name}.txt"
    srt_path = out_dir / f"{base_name}.srt"
    vtt_path = out_dir / f"{base_name}.vtt"

    payload = {"segments": segments, "language": cfg.language}
    json_path.write_text(json.dumps(payload, indent=2) + "\n")
    txt_path.write_text("\n".join(seg["text"] for seg in segments) + "\n")

    srt_lines: list[str] = []
    vtt_lines: list[str] = ["WEBVTT", ""]
    for i, seg in enumerate(segments, start=1):
        start = float(seg["start"])
        end = float(seg["end"])
        speaker = str(seg["speaker"])
        text = str(seg["text"])
        srt_lines.extend(
            [
                str(i),
                f"{_fmt_srt_ts(start)} --> {_fmt_srt_ts(end)}",
                f"[{speaker}] {text}",
                "",
            ]
        )
        vtt_lines.extend(
            [
                str(i),
                f"{_fmt_vtt_ts(start)} --> {_fmt_vtt_ts(end)}",
                f"[{speaker}] {text}",
                "",
            ]
        )

    srt_path.write_text("\n".join(srt_lines).rstrip() + "\n")
    vtt_path.write_text("\n".join(vtt_lines).rstrip() + "\n")
    print(f"    saved: {json_path}")


def run_transcription_and_diarization(
    wav_path: Path, cfg: AppConfig, out_dir: Path
) -> None:
    backend = cfg.backend

    if backend == "assemblyai":
        run_assemblyai_pipeline(wav_path, cfg, out_dir)
        return

    prefer_mlx = backend == "mlx" or (backend == "auto" and is_apple_silicon())
    if prefer_mlx:
        try:
            run_mlx_whisper_pipeline(wav_path, cfg, out_dir)
            return
        except FatalPipelineError:
            raise
        except (ImportError, RuntimeError, OSError, ValueError, TypeError) as err:
            if backend == "mlx":
                raise
            print("!! mlx-whisper pipeline failed; falling back to WhisperX.")
            print(f"   reason: {err}")

    run_whisperx(wav_path, cfg, out_dir)


def load_segments(json_path: Path) -> list[dict[str, Any]]:
    if not json_path.exists():
        print(f"!! WhisperX output missing: {json_path}", file=sys.stderr)
        raise SystemExit(2)

    data = json.loads(json_path.read_text())
    segments = data.get("segments", [])
    if not isinstance(segments, list) or not segments:
        print("!! No segments found in WhisperX JSON output.", file=sys.stderr)
        raise SystemExit(2)
    return segments
