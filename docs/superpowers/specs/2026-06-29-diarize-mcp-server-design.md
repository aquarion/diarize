# Diarize MCP Server — Design

## Overview

A Python MCP server that exposes the diarize tool to Claude Desktop. Claude can trigger a transcription job on a local audio file and poll for the result. No interactive steps — the pipeline runs non-interactively (`--yes`) and Claude reasons over the finished transcript itself.

## Files

```
mcp/
  server.py        MCP server: tool definitions, job state, subprocess management
  pyproject.toml   Dependencies: mcp SDK
  README.md        Claude Desktop config snippet and setup instructions
```

## Tools

### `transcribe(file_path: str, num_speakers: int) -> dict`

Starts a transcription job.

- Validates `file_path` exists on disk.
- Selects backend (see Backend Selection).
- Spawns the CLI subprocess with `--yes` (non-interactive).
- Stores job state in an in-memory dict keyed by a UUID job ID.
- Returns `{"job_id": "<uuid>", "backend": "swift"|"python"}`.

### `get_transcript(job_id: str) -> dict`

Polls a running job and returns the result when ready.

- If the subprocess is still running: returns `{"status": "running"}`.
- On successful exit (code 0): parses stdout for the `local : /path/to/transcript.md` line, reads that file, returns `{"status": "done", "transcript": "<markdown>", "output_path": "<path>"}`.
- On non-zero exit: returns `{"status": "failed", "error": "<captured stderr>"}`.
- Unknown job ID: returns `{"status": "failed", "error": "unknown job_id"}`.

No `--claude-guess` flag is used. Claude Desktop is Claude, so it names speakers from the raw transcript itself.

## Backend Selection

Checked once per `transcribe` call, at runtime:

1. If `platform.system() == "Darwin"` and `<repo_root>/swift/.build/release/diarize` exists → **Swift CLI**.
2. Otherwise → **Python CLI** (`python/app.py`), invoked with the repo's `.venv` Python if present, else system Python.

CLI arguments are identical for both backends: `<file_path> <num_speakers> --yes`.

## Job State

In-memory `dict[str, JobState]` where `JobState` holds:

- `proc`: `subprocess.Popen` handle
- `stdout`: full captured stdout string (collected by a reader thread; available once process exits)
- `stderr`: full captured stderr string
- `backend`: which CLI was used

State is lost if the server process restarts. Claude re-triggers the job if needed.

## Error Handling

- `transcribe` returns an error string (not an exception) if the file does not exist or no backend is available.
- `get_transcript` never raises — unknown or failed jobs return a `failed` status dict.
- Subprocess stderr is captured and included in the `failed` response.

## Claude Desktop Configuration

```json
{
  "mcpServers": {
    "diarize": {
      "command": "python",
      "args": ["/path/to/diarize/mcp/server.py"]
    }
  }
}
```

If using the repo's venv, point `command` at `.venv/bin/python` inside `mcp/`.
