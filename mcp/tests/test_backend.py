from unittest.mock import patch

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

    with patch("platform.system", return_value="Darwin"):
        result = server.select_backend()

    assert result is not None
    name, cmd = result
    assert name == "python"
    assert str(app_py) in " ".join(cmd)


def test_uses_venv_python_when_present(tmp_path, monkeypatch):
    app_py = tmp_path / "python" / "app.py"
    app_py.parent.mkdir(parents=True)
    app_py.touch()
    venv_py = tmp_path / "python" / ".venv" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.touch()
    monkeypatch.setattr(server, "REPO_ROOT", tmp_path)

    with patch("platform.system", return_value="Linux"):
        name, cmd = server.select_backend()

    assert name == "python"
    assert cmd[0] == str(venv_py)


def test_uses_windows_venv_python_when_present(tmp_path, monkeypatch):
    app_py = tmp_path / "python" / "app.py"
    app_py.parent.mkdir(parents=True)
    app_py.touch()
    venv_py = tmp_path / "python" / ".venv" / "Scripts" / "python.exe"
    venv_py.parent.mkdir(parents=True)
    venv_py.touch()
    monkeypatch.setattr(server, "REPO_ROOT", tmp_path)

    with patch("platform.system", return_value="Windows"):
        name, cmd = server.select_backend()

    assert name == "python"
    assert cmd[0] == str(venv_py)


def test_returns_none_when_nothing_available(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "REPO_ROOT", tmp_path)

    with patch("platform.system", return_value="Linux"):
        result = server.select_backend()

    assert result is None
