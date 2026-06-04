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
