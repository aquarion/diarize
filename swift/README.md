# Diarize — Swift

macOS app and CLI for audio transcription and speaker diarization. No Python environment required — uses on-device [WhisperKit](https://github.com/argmaxinc/argmax-oss-swift) and SpeakerKit models.

## Files

| Target | Purpose |
| --- | --- |
| `DiarizeKit` | Shared library: transcription, diarization, speaker mapping, vault export. |
| `DiarizeCLI` | CLI entry point (`diarize`). |
| `DiarizeApp` | SwiftUI macOS app with drag-and-drop interface. |

## Requirements

- macOS 14+
- Apple Silicon (WhisperKit and SpeakerKit are optimised for Apple Neural Engine / Metal)
- Xcode command line tools (`xcode-select --install`) or full Xcode

## Build

```bash
cd swift
swift build -c release
```

This builds both the `diarize` CLI and the `DiarizeApp` bundle.

## Quickstart

**CLI:**

```bash
.build/release/diarize /path/to/audio.wav <num_speakers>
```

On first run, config is created automatically and you are prompted for any missing required values (vault path).

You can also point it at a video file directly — if the input has a video
track, its audio is extracted automatically (via AVFoundation, no external
tools) before transcription. This only covers QuickTime-family containers
(`.mp4`, `.mov`, `.m4v`, ...) — AVFoundation has no native `.mkv` support, so
tools that default to `.mkv` (e.g. OBS) need to export as `.mp4`/`.mov`
first.

**App:**

```bash
open .build/release/DiarizeApp.app
```

Or open the `swift/` directory in Xcode and run the `DiarizeApp` scheme. Drag an audio or video file onto the window, set the speaker count, and click **Transcribe & Diarize**.

## CLI Options

`diarize <file> <num_speakers>` implicitly runs the `transcribe` subcommand
(the default), so existing invocations keep working unchanged.

```
--claude-guess      Ask Claude to guess speaker names from transcript context
-y, --yes           Non-interactive: accept all defaults
--vault-output      Override vault output path for this file
--config            Path to config JSON file
```

## Config Management

The `config` subcommand reads and edits the same config file `transcribe`
uses (respects `--config` the same way):

```bash
.build/release/diarize config path                    # print the config file location
.build/release/diarize config show                     # print the effective config (secrets masked)
.build/release/diarize config get language              # print one value
.build/release/diarize config set language fr            # set one value and save
```

`get`/`set` validate the key against the known config fields. The secret
field (`anthropic_api_key`) is masked in `show` and in `set`'s confirmation
output — only the last 4 characters are shown.

## Config Location

Config is read from and written to:

```
~/Library/Application Support/diarize/config.json
```

Override with `--config /path/to/config.json`.

### Config Keys

```json
{
  "language": "en",
  "whisperkit_model": "openai_whisper-large-v3-turbo",
  "anthropic_api_key": "",
  "output_dir": "./out",
  "transcript_title": "Session Transcript",
  "vault_path": "~/Obsidian",
  "vault_subdir": "Transcripts",
  "vault_filename_template": "{audio_stem}.md"
}
```

Set `anthropic_api_key` to enable `--claude-guess`.

## Output Layout

For input `my_audio.wav` with `output_dir` of `./out`:

```text
./out/my_audio_2026-06-02_14-30-00/
  my_audio.txt
  my_audio.srt
  my_audio.vtt
  transcript.md
  speakers.json
```

And Obsidian export:

```text
<vault_path>/<vault_subdir>/my_audio.md
```

The output directory name includes the file's creation timestamp to avoid collisions when the same filename is reused (e.g. SuperWhisper always outputs `output.wav`).

`speakers.json` stores the speaker label → name mapping for that recording. Pass `--yes` combined with `--claude-guess` to run fully non-interactively.

## Accuracy Tips

- Pass the exact speaker count — SpeakerKit performs best when told precisely how many speakers to find.
- Keep `language` explicit when known.
