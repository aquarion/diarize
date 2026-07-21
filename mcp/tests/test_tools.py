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


def test_get_transcript_unknown_job():
    result = server.get_transcript("no-such-id")
    assert result == {"status": "failed", "error": "unknown job_id"}


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


def test_collector_error_kills_still_running_process():
    """If a collector dies while the child is still alive, nothing would
    otherwise drain its pipe - kill the child so the sibling collector and
    the child itself can't block forever."""

    class _BrokenStdout:
        def __iter__(self):
            raise OSError("broken pipe")

        def close(self):
            pass

    proc = MagicMock()
    proc.stdout = _BrokenStdout()
    proc.stderr = io.BytesIO(b"")
    proc.returncode = None
    proc.poll.return_value = None  # still running when the error occurs

    job = server.Job(proc=proc, backend="swift")
    _wait(job)

    assert job.is_complete()
    proc.kill.assert_called_once()
