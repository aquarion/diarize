from pathlib import Path

import render

# --- fmt_ts ---


def test_fmt_ts_none_returns_placeholder():
    assert render.fmt_ts(None) == "[--:--:--]"


def test_fmt_ts_zero():
    assert render.fmt_ts(0) == "[00:00:00]"


def test_fmt_ts_seconds_only():
    assert render.fmt_ts(45) == "[00:00:45]"


def test_fmt_ts_minutes_and_seconds():
    assert render.fmt_ts(125) == "[00:02:05]"


def test_fmt_ts_hours():
    assert render.fmt_ts(3661) == "[01:01:01]"


def test_fmt_ts_truncates_fractional_seconds():
    assert render.fmt_ts(59.9) == "[00:00:59]"


# --- terminal_link ---


def test_terminal_link_wraps_osc8_escape_sequence(tmp_path):
    f = tmp_path / "transcript.md"
    f.write_text("hi")
    link = render.terminal_link(f)
    assert link.startswith("\033]8;;file://")
    assert link.endswith("\033]8;;\033\\")
    assert str(f) in link


# --- render_markdown ---


def test_render_markdown_includes_title_and_source(tmp_path):
    audio = tmp_path / "meeting.wav"
    md = render.render_markdown([], "My Title", audio)
    assert md.startswith("# My Title\n")
    assert f"- Source audio: `{audio}`" in md
    assert "- Generated:" in md


def test_render_markdown_renders_blocks_with_timestamps():
    blocks = [("Alice", 5.0, "Hello there"), ("Bob", 65.0, "Hi Alice")]
    md = render.render_markdown(blocks, "Title", Path("audio.wav"))
    assert "**Alice** [00:00:05]" in md
    assert "Hello there" in md
    assert "**Bob** [00:01:05]" in md
    assert "Hi Alice" in md


def test_render_markdown_ends_with_single_trailing_newline():
    md = render.render_markdown([("A", 0.0, "text")], "T", Path("a.wav"))
    assert md.endswith("\n")
    assert not md.endswith("\n\n")


def test_render_markdown_empty_blocks_still_has_header():
    md = render.render_markdown([], "T", Path("a.wav"))
    assert md.startswith("# T\n")
    assert md.endswith("\n")


# --- make_vault_target ---


class _FakeConfig:
    def __init__(self, vault_path, vault_subdir, vault_filename_template):
        self.vault_path = vault_path
        self.vault_subdir = vault_subdir
        self.vault_filename_template = vault_filename_template


def test_make_vault_target_with_subdir():
    cfg = _FakeConfig("/vault", "Transcripts", "{audio_stem}.md")
    target = render.make_vault_target(cfg, "my_meeting")
    assert target == Path("/vault") / "Transcripts" / "my_meeting.md"


def test_make_vault_target_without_subdir():
    cfg = _FakeConfig("/vault", "", "{audio_stem}.md")
    target = render.make_vault_target(cfg, "my_meeting")
    assert target == Path("/vault") / "my_meeting.md"


def test_make_vault_target_expands_user():
    cfg = _FakeConfig("~/vault", "Sub", "{audio_stem}.md")
    target = render.make_vault_target(cfg, "stem")
    assert not str(target).startswith("~")


def test_make_vault_target_custom_template():
    cfg = _FakeConfig("/vault", "Sub", "meeting-{audio_stem}-notes.md")
    target = render.make_vault_target(cfg, "abc")
    assert target.name == "meeting-abc-notes.md"
