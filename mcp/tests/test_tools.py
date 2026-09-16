import io
import json
import os
import subprocess
import threading
import time
from unittest.mock import MagicMock, patch

import server


def _make_proc(stdout: bytes, stderr: bytes, returncode: int) -> MagicMock:
    proc = MagicMock()
    proc.stdout = io.BytesIO(stdout)
    proc.stderr = io.BytesIO(stderr)
    proc.returncode = returncode
    proc.wait.return_value = returncode
    proc.pid = 4242  # a concrete int - the job registry must be able to
    # json-serialize it, unlike a MagicMock's auto-generated attribute
    return proc


def _wait(job, timeout: float = 2.0) -> None:
    """Poll until job.is_complete() or timeout."""
    deadline = time.monotonic() + timeout
    while not job.is_complete() and time.monotonic() < deadline:
        time.sleep(0.05)


def test_transcribe_missing_file():
    result = server.transcribe("/nonexistent/audio.wav", 2)
    assert "error" in result
    assert "not found" in result["error"]


def test_transcribe_no_backend(tmp_path, monkeypatch):
    audio = tmp_path / "audio.wav"
    audio.touch()
    monkeypatch.setattr(server, "REPO_ROOT", tmp_path)
    with patch("platform.system", return_value="Linux"):
        result = server.transcribe(str(audio), 2)
    assert "error" in result
    assert "no backend" in result["error"]


def test_transcribe_starts_job(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.touch()

    # Block the collectors: a completed job is evicted from server.jobs by
    # its background finalizer once persisted, which - for an instantly
    # completing mock proc - can otherwise race ahead of this assertion.
    unblock = threading.Event()

    class _BlockingStdout:
        def __iter__(self):
            unblock.wait()
            return iter([])

        def close(self):
            pass

    mock_proc = MagicMock()
    mock_proc.stdout = _BlockingStdout()
    mock_proc.stderr = io.BytesIO(b"")
    mock_proc.returncode = None
    mock_proc.poll.return_value = None

    with patch("server.select_backend", return_value=("swift", ["/bin/echo"])), patch(
        "subprocess.Popen", return_value=mock_proc
    ):
        result = server.transcribe(str(audio), 2)

    try:
        assert "job_id" in result
        assert result["backend"] == "swift"
        assert result["job_id"] in server.jobs
    finally:
        unblock.set()


def test_transcribe_spawns_backend_in_own_process_group(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.touch()
    mock_proc = _make_proc(b"    local       : /tmp/t.md\n", b"", 0)

    # Force the non-Darwin path so the caffeinate watcher doesn't add a second
    # Popen call - this test is only about how the backend itself is spawned.
    with patch("server.select_backend", return_value=("swift", ["/bin/echo"])), patch(
        "platform.system", return_value="Linux"
    ), patch("subprocess.Popen", return_value=mock_proc) as mock_popen:
        server.transcribe(str(audio), 2)

    # The backend must lead its own session/process group so cleanup can
    # signal the whole tree (the python backend's real worker is a child of
    # the `uv run` wrapper that proc points at).
    assert mock_popen.call_args_list[0].kwargs["start_new_session"] is True


def test_kill_after_collector_error_signals_process_group(monkeypatch):
    # os.killpg/getpgid and signal.SIGKILL are all POSIX-only, so this test
    # forces the POSIX branch and fakes all three into existence (create=True)
    # to exercise it even when actually running on Windows CI.
    monkeypatch.setattr(server, "_HAS_PROCESS_GROUP_KILL", True)
    proc = MagicMock()
    proc.poll.return_value = None  # still alive
    proc.pid = 4242

    job = server.Job.__new__(server.Job)  # skip __post_init__ collector threads
    job.proc = proc
    job.job_id = "kill-id"

    with patch("os.getpgid", return_value=4242, create=True) as mock_getpgid, patch(
        "os.killpg", create=True
    ) as mock_killpg, patch("signal.SIGKILL", 9, create=True):
        job._kill_after_collector_error()
        mock_getpgid.assert_called_once_with(4242)
        mock_killpg.assert_called_once_with(4242, 9)
    # proc.kill() must NOT be used - that would miss the orphaned worker.
    proc.kill.assert_not_called()


def test_kill_after_collector_error_survives_dead_process(monkeypatch):
    monkeypatch.setattr(server, "_HAS_PROCESS_GROUP_KILL", True)
    proc = MagicMock()
    proc.poll.return_value = None  # looked alive at poll()...

    job = server.Job.__new__(server.Job)
    job.proc = proc
    job.job_id = "dead-id"

    # ...but exited before killpg: ProcessLookupError must be swallowed.
    # os.killpg must still exist (create=True) even though it's never
    # reached - Python resolves it as the call target before evaluating
    # os.getpgid(...), the argument that actually raises.
    with patch("os.getpgid", side_effect=ProcessLookupError, create=True), patch(
        "os.killpg", create=True
    ):
        job._kill_after_collector_error()  # must not raise

    proc.wait.assert_called_once()


def test_kill_after_collector_error_uses_taskkill_on_windows(monkeypatch):
    monkeypatch.setattr(server, "_HAS_PROCESS_GROUP_KILL", False)
    proc = MagicMock()
    proc.poll.return_value = None  # still alive
    proc.pid = 4242

    job = server.Job.__new__(server.Job)
    job.proc = proc
    job.job_id = "kill-id"

    with patch("subprocess.run", return_value=MagicMock(returncode=0)) as mock_run:
        job._kill_after_collector_error()

    mock_run.assert_called_once_with(
        ["taskkill", "/T", "/F", "/PID", "4242"], capture_output=True
    )
    # proc.kill() must NOT be used when taskkill succeeds - that would miss
    # the orphaned worker taskkill /T is there to reach.
    proc.kill.assert_not_called()


def test_kill_after_collector_error_falls_back_when_taskkill_fails(monkeypatch):
    monkeypatch.setattr(server, "_HAS_PROCESS_GROUP_KILL", False)
    proc = MagicMock()
    proc.poll.return_value = None
    proc.pid = 4242

    job = server.Job.__new__(server.Job)
    job.proc = proc
    job.job_id = "kill-id"

    with patch("subprocess.run", return_value=MagicMock(returncode=128)):
        job._kill_after_collector_error()

    proc.kill.assert_called_once()


def test_transcribe_spawns_caffeinate_watcher_on_darwin(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.touch()
    mock_proc = _make_proc(b"    local       : /tmp/t.md\n", b"", 0)
    mock_proc.pid = 4242
    mock_caffeinate_proc = MagicMock()

    with patch("server.select_backend", return_value=("swift", ["/bin/echo"])), patch(
        "platform.system", return_value="Darwin"
    ), patch("shutil.which", return_value="/usr/bin/caffeinate"), patch(
        "subprocess.Popen", side_effect=[mock_proc, mock_caffeinate_proc]
    ) as mock_popen:
        server.transcribe(str(audio), 2)

    # The backend command itself must not be wrapped - Job.proc has to stay
    # the real backend process so kill()/wait() target it directly.
    backend_call_cmd = mock_popen.call_args_list[0][0][0]
    assert backend_call_cmd[0] == "/bin/echo"

    caffeinate_call_cmd = mock_popen.call_args_list[1][0][0]
    assert caffeinate_call_cmd == ["/usr/bin/caffeinate", "-i", "-w", "4242"]

    # The watcher is reaped on a daemon thread so it can't linger as a zombie;
    # wait for that thread to call wait() rather than asserting synchronously.
    deadline = time.monotonic() + 1.0
    while not mock_caffeinate_proc.wait.called and time.monotonic() < deadline:
        time.sleep(0.01)
    mock_caffeinate_proc.wait.assert_called_once()


def test_transcribe_skips_caffeinate_off_darwin(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.touch()
    mock_proc = _make_proc(b"    local       : /tmp/t.md\n", b"", 0)

    # Off macOS the platform guard must short-circuit before shutil.which is
    # even consulted - only the backend process should be spawned.
    with patch("server.select_backend", return_value=("swift", ["/bin/echo"])), patch(
        "platform.system", return_value="Linux"
    ), patch("shutil.which") as mock_which, patch(
        "subprocess.Popen", return_value=mock_proc
    ) as mock_popen:
        server.transcribe(str(audio), 2)

    assert mock_popen.call_count == 1
    mock_which.assert_not_called()


def test_transcribe_survives_caffeinate_spawn_failure(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.touch()
    mock_proc = _make_proc(b"    local       : /tmp/t.md\n", b"", 0)
    mock_proc.pid = 4242

    # caffeinate is on PATH but its Popen raises - the backend job already
    # started, so transcribe() must still register the job and return normally.
    with patch("server.select_backend", return_value=("swift", ["/bin/echo"])), patch(
        "platform.system", return_value="Darwin"
    ), patch("shutil.which", return_value="/usr/bin/caffeinate"), patch(
        "subprocess.Popen", side_effect=[mock_proc, OSError("boom")]
    ):
        result = server.transcribe(str(audio), 2)

    assert "job_id" in result
    assert result["backend"] == "swift"


def test_transcribe_no_caffeinate_when_unavailable(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.touch()
    mock_proc = _make_proc(b"    local       : /tmp/t.md\n", b"", 0)

    with patch("server.select_backend", return_value=("swift", ["/bin/echo"])), patch(
        "platform.system", return_value="Darwin"
    ), patch("shutil.which", return_value=None), patch(
        "subprocess.Popen", return_value=mock_proc
    ) as mock_popen:
        server.transcribe(str(audio), 2)

    assert mock_popen.call_count == 1


def test_get_transcript_unknown_job():
    result = server.get_transcript("no-such-id")
    assert result == {"status": "unknown", "error": "no such job_id"}


def test_get_transcript_running():
    unblock = threading.Event()

    class _BlockingStdout:
        """Blocks iteration until released, simulating an in-progress process."""

        def __iter__(self):
            unblock.wait()
            return iter([])

        def close(self):
            pass

    proc = MagicMock()
    proc.stdout = _BlockingStdout()
    proc.stderr = io.BytesIO(b"")
    proc.returncode = 0
    proc.poll.return_value = None

    job = server.Job(proc=proc, backend="swift")
    server.jobs["running-id"] = job

    result = server.get_transcript("running-id")
    assert result == {"status": "running"}
    unblock.set()


def test_get_transcript_running_with_message():
    unblock = threading.Event()
    first_line_seen = threading.Event()

    class _BlockingStdout:
        def __iter__(self):
            yield b"==> Transcribing audio...\n"
            first_line_seen.set()
            unblock.wait()

        def close(self):
            pass

    proc = MagicMock()
    proc.stdout = _BlockingStdout()
    proc.stderr = io.BytesIO(b"")
    proc.returncode = 0
    proc.poll.return_value = None

    job = server.Job(proc=proc, backend="swift")
    server.jobs["running-msg-id"] = job

    first_line_seen.wait(timeout=2.0)
    result = server.get_transcript("running-msg-id")
    assert result["status"] == "running"
    assert result["message"] == "Transcribing audio..."
    unblock.set()


def test_get_transcript_running_with_fraction():
    unblock = threading.Event()
    lines_seen = threading.Event()

    class _BlockingStdout:
        def __iter__(self):
            yield b"==> Transcribing audio...\n"
            yield b"progress:0.4200:transcribing\n"
            lines_seen.set()
            unblock.wait()

        def close(self):
            pass

    proc = MagicMock()
    proc.stdout = _BlockingStdout()
    proc.stderr = io.BytesIO(b"")
    proc.returncode = 0
    proc.poll.return_value = None

    job = server.Job(proc=proc, backend="swift")
    server.jobs["running-fraction-id"] = job

    lines_seen.wait(timeout=2.0)
    result = server.get_transcript("running-fraction-id")
    assert result["status"] == "running"
    assert result["fraction"] == 0.42
    assert result["stage"] == "transcribing"
    unblock.set()


def test_get_transcript_running_ignores_malformed_progress_line():
    unblock = threading.Event()
    lines_seen = threading.Event()

    class _BlockingStdout:
        def __iter__(self):
            yield b"progress:not-a-number:transcribing\n"
            lines_seen.set()
            unblock.wait()

        def close(self):
            pass

    proc = MagicMock()
    proc.stdout = _BlockingStdout()
    proc.stderr = io.BytesIO(b"")
    proc.returncode = 0
    proc.poll.return_value = None

    job = server.Job(proc=proc, backend="swift")
    server.jobs["running-bad-fraction-id"] = job

    lines_seen.wait(timeout=2.0)
    result = server.get_transcript("running-bad-fraction-id")
    assert result["status"] == "running"
    assert "fraction" not in result
    unblock.set()


def test_get_transcript_running_ignores_out_of_range_progress_lines():
    # nan/inf/out-of-range values would otherwise be stored verbatim and
    # (for nan/inf) produce invalid JSON for a client polling get_transcript.
    unblock = threading.Event()
    lines_seen = threading.Event()

    class _BlockingStdout:
        def __iter__(self):
            yield b"progress:2.0000:transcribing\n"
            yield b"progress:nan:transcribing\n"
            yield b"progress:inf:transcribing\n"
            yield b"progress:-1.0000:transcribing\n"
            lines_seen.set()
            unblock.wait()

        def close(self):
            pass

    proc = MagicMock()
    proc.stdout = _BlockingStdout()
    proc.stderr = io.BytesIO(b"")
    proc.returncode = 0
    proc.poll.return_value = None

    job = server.Job(proc=proc, backend="swift")
    server.jobs["running-out-of-range-fraction-id"] = job

    lines_seen.wait(timeout=2.0)
    result = server.get_transcript("running-out-of-range-fraction-id")
    assert result["status"] == "running"
    assert "fraction" not in result
    unblock.set()


def test_get_transcript_running_fraction_cleared_on_next_stage():
    # A stale transcription-stage fraction shouldn't leak into a later
    # stage (e.g. diarization) that never reports its own progress lines.
    unblock = threading.Event()
    lines_seen = threading.Event()

    class _BlockingStdout:
        def __iter__(self):
            yield b"==> Transcribing audio...\n"
            yield b"progress:0.9800:transcribing\n"
            yield b"==> Running pyannote diarization\n"
            lines_seen.set()
            unblock.wait()

        def close(self):
            pass

    proc = MagicMock()
    proc.stdout = _BlockingStdout()
    proc.stderr = io.BytesIO(b"")
    proc.returncode = 0
    proc.poll.return_value = None

    job = server.Job(proc=proc, backend="swift")
    server.jobs["running-stage-transition-id"] = job

    lines_seen.wait(timeout=2.0)
    result = server.get_transcript("running-stage-transition-id")
    assert result["status"] == "running"
    assert result["message"] == "Running pyannote diarization"
    assert "fraction" not in result
    assert "stage" not in result
    unblock.set()


def test_get_transcript_done(tmp_path):
    transcript = tmp_path / "transcript.md"
    transcript.write_text("# Meeting\n\nAlice: Hello.\nBob: Hi.")

    proc = _make_proc(
        f"==> Complete\n    local       : {transcript}\n".encode(),
        b"",
        0,
    )
    job = server.Job(proc=proc, backend="swift")
    server.jobs["done-id"] = job
    _wait(job)

    result = server.get_transcript("done-id")
    assert result["status"] == "done"
    assert "Alice" in result["transcript"]
    assert result["output_path"] == str(transcript)


def test_get_transcript_failed():
    proc = _make_proc(b"", b"WhisperKit load error\n", 1)
    job = server.Job(proc=proc, backend="swift")
    server.jobs["fail-id"] = job
    _wait(job)

    result = server.get_transcript("fail-id")
    assert result["status"] == "failed"
    assert "WhisperKit load error" in result["error"]


def test_get_transcript_missing_path(tmp_path):
    proc = _make_proc(b"==> Complete\n(no local line)\n", b"", 0)
    job = server.Job(proc=proc, backend="swift")
    server.jobs["nopath-id"] = job
    _wait(job)

    result = server.get_transcript("nopath-id")
    assert result["status"] == "failed"
    assert "transcript path" in result["error"]


def test_get_transcript_waits_for_slow_stderr():
    """Job must not report failure with a truncated error while stderr is
    still being collected, even though stdout finishes first."""
    release_stderr = threading.Event()

    class _SlowStderr:
        def read(self):
            release_stderr.wait(timeout=2.0)
            return b"WhisperKit load error\n"

        def close(self):
            pass

    proc = MagicMock()
    proc.stdout = io.BytesIO(b"")
    proc.stderr = _SlowStderr()
    proc.returncode = 1
    proc.wait.return_value = 1

    job = server.Job(proc=proc, backend="swift")
    server.jobs["slow-stderr-id"] = job

    # stdout collection finishes almost immediately, but stderr is still
    # blocked, so the job must not yet be reported as complete/failed.
    deadline = time.monotonic() + 1.0
    while not job._stdout_done.is_set() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert job._stdout_done.is_set()
    assert not job.is_complete()
    assert server.get_transcript("slow-stderr-id") == {"status": "running"}

    release_stderr.set()
    _wait(job)

    result = server.get_transcript("slow-stderr-id")
    assert result["status"] == "failed"
    assert "WhisperKit load error" in result["error"]


def test_get_transcript_stdout_collector_exception():
    class _BrokenStdout:
        def __iter__(self):
            raise OSError("broken pipe")

        def close(self):
            pass

    proc = MagicMock()
    proc.stdout = _BrokenStdout()
    proc.stderr = io.BytesIO(b"")
    proc.returncode = 0

    job = server.Job(proc=proc, backend="swift")
    server.jobs["broken-stdout-id"] = job
    _wait(job)

    assert job.is_complete()
    result = server.get_transcript("broken-stdout-id")
    assert result["status"] == "failed"
    assert "internal error collecting job output" in result["error"]
    assert "broken pipe" in result["error"]


def test_get_transcript_stalled_collector_reports_failure():
    """If the process has exited but the collectors never finish (e.g. a
    future bug reintroduces a stuck reader thread), get_transcript must
    eventually report failure instead of hanging on 'running' forever."""

    never_release = threading.Event()

    class _NeverEndingReader:
        def __iter__(self):
            never_release.wait()
            return iter([])

        def read(self):
            never_release.wait()
            return b""

        def close(self):
            pass

    proc = MagicMock()
    proc.stdout = _NeverEndingReader()
    proc.stderr = _NeverEndingReader()
    proc.returncode = 1
    proc.poll.return_value = 1

    job = server.Job(proc=proc, backend="swift")
    server.jobs["stalled-id"] = job

    assert not job.is_complete()
    assert job.stalled() is False  # first observation just records exit time
    job._exit_seen_at = time.monotonic() - (server.COLLECTOR_STALL_TIMEOUT + 1)
    assert job.stalled() is True

    result = server.get_transcript("stalled-id")
    assert result["status"] == "failed"
    assert "internal bug" in result["error"]

    never_release.set()


def test_get_transcript_stderr_collector_exception():
    class _BrokenStderr:
        def read(self):
            raise OSError("broken pipe")

        def close(self):
            pass

    proc = _make_proc(b"==> Complete\n    local       : /tmp/t.md\n", b"", 0)
    proc.stderr = _BrokenStderr()

    job = server.Job(proc=proc, backend="swift")
    server.jobs["broken-stderr-id"] = job
    _wait(job)

    assert job.is_complete()
    result = server.get_transcript("broken-stderr-id")
    assert result["status"] == "failed"
    assert "internal error collecting job output" in result["error"]
    assert "broken pipe" in result["error"]


def test_get_transcript_both_collectors_raise_simultaneously():
    """Both collector errors must be surfaced, not just the first one to
    set collector_error, and the result must stay a single coherent dict."""
    barrier = threading.Barrier(2)

    class _BrokenStdout:
        def __iter__(self):
            barrier.wait(timeout=2.0)
            raise OSError("stdout broke")

        def close(self):
            pass

    class _BrokenStderr:
        def read(self):
            barrier.wait(timeout=2.0)
            raise OSError("stderr broke")

        def close(self):
            pass

    proc = MagicMock()
    proc.stdout = _BrokenStdout()
    proc.stderr = _BrokenStderr()
    proc.returncode = 0
    proc.poll.return_value = 0

    job = server.Job(proc=proc, backend="swift")
    server.jobs["both-broken-id"] = job
    _wait(job)

    assert job.is_complete()
    result = server.get_transcript("both-broken-id")
    assert result["status"] == "failed"
    assert "stdout broke" in result["error"]
    assert "stderr broke" in result["error"]


# --- Persistent job registry ---


def _wait_for_terminal_record(job_id: str, timeout: float = 2.0) -> dict | None:
    """Poll the on-disk registry until job_id reaches a non-"running" status
    (i.e. the background finalizer thread has persisted it), or timeout."""
    deadline = time.monotonic() + timeout
    record = None
    while time.monotonic() < deadline:
        record = server._load_registry().get(job_id)
        if record is not None and record["status"] != "running":
            return record
        time.sleep(0.02)
    return record


def test_transcribe_persists_started_record(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.touch()

    # Block the collectors so the background finalizer can't race ahead and
    # flip this to a terminal status before the assertions below run.
    unblock = threading.Event()

    class _BlockingStdout:
        def __iter__(self):
            unblock.wait()
            return iter([])

        def close(self):
            pass

    mock_proc = MagicMock()
    mock_proc.stdout = _BlockingStdout()
    mock_proc.stderr = io.BytesIO(b"")
    mock_proc.returncode = None
    mock_proc.poll.return_value = None
    mock_proc.pid = 4242

    with patch("server.select_backend", return_value=("swift", ["/bin/echo"])), patch(
        "subprocess.Popen", return_value=mock_proc
    ):
        result = server.transcribe(str(audio), 3)

    try:
        record = server._load_registry()[result["job_id"]]
        assert record["backend"] == "swift"
        assert record["input_path"] == str(audio)
        assert record["num_speakers"] == 3
        assert record["pid"] == 4242
        assert record["status"] == "running"
        assert record["output_path"] is None
        assert record["started_at"] is not None
    finally:
        unblock.set()


def test_finalizer_persists_done_status(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.touch()
    transcript = tmp_path / "t.md"
    mock_proc = _make_proc(f"    local       : {transcript}\n".encode(), b"", 0)

    with patch("server.select_backend", return_value=("swift", ["/bin/echo"])), patch(
        "subprocess.Popen", return_value=mock_proc
    ):
        result = server.transcribe(str(audio), 2)

    record = _wait_for_terminal_record(result["job_id"])
    assert record["status"] == "done"
    assert record["output_path"] == str(transcript)
    assert record["error"] is None
    assert record["finished_at"] is not None


def test_finalizer_persists_failed_status(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.touch()
    mock_proc = _make_proc(b"", b"boom\n", 1)

    with patch("server.select_backend", return_value=("swift", ["/bin/echo"])), patch(
        "subprocess.Popen", return_value=mock_proc
    ):
        result = server.transcribe(str(audio), 2)

    record = _wait_for_terminal_record(result["job_id"])
    assert record["status"] == "failed"
    assert "boom" in record["error"]
    assert record["output_path"] is None


def test_finalizer_persists_failed_status_for_stalled_job(monkeypatch):
    # A stalled job (process exited, collectors never finish) never sets
    # the done Events the finalizer originally just blocked on - it must
    # instead notice via the same is_complete()/stalled() check
    # get_transcript's polling uses, or the registry entry would be stuck
    # at "running" forever (short of a server restart).
    monkeypatch.setattr(server, "COLLECTOR_STALL_TIMEOUT", 0.05)

    never_release = threading.Event()

    class _NeverEndingReader:
        def __iter__(self):
            never_release.wait()
            return iter([])

        def read(self):
            never_release.wait()
            return b""

        def close(self):
            pass

    proc = MagicMock()
    proc.stdout = _NeverEndingReader()
    proc.stderr = _NeverEndingReader()
    proc.returncode = 1
    proc.poll.return_value = 1
    proc.pid = 4242

    server._record_job_started(
        "stalled-finalize-id",
        backend="swift",
        input_path="/tmp/a.wav",
        num_speakers=2,
        pid=4242,
    )
    job = server.Job(proc=proc, backend="swift", job_id="stalled-finalize-id")
    server.jobs["stalled-finalize-id"] = job

    try:
        record = _wait_for_terminal_record("stalled-finalize-id", timeout=3.0)
        assert record is not None
        assert record["status"] == "failed"
        assert "internal bug" in record["error"]
    finally:
        never_release.set()


def test_finalizer_retries_persistence_after_transient_write_failure(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(server, "_PERSIST_RETRY_ATTEMPTS", 3)
    monkeypatch.setattr(server, "_PERSIST_RETRY_DELAY", 0.01)

    audio = tmp_path / "audio.wav"
    audio.touch()
    transcript = tmp_path / "t.md"
    transcript.write_text("hello")
    mock_proc, unblock = _make_blocking_proc(
        stdout=f"    local       : {transcript}\n".encode()
    )

    real_write_registry = server._write_registry
    call_count = {"n": 0}

    def _flaky_write(registry):
        call_count["n"] += 1
        # Fails the first call this mock sees, to simulate a transient
        # error before the finalizer's retry recovers. The collectors stay
        # blocked (so the finalizer can't possibly race ahead) until this
        # mock is installed *after* transcribe() has already returned -
        # i.e. after its own _record_job_started write has already
        # succeeded for real - so that first call is deterministically the
        # finalizer's, not a race between the two.
        if call_count["n"] == 1:
            return False
        return real_write_registry(registry)

    with patch("server.select_backend", return_value=("swift", ["/bin/echo"])), patch(
        "subprocess.Popen", return_value=mock_proc
    ):
        result = server.transcribe(str(audio), 2)
        monkeypatch.setattr(server, "_write_registry", _flaky_write)
        unblock.set()

    record = _wait_for_terminal_record(result["job_id"], timeout=2.0)
    assert record["status"] == "done"
    assert call_count["n"] >= 2
    assert result["job_id"] not in server.jobs


def test_finalizer_keeps_live_handle_after_exhausting_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "_PERSIST_RETRY_ATTEMPTS", 2)
    monkeypatch.setattr(server, "_PERSIST_RETRY_DELAY", 0.01)
    monkeypatch.setattr(server, "_write_registry", lambda registry: False)

    audio = tmp_path / "audio.wav"
    audio.touch()
    transcript = tmp_path / "t.md"
    transcript.write_text("hello")
    mock_proc = _make_proc(f"    local       : {transcript}\n".encode(), b"", 0)

    with patch("server.select_backend", return_value=("swift", ["/bin/echo"])), patch(
        "subprocess.Popen", return_value=mock_proc
    ):
        result = server.transcribe(str(audio), 2)

    job_id = result["job_id"]
    job = server.jobs[job_id]
    _wait(job)
    job._finalizer_done.wait(timeout=2.0)
    # A registry that can never be written must not lose the job: keeping
    # the live handle (rather than evicting on a false promise) means
    # get_transcript can still resolve it correctly from memory.
    assert job_id in server.jobs
    result2 = server.get_transcript(job_id)
    assert result2["status"] == "done"


def test_get_transcript_persists_immediately_on_live_completion(tmp_path):
    # Without this, a restart in the narrow window between this poll and
    # the finalizer's own next check (up to 0.5s later) could reconcile a
    # job this call already reported "done" to "interrupted".
    audio = tmp_path / "audio.wav"
    audio.touch()
    transcript = tmp_path / "t.md"
    mock_proc = _make_proc(f"    local       : {transcript}\n".encode(), b"", 0)

    with patch("server.select_backend", return_value=("swift", ["/bin/echo"])), patch(
        "subprocess.Popen", return_value=mock_proc
    ):
        result = server.transcribe(str(audio), 2)

    job_id = result["job_id"]
    # The finalizer can race ahead of this line and (since persistence here
    # is real, unlike the exhausted-retries test above) already evict the
    # job once it completes - in which case there's nothing left to wait on.
    live_job = server.jobs.get(job_id)
    if live_job is not None:
        _wait(live_job)

    server.get_transcript(job_id)

    record = server._load_registry()[job_id]
    assert record["status"] == "done"


def test_list_jobs_persists_immediately_on_live_completion(tmp_path, monkeypatch):
    # Kept as a no-op (always False) throughout so the finalizer can't beat
    # list_jobs to it and evict the job first - we want to observe
    # list_jobs's own call.
    call_count = {"n": 0}

    def _counting_update(*args, **kwargs):
        call_count["n"] += 1
        return False

    monkeypatch.setattr(server, "_update_job_record", _counting_update)

    transcript = tmp_path / "t.md"
    proc = _make_proc(f"    local       : {transcript}\n".encode(), b"", 0)
    server._record_job_started(
        "list-persist-id",
        backend="swift",
        input_path="/tmp/a.wav",
        num_speakers=2,
        pid=proc.pid,
    )
    job = server.Job(proc=proc, backend="swift", job_id="list-persist-id")
    server.jobs["list-persist-id"] = job
    _wait(job)
    # Let the finalizer's own retry loop fully exhaust and give up before
    # capturing the baseline call count below - otherwise a still-in-flight
    # retry could land on the *real* _update_job_record right after this
    # test returns and monkeypatch reverts, writing to whatever jobs.json
    # exists by the time a later test runs.
    job._finalizer_done.wait(timeout=2.0)

    calls_before = call_count["n"]
    server.list_jobs()

    assert call_count["n"] > calls_before


def test_get_transcript_reads_done_job_from_registry_when_not_live(tmp_path):
    transcript = tmp_path / "transcript.md"
    transcript.write_text("# Meeting\n\nAlice: Hello.")
    server._record_job_started(
        "past-done-id", backend="swift", input_path="/tmp/a.wav", num_speakers=2, pid=1
    )
    server._update_job_record(
        "past-done-id", status="done", output_path=str(transcript), error=None
    )

    result = server.get_transcript("past-done-id")
    assert result["status"] == "done"
    assert "Alice" in result["transcript"]
    assert result["output_path"] == str(transcript)


def test_get_transcript_reads_done_job_missing_file_from_registry(tmp_path):
    server._record_job_started(
        "past-missing-id",
        backend="swift",
        input_path="/tmp/a.wav",
        num_speakers=2,
        pid=1,
    )
    server._update_job_record(
        "past-missing-id",
        status="done",
        output_path=str(tmp_path / "gone.md"),
        error=None,
    )

    result = server.get_transcript("past-missing-id")
    assert result["status"] == "failed"


def test_get_transcript_reads_failed_job_from_registry_when_not_live():
    server._record_job_started(
        "past-failed-id",
        backend="swift",
        input_path="/tmp/a.wav",
        num_speakers=2,
        pid=1,
    )
    server._update_job_record(
        "past-failed-id", status="failed", output_path=None, error="exit code 1"
    )

    result = server.get_transcript("past-failed-id")
    assert result == {"status": "failed", "error": "exit code 1"}


def test_get_transcript_reads_interrupted_job_from_registry():
    server._record_job_started(
        "past-interrupted-id",
        backend="swift",
        input_path="/tmp/a.wav",
        num_speakers=2,
        pid=1,
    )
    server._update_job_record(
        "past-interrupted-id",
        status="interrupted",
        output_path=None,
        error="server restarted",
    )

    result = server.get_transcript("past-interrupted-id")
    assert result == {"status": "interrupted", "error": "server restarted"}


def test_get_transcript_registry_running_but_not_live_reports_interrupted():
    # Fail-safe: reconciliation should always convert "running" to
    # "interrupted" on startup, but get_transcript must not claim a job with
    # no live handle is still "running" even if that somehow didn't happen.
    server._record_job_started(
        "stale-running-id",
        backend="swift",
        input_path="/tmp/a.wav",
        num_speakers=2,
        pid=1,
    )

    result = server.get_transcript("stale-running-id")
    assert result["status"] == "interrupted"


def test_reconcile_registry_on_startup_marks_running_as_interrupted():
    server._record_job_started(
        "reconcile-id", backend="swift", input_path="/tmp/a.wav", num_speakers=2, pid=1
    )

    server._reconcile_registry_on_startup()

    record = server._load_registry()["reconcile-id"]
    assert record["status"] == "interrupted"
    assert "restarted" in record["error"]
    assert record["finished_at"] is not None


def test_reconcile_registry_on_startup_marks_running_as_interrupted_even_if_pid_alive():
    # transcribe()'s backend child runs in its own session and can easily
    # outlive a crashed/restarted server, with nothing left to ever finalize
    # it - so a still-alive pid must not be treated as evidence some other
    # live server instance owns this job (that used to leave it "running"
    # forever; see _reconcile_registry_on_startup's docstring).
    server._record_job_started(
        "live-pid-id",
        backend="swift",
        input_path="/tmp/a.wav",
        num_speakers=2,
        pid=os.getpid(),
    )

    server._reconcile_registry_on_startup()

    record = server._load_registry()["live-pid-id"]
    assert record["status"] == "interrupted"


def test_reconcile_registry_on_startup_leaves_terminal_statuses_alone():
    server._record_job_started(
        "already-done-id",
        backend="swift",
        input_path="/tmp/a.wav",
        num_speakers=2,
        pid=1,
    )
    server._update_job_record(
        "already-done-id", status="done", output_path="/tmp/out.md", error=None
    )

    server._reconcile_registry_on_startup()

    record = server._load_registry()["already-done-id"]
    assert record["status"] == "done"


def test_load_registry_raises_unreadable_on_corrupt_file():
    # Unlike a missing file (no job has ever started - a real empty
    # registry), corrupt JSON must not be silently treated as empty: a
    # write-path caller building on top of that would atomically replace
    # the file with just its own record, erasing every other persisted job.
    server.JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    server.JOBS_FILE.write_text("not json{{{")
    try:
        server._load_registry()
        assert False, "expected _RegistryUnreadable"
    except server._RegistryUnreadable:
        pass


def test_load_registry_raises_unreadable_on_read_error(monkeypatch):
    def _boom(*args, **kwargs):
        raise OSError("permission denied")

    server.JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    server.JOBS_FILE.write_text("{}")
    monkeypatch.setattr(server.Path, "read_text", _boom)
    try:
        server._load_registry()
        assert False, "expected _RegistryUnreadable"
    except server._RegistryUnreadable:
        pass


def test_record_job_started_does_not_wipe_registry_when_unreadable(monkeypatch):
    # A transient read failure must not be treated as "no jobs yet" - that
    # would make this write an atomic replace containing only the new
    # record, silently erasing every previously persisted job.
    server._record_job_started(
        "pre-existing-id",
        backend="swift",
        input_path="/tmp/a.wav",
        num_speakers=1,
        pid=1,
    )

    def _boom():
        raise server._RegistryUnreadable("boom")

    monkeypatch.setattr(server, "_load_registry", _boom)
    result = server._record_job_started(
        "new-id", backend="swift", input_path="/tmp/b.wav", num_speakers=1, pid=2
    )
    assert result is False

    monkeypatch.undo()
    registry = server._load_registry()
    assert "pre-existing-id" in registry
    assert "new-id" not in registry


def test_update_job_record_does_not_wipe_registry_when_unreadable(monkeypatch):
    server._record_job_started(
        "pre-existing-id2",
        backend="swift",
        input_path="/tmp/a.wav",
        num_speakers=1,
        pid=1,
    )

    def _boom():
        raise server._RegistryUnreadable("boom")

    monkeypatch.setattr(server, "_load_registry", _boom)
    result = server._update_job_record(
        "pre-existing-id2", status="done", output_path="/tmp/a.md", error=None
    )
    assert result is False

    monkeypatch.undo()
    registry = server._load_registry()
    assert registry["pre-existing-id2"]["status"] == "running"


def test_reconcile_registry_on_startup_skips_write_when_unreadable(monkeypatch):
    def _boom():
        raise server._RegistryUnreadable("boom")

    monkeypatch.setattr(server, "_load_registry", _boom)
    server._reconcile_registry_on_startup()  # must not raise


def test_get_transcript_reports_unavailable_when_registry_unreadable(monkeypatch):
    def _boom():
        raise server._RegistryUnreadable("boom")

    monkeypatch.setattr(server, "_load_registry", _boom)
    result = server.get_transcript("whatever-id")
    assert result["status"] == "unknown"
    assert "registry" in result["error"]


def test_list_jobs_degrades_when_registry_unreadable(monkeypatch):
    def _boom():
        raise server._RegistryUnreadable("boom")

    monkeypatch.setattr(server, "_load_registry", _boom)
    result = server.list_jobs()  # must not raise
    assert result == {"jobs": []}


def test_load_registry_returns_empty_on_non_dict_json():
    # Valid JSON that isn't the expected {job_id: record} shape must not
    # crash _reconcile_registry_on_startup (which calls .values() on this
    # at import time) - that would take the whole server down.
    server.JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    server.JOBS_FILE.write_text("[]")
    assert server._load_registry() == {}
    server.JOBS_FILE.write_text("null")
    assert server._load_registry() == {}


def _full_record(job_id: str, **overrides) -> dict:
    record = {
        "job_id": job_id,
        "backend": "swift",
        "input_path": "/tmp/a.wav",
        "num_speakers": 1,
        "pid": 1,
        "status": "done",
        "output_path": "/tmp/a.md",
        "error": None,
        "started_at": "2020-01-01T00:00:00",
        "finished_at": "2020-01-01T01:00:00",
    }
    record.update(overrides)
    return record


def test_load_registry_drops_non_dict_records():
    good = _full_record("good")
    server.JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    server.JOBS_FILE.write_text(json.dumps({"good": good, "bad": "not a record"}))
    assert server._load_registry() == {"good": good}


def test_load_registry_drops_records_missing_required_fields():
    # A hand-edited or partially corrupted jobs.json could have a record
    # missing a field that _reconcile_registry_on_startup/_prune_registry
    # index directly (e.g. "status", "started_at", "job_id") - loading it
    # unchanged would crash those callers, one of them at import time.
    good = _full_record("good")
    incomplete = {"job_id": "incomplete", "status": "running"}
    server.JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    server.JOBS_FILE.write_text(json.dumps({"good": good, "incomplete": incomplete}))
    assert server._load_registry() == {"good": good}


def test_load_registry_drops_records_with_mismatched_job_id():
    # _prune_registry deletes by record["job_id"], which must match the
    # dict key it's stored under, or that delete targets the wrong entry
    # (or a KeyError if the claimed job_id isn't a key at all).
    good = _full_record("good")
    mismatched = _full_record("other-id")
    server.JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    server.JOBS_FILE.write_text(
        json.dumps({"good": good, "mismatched-key": mismatched})
    )
    assert server._load_registry() == {"good": good}


def test_load_registry_drops_records_with_unhashable_status_without_crashing():
    # A list/dict value for "status" would make `x in _VALID_STATUSES` raise
    # TypeError (set membership hashes the operand) rather than just fail
    # validation - which would crash _reconcile_registry_on_startup at
    # import time, exactly what this validation exists to prevent.
    good = _full_record("good")
    bad = _full_record("bad", status=["running"])
    server.JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    server.JOBS_FILE.write_text(json.dumps({"good": good, "bad": bad}))
    assert server._load_registry() == {"good": good}


def test_load_registry_drops_records_with_unknown_status():
    good = _full_record("good")
    bogus = _full_record("bogus", status="not-a-real-status")
    server.JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    server.JOBS_FILE.write_text(json.dumps({"good": good, "bogus": bogus}))
    assert server._load_registry() == {"good": good}


def test_load_registry_drops_records_with_wrong_field_types():
    # A hand-edited non-string started_at would otherwise crash list_jobs's
    # sort (comparing str against the wrong type) and _prune_registry's
    # sort key.
    good = _full_record("good")
    bad_started_at = _full_record("bad-started-at", started_at=12345)
    bad_num_speakers = _full_record("bad-num-speakers", num_speakers="two")
    bad_output_path = _full_record("bad-output-path", output_path=123)
    server.JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    server.JOBS_FILE.write_text(
        json.dumps(
            {
                "good": good,
                "bad-started-at": bad_started_at,
                "bad-num-speakers": bad_num_speakers,
                "bad-output-path": bad_output_path,
            }
        )
    )
    assert server._load_registry() == {"good": good}


def test_record_job_started_does_not_clobber_already_terminal_record():
    # transcribe() now constructs the Job (and starts its finalizer thread)
    # before this is even attempted, so for a near-instantly completing job
    # the finalizer can persist a terminal outcome first. job_id is a fresh
    # uuid used for the first time by this exact job, so a terminal record
    # already existing for it can only mean that - overwriting it back to
    # "running" here would clobber an already-correct outcome.
    server._update_job_record(
        "already-done-id",
        status="done",
        output_path="/tmp/x.md",
        error=None,
        fallback_start={
            "backend": "swift",
            "input_path": "/tmp/a.wav",
            "num_speakers": 1,
            "pid": 4242,
            "started_at": "2020-01-01T00:00:00+00:00",
        },
    )

    result = server._record_job_started(
        "already-done-id",
        backend="swift",
        input_path="/tmp/a.wav",
        num_speakers=1,
        pid=4242,
    )

    assert result is True
    record = server._load_registry()["already-done-id"]
    assert record["status"] == "done"
    assert record["output_path"] == "/tmp/x.md"


def test_update_job_record_prunes(monkeypatch):
    # Pruning previously only happened in _record_job_started, so the
    # registry could stay over MAX_PERSISTED_JOBS indefinitely once no new
    # job started a fresh prune pass.
    monkeypatch.setattr(server, "MAX_PERSISTED_JOBS", 1)
    server._record_job_started(
        "old-id", backend="swift", input_path="/tmp/a.wav", num_speakers=1, pid=1
    )
    server._update_job_record(
        "old-id", status="done", output_path="/tmp/a.md", error=None
    )
    server._record_job_started(
        "new-id", backend="swift", input_path="/tmp/b.wav", num_speakers=1, pid=2
    )

    server._update_job_record(
        "new-id", status="done", output_path="/tmp/b.md", error=None
    )

    assert set(server._load_registry()) == {"new-id"}


def test_update_job_record_returns_false_when_no_record_exists():
    assert (
        server._update_job_record(
            "never-started-id", status="done", output_path="/tmp/x.md", error=None
        )
        is False
    )


def test_update_job_record_reconstructs_record_via_fallback_start():
    # If _record_job_started's own initial write failed (e.g. a transient
    # disk error), there's no record for a later retry to find no matter
    # how many times it tries - fallback_start lets it create one instead
    # of giving up, so the terminal outcome isn't stranded.
    result = server._update_job_record(
        "reconstructed-id",
        status="done",
        output_path="/tmp/x.md",
        error=None,
        fallback_start={
            "backend": "swift",
            "input_path": "/tmp/a.wav",
            "num_speakers": 2,
            "pid": 4242,
        },
    )

    assert result is True
    record = server._load_registry()["reconstructed-id"]
    assert record["status"] == "done"
    assert record["output_path"] == "/tmp/x.md"
    assert record["backend"] == "swift"
    assert record["input_path"] == "/tmp/a.wav"
    assert record["num_speakers"] == 2
    assert record["pid"] == 4242
    assert record["started_at"] is not None


def test_update_job_record_reconstructs_record_preserves_started_at():
    # fallback_start's own started_at (the job's real start time) must win
    # over the placeholder "now" this function would otherwise stamp -
    # using reconstruction time here would make a job that's been running
    # for hours look like it just started, corrupting list_jobs's ordering
    # and letting it dodge pruning ahead of genuinely recent jobs.
    result = server._update_job_record(
        "reconstructed-with-start-id",
        status="done",
        output_path="/tmp/x.md",
        error=None,
        fallback_start={
            "backend": "swift",
            "input_path": "/tmp/a.wav",
            "num_speakers": 2,
            "pid": 4242,
            "started_at": "2020-01-01T00:00:00+00:00",
        },
    )

    assert result is True
    record = server._load_registry()["reconstructed-with-start-id"]
    assert record["started_at"] == "2020-01-01T00:00:00+00:00"


def test_update_job_record_reconstructs_falls_back_to_now_when_started_at_empty():
    # A Job constructed without a real started_at (its "" default - e.g. one
    # built directly by a test, or hypothetically some future code path
    # that skips transcribe()) must not propagate that empty string into a
    # reconstructed record: "" is technically a valid str (passes
    # _is_valid_record's type check) but is a meaningless timestamp that
    # would sort before every real one and confuse list_jobs's ordering.
    result = server._update_job_record(
        "reconstructed-empty-start-id",
        status="done",
        output_path="/tmp/x.md",
        error=None,
        fallback_start={
            "backend": "swift",
            "input_path": "/tmp/a.wav",
            "num_speakers": 2,
            "pid": 4242,
            "started_at": "",
        },
    )

    assert result is True
    record = server._load_registry()["reconstructed-empty-start-id"]
    assert record["started_at"]


def _make_blocking_proc(
    pid: int = 4242, stdout: bytes = b""
) -> tuple[MagicMock, threading.Event]:
    """A mock subprocess whose stdout/stderr collectors block until
    unblocked, so a test can inspect state before the finalizer races ahead
    and persists/evicts the job. Once unblocked, the stdout collector
    yields `stdout`'s lines and the job resolves as done/failed exactly as
    _make_proc's would - so a test can deterministically trigger completion
    after setting up state that must exist first (e.g. patching in a flaky
    _write_registry only once transcribe()'s own initial write has already
    succeeded for real)."""
    unblock = threading.Event()

    class _BlockingStdout:
        def __iter__(self):
            unblock.wait()
            return iter(io.BytesIO(stdout).readlines())

        def close(self):
            pass

    proc = MagicMock()
    proc.stdout = _BlockingStdout()
    proc.stderr = io.BytesIO(b"")
    proc.returncode = 0
    proc.poll.return_value = None
    proc.pid = pid
    return proc, unblock


def test_transcribe_retries_registry_write_and_succeeds(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.touch()
    mock_proc, unblock = _make_blocking_proc()

    real_write_registry = server._write_registry
    calls = []

    def flaky_write(registry):
        calls.append(registry)
        if len(calls) == 1:
            return False
        return real_write_registry(registry)

    # Assert everything while the collectors are still blocked and the
    # patch is still in place, then unblock only at the very end: once
    # unblocked, the finalizer will call the patched _write_registry too
    # (it's the same job), and if that races ahead of these assertions it
    # can turn a 2-call count into 3, or persist/evict the job before the
    # "still running" checks below run.
    with patch("server.select_backend", return_value=("swift", ["/bin/echo"])), patch(
        "subprocess.Popen", return_value=mock_proc
    ), patch.object(server, "_write_registry", flaky_write):
        result = server.transcribe(str(audio), 2)
        assert len(calls) == 2
        assert result["job_id"] in server.jobs
        record = server._load_registry()[result["job_id"]]
        assert record["status"] == "running"
    unblock.set()


def test_transcribe_still_tracks_job_live_when_all_registry_writes_fail(tmp_path):
    audio = tmp_path / "audio.wav"
    audio.touch()
    mock_proc, unblock = _make_blocking_proc()

    # Same ordering as above: assert before unblocking. Once unblocked, the
    # finalizer's own call to the still-patched (always failing)
    # _write_registry would keep the record absent either way here, but
    # unblocking first and reverting the patch on `with` exit could let a
    # racing finalizer thread persist via the *real* _write_registry before
    # these assertions run, flipping both of them.
    with patch("server.select_backend", return_value=("swift", ["/bin/echo"])), patch(
        "subprocess.Popen", return_value=mock_proc
    ), patch.object(server, "_write_registry", lambda registry: False):
        result = server.transcribe(str(audio), 2)
        assert result["job_id"] in server.jobs
        assert result["job_id"] not in server._load_registry()
    unblock.set()


def test_transcribe_does_not_hold_registry_lock_during_retry_sleep(
    tmp_path, monkeypatch
):
    # A registry write failure for one job must not stall every other job's
    # registry access for the whole retry budget - see _record_job_started
    # and transcribe(). Force a slow retry delay and confirm another thread
    # can still acquire _registry_lock quickly (e.g. as list_jobs/another
    # transcribe() call would) *during* transcribe()'s sleep between
    # attempts - a short acquire timeout well under the retry delay means
    # this can only succeed if the lock was actually released for the
    # sleep, not just eventually freed once transcribe() finishes.
    monkeypatch.setattr(server, "_PERSIST_RETRY_DELAY", 2.0)
    audio = tmp_path / "audio.wav"
    audio.touch()
    mock_proc, unblock = _make_blocking_proc()

    real_write_registry = server._write_registry
    calls = []

    def flaky_write(registry):
        calls.append(registry)
        if len(calls) == 1:
            return False
        return real_write_registry(registry)

    acquired = threading.Event()

    def try_acquire_during_sleep():
        # Give transcribe()'s first (failing) attempt a moment to finish
        # and enter its 2s sleep before we try.
        time.sleep(0.3)
        if server._registry_lock.acquire(timeout=0.5):
            acquired.set()
            server._registry_lock.release()

    # Assert while still patched/blocked, same reasoning as the other two
    # transcribe() retry tests above - unblocking before checking len(calls)
    # would let the finalizer's own call to the still-patched flaky_write
    # add a third entry before the assertion runs.
    with patch("server.select_backend", return_value=("swift", ["/bin/echo"])), patch(
        "subprocess.Popen", return_value=mock_proc
    ), patch.object(server, "_write_registry", flaky_write):
        checker = threading.Thread(target=try_acquire_during_sleep, daemon=True)
        checker.start()
        server.transcribe(str(audio), 2)
        checker.join(timeout=3.0)
        assert len(calls) == 2
        assert acquired.is_set()
    unblock.set()


def test_persist_terminal_outcome_reconstructs_missing_record(tmp_path):
    # End-to-end: a Job whose registry record never existed (simulating the
    # initial write having failed) must still be persisted and evicted when
    # its outcome is resolved, via Job.input_path/num_speakers.
    transcript = tmp_path / "t.md"
    mock_proc = _make_proc(f"    local       : {transcript}\n".encode(), b"", 0)
    mock_proc.pid = 4242
    job = server.Job(
        proc=mock_proc,
        backend="swift",
        job_id="never-recorded-id",
        input_path="/tmp/a.wav",
        num_speakers=3,
    )
    # Mirror transcribe()'s own lifecycle-lock-aware insertion: the
    # finalizer thread (started by the Job() constructor above) can
    # complete and flag _eviction_pending before this line, since
    # persistence here is real (that's what this test is verifying) rather
    # than mocked to always fail. An unconditional insert would then
    # silently defeat that eviction.
    with job._lifecycle_lock:
        if not job._eviction_pending:
            server.jobs["never-recorded-id"] = job

    record = _wait_for_terminal_record("never-recorded-id")
    assert record["status"] == "done"
    assert record["backend"] == "swift"
    assert record["input_path"] == "/tmp/a.wav"
    assert record["num_speakers"] == 3
    assert "never-recorded-id" not in server.jobs


def test_update_job_record_protects_just_updated_record_from_its_own_prune(
    monkeypatch,
):
    # Without protecting the record being finalized, pruning could remove
    # the very entry this call just wrote (e.g. because it's the oldest, or
    # only, completed record) while still reporting success - after which
    # the finalizer would evict the live Job, and the job becomes truly
    # unrecoverable: not in `jobs`, not in the registry either.
    monkeypatch.setattr(server, "MAX_PERSISTED_JOBS", 1)
    server._record_job_started(
        "a-id", backend="swift", input_path="/tmp/a.wav", num_speakers=1, pid=1
    )
    server._record_job_started(
        "b-id", backend="swift", input_path="/tmp/b.wav", num_speakers=1, pid=2
    )

    result = server._update_job_record(
        "a-id", status="done", output_path="/tmp/a.md", error=None
    )

    assert result is True
    assert "a-id" in server._load_registry()


def test_update_job_record_returns_false_when_write_fails(monkeypatch):
    server._record_job_started(
        "write-fail-id", backend="swift", input_path="/tmp/a.wav", num_speakers=1, pid=1
    )
    monkeypatch.setattr(server, "_write_registry", lambda registry: False)

    result = server._update_job_record(
        "write-fail-id", status="done", output_path="/tmp/a.md", error=None
    )

    assert result is False


def test_reconcile_registry_on_startup_prunes_all_running_over_cap(monkeypatch):
    # _prune_registry alone can never touch a "running" entry, so a
    # registry that's over cap purely on running jobs stayed over cap
    # indefinitely until reconciliation converted them to a prunable
    # terminal status - reconciliation needs its own prune pass for that,
    # not just the one in _record_job_started/_update_job_record.
    monkeypatch.setattr(server, "MAX_PERSISTED_JOBS", 1)
    server._record_job_started(
        "old-running-id",
        backend="swift",
        input_path="/tmp/a.wav",
        num_speakers=1,
        pid=1,
    )
    server._record_job_started(
        "new-running-id",
        backend="swift",
        input_path="/tmp/b.wav",
        num_speakers=1,
        pid=2,
    )
    assert len(server._load_registry()) == 2

    server._reconcile_registry_on_startup()

    registry = server._load_registry()
    assert len(registry) == 1
    assert "new-running-id" in registry


def test_reconcile_registry_on_startup_prunes_over_cap_terminal_when_unchanged(
    monkeypatch,
):
    # A registry already over MAX_PERSISTED_JOBS with only terminal records
    # (nothing to convert from "running", e.g. left behind by the benign
    # transient overshoot _update_job_record's own protect/_pending_eviction
    # can allow) used to skip pruning here entirely, since the write was
    # gated on `changed` - leaving it over cap indefinitely across a
    # restart instead of just until the next job event.
    monkeypatch.setattr(server, "MAX_PERSISTED_JOBS", 1)
    server._record_job_started(
        "old-done-id", backend="swift", input_path="/tmp/a.wav", num_speakers=1, pid=1
    )
    server._update_job_record(
        "old-done-id", status="done", output_path="/tmp/a.md", error=None
    )
    monkeypatch.setattr(server, "MAX_PERSISTED_JOBS", 2)
    server._record_job_started(
        "new-done-id", backend="swift", input_path="/tmp/b.wav", num_speakers=1, pid=2
    )
    server._update_job_record(
        "new-done-id", status="done", output_path="/tmp/b.md", error=None
    )
    monkeypatch.setattr(server, "MAX_PERSISTED_JOBS", 1)
    assert len(server._load_registry()) == 2

    server._reconcile_registry_on_startup()

    registry = server._load_registry()
    assert len(registry) == 1
    assert "new-done-id" in registry


def test_persist_terminal_outcome_flags_eviction_pending_when_not_yet_inserted():
    # transcribe() now holds _registry_lock (reentrant) across writing a
    # job's initial record and inserting it into `jobs` as one atomic unit,
    # which structurally prevents a finalizer from completing
    # _persist_terminal_outcome (whose first step also needs that lock)
    # before insertion - so this can no longer be forced end-to-end through
    # transcribe() itself. Exercise the underlying mechanism directly
    # instead: a Job not yet present in `jobs` at all (as if some future
    # caller inserted after resolving its outcome, or another code path)
    # must be flagged rather than silently ignored, so whoever inserts it
    # later can still honor that flag - see transcribe()'s own check.
    proc = MagicMock()
    proc.pid = 4242
    with patch.object(server.Job, "__post_init__", lambda self: None):
        job = server.Job(proc=proc, backend="swift", job_id="not-inserted-id")

    server._record_job_started(
        "not-inserted-id",
        backend="swift",
        input_path="/tmp/a.wav",
        num_speakers=1,
        pid=4242,
    )

    persisted = server._persist_terminal_outcome(
        job, {"status": "done", "output_path": "/tmp/out.md"}
    )

    assert persisted is True
    assert job._eviction_pending is True
    assert "not-inserted-id" not in server.jobs
    record = server._load_registry()["not-inserted-id"]
    assert record["status"] == "done"

    # And the flag is honored by an insertion that checks it afterward,
    # mirroring transcribe()'s own lifecycle-lock-aware insert.
    with job._lifecycle_lock:
        if not job._eviction_pending:
            server.jobs["not-inserted-id"] = job
    assert "not-inserted-id" not in server.jobs


def test_persist_terminal_outcome_does_not_resurrect_pruned_record():
    # list_jobs snapshots `jobs` and processes it outside _registry_lock, so
    # by the time its loop reaches a given job, that job's own finalizer may
    # have already persisted its outcome, evicted it, and - if it was the
    # oldest completed record once no longer "running" - had it pruned by
    # some unrelated job's finalizer. A second, stale call for that same Job
    # object must not use fallback_start to reconstruct a record the cap
    # already forgot on purpose.
    proc = MagicMock()
    proc.pid = 4242
    with patch.object(server.Job, "__post_init__", lambda self: None):
        job = server.Job(proc=proc, backend="swift", job_id="pruned-id")
    server.jobs["pruned-id"] = job

    server._record_job_started(
        "pruned-id", backend="swift", input_path="/tmp/a.wav", num_speakers=1, pid=4242
    )
    first = server._persist_terminal_outcome(
        job, {"status": "done", "output_path": "/tmp/out.md"}
    )
    assert first is True
    assert job._outcome_persisted is True
    assert "pruned-id" not in server.jobs

    # Simulate some unrelated job's finalizer pruning this now-completed,
    # no-longer-protected record out of the registry entirely.
    server._write_registry({})
    assert "pruned-id" not in server._load_registry()

    second = server._persist_terminal_outcome(
        job, {"status": "done", "output_path": "/tmp/out.md"}
    )
    assert second is True
    # Must stay pruned - not resurrected via fallback_start.
    assert "pruned-id" not in server._load_registry()


def test_list_jobs_clamps_negative_limit():
    server._record_job_started(
        "some-id", backend="swift", input_path="/tmp/a.wav", num_speakers=1, pid=1
    )
    assert server.list_jobs(limit=-5) == {"jobs": []}


def test_list_jobs_survives_concurrent_transcribe(tmp_path):
    # transcribe() inserts into `jobs` from list_jobs's perspective "mid
    # iteration" if it runs on another thread at the wrong moment; iterating
    # a live, unsynchronized dict under that race can raise "dictionary
    # changed size during iteration".
    #
    # unittest.mock.patch is not itself thread-safe against concurrent
    # enter/exit on the same target, so it's applied once here from the
    # main thread around the whole concurrent section rather than inside
    # each worker thread.
    unblock = threading.Event()

    class _BlockingStdout:
        def __iter__(self):
            unblock.wait()
            return iter([])

        def close(self):
            pass

    def _make_blocking_proc(*_args, **_kwargs):
        proc = MagicMock()
        proc.stdout = _BlockingStdout()
        proc.stderr = io.BytesIO(b"")
        proc.returncode = None
        proc.poll.return_value = None
        return proc

    def _start_job(n: int):
        audio = tmp_path / f"audio-{n}.wav"
        audio.touch()
        server.transcribe(str(audio), 2)

    threads = [threading.Thread(target=_start_job, args=(n,)) for n in range(20)]
    try:
        with patch(
            "server.select_backend", return_value=("swift", ["/bin/echo"])
        ), patch("subprocess.Popen", side_effect=_make_blocking_proc):
            for t in threads:
                t.start()
            for _ in range(50):
                server.list_jobs()
            unblock.set()
            for t in threads:
                t.join(timeout=2.0)
    finally:
        unblock.set()


def test_list_jobs_sets_finished_at_for_completed_live_job_not_yet_finalized(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(server, "_update_job_record", lambda *a, **k: False)

    transcript = tmp_path / "t.md"
    proc = _make_proc(f"    local       : {transcript}\n".encode(), b"", 0)
    server._record_job_started(
        "finished-at-id",
        backend="swift",
        input_path="/tmp/a.wav",
        num_speakers=2,
        pid=proc.pid,
    )
    job = server.Job(proc=proc, backend="swift", job_id="finished-at-id")
    server.jobs["finished-at-id"] = job
    _wait(job)
    # Let the finalizer's own retry loop (against the mocked, always-failing
    # _update_job_record) fully exhaust and give up before this test
    # returns - otherwise a still-in-flight retry could land on the *real*
    # _update_job_record right after monkeypatch reverts, writing to
    # whatever jobs.json exists by the time a later test runs.
    job._finalizer_done.wait(timeout=2.0)

    result = server.list_jobs()
    by_id = {entry["job_id"]: entry for entry in result["jobs"]}
    assert by_id["finished-at-id"]["finished_at"] is not None


def test_prune_registry_drops_oldest_completed_first(monkeypatch):
    monkeypatch.setattr(server, "MAX_PERSISTED_JOBS", 2)
    registry = {
        "old": {"job_id": "old", "status": "done", "started_at": "2020-01-01T00:00:00"},
        "mid": {
            "job_id": "mid",
            "status": "failed",
            "started_at": "2020-06-01T00:00:00",
        },
        "new": {"job_id": "new", "status": "done", "started_at": "2021-01-01T00:00:00"},
        "still-running": {
            "job_id": "still-running",
            "status": "running",
            "started_at": "2019-01-01T00:00:00",
        },
    }
    server._prune_registry(registry)
    # overflow = 4 - 2 = 2: drops the 2 oldest *completed* entries ("old",
    # then "mid"), never the still-running one regardless of its age.
    assert set(registry) == {"new", "still-running"}


def test_prune_registry_protects_pending_eviction_entries(monkeypatch):
    # A cross-finalizer race: finalizer A's own _update_job_record call only
    # protects A's own record from ITS pruning pass (via `protect`), not
    # from a separately-running finalizer B's later pruning pass. Without
    # also excluding _pending_eviction, B's prune could remove A's
    # just-written record before A gets to act on the promise that record's
    # existence made (evicting A's live handle) - leaving A in neither
    # `jobs` nor the registry.
    monkeypatch.setattr(server, "MAX_PERSISTED_JOBS", 1)
    server._record_job_started(
        "a-id", backend="swift", input_path="/tmp/a.wav", num_speakers=1, pid=1
    )
    server._update_job_record(
        "a-id", status="done", output_path="/tmp/a.md", error=None
    )
    server._pending_eviction.add("a-id")
    try:
        server._record_job_started(
            "b-id", backend="swift", input_path="/tmp/b.wav", num_speakers=1, pid=2
        )
        server._update_job_record(
            "b-id", status="done", output_path="/tmp/b.md", error=None
        )

        registry = server._load_registry()
        assert "a-id" in registry
        assert "b-id" in registry
    finally:
        server._pending_eviction.discard("a-id")


def test_list_jobs_merges_registry_and_live_state(tmp_path):
    server._record_job_started(
        "old-done-id",
        backend="python",
        input_path="/tmp/old.wav",
        num_speakers=1,
        pid=1,
    )
    server._update_job_record(
        "old-done-id", status="done", output_path="/tmp/old.md", error=None
    )

    audio = tmp_path / "audio.wav"
    audio.touch()

    unblock = threading.Event()

    class _BlockingStdout:
        def __iter__(self):
            yield b"==> Transcribing audio...\n"
            unblock.wait()

        def close(self):
            pass

    live_proc = MagicMock()
    live_proc.stdout = _BlockingStdout()
    live_proc.stderr = io.BytesIO(b"")
    live_proc.returncode = None
    live_proc.poll.return_value = None
    live_proc.pid = 9999

    with patch("server.select_backend", return_value=("swift", ["/bin/echo"])), patch(
        "subprocess.Popen", return_value=live_proc
    ):
        started = server.transcribe(str(audio), 2)

    try:
        result = server.list_jobs()
        by_id = {entry["job_id"]: entry for entry in result["jobs"]}
        assert by_id["old-done-id"]["status"] == "done"
        assert by_id["old-done-id"]["output_path"] == "/tmp/old.md"
        assert by_id[started["job_id"]]["status"] == "running"
    finally:
        unblock.set()


def test_list_jobs_recomputes_outcome_for_completed_job_not_yet_finalized(
    tmp_path, monkeypatch
):
    # Simulate the background finalizer thread not having written yet, to
    # exercise the case where the on-disk registry still says "running" for
    # an already-completed live job - list_jobs must recompute the real
    # outcome rather than trust that stale snapshot.
    monkeypatch.setattr(server, "_update_job_record", lambda *a, **k: None)

    transcript = tmp_path / "t.md"
    proc = _make_proc(f"    local       : {transcript}\n".encode(), b"", 0)
    server._record_job_started(
        "fresh-done-id",
        backend="swift",
        input_path="/tmp/a.wav",
        num_speakers=2,
        pid=proc.pid,
    )
    job = server.Job(proc=proc, backend="swift", job_id="fresh-done-id")
    server.jobs["fresh-done-id"] = job
    _wait(job)
    # Let the finalizer's own retry loop (against the mocked, always-failing
    # _update_job_record) fully exhaust and give up before this test
    # returns - otherwise a still-in-flight retry could land on the *real*
    # _update_job_record right after monkeypatch reverts, writing to
    # whatever jobs.json exists by the time a later test runs.
    job._finalizer_done.wait(timeout=2.0)

    result = server.list_jobs()
    by_id = {entry["job_id"]: entry for entry in result["jobs"]}
    assert by_id["fresh-done-id"]["status"] == "done"
    assert by_id["fresh-done-id"]["output_path"] == str(transcript)


def test_list_jobs_reports_orphaned_running_registry_entry_as_interrupted():
    # Same fail-safe as get_transcript: a registry-only entry with no live
    # handle (e.g. a startup reconciliation write that itself failed) must
    # not be shown as still "running".
    server._record_job_started(
        "orphaned-running-id",
        backend="swift",
        input_path="/tmp/a.wav",
        num_speakers=1,
        pid=1,
    )

    result = server.list_jobs()

    by_id = {entry["job_id"]: entry for entry in result["jobs"]}
    assert by_id["orphaned-running-id"]["status"] == "interrupted"
    assert by_id["orphaned-running-id"]["error"] == server._ORPHANED_RUNNING_ERROR


def test_list_jobs_respects_limit():
    for i in range(5):
        server._record_job_started(
            f"job-{i}", backend="swift", input_path="/tmp/a.wav", num_speakers=1, pid=1
        )
    result = server.list_jobs(limit=2)
    assert len(result["jobs"]) == 2


def test_get_config_success():
    mock_result = MagicMock(stdout="en\n", stderr="", returncode=0)
    with patch("server.select_backend", return_value=("swift", ["/bin/echo"])), patch(
        "subprocess.run", return_value=mock_result
    ) as mock_run:
        result = server.get_config("language")

    assert result == {"key": "language", "value": "en"}
    args = mock_run.call_args[0][0]
    assert args == ["/bin/echo", "config", "get", "language"]


def test_get_config_unknown_key():
    mock_result = MagicMock(
        stdout="",
        stderr="!! Unknown config key: bogus\n    Valid keys: language, model\n",
        returncode=2,
    )
    with patch("server.select_backend", return_value=("swift", ["/bin/echo"])), patch(
        "subprocess.run", return_value=mock_result
    ):
        result = server.get_config("bogus")

    assert "error" in result
    assert "Valid keys" in result["error"]


def test_get_config_no_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "REPO_ROOT", tmp_path)
    with patch("platform.system", return_value="Linux"):
        result = server.get_config("language")
    assert "error" in result
    assert "no backend" in result["error"]


def test_get_config_timeout():
    with patch("server.select_backend", return_value=("swift", ["/bin/echo"])), patch(
        "subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="diarize", timeout=15),
    ):
        result = server.get_config("language")
    assert "error" in result
    assert "timed out" in result["error"]


def test_set_config_success():
    mock_result = MagicMock(
        stdout="==> Set language = fr in /tmp/config.json\n", stderr="", returncode=0
    )
    with patch("server.select_backend", return_value=("swift", ["/bin/echo"])), patch(
        "subprocess.run", return_value=mock_result
    ) as mock_run:
        result = server.set_config("language", "fr")

    assert result["status"] == "ok"
    assert "language = fr" in result["message"]
    args = mock_run.call_args[0][0]
    assert args == ["/bin/echo", "config", "set", "language", "fr"]


def test_set_config_unknown_key():
    mock_result = MagicMock(
        stdout="",
        stderr="!! Unknown config key: bogus\n    Valid keys: language, model\n",
        returncode=2,
    )
    with patch("server.select_backend", return_value=("swift", ["/bin/echo"])), patch(
        "subprocess.run", return_value=mock_result
    ):
        result = server.set_config("bogus", "x")

    assert "error" in result
    assert "Valid keys" in result["error"]


def test_set_config_no_backend(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "REPO_ROOT", tmp_path)
    with patch("platform.system", return_value="Linux"):
        result = server.set_config("language", "fr")
    assert "error" in result
    assert "no backend" in result["error"]


def test_collector_error_kills_still_running_process(monkeypatch):
    """If a collector dies while the child is still alive, nothing would
    otherwise drain its pipe - kill the child so the sibling collector and
    the child itself can't block forever."""
    monkeypatch.setattr(server, "_HAS_PROCESS_GROUP_KILL", True)

    class _BrokenStdout:
        def __iter__(self):
            raise OSError("broken pipe")

        def close(self):
            pass

    proc = MagicMock()
    proc.stdout = _BrokenStdout()
    proc.stderr = io.BytesIO(b"")
    proc.returncode = None
    proc.pid = 4242
    proc.poll.return_value = None  # still running when the error occurs

    with patch("os.getpgid", return_value=4242, create=True), patch(
        "os.killpg", create=True
    ) as mock_killpg, patch("signal.SIGKILL", 9, create=True):
        job = server.Job(proc=proc, backend="swift")
        _wait(job)

        assert job.is_complete()
        # The whole process group is signalled (not just proc.pid) so the
        # real worker survives no orphaning when proc is the `uv run` wrapper.
        mock_killpg.assert_called_once_with(4242, 9)
