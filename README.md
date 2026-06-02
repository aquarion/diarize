# Diarize Utility

General-purpose audio transcription + diarization utility that:

1. Transcribes a WAV file.
2. Detects speakers (diarization).
3. Prompts you to name each detected speaker label.
4. Produces a cleaned markdown transcript.
5. Exports the transcript into an Obsidian vault.

## Files

| File | Purpose |
| --- | --- |
| `app.py` | Main CLI utility. |
| `pyproject.toml` | Project metadata and dependencies. |
| `README.md` | Usage and configuration guide. |

## Installation

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e .
```

## Prerequisites

1. Hugging Face token with read access.
2. Accepted model access terms for:
   - <https://huggingface.co/pyannote/speaker-diarization-3.1>
   - <https://huggingface.co/pyannote/segmentation-3.0>

## Quickstart

```bash
python3 app.py /path/to/audio.wav
```

On first run, the utility creates config automatically and prompts for missing
required values (such as Hugging Face token and vault path).

## Config Location

Default config path:

- Linux: `~/.config/diarize/config.json`
- macOS: `~/Library/Application Support/diarize/config.json`
- Windows: `%APPDATA%/diarize/config.json`

Override with:

```bash
python3 app.py /path/to/audio.wav --config /path/to/config.json
```

Speaker mapping (`speakers.json`) is stored in each meeting's output directory
alongside the transcript, so each recording gets its own independent mapping.

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
python3 app.py /path/to/audio.wav --skip-whisperx
```

Ask Claude CLI to guess speaker names from transcript context (uses calendar/email
MCP tools if available):

```bash
python3 app.py /path/to/audio.wav --claude-guess
```

Accept all defaults non-interactively (useful combined with `--claude-guess`):

```bash
python3 app.py /path/to/audio.wav --claude-guess --yes
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

## PATH Extension

Some tools (e.g. ffmpeg) may need a specific version that isn't on your default
PATH. Use `extra_path` to prepend directories:

```json
{
  "extra_path": ["/opt/homebrew/opt/ffmpeg@7/bin"]
}
```

Entries are prepended in order, so they take priority over system PATH.

## Recommended Accuracy Tuning

- Set `min_speakers` and `max_speakers` as tightly as possible.
- Keep `language` explicit when known.
- Review speaker prompts carefully on first pass; mapping is persisted for reuse.
