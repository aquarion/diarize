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
import dataclasses
import datetime as dt
import json
import os
import sys
from pathlib import Path

from media import MediaProbeError, extract_audio, has_video_stream
from render import make_vault_target, render_markdown, terminal_link
from speakers import (
    coalesce_segments,
    guess_speakers_with_claude,
    load_speaker_mapping,
    prompt_for_speakers,
    save_speaker_mapping,
)
from transcribe import load_segments, run_transcription_and_diarization

from config import (
    DEFAULTS,
    _resolve_path,
    coerce_config_value,
    default_config_path,
    ensure_default_config,
    load_config,
    load_config_data,
    mask_secret,
    prompt_for_required_config,
    save_config_data,
)

HERE = Path(__file__).resolve().parent


def _normalize_argv(argv: list[str]) -> list[str]:
    """Default to the 'transcribe' subcommand so `app.py <wav> <n>` (the
    original invocation, predating the 'config' subcommand) keeps working."""
    if len(argv) > 1 and argv[1] in ("transcribe", "config", "-h", "--help"):
        return argv
    return [argv[0], "transcribe"] + argv[1:]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run diarization and export an Obsidian markdown transcript."
    )
    subparsers = parser.add_subparsers(dest="command")

    transcribe_parser = subparsers.add_parser(
        "transcribe", help="Transcribe and diarize an audio or video file (default)"
    )
    transcribe_parser.add_argument(
        "wav",
        help="Path to input audio file, or a video file (audio is extracted"
        " automatically)",
    )
    transcribe_parser.add_argument(
        "num_speakers",
        type=int,
        help="Number of speakers in the recording",
    )
    transcribe_parser.add_argument(
        "--config",
        default=str(default_config_path()),
        help="Path to JSON config (default: OS user config location)",
    )
    transcribe_parser.add_argument(
        "--skip-whisperx",
        action="store_true",
        help="Skip WhisperX run and only relabel from existing JSON output",
    )
    transcribe_parser.add_argument(
        "--claude-guess",
        action="store_true",
        help="Ask the Claude CLI to guess speaker names from the transcript",
    )
    transcribe_parser.add_argument(
        "--yes",
        "-y",
        action="store_true",
        help="Non-interactive: accept all defaults without prompting",
    )
    transcribe_parser.add_argument(
        "--vault-output",
        metavar="PATH",
        help="Override the vault destination path for this file"
        " (e.g. ~/Obsidian/Meetings/standup.md)",
    )

    config_common = argparse.ArgumentParser(add_help=False)
    config_common.add_argument(
        "--config",
        default=str(default_config_path()),
        help="Path to JSON config (default: OS user config location)",
    )

    config_parser = subparsers.add_parser("config", help="View or edit configuration")
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser(
        "show",
        parents=[config_common],
        help="Print the effective config (secrets masked)",
    )
    config_sub.add_parser(
        "path", parents=[config_common], help="Print the config file path"
    )
    get_parser = config_sub.add_parser(
        "get", parents=[config_common], help="Print one config value"
    )
    get_parser.add_argument("key", help="Config key, e.g. model")
    set_parser = config_sub.add_parser(
        "set", parents=[config_common], help="Set one config value and save"
    )
    set_parser.add_argument("key", help="Config key, e.g. model")
    set_parser.add_argument("value", help="New value (comma-separated for list fields)")

    return parser.parse_args(_normalize_argv(argv)[1:])


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


def _config_show(args: argparse.Namespace) -> int:
    cfg_path = _resolve_path(HERE, args.config)
    data = load_config_data(cfg_path)
    masked = {key: mask_secret(key, value) for key, value in sorted(data.items())}
    print(f"Config file: {cfg_path}")
    print(json.dumps(masked, indent=2))
    return 0


def _config_path(args: argparse.Namespace) -> int:
    print(_resolve_path(HERE, args.config))
    return 0


def _config_get(args: argparse.Namespace) -> int:
    if args.key not in DEFAULTS:
        print(f"!! Unknown config key: {args.key}", file=sys.stderr)
        print(f"    Valid keys: {', '.join(sorted(DEFAULTS))}", file=sys.stderr)
        return 2
    data = load_config_data(_resolve_path(HERE, args.config))
    value = data.get(args.key, DEFAULTS[args.key])
    print(value if isinstance(value, str) else json.dumps(value))
    return 0


def _config_set(args: argparse.Namespace) -> int:
    if args.key not in DEFAULTS:
        print(f"!! Unknown config key: {args.key}", file=sys.stderr)
        print(f"    Valid keys: {', '.join(sorted(DEFAULTS))}", file=sys.stderr)
        return 2
    try:
        value = coerce_config_value(args.key, args.value)
    except ValueError as err:
        print(f"!! {err}", file=sys.stderr)
        return 2
    cfg_path = _resolve_path(HERE, args.config)
    ensure_default_config(cfg_path)
    data = load_config_data(cfg_path)
    data[args.key] = value
    save_config_data(cfg_path, data)
    print(f"==> Set {args.key} = {mask_secret(args.key, value)} in {cfg_path}")
    return 0


def run_config_command(args: argparse.Namespace) -> int:
    handlers = {
        "show": _config_show,
        "path": _config_path,
        "get": _config_get,
        "set": _config_set,
    }
    return handlers[args.config_command](args)


def run_transcribe(args: argparse.Namespace) -> int:
    cfg_path = _resolve_path(HERE, args.config)
    ensure_default_config(cfg_path)
    data = load_config_data(cfg_path)
    data = prompt_for_required_config(
        cfg_path,
        data,
        skip_transcription=args.skip_whisperx,
        non_interactive=args.yes,
    )
    save_config_data(cfg_path, data)
    cfg = load_config(cfg_path)
    cfg = dataclasses.replace(cfg, num_speakers=args.num_speakers)

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

    if not args.skip_whisperx:
        try:
            if has_video_stream(wav_path):
                print(f"==> Input has a video track; extracting audio: {wav_path}")
                wav_path = extract_audio(wav_path, out_dir)
                print(f"    extracted: {wav_path}")
        except MediaProbeError as err:
            print(f"!! {err}", file=sys.stderr)
            return 2

    try:
        if args.vault_output:
            vault_md = Path(args.vault_output).expanduser()
        else:
            vault_md = make_vault_target(cfg, f"{ctime_str}_{wav_path.stem}")
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


def main(argv: list[str]) -> int:
    _relaunch_with_lib_path(argv)
    args = parse_args(argv)

    if args.command == "config":
        return run_config_command(args)
    return run_transcribe(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
