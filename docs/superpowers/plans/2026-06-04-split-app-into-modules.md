# Split app.py Into Modules Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the monolithic `app.py` (928 lines) into four focused modules plus a thin orchestration entry point.

**Architecture:** Each module owns one responsibility and imports only what it needs. `app.py` becomes pure CLI + orchestration (~90 lines). No behaviour changes — this is a mechanical move-and-import refactor.

**Tech Stack:** Python 3.13, dataclasses, pathlib, importlib (pyannote), mlx_whisper, whisperx, pyannote.audio

---

## File Map

| File | Responsibility | Key contents |
|---|---|---|
| `config.py` | Config dataclass, load/save, path helpers | `AppConfig`, `FatalPipelineError`, `DEFAULTS`, `load_config`, `prompt_for_required_config`, `_resolve_path` |
| `transcribe.py` | Whisper + pyannote pipelines, checkpointing | `run_transcription_and_diarization`, `run_mlx_whisper_pipeline`, `run_whisperx`, `load_segments`, helpers |
| `speakers.py` | Speaker mapping, labeling prompts, Claude guessing | `prompt_for_speakers`, `guess_speakers_with_claude`, `load_speaker_mapping`, `save_speaker_mapping`, `coalesce_segments` |
| `render.py` | Markdown rendering, vault targeting, terminal UX | `render_markdown`, `make_vault_target`, `terminal_link`, `fmt_ts` |
| `app.py` | CLI arg parsing, orchestration only | `parse_args`, `_relaunch_with_lib_path`, `main` |

---

## Task 1: Create `config.py`

**Files:**
- Create: `config.py`

- [ ] **Step 1: Create `config.py` with all config-related code**

```python
# config.py
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
    )
```

- [ ] **Step 2: Verify the file is importable**

Run: `cd /Users/aquarion/code/aquarion/diarize && poetry run python -c "from config import AppConfig, load_config; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add config.py
git commit -m "refactor: extract config.py from app.py"
```

---

## Task 2: Create `render.py`

**Files:**
- Create: `render.py`

- [ ] **Step 1: Create `render.py`**

```python
# render.py
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config import AppConfig


def terminal_link(path: Path) -> str:
    """Return an OSC 8 hyperlink for ``path`` that macOS Terminal renders as clickable."""
    url = path.resolve().as_uri()
    label = str(path)
    return f"\033]8;;{url}\033\\{label}\033]8;;\033\\"


def fmt_ts(seconds: float | None) -> str:
    if seconds is None:
        return "[--:--:--]"
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"[{h:02d}:{m:02d}:{s:02d}]"


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


def make_vault_target(cfg: "AppConfig", audio_stem: str) -> Path:
    vault_root = Path(cfg.vault_path).expanduser()
    subdir = Path(cfg.vault_subdir) if cfg.vault_subdir else Path("")
    filename = cfg.vault_filename_template.format(audio_stem=audio_stem)
    return vault_root / subdir / filename
```

- [ ] **Step 2: Verify the file is importable**

Run: `cd /Users/aquarion/code/aquarion/diarize && poetry run python -c "from render import render_markdown, fmt_ts, terminal_link, make_vault_target; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add render.py
git commit -m "refactor: extract render.py from app.py"
```

---

## Task 3: Create `speakers.py`

**Files:**
- Create: `speakers.py`

- [ ] **Step 1: Create `speakers.py`**

```python
# speakers.py
from __future__ import annotations

import json
import re
import subprocess
import datetime as dt
from pathlib import Path
from typing import Any

from render import fmt_ts, terminal_link


def load_speaker_mapping(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        k: v for k, v in data.items() if not k.startswith("_") and isinstance(v, str)
    }


def save_speaker_mapping(path: Path, mapping: dict[str, str]) -> None:
    payload: dict[str, Any] = {
        "_comment": [
            "Speaker mapping generated/updated by app.py.",
            "Values are used to label diarized transcript output.",
        ]
    }
    payload.update(dict(sorted(mapping.items())))
    path.write_text(json.dumps(payload, indent=2) + "\n")


def _extract_json(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return json.loads(m.group(1))
    m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if m:
        return json.loads(m.group(0))
    raise ValueError("No JSON object found in Claude response")


def guess_speakers_with_claude(
    detected: list[str], segments: list[dict[str, Any]], ctime: dt.datetime
) -> dict[str, str]:
    lines: list[str] = []
    for seg in segments[:120]:
        label = str(seg.get("speaker") or "UNKNOWN")
        text = str(seg.get("text") or "").strip()
        if text:
            lines.append(f"{fmt_ts(seg.get('start'))} {label}: {text}")

    ctime_label = ctime.strftime("%Y-%m-%d %H:%M:%S")
    prompt = (
        "The following is an excerpt from a diarized transcript of a meeting"
        " or conversation. "
        f"Speaker labels present: {', '.join(detected)}.\n"
        "Based on any names, titles, or context clues in the transcript, "
        "guess the real name of each speaker. "
        "If you cannot determine a real name, use a short descriptive label "
        "(e.g. 'Facilitator', 'Presenter', 'Participant 1').\n\n"
        "Return ONLY a valid JSON object mapping each label to a guessed name. "
        "No other text, no markdown.\n\n"
        "If you have access to a calendar, email, or other context that can"
        " help you make better guesses, use that information.\n"
        f"The transcript was created on {ctime_label}.\n\n"
        "Transcript excerpt:\n" + "\n".join(lines)
    )

    print("==> Asking Claude to guess speaker names...")
    try:
        result = subprocess.run(
            ["claude", "-p", prompt],
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
    except FileNotFoundError as err:
        raise RuntimeError("'claude' not found in PATH") from err
    except subprocess.TimeoutExpired as err:
        raise RuntimeError("Claude CLI timed out") from err
    except subprocess.CalledProcessError as err:
        raise RuntimeError(f"Claude CLI exited {err.returncode}") from err

    try:
        mapping = _extract_json(result.stdout)
        if not isinstance(mapping, dict):
            raise ValueError("Response was not a JSON object")
    except (json.JSONDecodeError, ValueError) as err:
        raise RuntimeError(f"Could not parse Claude response: {err}") from err

    guesses = {k: str(v) for k, v in mapping.items() if k in detected}
    if guesses:
        print("    Guesses:")
        for label, name in sorted(guesses.items()):
            print(f"      {label} -> {name}")
    return guesses


def prompt_for_speakers(
    detected: list[str],
    existing: dict[str, str],
    segments: list[dict[str, Any]],
    transcript_path: Path | None = None,
    non_interactive: bool = False,
) -> dict[str, str]:
    print("\n==> Speaker labeling")
    if transcript_path and transcript_path.exists():
        print(f"    Full transcript: {terminal_link(transcript_path)}")
    print(f"    Detected speakers: {', '.join(sorted(detected))}")

    speaker_segs: dict[str, list[dict[str, Any]]] = {}
    for seg in segments:
        label = str(seg.get("speaker") or "UNKNOWN")
        if str(seg.get("text") or "").strip():
            speaker_segs.setdefault(label, []).append(seg)

    sample: dict[str, list[str]] = {}
    for label, segs in speaker_segs.items():
        n = len(segs)
        picks = [segs[n // 6], segs[n // 2], segs[(5 * n) // 6]]
        sample[label] = [
            f"{fmt_ts(pick.get('start'))} {str(pick.get('text') or '').strip()}"
            for pick in picks
        ]

    result = dict(existing)
    if non_interactive:
        for label in detected:
            if label not in result:
                result[label] = label
            print(f"  {label} -> {result[label]}")
        return result

    print("Enter a display name for each detected speaker label.")
    print("Press Enter to keep current/default value shown in brackets.")
    for label in detected:
        default = existing.get(label, label)
        if label in sample:
            print(f"\n  Examples:")
            for s in sample[label]:
                print(f"    {s}")
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
```

- [ ] **Step 2: Verify the file is importable**

Run: `cd /Users/aquarion/code/aquarion/diarize && poetry run python -c "from speakers import prompt_for_speakers, coalesce_segments, load_speaker_mapping; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add speakers.py
git commit -m "refactor: extract speakers.py from app.py"
```

---

## Task 4: Create `transcribe.py`

**Files:**
- Create: `transcribe.py`

- [ ] **Step 1: Create `transcribe.py`**

```python
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
```

- [ ] **Step 2: Verify the file is importable**

Run: `cd /Users/aquarion/code/aquarion/diarize && poetry run python -c "from transcribe import run_transcription_and_diarization, load_segments; print('ok')"`
Expected: `ok`

- [ ] **Step 3: Commit**

```bash
git add transcribe.py
git commit -m "refactor: extract transcribe.py from app.py"
```

---

## Task 5: Rewrite `app.py` as thin orchestration

**Files:**
- Modify: `app.py`

- [ ] **Step 1: Replace `app.py` with orchestration-only version**

```python
#!/usr/bin/env python3
"""
Pure Python diarization pipeline.

Flow:
1) Run WhisperX (or mlx-whisper) on a WAV file.
2) Detect all speaker labels.
3) Prompt the user to name each detected speaker.
4) Write markdown transcript locally and into an Obsidian vault.

Configuration is loaded from config.json (or --config).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path

from config import (
    AppConfig,
    _resolve_path,
    default_config_path,
    ensure_default_config,
    load_config,
    load_config_data,
    prompt_for_required_config,
    save_config_data,
)
from render import make_vault_target, render_markdown, terminal_link
from speakers import (
    coalesce_segments,
    guess_speakers_with_claude,
    load_speaker_mapping,
    prompt_for_speakers,
    save_speaker_mapping,
)
from transcribe import load_segments, run_transcription_and_diarization

HERE = Path(__file__).resolve().parent


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
    parser.add_argument(
        "--claude-guess",
        action="store_true",
        help="Ask the Claude CLI to guess speaker names from the transcript",
    )
    parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Non-interactive: accept all defaults without prompting",
    )
    parser.add_argument(
        "--vault-output",
        metavar="PATH",
        help="Override the vault destination path for this file (e.g. ~/Obsidian/Meetings/standup.md)",
    )
    parser.add_argument(
        "num_speakers",
        type=int,
        help="Number of speakers in the recording",
    )
    return parser.parse_args(argv[1:])


def _relaunch_with_lib_path(argv: list[str]) -> None:
    """Re-exec with library path set from the start so dlopen picks it up."""
    cfg_path = default_config_path()
    for i, arg in enumerate(argv[1:], 1):
        if arg == "--config" and i + 1 < len(argv):
            cfg_path = Path(argv[i + 1]).expanduser()
            break
        if arg.startswith("--config="):
            cfg_path = Path(arg.split("=", 1)[1]).expanduser()
            break
    try:
        data = json.loads(cfg_path.read_text())
    except Exception:
        return
    extra_lib_path = [str(p) for p in data.get("extra_lib_path", [])]
    if not extra_lib_path:
        return
    lib_var = "DYLD_LIBRARY_PATH" if sys.platform == "darwin" else "LD_LIBRARY_PATH"
    current_dirs = os.environ.get(lib_var, "").split(os.pathsep)
    if all(p in current_dirs for p in extra_lib_path):
        return
    os.environ[lib_var] = (
        os.pathsep.join(extra_lib_path) + os.pathsep + os.environ.get(lib_var, "")
    )
    os.execv(sys.executable, [sys.executable] + argv)


def main(argv: list[str]) -> int:
    _relaunch_with_lib_path(argv)
    args = parse_args(argv)

    cfg_path = _resolve_path(HERE, args.config)
    ensure_default_config(cfg_path)
    data = load_config_data(cfg_path)
    data = prompt_for_required_config(
        cfg_path,
        data,
        require_hf_token=not args.skip_whisperx,
        non_interactive=args.yes,
    )
    save_config_data(cfg_path, data)
    cfg = load_config(cfg_path)
    cfg = cfg._replace(num_speakers=args.num_speakers)

    if cfg.extra_path:
        os.environ["PATH"] = (
            os.pathsep.join(cfg.extra_path) + os.pathsep + os.environ.get("PATH", "")
        )
    wav_path = _resolve_path(HERE, args.wav)
    if not wav_path.exists():
        print(f"!! Input WAV not found: {wav_path}", file=sys.stderr)
        return 2

    base_out_dir = _resolve_path(HERE, cfg.output_dir)
    wav_stat = wav_path.stat()
    ctime = getattr(wav_stat, "st_birthtime", wav_stat.st_ctime)
    ctime_dt = dt.datetime.fromtimestamp(ctime)
    ctime_str = ctime_dt.strftime("%Y-%m-%d_%H-%M-%S")
    out_dir = base_out_dir / f"{wav_path.stem}_{ctime_str}"
    out_dir.mkdir(parents=True, exist_ok=True)
    speakers_path = out_dir / Path(cfg.speakers_file).name
    whisper_json = out_dir / f"{wav_path.stem}.json"

    try:
        if args.vault_output:
            vault_md = Path(args.vault_output).expanduser()
        else:
            vault_md = make_vault_target(cfg, wav_path.stem)
        vault_md.parent.mkdir(parents=True, exist_ok=True)
    except (KeyError, IndexError) as err:
        print(f"!! Invalid vault_filename_template: {err}", file=sys.stderr)
        return 2
    except OSError as err:
        print(f"!! Cannot create vault directory: {err}", file=sys.stderr)
        return 2

    if not args.skip_whisperx:
        run_transcription_and_diarization(wav_path, cfg, out_dir)
    else:
        print("==> Skipping WhisperX run (--skip-whisperx)")

    segments = load_segments(whisper_json)
    detected = sorted({str(seg.get("speaker") or "UNKNOWN") for seg in segments})

    existing_map = load_speaker_mapping(speakers_path)
    if args.claude_guess:
        guesses = guess_speakers_with_claude(detected, segments, ctime_dt)
        for label, name in guesses.items():
            if label not in existing_map:
                existing_map[label] = name
    updated_map = prompt_for_speakers(
        detected,
        existing_map,
        segments,
        out_dir / f"{wav_path.stem}.txt",
        non_interactive=args.yes,
    )
    save_speaker_mapping(speakers_path, updated_map)

    blocks = coalesce_segments(segments, updated_map)
    markdown = render_markdown(blocks, cfg.transcript_title, wav_path)

    local_md = out_dir / "transcript.md"
    local_md.parent.mkdir(parents=True, exist_ok=True)
    local_md.write_text(markdown)

    vault_md.write_text(markdown)

    print("\n==> Complete")
    print(f"    speaker map updated: {speakers_path}")
    print(f"    local transcript    : {terminal_link(local_md)}")
    print(f"    vault transcript    : {terminal_link(vault_md)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
```

- [ ] **Step 2: Verify `--help` works (smoke test)**

Run: `cd /Users/aquarion/code/aquarion/diarize && poetry run python app.py --help`
Expected: Help text printed, exit 0, no import errors.

- [ ] **Step 3: Commit**

```bash
git add app.py
git commit -m "refactor: reduce app.py to CLI orchestration only"
```
