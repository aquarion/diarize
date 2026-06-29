# Diarize MCP Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python MCP server in `mcp/` that Claude Desktop can point at to trigger diarization jobs and retrieve finished transcripts.

**Architecture:** Two tools — `transcribe` starts a subprocess (Swift CLI on macOS if built, Python CLI otherwise) and returns a job ID; `get_transcript` polls the job and returns the finished transcript markdown. Job state lives in an in-memory dict; a background thread per job drains stdout/stderr so neither tool blocks.

**Tech Stack:** Python 3.10+, `mcp` SDK (FastMCP), stdlib only otherwise (`subprocess`, `threading`, `uuid`, `re`, `pathlib`).

## Global Constraints

- Python ≥ 3.10 (uses `match`, `X | Y` union types, `tuple[str, list[str]] | None`)
- `mcp >= 1.0` is the only non-stdlib dependency
- All files live under `mcp/` at the repo root
- Tools are synchronous (FastMCP default); no `asyncio` needed
- Repo root is inferred as `Path(__file__).parent.parent` — no hardcoded paths
- Tests use `pytest >= 7.0`; run from `mcp/` with `pytest tests/ -v`

---

### Task 1: Scaffold

**Files:**
- Create: `mcp/pyproject.toml`
- Create: `mcp/README.md`
- Create: `mcp/tests/conftest.py`

**Interfaces:**
- Produces: installable package `diarize-mcp`; `pytest` can discover tests

- [ ] **Step 1: Create `mcp/pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=68", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "diarize-mcp"
version = "0.1.0"
description = "MCP server for the diarize tool"
requires-python = ">=3.10"
dependencies = [
    "mcp>=1.0",
]

[project.optional-dependencies]
dev = ["pytest>=7.0"]

[tool.setuptools]
py-modules = ["server"]

[tool.pytest.ini_options]
pythonpath = ["."]
```

- [ ] **Step 2: Create `mcp/tests/conftest.py`**

```python
import pytest
import server


@pytest.fixture(autouse=True)
def clear_jobs():
    server.jobs.clear()
    yield
    server.jobs.clear()
```

- [ ] **Step 3: Install the package**

```bash
cd mcp
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Expected: `Successfully installed diarize-mcp-0.1.0 mcp-...`

- [ ] **Step 4: Create `mcp/README.md`**

````markdown
# Diarize MCP Server

MCP server that exposes `transcribe` and `get_transcript` tools to Claude Desktop.

## Setup

```bash
cd mcp
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Claude Desktop Configuration

Edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "diarize": {
      "command": "/absolute/path/to/diarize/mcp/.venv/bin/python",
      "args": ["/absolute/path/to/diarize/mcp/server.py"]
    }
  }
}
```

Replace `/absolute/path/to/diarize` with the actual repo path (e.g. `/Users/yourname/code/aquarion/diarize`).

## Tools

### `transcribe(file_path, num_speakers)`

Starts a transcription job. Picks the Swift CLI on macOS (if built), otherwise the Python CLI.

Returns `{"job_id": "<uuid>", "backend": "swift"|"python"}` or `{"error": "..."}`.

### `get_transcript(job_id)`

Polls the job started by `transcribe`.

Returns one of:
- `{"status": "running"}` — still processing
- `{"status": "done", "transcript": "<markdown>", "output_path": "<path>"}` — finished
- `{"status": "failed", "error": "<message>"}` — something went wrong

## Backend Selection

1. macOS + `swift/.build/release/diarize` exists → Swift CLI
2. Otherwise → Python CLI via `python/.venv/bin/python python/app.py` (or system Python if no venv)

## Usage Example

```
You: Transcribe /Users/me/recordings/meeting.wav, 3 speakers.
Claude: [calls transcribe → gets job_id]
Claude: [calls get_transcript until status is "done"]
Claude: Here's the transcript: ...
```
````

- [ ] **Step 5: Commit**

```bash
git add mcp/pyproject.toml mcp/README.md mcp/tests/conftest.py
git commit -m "feat(mcp): scaffold MCP server package"
```

---

### Task 2: Backend selection and stdout parsing

**Files:**
- Create: `mcp/server.py` (skeleton + `select_backend` + `parse_transcript_path`)
- Create: `mcp/tests/test_backend.py`
- Create: `mcp/tests/test_parsing.py`

**Interfaces:**
- Produces:
  - `select_backend() -> tuple[str, list[str]] | None` — `("swift", ["/path/to/diarize"])` or `("python", ["/path/to/python", "/path/to/app.py"])` or `None`
  - `parse_transcript_path(stdout: str, backend: str) -> str | None` — absolute path to `transcript.md` or `None`
  - `REPO_ROOT: Path`, `jobs: dict[str, Job]` (dict declared, `Job` class added in Task 3)

- [ ] **Step 1: Write failing tests for `select_backend`**

Create `mcp/tests/test_backend.py`:

```python
import sys
from pathlib import Path
from unittest.mock import patch

import server


def test_selects_swift_on_macos_when_built(tmp_path, monkeypatch):
    swift_cli = tmp_path / "swift" / ".build" / "release" / "diarize"
    swift_cli.parent.mkdir(parents=True)
    swift_cli.touch()
    monkeypatch.setattr(server, "REPO_ROOT", tmp_path)

    with patch("platform.system", return_value="Darwin"):
        name, cmd = server.select_backend()

    assert name == "swift"
    assert cmd == [str(swift_cli)]


def test_falls_back_to_python_when_swift_missing(tmp_path, monkeypatch):
    app_py = tmp_path / "python" / "app.py"
    app_py.parent.mkdir(parents=True)
    app_py.touch()
    monkeypatch.setattr(server, "REPO_ROOT", tmp_path)

    with patch("platform.system", return_value="Darwin"):
        result = server.select_backend()

    assert result is not None
    name, cmd = result
    assert name == "python"
    assert str(app_py) in " ".join(cmd)


def test_uses_venv_python_when_present(tmp_path, monkeypatch):
    app_py = tmp_path / "python" / "app.py"
    app_py.parent.mkdir(parents=True)
    app_py.touch()
    venv_py = tmp_path / "python" / ".venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.touch()
    monkeypatch.setattr(server, "REPO_ROOT", tmp_path)

    with patch("platform.system", return_value="Linux"):
        name, cmd = server.select_backend()

    assert name == "python"
    assert cmd[0] == str(venv_py)


def test_returns_none_when_nothing_available(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "REPO_ROOT", tmp_path)

    with patch("platform.system", return_value="Linux"):
        result = server.select_backend()

    assert result is None
```

- [ ] **Step 2: Write failing tests for `parse_transcript_path`**

Create `mcp/tests/test_parsing.py`:

```python
from server import parse_transcript_path

SWIFT_STDOUT = """\
==> Loading WhisperKit model: openai_whisper-large-v3-turbo
==> Complete
    speaker map : /tmp/out/audio_2026-06-29_10-00-00/speakers.json
    local       : /tmp/out/audio_2026-06-29_10-00-00/transcript.md
    vault       : /Users/me/Obsidian/Transcripts/audio.md
"""

# Python prints an OSC 8 hyperlink: ESC]8;;file:///abs/path ESC\ label ESC]8;; ESC\
_ESC = "\x1b"
PYTHON_STDOUT = (
    "==> Complete\n"
    "    speaker map updated: /tmp/out/audio_2026-06-29_10-00-00/speakers.json\n"
    f"    local transcript    : {_ESC}]8;;file:///tmp/out/audio_2026-06-29_10-00-00/transcript.md{_ESC}\\"
    f"out/audio_2026-06-29_10-00-00/transcript.md{_ESC}]8;;{_ESC}\\\n"
    "    vault transcript    : ...\n"
)


def test_parse_swift_stdout():
    path = parse_transcript_path(SWIFT_STDOUT, "swift")
    assert path == "/tmp/out/audio_2026-06-29_10-00-00/transcript.md"


def test_parse_python_stdout():
    path = parse_transcript_path(PYTHON_STDOUT, "python")
    assert path == "/tmp/out/audio_2026-06-29_10-00-00/transcript.md"


def test_returns_none_on_empty_swift():
    assert parse_transcript_path("", "swift") is None


def test_returns_none_on_empty_python():
    assert parse_transcript_path("", "python") is None


def test_returns_none_on_unrelated_output():
    assert parse_transcript_path("==> Loading model\n==> Transcribing\n", "swift") is None
```

- [ ] **Step 3: Run tests — expect ImportError (server.py doesn't exist yet)**

```bash
cd mcp
source .venv/bin/activate
pytest tests/test_backend.py tests/test_parsing.py -v
```

Expected: `ImportError: No module named 'server'`

- [ ] **Step 4: Create `mcp/server.py` with skeleton + both functions**

```python
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
```

- [ ] **Step 5: Run tests — expect PASS**

```bash
pytest tests/test_backend.py tests/test_parsing.py -v
```

Expected: all 9 tests PASS.

- [ ] **Step 6: Commit**

```bash
git add mcp/server.py mcp/tests/test_backend.py mcp/tests/test_parsing.py
git commit -m "feat(mcp): add backend selection and stdout parsing"
```

---

### Task 3: Job state and MCP tools

**Files:**
- Modify: `mcp/server.py` — add `Job` dataclass, `transcribe` tool, `get_transcript` tool
- Create: `mcp/tests/test_tools.py`

**Interfaces:**
- Consumes:
  - `select_backend() -> tuple[str, list[str]] | None`
  - `parse_transcript_path(stdout, backend) -> str | None`
  - `jobs: dict[str, Job]`
- Produces:
  - `Job(proc, backend)` — dataclass; `job.is_complete() -> bool`
  - `transcribe(file_path: str, num_speakers: int) -> dict`
  - `get_transcript(job_id: str) -> dict`

- [ ] **Step 1: Write failing tests**

Create `mcp/tests/test_tools.py`:

```python
import time
from pathlib import Path
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

    with patch("server.select_backend", return_value=("swift", ["/bin/echo"])), \
         patch("subprocess.Popen", return_value=mock_proc):
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
```

- [ ] **Step 2: Run tests — expect failures**

```bash
pytest tests/test_tools.py -v
```

Expected: `AttributeError: module 'server' has no attribute 'Job'`

- [ ] **Step 3: Add `Job`, `transcribe`, and `get_transcript` to `mcp/server.py`**

Replace the body of `server.py` after the `parse_transcript_path` function (keep everything above it unchanged):

```python
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
        return {"error": "no backend available (Swift CLI not built, Python CLI not found)"}
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
      {"status": "done", "transcript": "<markdown>", "output_path": "<path>"} on success.
      {"status": "failed", "error": "<message>"} on failure or unknown job_id.
    """
    job = jobs.get(job_id)
    if job is None:
        return {"status": "failed", "error": "unknown job_id"}
    if not job.is_complete():
        return {"status": "running"}
    if job.proc.returncode != 0:
        return {"status": "failed", "error": job.stderr or f"exit code {job.proc.returncode}"}
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
```

The full final `mcp/server.py` is:

```python
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
        return {"error": "no backend available (Swift CLI not built, Python CLI not found)"}
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
      {"status": "done", "transcript": "<markdown>", "output_path": "<path>"} on success.
      {"status": "failed", "error": "<message>"} on failure or unknown job_id.
    """
    job = jobs.get(job_id)
    if job is None:
        return {"status": "failed", "error": "unknown job_id"}
    if not job.is_complete():
        return {"status": "running"}
    if job.proc.returncode != 0:
        return {"status": "failed", "error": job.stderr or f"exit code {job.proc.returncode}"}
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
```

- [ ] **Step 4: Run all tests — expect PASS**

```bash
pytest tests/ -v
```

Expected: all 16 tests PASS. If any fail, fix before committing.

- [ ] **Step 5: Smoke-test the server imports cleanly**

```bash
python -c "import server; print('tools:', [t.name for t in server.mcp._tools.values()])"
```

Expected output (tool names may vary by mcp version):
```
tools: ['transcribe', 'get_transcript']
```

No traceback or ImportError.

- [ ] **Step 6: Commit**

```bash
git add mcp/server.py mcp/tests/test_tools.py
git commit -m "feat(mcp): add Job state and transcribe/get_transcript tools"
```
