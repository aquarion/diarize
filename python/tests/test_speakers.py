import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest
import speakers

# --- load_speaker_mapping ---


def test_load_speaker_mapping_missing_file_returns_empty(tmp_path):
    assert speakers.load_speaker_mapping(tmp_path / "missing.json") == {}


def test_load_speaker_mapping_invalid_json_returns_empty(tmp_path):
    p = tmp_path / "speakers.json"
    p.write_text("not json")
    assert speakers.load_speaker_mapping(p) == {}


def test_load_speaker_mapping_non_object_returns_empty(tmp_path):
    p = tmp_path / "speakers.json"
    p.write_text("[1, 2, 3]")
    assert speakers.load_speaker_mapping(p) == {}


def test_load_speaker_mapping_filters_comment_and_non_string_values(tmp_path):
    p = tmp_path / "speakers.json"
    p.write_text(
        json.dumps(
            {
                "_comment": ["ignore me"],
                "SPEAKER_00": "Alice",
                "SPEAKER_01": "Bob",
                "weird": 123,
            }
        )
    )
    assert speakers.load_speaker_mapping(p) == {
        "SPEAKER_00": "Alice",
        "SPEAKER_01": "Bob",
    }


# --- save_speaker_mapping / round trip ---


def test_save_speaker_mapping_round_trips_through_load(tmp_path):
    p = tmp_path / "speakers.json"
    mapping = {"SPEAKER_01": "Bob", "SPEAKER_00": "Alice"}
    speakers.save_speaker_mapping(p, mapping)
    assert speakers.load_speaker_mapping(p) == mapping


def test_save_speaker_mapping_writes_sorted_and_includes_comment(tmp_path):
    p = tmp_path / "speakers.json"
    speakers.save_speaker_mapping(p, {"b": "B", "a": "A"})
    raw = json.loads(p.read_text())
    assert "_comment" in raw
    assert list(raw.keys()).index("a") < list(raw.keys()).index("b")


# --- _extract_json ---


def test_extract_json_plain():
    assert speakers._extract_json('{"a": "b"}') == {"a": "b"}


def test_extract_json_markdown_fenced():
    text = 'Sure, here you go:\n```json\n{"a": "b"}\n```\nHope that helps.'
    assert speakers._extract_json(text) == {"a": "b"}


def test_extract_json_markdown_fenced_no_language_tag():
    text = '```\n{"a": "b"}\n```'
    assert speakers._extract_json(text) == {"a": "b"}


def test_extract_json_greedy_brace_fallback():
    text = 'Here is the mapping {"a": "b"} as requested.'
    assert speakers._extract_json(text) == {"a": "b"}


def test_extract_json_raises_when_nothing_found():
    with pytest.raises(ValueError, match="No JSON object found"):
        speakers._extract_json("no json anywhere in this text")


# --- coalesce_segments ---


def test_coalesce_segments_merges_consecutive_same_speaker():
    segments = [
        {"speaker": "SPEAKER_00", "start": 0.0, "text": "Hello"},
        {"speaker": "SPEAKER_00", "start": 1.0, "text": "there"},
        {"speaker": "SPEAKER_01", "start": 2.0, "text": "Hi"},
    ]
    blocks = speakers.coalesce_segments(
        segments, {"SPEAKER_00": "Alice", "SPEAKER_01": "Bob"}
    )
    assert blocks == [("Alice", 0.0, "Hello there"), ("Bob", 2.0, "Hi")]


def test_coalesce_segments_does_not_merge_across_different_speakers_reappearing():
    segments = [
        {"speaker": "SPEAKER_00", "start": 0.0, "text": "A"},
        {"speaker": "SPEAKER_01", "start": 1.0, "text": "B"},
        {"speaker": "SPEAKER_00", "start": 2.0, "text": "C"},
    ]
    blocks = speakers.coalesce_segments(segments, {})
    assert blocks == [
        ("SPEAKER_00", 0.0, "A"),
        ("SPEAKER_01", 1.0, "B"),
        ("SPEAKER_00", 2.0, "C"),
    ]


def test_coalesce_segments_skips_empty_text():
    segments = [
        {"speaker": "SPEAKER_00", "start": 0.0, "text": "  "},
        {"speaker": "SPEAKER_00", "start": 1.0, "text": "Real text"},
    ]
    blocks = speakers.coalesce_segments(segments, {})
    assert blocks == [("SPEAKER_00", 1.0, "Real text")]


def test_coalesce_segments_falls_back_to_raw_label_when_unmapped():
    segments = [{"speaker": "SPEAKER_00", "start": 0.0, "text": "hi"}]
    blocks = speakers.coalesce_segments(segments, {})
    assert blocks == [("SPEAKER_00", 0.0, "hi")]


def test_coalesce_segments_missing_speaker_key_defaults_to_unknown():
    segments = [{"start": 0.0, "text": "hi"}]
    blocks = speakers.coalesce_segments(segments, {})
    assert blocks == [("UNKNOWN", 0.0, "hi")]


# --- prompt_for_speakers (non-interactive) ---


def test_prompt_for_speakers_non_interactive_fills_unmapped_with_label():
    result = speakers.prompt_for_speakers(
        ["SPEAKER_00", "SPEAKER_01"], {}, [], non_interactive=True
    )
    assert result == {"SPEAKER_00": "SPEAKER_00", "SPEAKER_01": "SPEAKER_01"}


def test_prompt_for_speakers_non_interactive_preserves_existing_mapping():
    result = speakers.prompt_for_speakers(
        ["SPEAKER_00"], {"SPEAKER_00": "Alice"}, [], non_interactive=True
    )
    assert result == {"SPEAKER_00": "Alice"}


# --- guess_speakers_with_claude ---


def _mock_completed_process(stdout: str) -> MagicMock:
    proc = MagicMock()
    proc.stdout = stdout
    return proc


def test_guess_speakers_with_claude_parses_response():
    detected = ["SPEAKER_00", "SPEAKER_01"]
    segs = [{"speaker": "SPEAKER_00", "start": 0.0, "text": "Hi I'm Alice"}]
    with patch(
        "speakers.subprocess.run",
        return_value=_mock_completed_process('{"SPEAKER_00": "Alice"}'),
    ):
        import datetime as dt

        guesses = speakers.guess_speakers_with_claude(detected, segs, dt.datetime.now())
    assert guesses == {"SPEAKER_00": "Alice"}


def test_guess_speakers_with_claude_filters_to_detected_labels_only():
    import datetime as dt

    with patch(
        "speakers.subprocess.run",
        return_value=_mock_completed_process(
            '{"SPEAKER_00": "Alice", "SPEAKER_99": "Ghost"}'
        ),
    ):
        guesses = speakers.guess_speakers_with_claude(
            ["SPEAKER_00"], [], dt.datetime.now()
        )
    assert guesses == {"SPEAKER_00": "Alice"}


def test_guess_speakers_with_claude_missing_cli_raises_runtime_error():
    import datetime as dt

    with patch("speakers.subprocess.run", side_effect=FileNotFoundError()):
        with pytest.raises(RuntimeError, match="not found in PATH"):
            speakers.guess_speakers_with_claude([], [], dt.datetime.now())


def test_guess_speakers_with_claude_timeout_returns_empty_without_raising():
    import datetime as dt

    with patch(
        "speakers.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd="claude", timeout=120),
    ):
        guesses = speakers.guess_speakers_with_claude([], [], dt.datetime.now())
    assert guesses == {}


def test_guess_speakers_with_claude_nonzero_exit_raises_runtime_error():
    import datetime as dt

    err = subprocess.CalledProcessError(returncode=1, cmd="claude")
    with patch("speakers.subprocess.run", side_effect=err):
        with pytest.raises(RuntimeError, match="exited 1"):
            speakers.guess_speakers_with_claude([], [], dt.datetime.now())


def test_guess_speakers_with_claude_unparseable_response_raises_runtime_error():
    import datetime as dt

    with patch(
        "speakers.subprocess.run",
        return_value=_mock_completed_process("not json at all"),
    ):
        with pytest.raises(RuntimeError, match="Could not parse Claude response"):
            speakers.guess_speakers_with_claude([], [], dt.datetime.now())
