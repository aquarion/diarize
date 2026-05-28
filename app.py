#!/usr/bin/env python3
"""
Pure Python diarization pipeline.

Flow:
1) Run WhisperX on a WAV file.
2) Detect all speaker labels from WhisperX JSON output.
3) Prompt the user to name each detected speaker.
4) Write markdown transcript locally and into an Obsidian vault.

Configuration is loaded from config.json (or --config).
"""

from __future__ import annotations

import argparse
import datetime as dt
import getpass
import importlib
import json
import os
import platform
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast


HERE = Path(__file__).resolve().parent


@dataclass
class AppConfig:
    hf_token: str
    whisperx_bin: str
    model: str
    language: str
    min_speakers: int
    max_speakers: int
    use_mlx_whisper_on_apple_silicon: bool
    mlx_model: str
    use_cuda_if_available: bool
    cuda_compute_type: str
    compute_type: str
    batch_size: int
    output_dir: str
    speakers_file: str
    transcript_title: str
    vault_path: str
    vault_subdir: str
    vault_filename_template: str


DEFAULTS: dict[str, Any] = {
    "hf_token": "",
    "whisperx_bin": "whisperx",
    "model": "medium",
    "language": "en",
    "min_speakers": 5,
    "max_speakers": 7,
    "use_mlx_whisper_on_apple_silicon": False,
    "mlx_model": "mlx-community/whisper-large-v3-turbo",
    "use_cuda_if_available": True,
    "cuda_compute_type": "float16",
    "compute_type": "int8",
    "batch_size": 4,
    "output_dir": "./out",
    "speakers_file": "speakers.json",
    "transcript_title": "Session Transcript",
    "vault_path": "~/Obsidian",
    "vault_subdir": "Transcripts",
    "vault_filename_template": "{audio_stem}.md",
}

REQUIRED_ALWAYS: tuple[str, ...] = ("vault_path",)
REQUIRED_FOR_WHISPERX: tuple[str, ...] = ("hf_token",)


def default_config_path() -> Path:
    if sys.platform.startswith("linux"):
        return Path.home() / ".config" / "diarize" / "config.json"
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / "diarize"
            / "config.json"
        )
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "diarize" / "config.json"
        return Path.home() / "AppData" / "Roaming" / "diarize" / "config.json"
    return Path.home() / ".config" / "diarize" / "config.json"


def ensure_default_config(path: Path) -> None:
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(DEFAULTS, indent=2) + "\n")
    print(f"==> Created default config at: {path}")


def load_config_data(config_path: Path) -> dict[str, Any]:
    merged = dict(DEFAULTS)
    if config_path.exists():
        try:
            loaded = json.loads(config_path.read_text())
            if not isinstance(loaded, dict):
                raise ValueError("config root must be an object")
            merged.update(loaded)
        except (json.JSONDecodeError, ValueError) as err:
            print(f"!! Invalid config at {config_path}: {err}", file=sys.stderr)
            raise SystemExit(2) from err
    return merged


def save_config_data(config_path: Path, data: dict[str, Any]) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(data, indent=2) + "\n")


def prompt_for_required_config(
    config_path: Path, data: dict[str, Any], require_hf_token: bool
) -> dict[str, Any]:
    required = list(REQUIRED_ALWAYS)
    if require_hf_token:
        required.extend(REQUIRED_FOR_WHISPERX)

    prompts: dict[str, str] = {
        "vault_path": "Obsidian vault path",
        "hf_token": "Hugging Face token",
    }

    changed = False
    for key in required:
        current = str(data.get(key, "")).strip()
        if current:
            continue

        print(f"\n==> Required config missing: {key}")
        while True:
            if key == "hf_token":
                value = getpass.getpass(f"  {prompts[key]}: ").strip()
            else:
                value = input(f"  {prompts[key]}: ").strip()
            if value:
                data[key] = value
                changed = True
                break
            print("  Value is required.")

    if changed:
        save_config_data(config_path, data)
        print(f"\n==> Saved updated config: {config_path}")

    return data


def _resolve_path(base: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (base / path).resolve()
    return path


def _resolve_config_relative(config_path: Path, raw: str) -> Path:
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = (config_path.parent / path).resolve()
    return path


def load_config(config_path: Path) -> AppConfig:
    merged = load_config_data(config_path)

    return AppConfig(
        hf_token=str(merged["hf_token"]),
        whisperx_bin=str(merged["whisperx_bin"]),
        model=str(merged["model"]),
        language=str(merged["language"]),
        min_speakers=int(merged["min_speakers"]),
        max_speakers=int(merged["max_speakers"]),
        use_mlx_whisper_on_apple_silicon=bool(
            merged["use_mlx_whisper_on_apple_silicon"]
        ),
        mlx_model=str(merged["mlx_model"]),
        use_cuda_if_available=bool(merged["use_cuda_if_available"]),
        cuda_compute_type=str(merged["cuda_compute_type"]),
        compute_type=str(merged["compute_type"]),
        batch_size=int(merged["batch_size"]),
        output_dir=str(merged["output_dir"]),
        speakers_file=str(merged["speakers_file"]),
        transcript_title=str(merged["transcript_title"]),
        vault_path=str(merged["vault_path"]),
        vault_subdir=str(merged["vault_subdir"]),
        vault_filename_template=str(merged["vault_filename_template"]),
    )


def fmt_ts(seconds: float | None) -> str:
    if seconds is None:
        return "[--:--:--]"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"[{h:02d}:{m:02d}:{s:02d}]"


def load_speaker_mapping(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, str)}


def save_speaker_mapping(path: Path, mapping: dict[str, str]) -> None:
    payload: dict[str, Any] = {
        "_comment": [
            "Speaker mapping generated/updated by app.py.",
            "Values are used to label diarized transcript output.",
        ]
    }
    payload.update(dict(sorted(mapping.items())))
    path.write_text(json.dumps(payload, indent=2) + "\n")


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
        str(cfg.min_speakers),
        "--max_speakers",
        str(cfg.max_speakers),
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
    return sys.platform == "darwin" and platform.machine().lower() in {"arm64", "aarch64"}


def _segment_overlap(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
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
        raise RuntimeError("hf_token is required for diarization when using mlx-whisper.")

    print("==> Running mlx-whisper (Apple Silicon)")
    print(f"    model: {cfg.mlx_model}")
    raw = mlx_whisper.transcribe(str(wav_path), path_or_hf_repo=cfg.mlx_model)

    raw_segments = raw.get("segments", []) if isinstance(raw, dict) else []
    if not isinstance(raw_segments, list) or not raw_segments:
        raise RuntimeError("mlx-whisper returned no segments.")

    normalized: list[dict[str, Any]] = []
    for idx, seg in enumerate(raw_segments):
        if not isinstance(seg, dict):
            continue
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        start = float(seg.get("start", 0.0))
        end = float(seg.get("end", start))
        if end <= start:
            if idx + 1 < len(raw_segments) and isinstance(raw_segments[idx + 1], dict):
                end = float(raw_segments[idx + 1].get("start", start))
            if end <= start:
                end = start + 0.01
        normalized.append({"start": start, "end": end, "text": text})

    if not normalized:
        raise RuntimeError("mlx-whisper produced no usable segments.")

    try:
        pyannote_audio = importlib.import_module("pyannote.audio")
        pipeline_cls = getattr(pyannote_audio, "Pipeline")
    except (ImportError, AttributeError) as err:
        raise RuntimeError(
            "pyannote.audio is missing; install whisperx dependencies first."
        ) from err

    print("==> Running pyannote diarization")
    pretrained_kwargs: dict[str, Any] = {"use_auth_token": cfg.hf_token}
    pipeline = cast(Any, pipeline_cls).from_pretrained(
        "pyannote/speaker-diarization-3.1", **pretrained_kwargs
    )
    if pipeline is None:
        raise RuntimeError("Failed to initialize pyannote diarization pipeline.")

    diarization = cast(Any, pipeline)(
        str(wav_path),
        min_speakers=cfg.min_speakers,
        max_speakers=cfg.max_speakers,
    )

    diar_turns: list[dict[str, Any]] = []
    for turn, _, speaker in diarization.itertracks(yield_label=True):
        diar_turns.append(
            {
                "start": float(turn.start),
                "end": float(turn.end),
                "speaker": str(speaker),
            }
        )

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

    base_name = wav_path.stem
    json_path = out_dir / f"{base_name}.json"
    txt_path = out_dir / f"{base_name}.txt"
    srt_path = out_dir / f"{base_name}.srt"
    vtt_path = out_dir / f"{base_name}.vtt"

    payload = {
        "segments": normalized,
        "language": raw.get("language") if isinstance(raw, dict) else cfg.language,
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


def run_transcription_and_diarization(wav_path: Path, cfg: AppConfig, out_dir: Path) -> None:
    prefer_mlx = cfg.use_mlx_whisper_on_apple_silicon and is_apple_silicon()
    if prefer_mlx:
        try:
            run_mlx_whisper_pipeline(wav_path, cfg, out_dir)
            return
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


def prompt_for_speakers(detected: list[str], existing: dict[str, str]) -> dict[str, str]:
    print("\n==> Speaker labeling")
    print("Enter a display name for each detected speaker label.")
    print("Press Enter to keep current/default value shown in brackets.")

    result = dict(existing)
    for label in detected:
        default = existing.get(label, label)
        while True:
            typed = input(f"  {label} [{default}]: ").strip()
            chosen = typed or default
            if chosen:
                result[label] = chosen
                break
    return result


def coalesce_segments(
    segments: list[dict[str, Any]], mapping: dict[str, str]
) -> list[tuple[str, float, str]]:
    blocks: list[tuple[str, float, list[str]]] = []
    for seg in segments:
        speaker_label = str(seg.get("speaker") or "UNKNOWN")
        display_name = mapping.get(speaker_label, speaker_label)
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        start = float(seg.get("start", 0.0))
        if blocks and blocks[-1][0] == display_name:
            blocks[-1][2].append(text)
        else:
            blocks.append((display_name, start, [text]))
    return [(name, start, " ".join(parts)) for (name, start, parts) in blocks]


def render_markdown(
    blocks: list[tuple[str, float, str]], title: str, audio_path: Path
) -> str:
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    lines: list[str] = [
        f"# {title}",
        "",
        f"- Source audio: `{audio_path}`",
        f"- Generated: {now}",
        "",
    ]
    for name, start, text in blocks:
        lines.append(f"**{name}** {fmt_ts(start)}")
        lines.append("")
        lines.append(text)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def make_vault_target(cfg: AppConfig, audio_stem: str) -> Path:
    vault_root = Path(cfg.vault_path).expanduser()
    subdir = Path(cfg.vault_subdir) if cfg.vault_subdir else Path("")
    filename = cfg.vault_filename_template.format(audio_stem=audio_stem)
    return vault_root / subdir / filename


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run diarization and export an Obsidian markdown transcript."
    )
    parser.add_argument("wav", help="Path to input WAV file")
    parser.add_argument(
        "--config",
        default=str(default_config_path()),
        help="Path to JSON config (default: OS user config location)",
    )
    parser.add_argument(
        "--skip-whisperx",
        action="store_true",
        help="Skip WhisperX run and only relabel from existing JSON output",
    )
    return parser.parse_args(argv[1:])


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    cfg_path = _resolve_path(HERE, args.config)
    ensure_default_config(cfg_path)
    data = load_config_data(cfg_path)
    data = prompt_for_required_config(
        cfg_path,
        data,
        require_hf_token=not args.skip_whisperx,
    )
    save_config_data(cfg_path, data)
    cfg = load_config(cfg_path)

    wav_path = _resolve_path(HERE, args.wav)
    if not wav_path.exists():
        print(f"!! Input WAV not found: {wav_path}", file=sys.stderr)
        return 2

    base_out_dir = _resolve_path(HERE, cfg.output_dir)
    out_dir = base_out_dir / wav_path.stem
    speakers_path = _resolve_config_relative(cfg_path, cfg.speakers_file)
    whisper_json = out_dir / f"{wav_path.stem}.json"

    if not args.skip_whisperx:
        run_transcription_and_diarization(wav_path, cfg, out_dir)
    else:
        print("==> Skipping WhisperX run (--skip-whisperx)")

    segments = load_segments(whisper_json)
    detected = sorted({str(seg.get("speaker") or "UNKNOWN") for seg in segments})

    existing_map = load_speaker_mapping(speakers_path)
    updated_map = prompt_for_speakers(detected, existing_map)
    save_speaker_mapping(speakers_path, updated_map)

    blocks = coalesce_segments(segments, updated_map)
    markdown = render_markdown(blocks, cfg.transcript_title, wav_path)

    local_md = out_dir / "transcript.md"
    local_md.parent.mkdir(parents=True, exist_ok=True)
    local_md.write_text(markdown)

    vault_md = make_vault_target(cfg, wav_path.stem)
    vault_md.parent.mkdir(parents=True, exist_ok=True)
    vault_md.write_text(markdown)

    print("\n==> Complete")
    print(f"    speaker map updated: {speakers_path}")
    print(f"    local transcript    : {local_md}")
    print(f"    vault transcript    : {vault_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
