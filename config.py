from __future__ import annotations

import getpass
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class FatalPipelineError(Exception):
    """Raised for environment errors that make any diarization path unviable."""


@dataclass
class AppConfig:
    hf_token: str
    whisperx_bin: str
    model: str
    language: str
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
    extra_path: list[str]
    extra_lib_path: list[str]
    num_speakers: int = 2


DEFAULTS: dict[str, Any] = {
    "hf_token": "",
    "whisperx_bin": "whisperx",
    "model": "medium",
    "language": "en",
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
    "extra_path": [],
    "extra_lib_path": [],
    "num_speakers": 2,
}

REQUIRED_ALWAYS: tuple[str, ...] = ("vault_path",)
REQUIRED_FOR_WHISPERX: tuple[str, ...] = ("hf_token",)


def default_config_path() -> Path:
    if sys.platform.startswith("linux"):
        return Path.home() / ".config" / "diarize" / "config.json"
    if sys.platform == "darwin":
        return (
            Path.home() / "Library" / "Application Support" / "diarize" / "config.json"
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
    config_path: Path,
    data: dict[str, Any],
    require_hf_token: bool,
    non_interactive: bool = False,
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

        if non_interactive:
            print(
                f"!! Required config missing: {key} — cannot proceed"
                " non-interactively.",
                file=sys.stderr,
            )
            raise SystemExit(2)

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
        extra_path=[str(p) for p in merged.get("extra_path", [])],
        extra_lib_path=[str(p) for p in merged.get("extra_lib_path", [])],
        num_speakers=int(merged.get("num_speakers", 2)),
    )
