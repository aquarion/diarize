from __future__ import annotations

import logging
import logging.handlers
import os
import platform
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import unquote

from platformdirs import user_log_dir

from mcp.server.fastmcp import FastMCP

REPO_ROOT = Path(__file__).parent.parent
mcp = FastMCP("diarize")
jobs: dict[str, Job] = {}

LOG_DIR = Path(os.environ.get("DIARIZE_LOG_DIR", user_log_dir("diarize")))
LOG_FILE = LOG_DIR / "mcp.log"
logger = logging.getLogger("diarize.mcp")
logger.setLevel(logging.INFO)
if not logger.handlers:
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        _handler: logging.Handler = logging.handlers.RotatingFileHandler(
            LOG_FILE, maxBytes=5_000_000, backupCount=3
        )
        _handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        )
    except OSError:
        # Diagnostic logging must never take down the server - fall back to
        # discarding log records if the log directory isn't writable.
        _handler = logging.NullHandler()
    logger.addHandler(_handler)


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
        for line in stdout.splitlines():
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


# If the child process has exited but the collector threads haven't
# reported completion within this many seconds, something is wrong with
# the collectors themselves (e.g. a bug reintroducing an unhandled
# exception) - surface a failure instead of hanging on "running" forever.
COLLECTOR_STALL_TIMEOUT = 10.0


@dataclass
class Job:
    proc: subprocess.Popen
    backend: str
    job_id: str = ""
    stdout: str = field(default="", init=False)
    stderr: str = field(default="", init=False)
    last_message: str = field(default="", init=False)
    collector_error: str = field(default="", init=False)
    _stdout_done: threading.Event = field(default_factory=threading.Event, init=False)
    _stderr_done: threading.Event = field(default_factory=threading.Event, init=False)
    _exit_seen_at: float | None = field(default=None, init=False)
    _error_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _stall_reported: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        threading.Thread(target=self._collect_stdout, daemon=True).start()
        threading.Thread(target=self._collect_stderr, daemon=True).start()

    def _record_collector_error(self, message: str) -> None:
        with self._error_lock:
            self.collector_error = (
                f"{self.collector_error}; {message}"
                if self.collector_error
                else message
            )

    def _kill_after_collector_error(self) -> None:
        """Best-effort: if the child is still alive, one collector dying
        means nobody is draining its pipe anymore, which can leave the
        child blocked on a full pipe write and the sibling collector
        blocked reading forever. Kill it so both collectors unblock."""
        try:
            if self.proc.poll() is None:
                self.proc.kill()
            self.proc.wait(timeout=5)
        except Exception:
            logger.exception(
                "failed to kill/reap job %s after collector error", self.job_id
            )

    def _collect_stdout(self) -> None:
        try:
            assert self.proc.stdout is not None
            for raw in self.proc.stdout:
                line = raw.decode(errors="replace")
                self.stdout += line
                stripped = line.strip()
                if stripped.startswith("==>"):
                    self.last_message = stripped[3:].strip()
            self.proc.stdout.close()
            self.proc.wait()
        except Exception as e:
            self._record_collector_error(f"stdout collector failed: {e!r}")
            logger.exception(
                "stdout collector failed for job %s (backend=%s)",
                self.job_id,
                self.backend,
            )
            self._kill_after_collector_error()
        finally:
            self._stdout_done.set()

    def _collect_stderr(self) -> None:
        try:
            assert self.proc.stderr is not None
            self.stderr = self.proc.stderr.read().decode(errors="replace")
            self.proc.stderr.close()
        except Exception as e:
            self._record_collector_error(f"stderr collector failed: {e!r}")
            logger.exception(
                "stderr collector failed for job %s (backend=%s)",
                self.job_id,
                self.backend,
            )
            self._kill_after_collector_error()
        finally:
            self._stderr_done.set()

    def is_complete(self) -> bool:
        return self._stdout_done.is_set() and self._stderr_done.is_set()

    def stalled(self) -> bool:
        """True if the process has already exited but the collector
        threads have failed to report completion within the timeout -
        a sign the collectors themselves are stuck or broken. Once
        detected, latches a collector_error so later polls stay
        consistent even if the collectors eventually do finish."""
        if self.is_complete():
            return False
        if self.proc.poll() is None:
            self._exit_seen_at = None
            return False
        if self._exit_seen_at is None:
            self._exit_seen_at = time.monotonic()
            return False
        if time.monotonic() - self._exit_seen_at <= COLLECTOR_STALL_TIMEOUT:
            return False
        if not self._stall_reported:
            self._stall_reported = True
            self._record_collector_error(
                f"process exited (code {self.proc.returncode}) but output "
                "collection did not finish within timeout - possible internal bug"
            )
        return True


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
        logger.error("no backend available for %s", p)
        return {
            "error": "no backend available (Swift CLI not built, Python CLI not found)"
        }
    backend_name, cmd = backend_info
    proc = subprocess.Popen(
        cmd + [str(p), str(num_speakers), "--yes"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        cwd=str(REPO_ROOT),
    )
    job_id = str(uuid.uuid4())
    jobs[job_id] = Job(proc=proc, backend=backend_name, job_id=job_id)
    logger.info(
        "started job %s (backend=%s, pid=%s, file=%s, num_speakers=%s)",
        job_id,
        backend_name,
        proc.pid,
        p,
        num_speakers,
    )
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
    if not job.is_complete() and not job.stalled():
        result: dict = {"status": "running"}
        if job.last_message:
            result["message"] = job.last_message
        return result
    if job.collector_error:
        logger.error("job %s failed: %s", job_id, job.collector_error)
        return {
            "status": "failed",
            "error": (
                f"internal error collecting job output: {job.collector_error} "
                f"(see {LOG_FILE} for details)"
            ),
        }
    if job.proc.returncode != 0:
        logger.error(
            "job %s failed with exit code %s\nstderr:\n%s",
            job_id,
            job.proc.returncode,
            job.stderr,
        )
        return {
            "status": "failed",
            "error": job.stderr or f"exit code {job.proc.returncode}",
        }
    path = parse_transcript_path(job.stdout, job.backend)
    if path is None:
        logger.error(
            "job %s: could not find transcript path in stdout:\n%s", job_id, job.stdout
        )
        return {"status": "failed", "error": "could not find transcript path in output"}
    try:
        transcript_text = Path(path).read_text()
    except Exception as e:
        logger.error("job %s: failed to read transcript at %s: %s", job_id, path, e)
        return {"status": "failed", "error": str(e)}
    logger.info("job %s done, output_path=%s", job_id, path)
    return {"status": "done", "transcript": transcript_text, "output_path": path}


if __name__ == "__main__":
    mcp.run()
