"""Offline acceptance checks for the M0 packaging foundation."""

from __future__ import annotations

import configparser
import hashlib
import importlib
import importlib.util
import json
import subprocess
import tarfile
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
WEB_ASSET_FILES = {"app.js", "index.html", "style.css"}
PRIVATE_CLASSIFIER = "Private :: Do Not Upload"
# Pinned manifest of the vendored subtree. To regenerate after adding a file to
# VENDORED_DIVERGENCES (the documented procedure), recompute the aggregate over
# every non-allowlisted, non-cache file exactly as
# test_undiverged_vendor_files_match_pinned_upstream_manifest does, then update
# both VENDORED_FILE_COUNT and UPSTREAM_070_BASELINE_SHA256 below:
#
#   python - <<'PY'
#   import hashlib; from pathlib import Path
#   root = Path("src/tinytroupe")
#   recs = []
#   for p in root.rglob("*"):
#       rel = p.relative_to(root).as_posix()
#       if (not p.is_file() or rel in VENDORED_DIVERGENCES
#               or "__pycache__" in p.parts or p.suffix == ".pyc"
#               or any(x.endswith(".egg-info") for x in p.parts)):
#           continue
#       b = p.read_bytes()
#       recs.append((rel, hashlib.sha1(f"blob {len(b)}\0".encode()+b).hexdigest()))
#   agg = hashlib.sha256()
#   for rel, blob in sorted(recs):
#       agg.update(rel.encode()); agg.update(b"\0")
#       agg.update(blob.encode()); agg.update(b"\n")
#   print(len(recs), agg.hexdigest())
#   PY
UPSTREAM_070_BASELINE_SHA256 = (
    "ae4f9be6044110b71a2a9df3f7821d54bea9b2f35569369090eef97f455b546b"
)
VENDORED_FILE_COUNT = 84
VENDORED_DIVERGENCES = {
    "FORK.md",
    "LICENSE",
    "pyproject.toml",
    "config.ini",
    "session.py",
    "__init__.py",
    "agent/tiny_person.py",
    "clients/__init__.py",
    "clients/ollama_client.py",
    "clients/openai_client.py",
    "environment/tiny_world.py",
    "extraction/results_extractor.py",
    "utils/behavior.py",
    "utils/config.py",
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


def test_workspace_packages_fail_safe_against_registry_uploads():
    """FR-0.4: both git-source-only packages must reject PyPI uploads."""
    package_projects = (
        ROOT / "src" / "tinyic" / "pyproject.toml",
        ROOT / "src" / "tinytroupe" / "pyproject.toml",
    )

    for project_path in package_projects:
        project = _load_toml(project_path)
        assert PRIVATE_CLASSIFIER in project["project"]["classifiers"], project_path


def test_tinyic_declares_httpx_with_socks_extra():
    """TIC-008: tinyic imports httpx directly (models/adapters/_http.py) and must
    declare it with the ``socks`` extra so SOCKS-proxy environments work across
    every wire surface (debate, data pipeline, doctor, persona research) instead
    of free-riding on the vendored tinytroupe's httpx dependency."""
    project = _load_toml(ROOT / "src" / "tinyic" / "pyproject.toml")
    dependencies = project["project"]["dependencies"]
    assert any(
        dep.replace(" ", "").startswith("httpx[socks]") for dep in dependencies
    ), dependencies
    # A lock regression that drops the pure-Python socks backend fails loudly.
    assert importlib.util.find_spec("socksio") is not None


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


def test_openai_client_has_no_obsolete_proxy_patch():
    """A3: later documented fixes must not resurrect the removed proxy patch."""
    client_path = ROOT / "src" / "tinytroupe" / "clients" / "openai_client.py"
    client_bytes = client_path.read_bytes().replace(b"\r\n", b"\n")
    client_text = client_bytes.decode("utf-8")

    assert "PATCH(tinyIC)" not in client_text
    assert "codex-for.me" not in client_text.casefold()
    assert '"stream": False' in client_text
    assert "beta.chat.completions.parse" in client_text


def test_undiverged_vendor_files_match_pinned_upstream_manifest():
    """All non-allowlisted files retain the verified upstream 0.7.0 bytes."""
    vendor_root = ROOT / "src" / "tinytroupe"
    records: list[tuple[str, str]] = []
    for path in vendor_root.rglob("*"):
        relative = path.relative_to(vendor_root).as_posix()
        if (
            not path.is_file()
            or relative in VENDORED_DIVERGENCES
            or "__pycache__" in path.parts
            or any(part.endswith(".egg-info") for part in path.parts)
            or path.suffix == ".pyc"
        ):
            continue
        content = path.read_bytes()
        blob = hashlib.sha1(
            f"blob {len(content)}\0".encode("ascii") + content
        ).hexdigest()
        records.append((relative, blob))

    aggregate = hashlib.sha256()
    for relative, blob in sorted(records):
        aggregate.update(relative.encode("utf-8"))
        aggregate.update(b"\0")
        aggregate.update(blob.encode("ascii"))
        aggregate.update(b"\n")

    assert len(records) == VENDORED_FILE_COUNT
    assert aggregate.hexdigest() == UPSTREAM_070_BASELINE_SHA256


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


def test_tinyic_sdist_rebuilds_wheel_with_package_data(tmp_path):
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

    build_output = result.stdout + result.stderr
    assert result.returncode == 0, build_output
    assert "Package 'tinyic.web.assets' is absent" not in build_output

    sdists = list(tmp_path.glob("tinyic-*.tar.gz"))
    assert len(sdists) == 1
    with tarfile.open(sdists[0]) as sdist:
        members = {member.name for member in sdist.getmembers()}
        root = sdists[0].name.removesuffix(".tar.gz")
        expected_assets = {
            f"{root}/web/assets/{filename}" for filename in WEB_ASSET_FILES
        }
        assert expected_assets <= members

    wheels = list(tmp_path.glob("tinyic-*.whl"))
    assert len(wheels) == 1
    with ZipFile(wheels[0]) as wheel:
        members = set(wheel.namelist())
        expected = {
            f"tinyic/personas/configs/{filename}"
            for filename in PERSONA_CONFIG_FILES
        }
        assert expected <= members
        packaged_personas = {
            member
            for member in members
            if member.startswith("tinyic/personas/configs/")
            and member.endswith(".agent.json")
        }
        assert packaged_personas == expected
        expected_assets = {
            f"tinyic/web/assets/{filename}" for filename in WEB_ASSET_FILES
        }
        assert expected_assets <= members
        for config_path in expected:
            config = json.loads(wheel.read(config_path))
            assert config["persona"]["name"]
            assert len(config["persona"]) >= 5
