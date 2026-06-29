from __future__ import annotations

import platform
import re
import subprocess
import sys
import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from mcp.server.fastmcp import FastMCP

REPO_ROOT = Path(__file__).parent.parent
mcp = FastMCP("diarize")
jobs: dict[str, Job] = {}


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
        for line in stdout.split("\n"):
            if "local transcript" in line:
                m = re.search(r"\x1b\]8;;file://([^\x1b]+)\x1b\\", line)
                if m:
                    return unquote(m.group(1))
    else:
        for line in stdout.splitlines():
            s = line.strip()
            if s.startswith("local") and ":" in s:
                path = s.split(":", 1)[1].strip()
                if path:
                    return path
    return None


@dataclass
class Job:
    proc: subprocess.Popen
    backend: str
    stdout: str = field(default="", init=False)
    stderr: str = field(default="", init=False)
    _done: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        t = threading.Thread(target=self._collect, daemon=True)
        t.start()

    def _collect(self) -> None:
        out, err = self.proc.communicate()
        self.stdout = out.decode(errors="replace")
        self.stderr = err.decode(errors="replace")
        self._done = True

    def is_complete(self) -> bool:
        return self._done


@mcp.tool()
def transcribe(file_path: str, num_speakers: int) -> dict:
    """Start a transcription and diarization job.

    Returns {"job_id": "<uuid>", "backend": "swift"|"python"} on success,
    or {"error": "<message>"} on failure.
    """
    p = Path(file_path).expanduser()
    if not p.exists():
        return {"error": f"file not found: {file_path}"}
    backend_info = select_backend()
    if backend_info is None:
        return {
            "error": "no backend available (Swift CLI not built, Python CLI not found)"
        }
    backend_name, cmd = backend_info
    proc = subprocess.Popen(
        cmd + [str(p), str(num_speakers), "--yes"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    job_id = str(uuid.uuid4())
    jobs[job_id] = Job(proc=proc, backend=backend_name)
    return {"job_id": job_id, "backend": backend_name}


@mcp.tool()
def get_transcript(job_id: str) -> dict:
    """Poll a transcription job.

    Returns:
      {"status": "running"} while the job is in progress.
      {"status": "done", "transcript": "<markdown>", "output_path": "<path>"}
      on success.
      {"status": "failed", "error": "<message>"} on failure or unknown job_id.
    """
    job = jobs.get(job_id)
    if job is None:
        return {"status": "failed", "error": "unknown job_id"}
    if not job.is_complete():
        return {"status": "running"}
    if job.proc.returncode != 0:
        return {
            "status": "failed",
            "error": job.stderr or f"exit code {job.proc.returncode}",
        }
    path = parse_transcript_path(job.stdout, job.backend)
    if path is None:
        return {"status": "failed", "error": "could not find transcript path in output"}
    try:
        return {
            "status": "done",
            "transcript": Path(path).read_text(),
            "output_path": path,
        }
    except Exception as e:
        return {"status": "failed", "error": str(e)}


if __name__ == "__main__":
    mcp.run()
