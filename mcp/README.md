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
