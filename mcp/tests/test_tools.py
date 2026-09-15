import io
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
    mock_proc = _make_proc(b"    local       : /tmp/t.md\n", b"", 0)

    with patch("server.select_backend", return_value=("swift", ["/bin/echo"])), patch(
        "subprocess.Popen", return_value=mock_proc
    ):
        result = server.transcribe(str(audio), 2)

    assert "job_id" in result
    assert result["backend"] == "swift"
    assert result["job_id"] in server.jobs


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
    mock_proc = _make_proc(b"    local       : /tmp/t.md\n", b"", 0)
    mock_proc.pid = 4242

    with patch("server.select_backend", return_value=("swift", ["/bin/echo"])), patch(
        "subprocess.Popen", return_value=mock_proc
    ):
        result = server.transcribe(str(audio), 3)

    record = server._load_registry()[result["job_id"]]
    assert record["backend"] == "swift"
    assert record["input_path"] == str(audio)
    assert record["num_speakers"] == 3
    assert record["pid"] == 4242
    assert record["status"] == "running"
    assert record["output_path"] is None
    assert record["started_at"] is not None


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
        "past-missing-id", backend="swift", input_path="/tmp/a.wav", num_speakers=2, pid=1
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
        "past-failed-id", backend="swift", input_path="/tmp/a.wav", num_speakers=2, pid=1
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
        "stale-running-id", backend="swift", input_path="/tmp/a.wav", num_speakers=2, pid=1
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


def test_reconcile_registry_on_startup_leaves_terminal_statuses_alone():
    server._record_job_started(
        "already-done-id", backend="swift", input_path="/tmp/a.wav", num_speakers=2, pid=1
    )
    server._update_job_record(
        "already-done-id", status="done", output_path="/tmp/out.md", error=None
    )

    server._reconcile_registry_on_startup()

    record = server._load_registry()["already-done-id"]
    assert record["status"] == "done"


def test_load_registry_returns_empty_on_corrupt_file():
    server.JOBS_FILE.parent.mkdir(parents=True, exist_ok=True)
    server.JOBS_FILE.write_text("not json{{{")
    assert server._load_registry() == {}


def test_prune_registry_drops_oldest_completed_first(monkeypatch):
    monkeypatch.setattr(server, "MAX_PERSISTED_JOBS", 2)
    registry = {
        "old": {"job_id": "old", "status": "done", "started_at": "2020-01-01T00:00:00"},
        "mid": {"job_id": "mid", "status": "failed", "started_at": "2020-06-01T00:00:00"},
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


def test_list_jobs_merges_registry_and_live_state(tmp_path):
    server._record_job_started(
        "old-done-id", backend="python", input_path="/tmp/old.wav", num_speakers=1, pid=1
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

    result = server.list_jobs()
    by_id = {entry["job_id"]: entry for entry in result["jobs"]}
    assert by_id["fresh-done-id"]["status"] == "done"
    assert by_id["fresh-done-id"]["output_path"] == str(transcript)


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
