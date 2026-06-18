# speakers.py
from __future__ import annotations

import datetime as dt
import json
import re
import subprocess
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


_CLAUDE_GUESS_CHAR_BUDGET = 2000
_CLAUDE_GUESS_TIMEOUT = 120


def guess_speakers_with_claude(
    detected: list[str], segments: list[dict[str, Any]], ctime: dt.datetime
) -> dict[str, str]:
    lines: list[str] = []
    chars = 0
    for seg in segments:
        label = str(seg.get("speaker") or "UNKNOWN")
        text = str(seg.get("text") or "").strip()
        if not text:
            continue
        line = f"{fmt_ts(seg.get('start'))} {label}: {text}"
        chars += len(line) + 1
        if chars > _CLAUDE_GUESS_CHAR_BUDGET:
            break
        lines.append(line)

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
            timeout=_CLAUDE_GUESS_TIMEOUT,
        )
    except FileNotFoundError as err:
        raise RuntimeError("'claude' not found in PATH") from err
    except subprocess.TimeoutExpired:
        print("!! Claude CLI timed out — skipping speaker name guesses.")
        return {}
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
            print("\n  Examples:")
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
