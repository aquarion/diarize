# Diarize MCP Server

MCP server that exposes `transcribe`, `get_transcript`, `list_jobs`, `get_config`, and `set_config` tools to Claude Desktop.

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

Run at most one instance of this server against a given `jobs.json` at a
time. Its job registry is coordinated with in-process locking only - there's
no cross-process file locking - so two live instances can race and corrupt
each other's writes to it.

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

### `transcribe(file_path, num_speakers, output_path=None)`

Starts a transcription job. Picks the Swift CLI on macOS (if built), otherwise the Python CLI.

`output_path`, if given, overrides the configured vault destination for
this job only - the transcript is written exactly there instead of being
templated from `vault_path`/`vault_subdir`/`vault_filename_template`
config, and stored config is left untouched. Parent directories are
created as needed. Once the job finishes, `get_transcript`'s
`"output_path"` reports exactly this path.

Returns `{"job_id": "<uuid>", "backend": "swift"|"python"}` or `{"error": "..."}`.

### `get_transcript(job_id)`

Polls the job started by `transcribe`. Job state is persisted to a small
on-disk registry (`jobs.json` next to the log file) as soon as it's known, so
a job started before an MCP server restart is still recognized afterward -
`job_id` isn't forgotten just because the process that started it is gone.

Returns one of:
- `{"status": "running"}` — still processing. May also include `"message"`
  (last human-readable stage description) and, once the transcription stage
  reports fine-grained progress, `"fraction"` (0-1) and `"stage"`.
- `{"status": "done", "transcript": "<markdown>", "output_path": "<path>"}` — finished
- `{"status": "failed", "error": "<message>"}` — something went wrong
- `{"status": "interrupted", "error": "<message>"}` — the MCP server
  restarted while this job was running, so its actual outcome is unknown;
  check the configured output location, or re-run
- `{"status": "unknown", "error": "no such job_id"}` — this `job_id` was
  never seen, *or* it's old enough to have been pruned from the registry.
  The registry is capped at 200 entries total, but a still-running job is
  never pruned - only the oldest *completed* entries are, once the cap is
  exceeded, so with many jobs running at once fewer than 200 completed
  ones may be retained (and the file can briefly exceed 200 entries while
  they're all running). Distinct from `"failed"`: not evidence of an
  error. But unlike a job that was truly never seen, a pruned one may have
  completed and written real output - check the configured output
  location before re-running.

### `list_jobs(limit=20)`

Lists recent transcription jobs, most recently started first - including ones
from before a server restart, which `get_transcript` alone can't surface
without already knowing their `job_id`.

Returns `{"jobs": [{"job_id", "backend", "input_path", "num_speakers", "pid",
"status", "output_path", "error", "started_at", "finished_at"}, ...]}`. `pid`
is the backend process's id, recorded for diagnostic use only (e.g. manually
checking whether a process is still around) - not evidence either way about
whether this server is still tracking the job: it converts every persisted
`"running"` record to `"interrupted"` on restart unconditionally, since a
backend child can outlive a crashed/restarted server and a live pid doesn't
prove anything is still watching it. A job still running also carries
`"message"` and, once available, `"fraction"` / `"stage"` - the same fields
`get_transcript` reports for it.

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
