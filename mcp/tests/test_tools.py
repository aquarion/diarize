import time
from unittest.mock import MagicMock, patch

import server


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
    mock_proc = MagicMock()
    mock_proc.communicate.return_value = (b"    local       : /tmp/t.md\n", b"")
    mock_proc.returncode = 0

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
    mock_proc = MagicMock()
    import threading

    unblock = threading.Event()

    def slow_communicate():
        unblock.wait()
        return (b"", b"")

    mock_proc.communicate.side_effect = slow_communicate
    mock_proc.returncode = None

    job = server.Job(proc=mock_proc, backend="swift")
    server.jobs["running-id"] = job

    result = server.get_transcript("running-id")
    assert result == {"status": "running"}
    unblock.set()


def test_get_transcript_done(tmp_path):
    transcript = tmp_path / "transcript.md"
    transcript.write_text("# Meeting\n\nAlice: Hello.\nBob: Hi.")

    mock_proc = MagicMock()
    mock_proc.communicate.return_value = (
        f"==> Complete\n    local       : {transcript}\n".encode(),
        b"",
    )
    mock_proc.returncode = 0

    job = server.Job(proc=mock_proc, backend="swift")
    server.jobs["done-id"] = job
    _wait(job)

    result = server.get_transcript("done-id")
    assert result["status"] == "done"
    assert "Alice" in result["transcript"]
    assert result["output_path"] == str(transcript)


def test_get_transcript_failed():
    mock_proc = MagicMock()
    mock_proc.communicate.return_value = (b"", b"WhisperKit load error\n")
    mock_proc.returncode = 1

    job = server.Job(proc=mock_proc, backend="swift")
    server.jobs["fail-id"] = job
    _wait(job)

    result = server.get_transcript("fail-id")
    assert result["status"] == "failed"
    assert "WhisperKit load error" in result["error"]


def test_get_transcript_missing_path(tmp_path):
    mock_proc = MagicMock()
    mock_proc.communicate.return_value = (b"==> Complete\n(no local line)\n", b"")
    mock_proc.returncode = 0

    job = server.Job(proc=mock_proc, backend="swift")
    server.jobs["nopath-id"] = job
    _wait(job)

    result = server.get_transcript("nopath-id")
    assert result["status"] == "failed"
    assert "transcript path" in result["error"]
