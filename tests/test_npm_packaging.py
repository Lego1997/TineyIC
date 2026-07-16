"""Offline acceptance checks for the npm distribution wrapper."""

from __future__ import annotations

import configparser
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tomllib
from pathlib import Path, PurePosixPath

import pytest


ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN_PARTS = {
    ".git",
    ".tinyic",
    ".venv",
    "__pycache__",
    "artifacts",
    "build",
    "dist",
    "tests",
}
SECRET_PATTERNS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    re.compile(rb"\bnpm_[A-Za-z0-9]{36,}\b"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    re.compile(rb"\bsk-proj-[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"\bsk-(?!ant-|proj-)[A-Za-z0-9_-]{20,}\b"),
    re.compile(rb"\bAIza[0-9A-Za-z_-]{30,}\b"),
    re.compile(rb"\bxai-[A-Za-z0-9_-]{20,}\b"),
)


def _run(command: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=kwargs.pop("cwd", ROOT),
        capture_output=True,
        check=False,
        text=True,
        timeout=kwargs.pop("timeout", 180),
        **kwargs,
    )


@pytest.fixture(scope="module")
def npm_tarball(tmp_path_factory: pytest.TempPathFactory) -> Path:
    configured = os.environ.get("TINYIC_NPM_TARBALL")
    if configured:
        tarball = Path(configured).resolve()
        assert tarball.is_file(), f"configured npm tarball does not exist: {tarball}"
        return tarball
    destination = tmp_path_factory.mktemp("npm-pack")
    result = _run(
        [
            "npm",
            "pack",
            "--json",
            "--pack-destination",
            str(destination),
        ]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert len(payload) == 1
    tarball = destination / payload[0]["filename"]
    assert tarball.is_file()
    return tarball


@pytest.fixture(scope="module")
def installed_npm_package(
    npm_tarball: Path,
    tmp_path_factory: pytest.TempPathFactory,
) -> tuple[Path, Path, Path]:
    prefix = tmp_path_factory.mktemp("npm-prefix")
    result = _run(
        [
            "npm",
            "install",
            "--global",
            "--prefix",
            str(prefix),
            str(npm_tarball),
            "--offline",
            "--ignore-scripts",
            "--no-audit",
            "--no-fund",
        ]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    root_result = _run(["npm", "root", "--global", "--prefix", str(prefix)])
    assert root_result.returncode == 0, root_result.stdout + root_result.stderr
    package_root = Path(root_result.stdout.strip()) / "@lego1997" / "tinyic"
    executable_directory = prefix if os.name == "nt" else prefix / "bin"
    assert package_root.is_dir()
    assert (executable_directory / ("tinyic.cmd" if os.name == "nt" else "tinyic")).exists()
    return prefix, package_root, executable_directory


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\n")
    return digest.hexdigest()


def test_npm_metadata_matches_python_distribution():
    metadata = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    with (ROOT / "src" / "tinyic" / "pyproject.toml").open("rb") as stream:
        python_project = tomllib.load(stream)

    assert metadata["name"] == "@lego1997/tinyic"
    assert metadata["version"] == python_project["project"]["version"]
    assert metadata["bin"] == {"tinyic": "bin/tinyic.mjs"}
    assert metadata["license"] == "MIT"
    assert metadata["engines"]["node"] == ">=22.14"
    assert metadata["os"] == ["darwin", "linux"]
    assert metadata["repository"]["url"] == (
        "git+https://github.com/Lego1997/TineyIC.git"
    )
    assert metadata["scripts"]["test:offline"] == (
        "npm test && uv run --offline pytest -q"
    )
    assert metadata["scripts"]["prepublishOnly"] == "npm run test:offline"
    for field in (
        "dependencies",
        "devDependencies",
        "optionalDependencies",
        "peerDependencies",
        "bundledDependencies",
        "bundleDependencies",
    ):
        assert field not in metadata
    for lifecycle in (
        "preinstall",
        "install",
        "postinstall",
        "prepublish",
        "prepare",
        "preprepare",
        "postprepare",
        "dependencies",
        "predependencies",
        "postdependencies",
    ):
        assert lifecycle not in metadata.get("scripts", {})


def test_npm_pack_is_exactly_the_reviewed_runtime_workspace(npm_tarball: Path):
    tracked_result = _run(
        ["git", "ls-files", "src/tinyic", "src/tinytroupe"]
    )
    assert tracked_result.returncode == 0, tracked_result.stderr
    tracked = {line for line in tracked_result.stdout.splitlines() if line}
    required = tracked | {
        ".python-version",
        "LICENSE",
        "README.md",
        "bin/tinyic.mjs",
        "config.ini",
        "docs/assets/tinyic-debate-transcript.jpg",
        "docs/assets/tinyic-town-hall.jpg",
        "package.json",
        "pyproject.toml",
        "scripts/verify-npm-package.mjs",
        "tinyic.toml",
        "uv.lock",
    }

    with tarfile.open(npm_tarball, "r:gz") as archive:
        members = {member.name for member in archive.getmembers() if member.isfile()}

    expected = {f"package/{relative}" for relative in required}
    missing = expected - members
    unexpected = members - expected
    assert not missing, f"npm tarball is missing: {sorted(missing)}"
    assert not unexpected, f"npm tarball contains unreviewed files: {sorted(unexpected)}"


def test_npm_pack_excludes_private_generated_and_unsafe_entries(npm_tarball: Path):
    with tarfile.open(npm_tarball, "r:gz") as archive:
        for member in archive.getmembers():
            pure = PurePosixPath(member.name)
            assert not pure.is_absolute()
            assert ".." not in pure.parts
            assert member.isfile() or member.isdir()
            relative_parts = set(pure.parts[1:])
            assert not (relative_parts & FORBIDDEN_PARTS), member.name
            assert not any(
                part == ".npmrc" or part == ".env" or part.startswith(".env.")
                for part in pure.parts[1:]
            ), member.name
            assert not member.name.endswith((".jsonl", ".log", ".pyc"))
            assert ".egg-info/" not in member.name
            assert member.name not in {
                "package/AGENTS.local.md",
                "package/docs/CODEX_KICKOFF_V22.md",
                "package/package-lock.json",
                "package/plan.md",
            }
            if member.isfile():
                extracted = archive.extractfile(member)
                assert extracted is not None
                content = extracted.read()
                for pattern in SECRET_PATTERNS:
                    assert pattern.search(content) is None, member.name


def test_npm_bin_is_executable(npm_tarball: Path):
    with tarfile.open(npm_tarball, "r:gz") as archive:
        launcher = archive.getmember("package/bin/tinyic.mjs")
    assert launcher.mode & 0o111


def test_readme_documents_npm_install_and_interface_screenshots():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "npm install --global @lego1997/tinyic" in readme
    assert "docs/assets/tinyic-town-hall.jpg" in readme
    assert "docs/assets/tinyic-debate-transcript.jpg" in readme


def test_npm_ci_covers_supported_runtime_boundaries():
    workflow = (ROOT / ".github" / "workflows" / "npm-package.yml").read_text(
        encoding="utf-8"
    )
    assert "ubuntu-24.04" in workflow
    assert "macos-15" in workflow
    assert "node: 22.14.0" in workflow
    assert "node: 24" in workflow
    assert "uv: 0.7.12" in workflow
    assert "npm run test:offline" in workflow
    assert "TINYIC_NPM_TARBALL" in workflow


def test_npm_release_workflow_is_tokenless_and_staged():
    workflow = (ROOT / ".github" / "workflows" / "publish-npm.yml").read_text(
        encoding="utf-8"
    )
    assert "workflow_dispatch:" in workflow
    assert "environment: npm-production" in workflow
    assert "id-token: write" in workflow
    assert 'test "$GITHUB_REF" = "refs/tags/$RELEASE_TAG"' in workflow
    assert 'test "$GITHUB_SHA" = "$(git rev-parse HEAD)"' in workflow
    assert "@lego1997/tinyic" in workflow
    assert "npm view tinyic" not in workflow
    assert "stable semantic versions only" in workflow
    assert "must still be newer than the current latest" in workflow
    assert "npm stage publish" in workflow
    assert "npm publish " not in workflow
    assert "NPM_TOKEN" not in workflow
    assert "NODE_AUTH_TOKEN" not in workflow
    assert "--allow-publish" not in workflow


def test_packed_workspace_lock_is_valid_offline(npm_tarball: Path, tmp_path: Path):
    with tarfile.open(npm_tarball, "r:gz") as archive:
        archive.extractall(tmp_path, filter="data")
    result = _run(
        [
            "uv",
            "lock",
            "--check",
            "--offline",
            "--project",
            str(tmp_path / "package"),
        ],
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(os.name == "nt", reason="fake uv shim is POSIX-only")
def test_packaged_source_digest_separates_modified_local_artifacts(
    npm_tarball: Path,
    tmp_path: Path,
):
    roots = [tmp_path / "first", tmp_path / "second"]
    for root in roots:
        root.mkdir()
        with tarfile.open(npm_tarball, "r:gz") as archive:
            archive.extractall(root, filter="data")
    modified = roots[1] / "package" / "src" / "tinyic" / "__init__.py"
    modified.write_text(
        modified.read_text(encoding="utf-8") + "\n# local artifact variant\n",
        encoding="utf-8",
    )

    fake_bin = tmp_path / "fake-bin-digest"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    shutil.copy2(ROOT / "tests" / "npm" / "fixtures" / "fake-uv.mjs", fake_uv)
    fake_uv.chmod(0o755)
    environment = os.environ.copy()
    environment.pop("TINYIC_CONFIG", None)
    environment.update(
        {
            "FAKE_UV_MODE": "inspect",
            "PATH": os.pathsep.join([str(fake_bin), environment.get("PATH", "")]),
            "TINYIC_NPM_CACHE_DIR": str(tmp_path / "shared-cache"),
        }
    )

    projects = []
    for root in roots:
        result = _run(
            ["node", str(root / "package" / "bin" / "tinyic.mjs"), "--version"],
            cwd=tmp_path,
            env=environment,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        payload = json.loads(result.stdout)
        projects.append(payload["argv"][payload["argv"].index("--project") + 1])
    assert projects[0] != projects[1]


@pytest.mark.skipif(os.name == "nt", reason="fake uv shim is POSIX-only")
def test_local_tarball_global_install_exposes_tinyic_on_path(
    installed_npm_package: tuple[Path, Path, Path],
    tmp_path: Path,
):
    _, package_root, executable_directory = installed_npm_package
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    shutil.copy2(ROOT / "tests" / "npm" / "fixtures" / "fake-uv.mjs", fake_uv)
    fake_uv.chmod(0o755)
    caller = tmp_path / "caller space"
    caller.mkdir()
    environment = os.environ.copy()
    environment.pop("TINYIC_CONFIG", None)
    environment.update(
        {
            "FAKE_UV_MODE": "inspect",
            "PATH": os.pathsep.join(
                [str(executable_directory), str(fake_bin), environment.get("PATH", "")]
            ),
            "TINYIC_NPM_CACHE_DIR": str(tmp_path / "npm-cache"),
        }
    )
    result = _run(
        ["tinyic", "--version", "value with spaces"],
        cwd=caller,
        env=environment,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    payload = json.loads(result.stdout)
    assert payload["cwd"] == str(caller)
    project_index = payload["argv"].index("--project") + 1
    cached_project = Path(payload["argv"][project_index])
    assert cached_project != package_root
    assert cached_project.is_relative_to(tmp_path / "npm-cache")
    assert (cached_project / "src" / "tinyic" / "cli.py").is_file()
    assert payload["tinyicConfig"] == str(cached_project / "tinyic.toml")
    assert payload["argv"][-2:] == ["--version", "value with spaces"]
    expected_config = configparser.ConfigParser()
    expected_config.read(
        [
            package_root / "src" / "tinytroupe" / "config.ini",
            package_root / "config.ini",
        ]
    )
    cached_config = configparser.ConfigParser()
    cached_config.read(cached_project / "src" / "tinytroupe" / "config.ini")
    assert {section: dict(cached_config[section]) for section in cached_config.sections()} == {
        section: dict(expected_config[section]) for section in expected_config.sections()
    }


@pytest.mark.timeout(300)
def test_global_install_runs_real_cli_offline_without_mutating_package(
    installed_npm_package: tuple[Path, Path, Path],
    tmp_path: Path,
):
    _, package_root, executable_directory = installed_npm_package
    before = _tree_digest(package_root)
    caller = tmp_path / "real-cli-caller"
    caller.mkdir()
    environment = os.environ.copy()
    environment.pop("TINYIC_CONFIG", None)
    environment.update(
        {
            "PATH": os.pathsep.join(
                [str(executable_directory), environment.get("PATH", "")]
            ),
            "TINYIC_NPM_CACHE_DIR": str(tmp_path / "npm-cache"),
            "UV_OFFLINE": "1",
            "UV_PYTHON_DOWNLOADS": "never",
        }
    )
    result = _run(
        ["tinyic", "--version"],
        cwd=caller,
        env=environment,
        timeout=240,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout == "tinyic 2.2.0\n"
    assert _tree_digest(package_root) == before
    assert not (package_root / ".venv").exists()


@pytest.mark.timeout(300)
def test_concurrent_real_first_launches_share_one_complete_runtime(
    installed_npm_package: tuple[Path, Path, Path],
    tmp_path: Path,
):
    _, package_root, executable_directory = installed_npm_package
    before = _tree_digest(package_root)
    caller = tmp_path / "concurrent-caller"
    caller.mkdir()
    cache = tmp_path / "concurrent-cache"
    environment = os.environ.copy()
    environment.pop("TINYIC_CONFIG", None)
    environment.update(
        {
            "PATH": os.pathsep.join(
                [str(executable_directory), environment.get("PATH", "")]
            ),
            "TINYIC_NPM_CACHE_DIR": str(cache),
            "UV_OFFLINE": "1",
            "UV_PYTHON_DOWNLOADS": "never",
        }
    )
    processes = [
        subprocess.Popen(
            ["tinyic", "--version"],
            cwd=caller,
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for _ in range(6)
    ]
    results: list[tuple[int, str, str]] = []
    try:
        for process in processes:
            stdout, stderr = process.communicate(timeout=240)
            results.append((process.returncode, stdout, stderr))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait()

    for returncode, stdout, stderr in results:
        assert returncode == 0, stderr
        assert stdout == "tinyic 2.2.0\n"
    releases = list((cache / "tinyic" / "npm").iterdir())
    assert len(releases) == 1
    release = releases[0]
    assert (release / "environment" / ".tinyic-npm-runtime").is_file()
    assert not (release / "environment.lock").exists()
    assert not list(release.glob(".runtime-*"))
    assert _tree_digest(package_root) == before


def test_node_launcher_contract():
    result = _run(["npm", "test", "--silent"])
    assert result.returncode == 0, result.stdout + result.stderr
