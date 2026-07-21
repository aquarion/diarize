# Diarize Utility

General-purpose audio transcription + diarization utility that:

1. Transcribes an audio (or video) file.
2. Detects speakers (diarization).
3. Prompts you to name each detected speaker label.
4. Produces a cleaned markdown transcript.
5. Exports the transcript into an Obsidian vault.

## Files

| File | Purpose |
| --- | --- |
| `app.py` | CLI entry point and orchestration. |
| `config.py` | `AppConfig` dataclass, config load/save, path helpers. |
| `media.py` | Detects video input and extracts its audio track before transcription. |
| `render.py` | Markdown rendering, vault targeting, terminal output helpers. |
| `speakers.py` | Speaker mapping, interactive labeling, Claude name guessing. |
| `transcribe.py` | WhisperX and mlx-whisper+pyannote transcription pipelines. |
| `pyproject.toml` | Project metadata and dependencies. |
| `README.md` | Usage and configuration guide. |

## Installation

Uses [uv](https://docs.astral.sh/uv/) to manage the Python version and
dependencies — no manual venv to create, activate, or let drift.
[Install uv](https://docs.astral.sh/uv/getting-started/installation/) if you
don't have it, then just run the tool:

```bash
uv run app.py /path/to/audio.wav <num_speakers>
```

`uv run` resolves and syncs `.venv` from `pyproject.toml`/`uv.lock` (pinned to
Python 3.13 via `.python-version`) on every invocation — self-healing, so
there's no stale or missing environment to debug. This is also how the MCP
server invokes this backend (see `mcp/server.py`).

## Testing

```bash
uv run --extra dev pytest
```

Covers `config.py`, `render.py`, `speakers.py`, `transcribe.py`, `media.py`,
and `app.py`'s CLI/config-subcommand layer, all via mocked subprocess calls
so no GPU, HF token, or real audio file is needed. `media.py` additionally
gets a handful of real integration tests against actual `ffmpeg`/`ffprobe`
output, which skip automatically if those aren't on `PATH`.

## Prerequisites

1. Hugging Face token with read access.
2. Accepted model access terms for:
   - <https://huggingface.co/pyannote/speaker-diarization-3.1>
   - <https://huggingface.co/pyannote/segmentation-3.0>

## Quickstart

```bash
uv run app.py /path/to/audio.wav <num_speakers>
```

You can also point it at a video file (e.g. an OBS recording) directly — if
the input has a video track, its audio is automatically extracted to a WAV
in the run's output directory before transcription. Requires `ffmpeg`/
`ffprobe` on `PATH` (or in `extra_path`, see below).

On first run, the utility creates config automatically and prompts for missing
required values (such as Hugging Face token and vault path).

## Config Location

Default config path:

- Linux: `~/.config/diarize/config.json`
- macOS: `~/Library/Application Support/diarize/config.json`
- Windows: `%APPDATA%/diarize/config.json`

Override with:

```bash
uv run app.py /path/to/audio.wav <num_speakers> --config /path/to/config.json
```

Speaker mapping (`speakers.json`) is stored in each meeting's output directory
alongside the transcript, so each recording gets its own independent mapping.

## Config Management

The `config` subcommand reads and edits the same config file `transcribe`
uses (respects `--config` the same way):

```bash
uv run app.py config path                  # print the config file location
uv run app.py config show                  # print the effective config (secrets masked)
uv run app.py config get model              # print one value
uv run app.py config set model large-v3     # set one value and save
uv run app.py config set extra_path "/a,/b" # list fields take comma-separated values
```

`get`/`set` validate the key against the known config fields and reject
anything else. Secret fields (`hf_token`, `assemblyai_api_key`) are masked in
`show` and in `set`'s confirmation output — only the last 4 characters are
shown, enough to confirm which value is loaded without exposing it.

## Runtime Selection

The utility chooses transcription runtime in this order:

1. If enabled and on Apple Silicon, try `mlx-whisper` + pyannote diarization.
2. Otherwise use WhisperX.
3. If WhisperX is using CUDA and fails, retry automatically on CPU.

### CUDA Auto-Detection

CUDA is enabled by default when available. Detection uses `torch.cuda` when
possible, with a fallback probe using `nvidia-smi`.

Relevant config keys:

```json
{
  "use_cuda_if_available": true,
  "cuda_compute_type": "float16",
  "compute_type": "int8"
}
```

### Apple Silicon Option

Enable mlx-whisper path:

```json
{
  "use_mlx_whisper_on_apple_silicon": true,
  "mlx_model": "mlx-community/whisper-large-v3-turbo"
}
```

## Useful Options

Re-label from existing JSON without rerunning transcription:

```bash
uv run app.py /path/to/audio.wav 3 --skip-whisperx
```

Ask Claude CLI to guess speaker names from transcript context (uses calendar/email
MCP tools if available):

```bash
uv run app.py /path/to/audio.wav 3 --claude-guess
```

Accept all defaults non-interactively (useful combined with `--claude-guess`):

```bash
uv run app.py /path/to/audio.wav 3 --claude-guess --yes
```

Override the vault output path for a specific file:

```bash
uv run app.py /path/to/audio.wav 3 --vault-output ~/Obsidian/Meetings/standup.md
```

## Output Layout

For input `my_audio.wav` and default `output_dir` of `./out`, the directory
is named with the file's creation time to avoid collisions when the same
filename is reused (e.g. SuperWhisper always outputs `output.wav`):

```text
./out/my_audio_2026-06-02_14-30-00/
  my_audio.json
  my_audio.srt
  my_audio.vtt
  my_audio.txt
  transcript.md
  speakers.json
```

And Obsidian export:

```text
<vault_path>/<vault_subdir>/my_audio.md
```

## PATH and Library Extension

Some tools (e.g. ffmpeg) may need a specific version that isn't on your default
PATH. Use `extra_path` to prepend binary directories and `extra_lib_path` to
prepend shared library directories (sets `DYLD_LIBRARY_PATH` on macOS,
`LD_LIBRARY_PATH` on Linux):

```json
{
  "extra_path": ["/opt/homebrew/opt/ffmpeg@7/bin"],
  "extra_lib_path": ["/opt/homebrew/opt/ffmpeg@7/lib"]
}
```

This is scoped to the diarize process only, so it won't affect other tools on
your system. Entries are prepended in order, taking priority over system paths.

## Recommended Accuracy Tuning

- Pass the exact speaker count as the `num_speakers` argument — pyannote performs best when told precisely how many speakers to find.
- Keep `language` explicit when known.
- Review speaker prompts carefully on first pass; mapping is persisted for reuse.
