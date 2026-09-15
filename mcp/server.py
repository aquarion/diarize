from __future__ import annotations

import json
import logging
import logging.handlers
import os
import platform
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
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


# --- Persistent job registry ---
#
# `jobs` (above) is in-memory only and forgotten on restart, which used to
# make a still-running (or just-finished) job indistinguishable from one
# that never existed. This registry persists the essentials - enough to
# tell a caller "yes, that job existed, here's what happened to it" even
# after the process that ran it is gone.

JOBS_FILE = LOG_DIR / "jobs.json"
_registry_lock = threading.Lock()

# Keep the on-disk registry bounded so it can't grow forever over a long
# server lifetime.
MAX_PERSISTED_JOBS = 200


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_registry() -> dict[str, dict]:
    try:
        data = json.loads(JOBS_FILE.read_text())
    except (OSError, ValueError):
        return {}
    # Defend against a corrupted/hand-edited file: valid JSON that isn't the
    # shape we expect (e.g. "[]", "null", or a record that isn't an object)
    # must not crash callers like _reconcile_registry_on_startup, which runs
    # at import time - that would take the whole server down.
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if isinstance(v, dict)}


def _write_registry(registry: dict[str, dict]) -> None:
    """Atomically replace the registry file so a crash mid-write can't leave
    it corrupted. Persistence is best-effort: a failure here is logged, not
    raised - it must never take down a job or the server."""
    try:
        JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = JOBS_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(registry, indent=2))
        os.replace(tmp, JOBS_FILE)
    except (OSError, TypeError) as e:
        # TypeError: a value in the registry wasn't JSON-serializable - a
        # bug elsewhere, but persistence failing must never take a job or
        # the server down over it.
        logger.warning("failed to persist job registry: %s", e)


def _prune_registry(registry: dict[str, dict]) -> None:
    """Drop the oldest *completed* entries beyond MAX_PERSISTED_JOBS. Never
    drops a "running" entry - losing track of an in-flight job is exactly
    the bug this registry exists to fix."""
    overflow = len(registry) - MAX_PERSISTED_JOBS
    if overflow <= 0:
        return
    completed = sorted(
        (r for r in registry.values() if r["status"] != "running"),
        key=lambda r: r["started_at"],
    )
    for record in completed[:overflow]:
        del registry[record["job_id"]]


def _record_job_started(
    job_id: str, *, backend: str, input_path: str, num_speakers: int, pid: int
) -> None:
    with _registry_lock:
        registry = _load_registry()
        registry[job_id] = {
            "job_id": job_id,
            "backend": backend,
            "input_path": input_path,
            "num_speakers": num_speakers,
            "pid": pid,
            "status": "running",
            "output_path": None,
            "error": None,
            "started_at": _now_iso(),
            "finished_at": None,
        }
        _prune_registry(registry)
        _write_registry(registry)


def _update_job_record(
    job_id: str, *, status: str, output_path: str | None, error: str | None
) -> bool:
    """Returns True if a record for job_id existed and was updated - i.e.
    it's now safe to rely on the registry alone for this job, since a
    caller (Job._finalize_and_persist) uses this to decide whether it can
    drop its own in-memory handle."""
    with _registry_lock:
        registry = _load_registry()
        record = registry.get(job_id)
        if record is None:
            return False  # e.g. registry file was lost/wiped after the job started
        record["status"] = status
        record["output_path"] = output_path
        record["error"] = error
        record["finished_at"] = _now_iso()
        _prune_registry(registry)
        _write_registry(registry)
        return True


def _reconcile_registry_on_startup() -> None:
    """A job recorded as "running" from a previous server process cannot
    actually still be tracked as running - that process's handle to the
    subprocess (and its stdout/stderr pipes) is gone. Mark these
    "interrupted" rather than leaving them to look perpetually in-progress,
    so a caller knows to check for output on disk or just re-run."""
    with _registry_lock:
        registry = _load_registry()
        changed = False
        for record in registry.values():
            if record["status"] == "running":
                record["status"] = "interrupted"
                record["error"] = (
                    "the MCP server restarted while this job was running; "
                    "its actual outcome is unknown - check the configured "
                    "output location, or re-run the transcription"
                )
                record["finished_at"] = _now_iso()
                changed = True
        if changed:
            _write_registry(registry)


_reconcile_registry_on_startup()


class BackendUnavailableError(Exception):
    """Raised by select_backend() with a precise reason no backend could be
    selected (not just a generic "no backend available")."""


def select_backend() -> tuple[str, list[str]]:
    """Return (backend_name, argv_prefix).

    Raises BackendUnavailableError if no backend can be used.
    """
    if platform.system() == "Darwin":
        swift_cli = REPO_ROOT / "swift" / ".build" / "release" / "diarize"
        if swift_cli.exists():
            return "swift", [str(swift_cli)]
    python_dir = REPO_ROOT / "python"
    app_py = python_dir / "app.py"
    if not app_py.exists():
        raise BackendUnavailableError(f"neither the Swift CLI nor {app_py} was found")
    uv_exe = shutil.which("uv")
    if uv_exe is None:
        raise BackendUnavailableError(f"found {app_py} but uv is not on PATH to run it")
    # `uv run` resolves/syncs python/.venv from pyproject.toml + uv.lock on
    # every invocation (self-healing - no stale or missing venv to silently
    # fall back from, unlike the old manual venv-path lookup).
    return "python", [uv_exe, "run", "--directory", str(python_dir), "app.py"]


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

# config get/set complete near-instantly (no model loading, no transcription)
# so these run synchronously rather than through the async Job/poll pattern.
CONFIG_COMMAND_TIMEOUT = 15.0

# os.killpg/os.getpgid are POSIX-only - absent on Windows, where the Python
# backend also ships. Checked once at import time rather than per-call.
_HAS_PROCESS_GROUP_KILL = hasattr(os, "killpg")


@dataclass
class Job:
    proc: subprocess.Popen
    backend: str
    job_id: str = ""
    stdout: str = field(default="", init=False)
    stderr: str = field(default="", init=False)
    last_message: str = field(default="", init=False)
    last_fraction: float | None = field(default=None, init=False)
    last_stage: str | None = field(default=None, init=False)
    collector_error: str = field(default="", init=False)
    _stdout_done: threading.Event = field(default_factory=threading.Event, init=False)
    _stderr_done: threading.Event = field(default_factory=threading.Event, init=False)
    _exit_seen_at: float | None = field(default=None, init=False)
    _error_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _stall_reported: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        threading.Thread(target=self._collect_stdout, daemon=True).start()
        threading.Thread(target=self._collect_stderr, daemon=True).start()
        # Persists this job's outcome to the on-disk registry as soon as it's
        # known, independent of whether any client ever calls get_transcript
        # again to observe it - otherwise a client that stops polling right
        # after a job finishes would leave it stuck at "running" in the
        # registry until the next restart marks it "interrupted".
        threading.Thread(target=self._finalize_and_persist, daemon=True).start()

    def _finalize_and_persist(self) -> None:
        # Mirrors get_transcript's own is_complete()/stalled() check rather
        # than just waiting on the done Events: a stalled job (collectors
        # stuck, process already exited) never sets those Events, and
        # without this loop this thread would block forever, leaving the
        # registry stuck at "running" instead of picking up the "failed"
        # outcome stalled() detection makes available via collector_error.
        while not self.is_complete() and not self.stalled():
            # Wait on whichever collector hasn't finished yet - waiting on
            # an already-set Event returns immediately, so always waiting on
            # _stdout_done specifically would busy-spin for however long
            # stderr outlives stdout.
            pending = self._stderr_done if self._stdout_done.is_set() else self._stdout_done
            pending.wait(timeout=0.5)
        outcome = _resolve_job_outcome(self)
        persisted = _update_job_record(
            self.job_id,
            status=outcome["status"],
            output_path=outcome.get("output_path"),
            error=outcome.get("error"),
        )
        # Everything needed to answer a later get_transcript/list_jobs call
        # for this job now lives in the (tiny) persisted record - drop the
        # live handle so a long-running server doesn't retain every job's
        # full stdout/stderr in memory forever. Only once we know the
        # registry actually has it, though - a Job with no registry entry
        # (e.g. one built directly rather than via transcribe()) would
        # otherwise become unreachable by any get_transcript call.
        if persisted:
            jobs.pop(self.job_id, None)

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
        blocked reading forever. Kill it so both collectors unblock.

        Kills the whole process tree, not just proc.pid: on the python
        backend proc is the `uv run` wrapper, so killing it alone would
        orphan the real worker it spawned. On POSIX this is proc's process
        group (spawned with start_new_session=True); Windows has no
        equivalent, so `taskkill /T` is used instead to walk the same
        parent-child tree. Falls back to proc.kill() if that can't be
        resolved (e.g. proc already reaped)."""
        try:
            if self.proc.poll() is None:
                if _HAS_PROCESS_GROUP_KILL:
                    try:
                        os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
                    except ProcessLookupError:
                        pass  # already exited between poll() and here
                else:
                    result = subprocess.run(
                        ["taskkill", "/T", "/F", "/PID", str(self.proc.pid)],
                        capture_output=True,
                    )
                    if result.returncode != 0:
                        self.proc.kill()  # e.g. proc already exited
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
                    # Every "==>" line is a stage transition in both CLIs'
                    # output convention, so any fraction/stage from the
                    # previous stage no longer applies (e.g. don't keep
                    # reporting 98% "transcribing" once diarization starts).
                    self.last_fraction = None
                    self.last_stage = None
                elif stripped.startswith("progress:"):
                    # "progress:<fraction 0-1>:<stage>", emitted by both backends
                    # during the (long) transcription stage. Malformed or
                    # out-of-contract lines are ignored rather than failing
                    # the job or handing a client nan/inf/out-of-range JSON.
                    parts = stripped.split(":", 2)
                    if len(parts) == 3:
                        try:
                            fraction = float(parts[1])
                        except ValueError:
                            pass
                        else:
                            # Comparisons against float("nan") are always
                            # False, so this range check also rejects nan
                            # (and +/-inf) along with plain out-of-range
                            # values - no separate isnan/isinf check needed.
                            if 0.0 <= fraction <= 1.0:
                                self.last_fraction = fraction
                                self.last_stage = parts[2]
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


def _reap_caffeinate(watcher: subprocess.Popen, pid: int) -> None:
    """Reap the caffeinate watcher so it can't linger as a zombie after it
    self-exits (which happens once the backend pid dies). Runs on a daemon
    thread; route any failure to the log file rather than letting a bare
    thread target dump a traceback to an unseen stderr."""
    try:
        watcher.wait()
    except OSError as e:
        logger.warning("caffeinate watcher for pid %s exited abnormally: %s", pid, e)


@mcp.tool()
def transcribe(file_path: str, num_speakers: int) -> dict:
    """Start a transcription and diarization job.

    Returns {"job_id": "<uuid>", "backend": "swift"|"python"} on success,
    or {"error": "<message>"} on failure.
    """
    p = Path(file_path).expanduser()
    if not p.exists():
        return {"error": f"file not found: {file_path}"}
    try:
        backend_name, cmd = select_backend()
    except BackendUnavailableError as e:
        logger.error("no backend available for %s: %s", p, e)
        return {"error": f"no backend available: {e}"}
    proc = subprocess.Popen(
        cmd + [str(p), str(num_speakers), "--yes"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        cwd=str(REPO_ROOT),
        # CPython fully buffers stdout when it isn't a tty (i.e. when piped,
        # as here), which would otherwise hold back every "==> ..." progress
        # line until the process exits - defeating live streaming. Set via
        # env rather than a `python -u` flag since `uv run` owns the actual
        # interpreter invocation now. Harmless no-op for the Swift backend.
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
        # Give the backend its own process group so we can signal the whole
        # tree on cleanup. The python backend is spawned as `uv run app.py`,
        # so proc.pid is the uv wrapper - a SIGKILL to it alone can't be
        # forwarded (SIGKILL is uncatchable) and would orphan the real worker.
        # Killing the group reaches that worker (and any swift helper procs).
        start_new_session=True,
    )
    # Transcription jobs can run long enough that macOS puts the machine to
    # sleep mid-job. Rather than wrapping proc itself (which would make
    # Job.proc.kill() target caffeinate instead of the real backend and
    # leave it running orphaned), spawn caffeinate as an independent watcher
    # tied to the backend's pid - it holds the assertion until that pid
    # exits, however the job ends.
    caffeinate_exe = (
        shutil.which("caffeinate") if platform.system() == "Darwin" else None
    )
    if caffeinate_exe is not None:
        # Best-effort only: if the watcher fails to spawn (e.g. OSError even
        # though shutil.which found the binary), don't abort the backend job
        # that already started above - just proceed without sleep prevention.
        try:
            caffeinate_proc = subprocess.Popen(
                [caffeinate_exe, "-i", "-w", str(proc.pid)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL,
            )
            threading.Thread(
                target=_reap_caffeinate,
                args=(caffeinate_proc, proc.pid),
                daemon=True,
            ).start()
        except OSError as e:
            logger.warning(
                "could not spawn caffeinate watcher for pid %s: %s"
                "; job will run without sleep prevention",
                proc.pid,
                e,
            )
    job_id = str(uuid.uuid4())
    # Record before constructing Job: Job.__post_init__ spawns the finalizer
    # thread immediately, which would find no record to update if the job
    # somehow finished and that thread ran before this record existed.
    _record_job_started(
        job_id,
        backend=backend_name,
        input_path=str(p),
        num_speakers=num_speakers,
        pid=proc.pid,
    )
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


def _resolve_job_outcome(job: Job) -> dict:
    """Determine whether a complete job succeeded or failed, and its output
    path if so. Shared between get_transcript's polling and the background
    finalizer that persists the outcome to the on-disk registry as soon as
    it's known, independent of whether anyone ever polls."""
    job_id = job.job_id
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
    return {"status": "done", "output_path": path}


@mcp.tool()
def get_transcript(job_id: str) -> dict:
    """Poll a transcription job.

    Returns:
      {"status": "running"} while the job is in progress. May also include
      "message" (last human-readable stage description) and, once the
      transcription stage reports fine-grained progress, "fraction" (0-1)
      and "stage".
      {"status": "done", "transcript": "<markdown>", "output_path": "<path>"}
      on success.
      {"status": "failed", "error": "<message>"} on failure.
      {"status": "interrupted", "error": "<message>"} if the MCP server
      restarted while this job was running - its actual outcome is unknown.
      {"status": "unknown", "error": "no such job_id"} if this job_id was
      never seen, or is old enough to have been pruned from the registry
      (distinct from "failed": nothing to act on, no compute to
      retry-avoid).
    """
    job = jobs.get(job_id)
    if job is None:
        return _get_transcript_from_registry(job_id)
    if not job.is_complete() and not job.stalled():
        result: dict = {"status": "running"}
        if job.last_message:
            result["message"] = job.last_message
        if job.last_fraction is not None:
            result["fraction"] = job.last_fraction
            result["stage"] = job.last_stage
        return result
    outcome = _resolve_job_outcome(job)
    if outcome["status"] != "done":
        return outcome
    return _read_transcript_result(outcome["output_path"], job_id)


def _read_transcript_result(path: str, job_id: str) -> dict:
    """Shared tail of a successful "done" resolution, whether the job was
    resolved live or read back from the persisted registry."""
    try:
        transcript_text = Path(path).read_text()
    except Exception as e:
        logger.error("job %s: failed to read transcript at %s: %s", job_id, path, e)
        return {"status": "failed", "error": str(e)}
    logger.info("job %s done, output_path=%s", job_id, path)
    return {"status": "done", "transcript": transcript_text, "output_path": path}


def _get_transcript_from_registry(job_id: str) -> dict:
    """get_transcript's fallback for a job_id no longer (or never) tracked
    live - e.g. because the server restarted since it was started. Consults
    the persisted registry so a restart can't turn "this job existed" into
    "unknown job_id"."""
    record = _load_registry().get(job_id)
    if record is None:
        return {"status": "unknown", "error": "no such job_id"}
    if record["status"] == "done":
        return _read_transcript_result(record["output_path"], job_id)
    if record["status"] == "running":
        # Shouldn't normally happen - _reconcile_registry_on_startup converts
        # these to "interrupted" before the server starts serving requests -
        # but fail safe rather than claim a job with no live handle is running.
        return {
            "status": "interrupted",
            "error": "job is no longer being tracked; check the configured "
            "output location, or re-run the transcription",
        }
    return {"status": record["status"], "error": record.get("error") or "unknown error"}


@mcp.tool()
def list_jobs(limit: int = 20) -> dict:
    """List recent transcription jobs, most recently started first - including
    ones from before a server restart, which get_transcript alone can't see.

    Returns {"jobs": [{"job_id", "backend", "input_path", "num_speakers",
    "status", "output_path", "error", "started_at", "finished_at"}, ...]}.
    A job still tracked live also carries "message" and, once transcription
    reports fine-grained progress, "fraction"/"stage" - see get_transcript.
    """
    registry = _load_registry()
    # Snapshot via list(): transcribe() can insert into `jobs` from another
    # thread mid-request, and iterating the live dict directly can raise
    # "dictionary changed size during iteration".
    for job_id, job in list(jobs.items()):
        record = dict(
            registry.get(
                job_id,
                {
                    "job_id": job_id,
                    "backend": job.backend,
                    "input_path": None,
                    "num_speakers": None,
                    "pid": job.proc.pid,
                    "status": "running",
                    "output_path": None,
                    "error": None,
                    "started_at": None,
                    "finished_at": None,
                },
            )
        )
        if not job.is_complete() and not job.stalled():
            record["status"] = "running"
            if job.last_message:
                record["message"] = job.last_message
            if job.last_fraction is not None:
                record["fraction"] = job.last_fraction
                record["stage"] = job.last_stage
        else:
            # The job finished, but the background finalizer thread that
            # persists this to disk runs asynchronously - compute the
            # outcome directly rather than risk showing a stale "running"
            # snapshot from before it's had a chance to write.
            outcome = _resolve_job_outcome(job)
            record["status"] = outcome["status"]
            record["output_path"] = outcome.get("output_path", record.get("output_path"))
            record["error"] = outcome.get("error", record.get("error"))
            record["finished_at"] = record.get("finished_at") or _now_iso()
        registry[job_id] = record
    entries = sorted(
        registry.values(), key=lambda r: r.get("started_at") or "", reverse=True
    )
    return {"jobs": entries[: max(limit, 0)]}


@mcp.tool()
def get_config(key: str) -> dict:
    """Get a diarize config value (e.g. "vault_path", "model", "language").

    Returns {"key": "<key>", "value": "<value>"} on success,
    or {"error": "<message>"} on failure (e.g. unknown key - the error
    lists the valid keys).
    """
    try:
        backend_name, cmd = select_backend()
    except BackendUnavailableError as e:
        logger.error("no backend available for get_config(%s): %s", key, e)
        return {"error": f"no backend available: {e}"}
    try:
        result = subprocess.run(
            cmd + ["config", "get", key],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            cwd=str(REPO_ROOT),
            timeout=CONFIG_COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        logger.error("get_config(%s) timed out (backend=%s)", key, backend_name)
        return {"error": f"config get timed out after {CONFIG_COMMAND_TIMEOUT}s"}
    if result.returncode != 0:
        logger.error(
            "get_config(%s) failed (backend=%s): %s", key, backend_name, result.stderr
        )
        return {"error": result.stderr.strip() or f"exit code {result.returncode}"}
    return {"key": key, "value": result.stdout.strip()}


@mcp.tool()
def set_config(key: str, value: str) -> dict:
    """Set a diarize config value and save it.

    List fields (e.g. "extra_path") take comma-separated values.
    Returns {"status": "ok", "message": "<confirmation>"} on success,
    or {"error": "<message>"} on failure (e.g. unknown key, wrong type -
    the error explains which).
    """
    try:
        backend_name, cmd = select_backend()
    except BackendUnavailableError as e:
        logger.error("no backend available for set_config(%s): %s", key, e)
        return {"error": f"no backend available: {e}"}
    try:
        result = subprocess.run(
            cmd + ["config", "set", key, value],
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            cwd=str(REPO_ROOT),
            timeout=CONFIG_COMMAND_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        logger.error("set_config(%s) timed out (backend=%s)", key, backend_name)
        return {"error": f"config set timed out after {CONFIG_COMMAND_TIMEOUT}s"}
    if result.returncode != 0:
        logger.error(
            "set_config(%s) failed (backend=%s): %s", key, backend_name, result.stderr
        )
        return {"error": result.stderr.strip() or f"exit code {result.returncode}"}
    logger.info("set_config(%s) ok (backend=%s)", key, backend_name)
    return {"status": "ok", "message": result.stdout.strip()}


if __name__ == "__main__":
    mcp.run()
