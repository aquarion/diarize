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

from mcp.server.mcpserver import MCPServer

REPO_ROOT = Path(__file__).parent.parent
mcp = MCPServer("diarize")
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
# Reentrant: transcribe() holds this across both writing a job's initial
# registry record and inserting its live handle into `jobs` (see below), so
# that a job's finalizer thread - which also takes this lock, from a
# different thread, while handling a job that finishes fast enough to race
# its own construction - can still acquire it (blocking briefly) rather than
# deadlocking against the same thread already holding it further up the
# call stack.
_registry_lock = threading.RLock()

# Keep the on-disk registry bounded so it can't grow forever over a long
# server lifetime.
MAX_PERSISTED_JOBS = 200


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# Every field a well-formed record has - anything missing one of these would
# raise KeyError the moment _reconcile_registry_on_startup, _prune_registry,
# list_jobs, etc. index into it.
_RECORD_FIELDS = {
    "job_id",
    "backend",
    "input_path",
    "num_speakers",
    "pid",
    "status",
    "output_path",
    "error",
    "started_at",
    "finished_at",
}


# A status other than one of these is either a bug or hand-edited
# corruption; _reconcile_registry_on_startup and _prune_registry both
# special-case "running" specifically, so anything else must be one of the
# statuses this module actually assigns.
_VALID_STATUSES = {"running", "done", "failed", "interrupted"}


def _is_valid_record(key: str, value: object) -> bool:
    if not (
        isinstance(value, dict)
        and _RECORD_FIELDS.issubset(value)
        and value.get("job_id") == key
        # isinstance check first: `x in a_set` hashes x, and a list/dict
        # value for "status" would raise TypeError rather than just fail
        # the membership test - defeating the whole point of validating
        # before this reaches _reconcile_registry_on_startup at import time.
        and isinstance(value.get("status"), str)
        and value.get("status") in _VALID_STATUSES
        and isinstance(value.get("backend"), str)
        and isinstance(value.get("input_path"), str)
        and isinstance(value.get("num_speakers"), int)
        and isinstance(value.get("pid"), int)
        # started_at is always set by the time a record is written; unlike
        # output_path/error/finished_at it's never legitimately None.
        and isinstance(value.get("started_at"), str)
    ):
        return False
    # output_path/error/finished_at are None until the job completes, and
    # strings afterward - list_jobs's sort key and _prune_registry both
    # index started_at (checked above) and would otherwise raise comparing
    # incompatible types (e.g. a hand-edited int) against real records.
    return all(
        value.get(f) is None or isinstance(value.get(f), str)
        for f in ("output_path", "error", "finished_at")
    )


class _RegistryUnreadable(Exception):
    """Raised by _load_registry() for a read failure that must not be
    treated as an empty registry, unlike FileNotFoundError (see below): a
    write-path caller that mistook this for "no jobs yet" would then
    atomically replace the file with just its own new/updated record,
    silently erasing every other persisted job. Callers that only read
    (e.g. get_transcript's registry fallback) may still degrade to
    reporting nothing found; callers that write must catch this and skip
    the write instead."""


def _load_registry() -> dict[str, dict]:
    try:
        data = json.loads(JOBS_FILE.read_text())
    except FileNotFoundError:
        # Expected and silent: no job has ever started yet.
        return {}
    except (OSError, ValueError) as e:
        # A genuinely corrupted file or a transient I/O error (permissions,
        # a disk/NFS hiccup) - as opposed to FileNotFoundError, which just
        # means no job has started yet.
        logger.error("failed to read job registry at %s: %s", JOBS_FILE, e)
        raise _RegistryUnreadable(str(e)) from e
    # Defend against a corrupted/hand-edited file: valid JSON that isn't the
    # shape we expect (e.g. "[]", "null", a record that isn't an object, or
    # one missing fields other code indexes directly) must not crash callers
    # like _reconcile_registry_on_startup, which runs at server startup -
    # that would prevent the server from starting at all.
    if not isinstance(data, dict):
        return {}
    return {k: v for k, v in data.items() if _is_valid_record(k, v)}


def _write_registry(registry: dict[str, dict]) -> bool:
    """Atomically replace the registry file so a crash mid-write can't leave
    it corrupted. Persistence is best-effort - a failure here is logged, not
    raised, since it must never take down a job or the server - but the
    result is still reported so a caller (_update_job_record) can tell
    whether the update it just made is actually durable."""
    try:
        JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
        # Unique per call, not a fixed "jobs.json.tmp": two processes (e.g.
        # two MCP clients) writing at once would otherwise both target the
        # same temp path and torn-write into each other's file before either
        # os.replace() runs.
        tmp_name = f"{JOBS_FILE.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
        tmp = JOBS_FILE.with_name(tmp_name)
        try:
            tmp.write_text(json.dumps(registry, indent=2))
            os.replace(tmp, JOBS_FILE)
            return True
        finally:
            # A leftover from a failed write (disk full, a held file handle
            # on Windows) is now a uniquely-named file, not a fixed name the
            # next call overwrites - without this it would accumulate
            # forever instead of staying capped at one. missing_ok=True
            # makes this a no-op on the success path, where os.replace()
            # already consumed tmp.
            tmp.unlink(missing_ok=True)
    except (OSError, TypeError) as e:
        # TypeError: a value in the registry wasn't JSON-serializable - a
        # bug elsewhere, but persistence failing must never take a job or
        # the server down over it.
        logger.warning("failed to persist job registry: %s", e)
        return False


# job_ids currently between "just persisted a terminal outcome" and "live
# handle evicted" (see _persist_terminal_outcome) - always accessed under
# _registry_lock. A *different* job's _update_job_record call in that window
# must not prune one of these: its own `protect` argument only shields
# itself, and once its write lands and returns True, the caller commits to
# evicting the live Job on the strength of that promise. Without also
# excluding this set, another finalizer completing at the same moment could
# prune the first job's now-unprotected record before it gets a chance to
# act on that promise, leaving the job in neither `jobs` nor the registry.
_pending_eviction: set[str] = set()


def _prune_registry(registry: dict[str, dict], *, protect: str | None = None) -> None:
    """Drop the oldest *completed* entries beyond MAX_PERSISTED_JOBS. Never
    drops a "running" entry - losing track of an in-flight job is exactly
    the bug this registry exists to fix - nor `protect` nor anything in
    _pending_eviction (see above)."""
    overflow = len(registry) - MAX_PERSISTED_JOBS
    if overflow <= 0:
        return
    completed = sorted(
        (
            r
            for r in registry.values()
            if r["status"] != "running"
            and r["job_id"] != protect
            and r["job_id"] not in _pending_eviction
        ),
        key=lambda r: r["started_at"],
    )
    for record in completed[:overflow]:
        del registry[record["job_id"]]


def _record_job_started(
    job_id: str,
    *,
    backend: str,
    input_path: str,
    num_speakers: int,
    pid: int,
    started_at: str | None = None,
) -> bool:
    """Writes this job's initial "running" record in a single attempt and
    reports whether it actually reached disk.

    Deliberately not retried in here: transcribe() (the only real caller)
    drives its own retry loop and only holds _registry_lock while an
    individual attempt is in flight, sleeping between attempts with the
    lock released so a transient failure for one job's registration
    doesn't stall every other job's registry access (get_transcript,
    list_jobs, other finalizers) for the whole retry budget."""
    with _registry_lock:
        try:
            registry = _load_registry()
        except _RegistryUnreadable:
            # Skip the write rather than risk building it on top of a
            # registry _load_registry couldn't actually read - see
            # _RegistryUnreadable. Reported as a failed attempt, same as a
            # failed write, so transcribe()'s own retry loop naturally
            # retries it.
            return False
        existing = registry.get(job_id)
        if existing is not None and existing["status"] != "running":
            # job_id is a freshly minted uuid used for the first time by
            # this call's own job, so a terminal record already existing
            # for it can only mean that job's own finalizer thread (started
            # the moment transcribe() constructed it, before this retry
            # loop ever got a chance to run) raced ahead and persisted its
            # outcome already - e.g. a near-instantly completing job.
            # Report success without touching it: writing "running" here
            # would clobber an already-correct terminal record right back
            # to looking in-progress.
            return True
        registry[job_id] = {
            "job_id": job_id,
            "backend": backend,
            "input_path": input_path,
            "num_speakers": num_speakers,
            "pid": pid,
            "status": "running",
            "output_path": None,
            "error": None,
            "started_at": started_at or _now_iso(),
            "finished_at": None,
        }
        _prune_registry(registry)
        return _write_registry(registry)


def _update_job_record(
    job_id: str,
    *,
    status: str,
    output_path: str | None,
    error: str | None,
    fallback_start: dict | None = None,
) -> bool:
    """Returns True only if a record for job_id exists and the update
    actually reached disk - i.e. it's now safe to rely on the registry alone
    for this job, since a caller (Job._finalize_and_persist) uses this to
    decide whether it can drop its own in-memory handle.

    If no record exists yet - e.g. _record_job_started's own initial write
    failed - and `fallback_start` (backend/input_path/num_speakers/pid) is
    given, one is created here instead of giving up, so a transient failure
    on that first write doesn't strand the terminal outcome forever no
    matter how many times the caller retries: every retry would otherwise
    keep finding nothing to update."""
    with _registry_lock:
        try:
            registry = _load_registry()
        except _RegistryUnreadable:
            # Same reasoning as _record_job_started: don't build a write on
            # top of a registry that couldn't actually be read. The
            # finalizer's own retry loop (or a later get_transcript/
            # list_jobs call, which also invoke this) will try again.
            return False
        record = registry.get(job_id)
        if record is None:
            if fallback_start is None:
                return False
            # fallback_start (see _persist_terminal_outcome) carries the
            # job's actual start time and must win over the "now" default
            # below - stamping "now" here instead would make a
            # reconstructed record look like it just started, corrupting
            # list_jobs's chronological order and letting a long-finished
            # job dodge pruning ahead of genuinely recent ones. Resolved
            # with `or` rather than a plain dict-merge override so a Job
            # constructed without a real started_at (its "" default) falls
            # back to "now" instead of writing an empty string to disk.
            record = {
                "job_id": job_id,
                **fallback_start,
                "started_at": fallback_start.get("started_at") or _now_iso(),
            }
            registry[job_id] = record
        record["status"] = status
        record["output_path"] = output_path
        record["error"] = error
        record["finished_at"] = _now_iso()
        _prune_registry(registry, protect=job_id)
        return _write_registry(registry)


def _reconcile_registry_on_startup() -> None:
    """A job recorded as "running" from a previous server process cannot
    actually still be tracked as running - that process's handle to the
    subprocess (and its stdout/stderr pipes) is gone, and there's no
    reattachment path (see transcribe()). Mark these "interrupted" rather
    than leaving them to look perpetually in-progress, so a caller knows to
    check for output on disk or just re-run.

    This used to skip a record whose pid was still alive, on the theory
    that it must belong to a different, still-live server process. That
    doesn't actually hold: transcribe()'s backend child runs in its own
    session, so it can easily outlive a crashed/restarted server while
    nothing is left to ever finalize it - which left such a record stuck at
    "running" forever, worse than the misclassification it was meant to
    prevent (which self-heals: a genuinely-still-live other instance's own
    finalizer overwrites this with the real outcome once that job actually
    finishes, since _update_job_record always writes unconditionally)."""
    with _registry_lock:
        try:
            registry = _load_registry()
        except _RegistryUnreadable:
            # Don't write anything on top of a registry that couldn't
            # actually be read - see _RegistryUnreadable. Reconciliation
            # just doesn't run this startup; already logged by
            # _load_registry.
            return
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
        # Always attempt a prune pass, not just when something above
        # changed: a registry that's already over MAX_PERSISTED_JOBS with
        # only terminal records (e.g. left behind by a burst of concurrent
        # finalizers - see _pending_eviction) would otherwise stay over cap
        # across a restart indefinitely, since nothing else prunes it until
        # the next job starts or finishes. Only a pruned-away entry or an
        # interrupted-conversion above needs a write, though - not every
        # startup should touch disk.
        before = len(registry)
        _prune_registry(registry)
        if changed or len(registry) != before:
            _write_registry(registry)


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
    # Carried only so the finalizer can reconstruct a registry record via
    # _update_job_record's fallback_start if _record_job_started's own
    # initial write failed - not otherwise used by this class. started_at
    # in particular must be the job's actual start time (set by transcribe()
    # to the same value it passes to _record_job_started), not the time of
    # reconstruction - see _update_job_record.
    input_path: str = ""
    num_speakers: int = 0
    started_at: str = ""
    # Set when transcribe()'s caller supplied output_path: the backend was
    # told (via --vault-output) to write there instead of templating a
    # destination from config, so _resolve_job_outcome can use this path
    # directly rather than scraping it back out of stdout.
    output_path_override: str | None = None
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
    # Guards against the finalizer thread (started in __post_init__, i.e.
    # before this Job has necessarily been inserted into `jobs`) evicting a
    # key that transcribe() then re-adds afterward - see _finalize_and_persist.
    _lifecycle_lock: threading.Lock = field(default_factory=threading.Lock, init=False)
    _eviction_pending: bool = field(default=False, init=False)
    # Set once a call to _persist_terminal_outcome has actually written this
    # job's outcome to disk - guards against a later, stale call (e.g. from
    # list_jobs, racing this job's own finalizer) re-persisting and
    # resurrecting a record that's since been pruned. See
    # _persist_terminal_outcome.
    _outcome_persisted: bool = field(default=False, init=False)
    # Set once _finalize_and_persist has made its last attempt (successful
    # or not) - not used by production code (which never needs to wait for
    # its own background thread), only by tests that need to know a
    # monkeypatched failure function is safe to revert without a retry
    # attempt still in flight landing on the real implementation afterward.
    _finalizer_done: threading.Event = field(
        default_factory=threading.Event, init=False
    )

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
        try:
            # Mirrors get_transcript's own is_complete()/stalled() check
            # rather than just waiting on the done Events: a stalled job
            # (collectors stuck, process already exited) never sets those
            # Events, and without this loop this thread would block
            # forever, leaving the registry stuck at "running" instead of
            # picking up the "failed" outcome stalled() detection makes
            # available via collector_error.
            while not self.is_complete() and not self.stalled():
                # Wait on whichever collector hasn't finished yet - waiting
                # on an already-set Event returns immediately, so always
                # waiting on _stdout_done specifically would busy-spin for
                # however long stderr outlives stdout.
                pending = (
                    self._stderr_done
                    if self._stdout_done.is_set()
                    else self._stdout_done
                )
                pending.wait(timeout=0.5)
            outcome = _resolve_job_outcome(self)
            for attempt in range(_PERSIST_RETRY_ATTEMPTS):
                if _persist_terminal_outcome(self, outcome):
                    return
                if attempt < _PERSIST_RETRY_ATTEMPTS - 1:
                    time.sleep(_PERSIST_RETRY_DELAY)
            # A transient failure (disk full, permissions) never got a
            # chance to heal within the retry budget - the live handle is
            # deliberately kept (not evicted) so get_transcript/list_jobs
            # can still resolve this job correctly from it; only surviving
            # an actual server restart before some future write succeeds
            # is lost.
            logger.error(
                "job %s: failed to persist final outcome (%s) after %d "
                "attempts; the live handle is being kept so it can still "
                "be resolved, but this outcome will be lost if the server "
                "restarts first",
                self.job_id,
                outcome["status"],
                _PERSIST_RETRY_ATTEMPTS,
            )
        except Exception:
            # Matches the collector threads' own except Exception pattern:
            # an unhandled exception here would otherwise hit Python's
            # default thread excepthook (stderr, not mcp.log) and silently
            # kill this thread, leaving the job stuck "running" until a
            # lucky poll resolves it live or a restart wrongly marks it
            # "interrupted" over its real outcome.
            logger.exception(
                "job %s: finalizer thread failed unexpectedly", self.job_id
            )
        finally:
            self._finalizer_done.set()

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


def _track_job(job: Job) -> None:
    """Inserts job into `jobs`, unless its finalizer thread (started by
    Job.__post_init__, as soon as the Job was constructed) has already run
    to completion and found nothing to evict - in which case it left a flag
    here for us to honor instead, so an already-finished Job (full
    stdout/stderr and all) doesn't get added back permanently. See
    Job._finalize_and_persist."""
    with job._lifecycle_lock:
        if not job._eviction_pending:
            jobs[job.job_id] = job


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
def transcribe(
    file_path: str, num_speakers: int, output_path: str | None = None
) -> dict:
    """Start a transcription and diarization job.

    output_path, if given, overrides the configured vault destination for
    this job only (stored vault_path/vault_subdir/vault_filename_template
    config is untouched) - the transcript is written exactly there instead
    of being templated from config. Parent directories are created as
    needed.

    Returns {"job_id": "<uuid>", "backend": "swift"|"python"} on success,
    or {"error": "<message>"} on failure.
    """
    p = Path(file_path).expanduser()
    if not p.exists():
        return {"error": f"file not found: {file_path}"}
    resolved_output: str | None = None
    if output_path:
        # Must be absolute, not just expanded: the backend subprocess runs
        # with cwd=REPO_ROOT (below), while this process's own cwd is
        # wherever it was launched from (e.g. mcp/, per the Claude Desktop
        # config) - a relative path would get written relative to one
        # directory and later read back by _resolve_job_outcome relative to
        # the other.
        resolved_output = str(Path(output_path).expanduser().resolve())
    try:
        backend_name, cmd = select_backend()
    except BackendUnavailableError as e:
        logger.error("no backend available for %s: %s", p, e)
        return {"error": f"no backend available: {e}"}
    argv = cmd + [str(p), str(num_speakers), "--yes"]
    if resolved_output is not None:
        argv += ["--vault-output", resolved_output]
    proc = subprocess.Popen(
        argv,
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
    # started_at is shared between the registry record and the Job itself
    # so a later fallback reconstruction (see _update_job_record) reports
    # this job's real start time rather than whenever it happened to finish.
    started_at = _now_iso()
    # Constructed immediately - before attempting the registry write below -
    # so its stdout/stderr collector threads start draining the backend's
    # pipes right away. If construction instead waited on a registry-write
    # retry loop (as an earlier version of this function did), a backend
    # that emits enough output during that multi-second retry window could
    # block writing to its own undrained, now-full stdout/stderr pipe.
    job = Job(
        proc=proc,
        backend=backend_name,
        job_id=job_id,
        input_path=str(p),
        num_speakers=num_speakers,
        started_at=started_at,
        output_path_override=resolved_output,
    )
    tracked = False
    for attempt in range(_PERSIST_RETRY_ATTEMPTS):
        # The write and the `jobs` insertion happen under _registry_lock
        # (reentrant - see its definition) as one atomic unit only on the
        # attempt that actually succeeds: otherwise a concurrent list_jobs()
        # call could load the registry right after this writes the job as
        # "running" but before it's inserted into `jobs`, and - having no
        # live handle to show for it - misclassify it as orphaned/
        # interrupted even though it's about to be tracked normally. A
        # *failed* attempt writes nothing, so it doesn't need that
        # protection - and releasing the lock before sleeping between
        # attempts (rather than holding it the whole retry budget) means a
        # registry write failure for this job doesn't stall every other
        # job's get_transcript/list_jobs/finalizer in the meantime.
        with _registry_lock:
            if _record_job_started(
                job_id,
                backend=backend_name,
                input_path=str(p),
                num_speakers=num_speakers,
                pid=proc.pid,
                started_at=started_at,
            ):
                _track_job(job)
                tracked = True
                break
        if attempt < _PERSIST_RETRY_ATTEMPTS - 1:
            time.sleep(_PERSIST_RETRY_DELAY)
    if not tracked:
        # Every attempt failed - the job is already running regardless, so
        # it must still be tracked live even with no durable "running"
        # record. list_jobs's synthetic fallback covers a live job with no
        # registry record, and the finalizer will still persist a full
        # record via fallback_start once the job completes (see
        # _persist_terminal_outcome) - this job just can't survive a
        # restart before then. Logged at error, matching the finalizer's own
        # exhausted-retries logging: a restart before completion would lose
        # this job entirely (report "unknown" rather than "interrupted").
        logger.error(
            "job %s: failed to persist initial 'running' record after %d "
            "attempts; the job is still tracked live, but a server restart "
            "before it completes will lose it entirely",
            job_id,
            _PERSIST_RETRY_ATTEMPTS,
        )
        _track_job(job)
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
    if job.output_path_override is not None:
        # The backend was told exactly where to write via --vault-output, so
        # that's the transcript's location - no need to scrape stdout for
        # it (and stdout's "local"/"vault" lines are about the *default*
        # destination, which isn't where this job's output actually is).
        path = job.output_path_override
    else:
        path = parse_transcript_path(job.stdout, job.backend)
        if path is None:
            logger.error(
                "job %s: could not find transcript path in stdout:\n%s",
                job_id,
                job.stdout,
            )
            return {
                "status": "failed",
                "error": "could not find transcript path in output",
            }
    try:
        Path(path).read_text()
    except Exception as e:
        # Same check _read_transcript_result makes before handing a "done"
        # transcript back to a live caller - duplicated here (rather than
        # left for that later call alone) so the *persisted* record can't
        # disagree with it: without this, a missing/unreadable output file
        # would get durably recorded as "done"/error=None here, while a
        # live poll right now correctly reports "failed" - and once this
        # job's live handle is evicted, nothing more will ever recompute
        # that record, so registry-only readers (list_jobs, a restart) would
        # see a permanently wrong "done" no live poll ever actually returned.
        logger.error("job %s: transcript at %s is not readable: %s", job_id, path, e)
        return {"status": "failed", "error": str(e)}
    return {"status": "done", "output_path": path}


# How hard Job._finalize_and_persist tries to ride out a transient registry
# write failure (disk full, permissions) before giving up on this job ever
# surviving a restart.
_PERSIST_RETRY_ATTEMPTS = 5
_PERSIST_RETRY_DELAY = 1.0


def _persist_terminal_outcome(job: Job, outcome: dict) -> bool:
    """Persists a job's terminal outcome and, once durable, evicts its live
    handle from `jobs`. Idempotent (a second call after eviction is a
    harmless no-op registry write) and safe to call from multiple places:
    both the finalizer thread and get_transcript/list_jobs call this the
    moment either one resolves a terminal outcome for a live job, so
    whichever notices first closes the window where a server restart could
    otherwise reconcile an already-finished job to "interrupted" before the
    finalizer's own next check got around to persisting it."""
    # The whole check-then-persist-then-evict sequence runs under one
    # unbroken _registry_lock hold (reentrant, so _update_job_record's own
    # internal `with _registry_lock:` nests harmlessly) rather than
    # re-acquiring the lock between steps. Two truly concurrent callers for
    # the same job - almost always its own finalizer thread racing
    # get_transcript/list_jobs - would otherwise both read _outcome_persisted
    # as False before either finishes, and the second could still resurrect
    # an already-pruned record via fallback_start after the first caller's
    # full persist+evict+prune cycle completed in between the check and the
    # write. Nesting job._lifecycle_lock *inside* this (never the reverse)
    # matches the only other place that takes both locks (transcribe() via
    # _track_job), avoiding a lock-ordering deadlock.
    with _registry_lock:
        # A prior call for this exact job may have already durably
        # persisted this outcome - see this function's docstring.
        # _outcome_persisted (set only after a previous call's write
        # actually succeeded) is the signal for that - unlike checking
        # `jobs.get(job.job_id) is not job`, it can't be confused with the
        # legitimate case below where this is the *first* call and the job
        # simply hasn't been inserted into `jobs` yet.
        if job._outcome_persisted:
            return True
        _pending_eviction.add(job.job_id)
        try:
            persisted = _update_job_record(
                job.job_id,
                status=outcome["status"],
                output_path=outcome.get("output_path"),
                error=outcome.get("error"),
                fallback_start={
                    "backend": job.backend,
                    "input_path": job.input_path,
                    "num_speakers": job.num_speakers,
                    "pid": job.proc.pid,
                    "started_at": job.started_at,
                    "output_path": None,
                    "error": None,
                    "finished_at": None,
                },
            )
            if not persisted:
                return False
            job._outcome_persisted = True
            with job._lifecycle_lock:
                # For a job that completes near-instantly, this can run
                # before transcribe() has inserted `job` into `jobs` at all
                # - popping now would be a silent no-op, and the later
                # insertion would then leave this already-finished Job (and
                # its stdout/stderr) in `jobs` permanently, with nothing
                # left to ever evict it again. Flag it instead so
                # transcribe() can evict it itself once it does insert.
                if jobs.get(job.job_id) is job:
                    del jobs[job.job_id]
                else:
                    job._eviction_pending = True
            return True
        finally:
            _pending_eviction.discard(job.job_id)


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
      (distinct from "failed": not evidence of an error - but unlike a job
      that was truly never seen, a pruned one may have completed and
      written real output, so check the configured output location before
      re-running).
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
    # Persist immediately rather than leaving it solely to the finalizer
    # thread's own (up to 0.5s later) check: without this, a restart in the
    # narrow window right after this poll but before that thread wakes up
    # could reconcile a job this call already reported "done" to
    # "interrupted" for whoever asks next.
    _persist_terminal_outcome(job, outcome)
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


# A registry entry can say "running" with no live Job behind it - normally
# _reconcile_registry_on_startup converts these to "interrupted" before the
# server starts serving requests, but that conversion is itself a
# best-effort write that can fail (see _reconcile_registry_on_startup). Both
# get_transcript and list_jobs fail safe here rather than claim a job with
# no live handle is still running.
_ORPHANED_RUNNING_ERROR = (
    "job is no longer being tracked; check the configured output location, "
    "or re-run the transcription"
)


def _get_transcript_from_registry(job_id: str) -> dict:
    """get_transcript's fallback for a job_id no longer (or never) tracked
    live - e.g. because the server restarted since it was started. Consults
    the persisted registry so a restart can't turn "this job existed" into
    "unknown job_id"."""
    try:
        record = _load_registry().get(job_id)
    except _RegistryUnreadable as e:
        # Read-only here - no write to protect - so degrade to a clear
        # "can't tell right now" rather than crashing this tool call.
        return {"status": "unknown", "error": f"job registry unavailable: {e}"}
    if record is None:
        return {"status": "unknown", "error": "no such job_id"}
    if record["status"] == "done":
        return _read_transcript_result(record["output_path"], job_id)
    if record["status"] == "running":
        return {"status": "interrupted", "error": _ORPHANED_RUNNING_ERROR}
    return {"status": record["status"], "error": record.get("error") or "unknown error"}


@mcp.tool()
def list_jobs(limit: int = 20) -> dict:
    """List recent transcription jobs, most recently started first - including
    ones from before a server restart, which get_transcript alone can't see.

    Returns {"jobs": [{"job_id", "backend", "input_path", "num_speakers",
    "pid", "status", "output_path", "error", "started_at", "finished_at"}, ...]}.
    A job still tracked live also carries "message" and, once transcription
    reports fine-grained progress, "fraction"/"stage" - see get_transcript.
    """
    # Both read together under _registry_lock: transcribe() writes a job's
    # registry record and inserts it into `jobs` as one atomic unit under
    # the same lock (see transcribe()), so reading them separately here
    # could otherwise catch a job in between - registered but not yet
    # live - and the orphaned-running fail-safe below would then
    # misclassify it as interrupted rather than about to be tracked.
    # list() also avoids "dictionary changed size during iteration" against
    # a concurrent transcribe() insertion.
    with _registry_lock:
        try:
            registry = _load_registry()
        except _RegistryUnreadable:
            # Read-only for this response - no write here risks clobbering
            # anything (per-job terminal updates below go through
            # _persist_terminal_outcome/_update_job_record, which already
            # guard against this themselves). Worst case this call just
            # can't show older completed jobs this time.
            registry = {}
        jobs_snapshot = list(jobs.items())
    live_ids = {job_id for job_id, _ in jobs_snapshot}
    for job_id, job in jobs_snapshot:
        record = dict(
            registry.get(
                job_id,
                {
                    "job_id": job_id,
                    "backend": job.backend,
                    "input_path": job.input_path,
                    "num_speakers": job.num_speakers,
                    "pid": job.proc.pid,
                    "status": "running",
                    "output_path": None,
                    "error": None,
                    "started_at": job.started_at or None,
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
            # snapshot from before it's had a chance to write, and persist
            # it immediately ourselves (same as get_transcript) so a
            # restart right after this call can't reconcile a job we just
            # reported as done/failed to "interrupted" instead.
            outcome = _resolve_job_outcome(job)
            _persist_terminal_outcome(job, outcome)
            record["status"] = outcome["status"]
            record["output_path"] = outcome.get(
                "output_path", record.get("output_path")
            )
            record["error"] = outcome.get("error", record.get("error"))
            record["finished_at"] = record.get("finished_at") or _now_iso()
        registry[job_id] = record
    for job_id, record in registry.items():
        # Same fail-safe as _get_transcript_from_registry: a registry-only
        # entry (no live handle backing it, e.g. this server is post-restart
        # and reconciliation's own write failed) must not be shown as
        # "running" - see _ORPHANED_RUNNING_ERROR.
        if job_id not in live_ids and record["status"] == "running":
            record["status"] = "interrupted"
            record["error"] = _ORPHANED_RUNNING_ERROR
            record["finished_at"] = record.get("finished_at") or _now_iso()
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
    # Deliberately not run at import time: importing this module (e.g. for
    # tests, or any tooling) must not itself reconcile - and possibly
    # rewrite - a real, on-disk jobs.json as a side effect.
    _reconcile_registry_on_startup()
    mcp.run()
