# Diarize MCP Server

MCP server that exposes `transcribe`, `get_transcript`, `get_config`, and `set_config` tools to Claude Desktop.

## Setup

macOS / Linux:

```bash
cd mcp
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

Windows:

```powershell
cd mcp
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

## Claude Desktop Configuration

macOS — edit `~/Library/Application Support/Claude/claude_desktop_config.json`:

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

Windows — edit `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "diarize": {
      "command": "C:\\absolute\\path\\to\\diarize\\mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\absolute\\path\\to\\diarize\\mcp\\server.py"]
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
