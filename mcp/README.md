# Diarize MCP Server

MCP server that exposes `transcribe`, `get_transcript`, `get_config`, and `set_config` tools to Claude Desktop.

## Setup

Uses [uv](https://docs.astral.sh/uv/) to manage the Python version and
dependencies — no manual venv to create or activate.
[Install uv](https://docs.astral.sh/uv/getting-started/installation/) if you
don't have it:

```bash
cd mcp
uv sync
```

`uv run` (used by the Claude Desktop config below, and by `select_backend()`
when invoking the `python/` backend) resolves/syncs `.venv` from
`pyproject.toml` + `uv.lock` on every launch — no separately-managed venv
that can go stale or point at a Python interpreter that's since moved
(the original motivation for this over a plain `pip`/venv setup).

## Claude Desktop Configuration

Point `command` at your `uv` binary's **absolute path**, not just `"uv"` —
Claude Desktop launches this as a GUI subprocess, which may not inherit your
shell's `PATH`. Find yours with `which uv` (macOS/Linux) or `where.exe uv`
(Windows) first.

macOS/Linux — edit `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or the equivalent Linux client config:

```json
{
  "mcpServers": {
    "diarize": {
      "command": "/absolute/path/to/uv",
      "args": ["run", "--directory", "/absolute/path/to/diarize/mcp", "server.py"]
    }
  }
}
```

Replace `/absolute/path/to/diarize` with the actual repo path (e.g. `/Users/yourname/code/aquarion/diarize`).

Windows — edit `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "diarize": {
      "command": "C:\\absolute\\path\\to\\uv.exe",
      "args": ["run", "--directory", "C:\\absolute\\path\\to\\diarize\\mcp", "server.py"]
    }
  }
}
```

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

### `get_config(key)`

Reads a diarize config value (e.g. `"vault_path"`, `"model"`, `"language"`) via the
selected backend's `config get` command.

Returns `{"key": "<key>", "value": "<value>"}` or `{"error": "<message>"}` — the
error lists valid keys if `key` is unknown.

### `set_config(key, value)`

Sets a diarize config value via the selected backend's `config set` command. List
fields (e.g. `"extra_path"`) take comma-separated values.

Returns `{"status": "ok", "message": "<confirmation>"}` or `{"error": "<message>"}`.

## Backend Selection

1. macOS + `swift/.build/release/diarize` exists → Swift CLI
2. Otherwise → Python CLI via `uv run --directory python app.py`. Requires
   [uv](https://docs.astral.sh/uv/) on `PATH` — it resolves/syncs
   `python/.venv` from `python/pyproject.toml` + `python/uv.lock` on every
   invocation, so there's no separately-managed venv to set up or for this
   server to go stale against.

## Usage Example

```
You: Transcribe /Users/me/recordings/meeting.wav, 3 speakers.
Claude: [calls transcribe → gets job_id]
Claude: [calls get_transcript until status is "done"]
Claude: Here's the transcript: ...

You: What's my vault path set to?
Claude: [calls get_config("vault_path")]
Claude: It's set to ~/Obsidian.

You: Switch my transcript language to French.
Claude: [calls set_config("language", "fr")]
Claude: Done — language is now fr.
```

Note: valid config keys differ between backends (e.g. Python's `model` vs Swift's
`whisperkit_model`) — an unknown-key error lists the real keys for whichever
backend is active.
