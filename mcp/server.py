from __future__ import annotations

import platform
import re
import sys
from pathlib import Path
from urllib.parse import unquote

from mcp.server.fastmcp import FastMCP

REPO_ROOT = Path(__file__).parent.parent
mcp = FastMCP("diarize")
jobs: dict[str, object] = {}  # value type completed in Task 3


def select_backend() -> tuple[str, list[str]] | None:
    """Return (backend_name, argv_prefix) or None if no backend is available."""
    if platform.system() == "Darwin":
        swift_cli = REPO_ROOT / "swift" / ".build" / "release" / "diarize"
        if swift_cli.exists():
            return "swift", [str(swift_cli)]
    app_py = REPO_ROOT / "python" / "app.py"
    if app_py.exists():
        venv_py = REPO_ROOT / "python" / ".venv" / "bin" / "python"
        python_exe = str(venv_py) if venv_py.exists() else sys.executable
        return "python", [python_exe, str(app_py)]
    return None


def parse_transcript_path(stdout: str, backend: str) -> str | None:
    """Extract the absolute local transcript path from CLI stdout."""
    if backend == "python":
        # Python render.py wraps the path in an OSC 8 hyperlink:
        # ESC]8;;file:///abs/path ESC\ label ESC]8;; ESC\
        for line in stdout.split("\n"):
            if "local transcript" in line:
                m = re.search(r"\x1b\]8;;file://([^\x1b]+)\x1b\\", line)
                if m:
                    return unquote(m.group(1))
    else:
        # Swift prints plain text: "    local       : /abs/path"
        for line in stdout.splitlines():
            s = line.strip()
            if s.startswith("local") and ":" in s:
                path = s.split(":", 1)[1].strip()
                if path:
                    return path
    return None


if __name__ == "__main__":
    mcp.run()
