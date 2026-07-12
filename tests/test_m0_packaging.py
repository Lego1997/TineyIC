"""Offline acceptance checks for the M0 packaging foundation."""

from __future__ import annotations

import configparser
import hashlib
import importlib
import json
import subprocess
import tomllib
from pathlib import Path
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
PERSONA_CONFIG_FILES = {
    "benjamin_graham.agent.json",
    "charlie_munger.agent.json",
    "howard_marks.agent.json",
    "li_lu.agent.json",
    "peter_lynch.agent.json",
    "warren_buffett.agent.json",
}


def _load_toml(path: Path) -> dict:
    with path.open("rb") as file:
        return tomllib.load(file)


def test_plain_uv_sync_includes_both_workspace_packages():
    """A2: the root project must install both workspace members by default."""
    project = _load_toml(ROOT / "pyproject.toml")

    assert set(project["project"]["dependencies"]) >= {"tinyic", "tinytroupe"}
    assert project["tool"]["uv"]["sources"]["tinyic"] == {"workspace": True}
    assert project["tool"]["uv"]["sources"]["tinytroupe"] == {"workspace": True}


def test_uv_lock_is_not_ignored():
    """A2: the reproducibility lockfile must be eligible for source control."""
    assert (ROOT / "uv.lock").is_file(), "uv.lock has not been generated"
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "uv.lock"],
        cwd=ROOT,
        check=False,
    )

    assert result.returncode == 1, "uv.lock is still excluded by a gitignore rule"


def test_committed_config_has_no_proxy_endpoint():
    """D3: default committed configuration must not select a third-party proxy."""
    config_path = ROOT / "config.ini"
    config_text = config_path.read_text(encoding="utf-8")
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(config_text)

    assert not config.has_option("OpenAI", "BASE_URL")
    assert "codex-for.me" not in config_text.casefold()


def test_committed_config_uses_json_api_cache():
    """FR-0.1: root overrides must preserve upstream's JSON cache format."""
    config_path = ROOT / "config.ini"
    config_text = config_path.read_text(encoding="utf-8")
    config = configparser.ConfigParser(interpolation=None)
    config.read_string(config_text)

    cache_name = config.get("OpenAI", "CACHE_FILE_NAME")
    assert Path(cache_name).suffix.casefold() == ".json"
    assert ".pickle" not in config_text.casefold()


def test_openai_client_matches_unpatched_upstream_070():
    """A3/B12: prove the obsolete proxy client patch vanished in the rebase."""
    client_path = ROOT / "src" / "tinytroupe" / "clients" / "openai_client.py"
    client_bytes = client_path.read_bytes().replace(b"\r\n", b"\n")

    assert hashlib.sha256(client_bytes).hexdigest() == (
        "b43ee2d506a552029da868c1be8b16ae506b02282f648d729006b12339e73db0"
    )
    client_text = client_bytes.decode("utf-8")
    assert "PATCH(tinyIC)" not in client_text
    assert "codex-for.me" not in client_text.casefold()


def test_tinyic_console_entry_point_help(capsys):
    """FR-0.3: the installed ``tinyic`` command has a working help path."""
    package = _load_toml(ROOT / "src" / "tinyic" / "pyproject.toml")
    target = package["project"]["scripts"]["tinyic"]
    module_name, function_name = target.split(":", maxsplit=1)
    command = getattr(importlib.import_module(module_name), function_name)

    try:
        command(["--help"])
    except SystemExit as exc:
        assert exc.code == 0

    output = capsys.readouterr().out
    assert "TinyIC" in output
    assert "usage:" in output


def test_tinyic_sdist_rebuilds_wheel_with_persona_configs(tmp_path):
    """FR-0.3: the published artifact remains buildable and self-contained."""
    result = subprocess.run(
        [
            "uv",
            "build",
            "--offline",
            "--package",
            "tinyic",
            "--out-dir",
            str(tmp_path),
        ],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr

    wheels = list(tmp_path.glob("tinyic-*.whl"))
    assert len(wheels) == 1
    with ZipFile(wheels[0]) as wheel:
        members = set(wheel.namelist())
        expected = {
            f"tinyic/personas/configs/{filename}"
            for filename in PERSONA_CONFIG_FILES
        }
        assert expected <= members
        for config_path in expected:
            config = json.loads(wheel.read(config_path))
            assert config["persona"]["name"]
            assert len(config["persona"]) >= 5
