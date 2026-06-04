# transcribe.py
from __future__ import annotations

import importlib
import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

from config import AppConfig, FatalPipelineError


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
    if cfg.use_cuda_if_available and has_cuda_available():
        return "cuda", cfg.cuda_compute_type
    return None, cfg.compute_type


def _build_whisperx_cmd(
    wav_path: Path,
    cfg: AppConfig,
    out_dir: Path,
    compute_type: str,
    device: str | None,
) -> list[str]:
    cmd = [
        cfg.whisperx_bin,
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


def run_whisperx(wav_path: Path, cfg: AppConfig, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    device, compute_type = resolve_whisperx_runtime(cfg)
    cmd = _build_whisperx_cmd(wav_path, cfg, out_dir, compute_type, device)

    print("==> Running WhisperX")
    print(f"    runtime: device={device or 'cpu'} compute_type={compute_type}")
    print("    command:", " ".join(cmd))
    try:
        subprocess.run(cmd, check=True)
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
            subprocess.run(fallback_cmd, check=True)
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
            "mlx-whisper is not installed. Install it, or disable "
            "use_mlx_whisper_on_apple_silicon."
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
        raw = mlx_whisper.transcribe(
            str(wav_path), path_or_hf_repo=cfg.mlx_model, verbose=False
        )

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


def run_transcription_and_diarization(
    wav_path: Path, cfg: AppConfig, out_dir: Path
) -> None:
    prefer_mlx = cfg.use_mlx_whisper_on_apple_silicon and is_apple_silicon()
    if prefer_mlx:
        try:
            run_mlx_whisper_pipeline(wav_path, cfg, out_dir)
            return
        except FatalPipelineError:
            raise
        except (ImportError, RuntimeError, OSError, ValueError, TypeError) as err:
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
