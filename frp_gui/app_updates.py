from __future__ import annotations

import shutil
import stat
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import BinaryIO


PROJECT_DIRS = ("frp_gui", "scripts", "sample")
PROJECT_FILES = ("run.py", "requirements.txt", "README.md", ".gitignore")


@dataclass
class AppUpdateResult:
    ok: bool
    message: str
    details: list[str] = field(default_factory=list)
    backup_path: Path | None = None


def update_status(app_root: Path) -> dict[str, str | bool | None]:
    git = shutil.which("git")
    status: dict[str, str | bool | None] = {
        "git_available": bool(git),
        "git_repo": (app_root / ".git").exists(),
        "git_ready": False,
        "branch": None,
        "commit": None,
        "remote": None,
        "message": None,
    }
    if not git:
        status["message"] = "git is not installed or not available in PATH."
        return status
    if not status["git_repo"]:
        status["message"] = "This installation is not a git checkout."
        return status

    branch = _run([git, "rev-parse", "--abbrev-ref", "HEAD"], app_root)
    commit = _run([git, "rev-parse", "--short", "HEAD"], app_root)
    remote = _run([git, "remote", "get-url", "origin"], app_root)
    if branch.returncode == 0:
        status["branch"] = branch.stdout.strip()
    if commit.returncode == 0:
        status["commit"] = commit.stdout.strip()
    if remote.returncode == 0:
        status["remote"] = remote.stdout.strip()

    if not status["commit"]:
        status["message"] = "Git repository has no commits yet."
    elif not status["remote"]:
        status["message"] = "Git remote origin is not configured."
    else:
        status["git_ready"] = True
        status["message"] = "Git update is available."
    return status


def update_from_git(app_root: Path) -> AppUpdateResult:
    git = shutil.which("git")
    if not git:
        return AppUpdateResult(False, "git is not installed or not available in PATH.")
    if not (app_root / ".git").exists():
        return AppUpdateResult(False, "This installation is not a git checkout.")

    status = update_status(app_root)
    if not status["git_ready"]:
        return AppUpdateResult(False, str(status["message"] or "Git checkout is not ready for updates."))

    backup_path = create_app_backup(app_root)
    details = [f"Backup created: {backup_path}"]

    fetch = _run([git, "fetch", "--tags", "--prune", "origin"], app_root)
    details.append(_format_command_result("git fetch --tags --prune origin", fetch))
    if fetch.returncode != 0:
        return AppUpdateResult(False, "Git fetch failed. No files were replaced by FRP Gui.", details, backup_path)

    pull = _run([git, "pull", "--ff-only"], app_root)
    details.append(_format_command_result("git pull --ff-only", pull))
    if pull.returncode != 0:
        return AppUpdateResult(False, "Git pull failed. Check the output below.", details, backup_path)

    return AppUpdateResult(True, "Git update completed. Restart FRP Gui to run the new code.", details, backup_path)


def update_from_zip(app_root: Path, zip_stream: BinaryIO) -> AppUpdateResult:
    with tempfile.TemporaryDirectory(prefix="frp-gui-update-") as tmp_name:
        extract_dir = Path(tmp_name) / "extract"
        extract_dir.mkdir()
        try:
            _safe_extract_zip(zip_stream, extract_dir)
            source_root = _find_project_root(extract_dir)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            return AppUpdateResult(False, f"ZIP update failed validation: {exc}")

        backup_path = create_app_backup(app_root)
        details = [
            f"Backup created: {backup_path}",
            f"Source root detected: {source_root.name}",
        ]
        try:
            copied = _copy_project_files(source_root, app_root)
        except (OSError, ValueError) as exc:
            return AppUpdateResult(False, f"ZIP update failed while copying files: {exc}", details, backup_path)
        details.extend(f"Updated: {item}" for item in copied)

    return AppUpdateResult(True, "ZIP update installed. Restart FRP Gui to run the new code.", details, backup_path)


def update_from_release_zip(app_root: Path, zip_url: str, version: str, timeout: int = 30) -> AppUpdateResult:
    request = urllib.request.Request(
        zip_url,
        headers={
            "Accept": "application/zip",
            "User-Agent": "frp-gui-release-update",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            zip_data = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return AppUpdateResult(False, f"Release download failed: {exc}")

    result = update_from_zip(app_root, BytesIO(zip_data))
    if result.ok:
        result.message = f"Release {version} installed. Restart FRP Gui to run the new code."
    result.details.insert(0, f"Downloaded official release ZIP: {version}")
    return result


def create_app_backup(app_root: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_root = app_root / "data" / "app-updates" / "backups" / timestamp
    backup_root.mkdir(parents=True, exist_ok=False)

    for directory in PROJECT_DIRS:
        source = app_root / directory
        if source.exists():
            shutil.copytree(source, backup_root / directory, ignore=_ignore_runtime_files)

    for filename in PROJECT_FILES:
        source = app_root / filename
        if source.exists():
            shutil.copy2(source, backup_root / filename)

    return backup_root


def _safe_extract_zip(zip_stream: BinaryIO, destination: Path) -> None:
    with zipfile.ZipFile(zip_stream) as archive:
        for info in archive.infolist():
            target = (destination / info.filename).resolve()
            if not target.is_relative_to(destination.resolve()):
                raise ValueError(f"Unsafe ZIP path: {info.filename}")

            mode = info.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise ValueError(f"Symlinks are not allowed in update ZIP files: {info.filename}")

            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(info) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)


def _find_project_root(extract_dir: Path) -> Path:
    candidates = [extract_dir]
    candidates.extend(path for path in extract_dir.iterdir() if path.is_dir())

    for candidate in candidates:
        if (candidate / "frp_gui").is_dir() and (candidate / "run.py").is_file():
            return candidate

    raise ValueError("ZIP must contain run.py and the frp_gui directory.")


def _copy_project_files(source_root: Path, app_root: Path) -> list[str]:
    copied: list[str] = []

    for directory in PROJECT_DIRS:
        source = source_root / directory
        if not source.exists():
            continue
        target = app_root / directory
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target, ignore=_ignore_runtime_files)
        copied.append(directory)

    for filename in PROJECT_FILES:
        source = source_root / filename
        if not source.exists():
            continue
        shutil.copy2(source, app_root / filename)
        copied.append(filename)

    if "frp_gui" not in copied or "run.py" not in copied:
        raise ValueError("Update ZIP did not contain the required application files.")

    return copied


def _ignore_runtime_files(_directory: str, names: list[str]) -> set[str]:
    ignored = {"__pycache__", ".DS_Store"}
    return {name for name in names if name in ignored or name.endswith((".pyc", ".pyo"))}


def _run(command: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, cwd=cwd, check=False, capture_output=True, text=True, timeout=120)


def _format_command_result(label: str, result: subprocess.CompletedProcess[str]) -> str:
    output = (result.stdout + "\n" + result.stderr).strip()
    if output:
        return f"$ {label}\n{output}"
    return f"$ {label}\nexit code {result.returncode}"
