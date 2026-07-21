from unittest.mock import patch

import pytest
import server


def test_selects_swift_on_macos_when_built(tmp_path, monkeypatch):
    swift_cli = tmp_path / "swift" / ".build" / "release" / "diarize"
    swift_cli.parent.mkdir(parents=True)
    swift_cli.touch()
    monkeypatch.setattr(server, "REPO_ROOT", tmp_path)

    with patch("platform.system", return_value="Darwin"):
        name, cmd = server.select_backend()

    assert name == "swift"
    assert cmd == [str(swift_cli)]


def test_falls_back_to_python_when_swift_missing(tmp_path, monkeypatch):
    app_py = tmp_path / "python" / "app.py"
    app_py.parent.mkdir(parents=True)
    app_py.touch()
    monkeypatch.setattr(server, "REPO_ROOT", tmp_path)

    with patch("platform.system", return_value="Darwin"), patch(
        "shutil.which", return_value="/usr/local/bin/uv"
    ):
        name, cmd = server.select_backend()

    assert name == "python"
    assert cmd == [
        "/usr/local/bin/uv",
        "run",
        "--directory",
        str(app_py.parent),
        "app.py",
    ]


def test_python_backend_runs_via_uv_on_any_platform(tmp_path, monkeypatch):
    """uv itself resolves/syncs the venv, so backend selection no longer
    branches on OS to find a venv interpreter - one code path everywhere."""
    app_py = tmp_path / "python" / "app.py"
    app_py.parent.mkdir(parents=True)
    app_py.touch()
    monkeypatch.setattr(server, "REPO_ROOT", tmp_path)

    for os_name in ("Windows", "Linux", "Darwin"):
        with patch("platform.system", return_value=os_name), patch(
            "shutil.which", return_value="/path/to/uv"
        ):
            name, cmd = server.select_backend()
        assert name == "python"
        assert cmd[0] == "/path/to/uv"
        assert cmd[1:3] == ["run", "--directory"]


def test_raises_precise_error_when_python_found_but_uv_missing(tmp_path, monkeypatch):
    app_py = tmp_path / "python" / "app.py"
    app_py.parent.mkdir(parents=True)
    app_py.touch()
    monkeypatch.setattr(server, "REPO_ROOT", tmp_path)

    with patch("platform.system", return_value="Linux"), patch(
        "shutil.which", return_value=None
    ):
        with pytest.raises(server.BackendUnavailableError, match="uv is not on PATH"):
            server.select_backend()


def test_raises_precise_error_when_nothing_available(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "REPO_ROOT", tmp_path)

    with patch("platform.system", return_value="Linux"):
        with pytest.raises(
            server.BackendUnavailableError, match="neither the Swift CLI nor"
        ):
            server.select_backend()
