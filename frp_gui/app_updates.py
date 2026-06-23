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
APP_BACKUP_META_FILE = "backup-meta.txt"


@dataclass
class AppUpdateResult:
    ok: bool
    message: str
    details: list[str] = field(default_factory=list)
    backup_path: Path | None = None


@dataclass
class AppBackupEntry:
    backup_id: str
    path: Path
    created_at: str
    size: int
    reason: str
    comment: str


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

    backup_path = create_app_backup(app_root, "Before Git update", "Created automatically before running git pull.")
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


def update_from_zip(app_root: Path, zip_stream: BinaryIO, backup_reason: str = "Before ZIP update", backup_comment: str = "Created automatically before installing an uploaded ZIP.") -> AppUpdateResult:
    with tempfile.TemporaryDirectory(prefix="frp-gui-update-") as tmp_name:
        extract_dir = Path(tmp_name) / "extract"
        extract_dir.mkdir()
        try:
            _safe_extract_zip(zip_stream, extract_dir)
            source_root = _find_project_root(extract_dir)
        except (OSError, ValueError, zipfile.BadZipFile) as exc:
            return AppUpdateResult(False, f"ZIP update failed validation: {exc}")

        backup_path = create_app_backup(app_root, backup_reason, backup_comment)
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


def update_from_release(app_root: Path, zip_url: str, version: str, timeout: int = 30) -> AppUpdateResult:
    git_result = _update_git_checkout_to_release(app_root, version)
    if git_result is not None:
        return git_result

    return update_from_release_zip(app_root, zip_url, version, timeout)


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

    result = update_from_zip(
        app_root,
        BytesIO(zip_data),
        backup_reason=f"Before installing release {version}",
        backup_comment=f"Created automatically before updating to release {version}.",
    )
    if result.ok:
        result.message = f"Release {version} installed. Restart FRP Gui to run the new code."
    result.details.insert(0, f"Downloaded official release ZIP: {version}")
    return result


def _update_git_checkout_to_release(app_root: Path, version: str) -> AppUpdateResult | None:
    git = shutil.which("git")
    if not git or not (app_root / ".git").exists():
        return None

    backup_path = create_app_backup(
        app_root,
        f"Before installing release {version}",
        f"Created automatically before moving the git checkout to release {version}.",
    )
    details = [f"Backup created: {backup_path}"]

    fetch = _run([git, "fetch", "--tags", "--prune", "origin"], app_root)
    details.append(_format_command_result("git fetch --tags --prune origin", fetch))
    if fetch.returncode != 0:
        return AppUpdateResult(False, "Git fetch failed. No files were replaced by FRP Gui.", details, backup_path)

    tag_ref = f"refs/tags/{version}"
    tag_check = _run([git, "rev-parse", "--verify", tag_ref], app_root)
    details.append(_format_command_result(f"git rev-parse --verify {tag_ref}", tag_check))
    if tag_check.returncode != 0:
        return AppUpdateResult(False, f"Release tag {version} was not found after fetch.", details, backup_path)

    checkout = _run([git, "checkout", "--force", "-B", "main", tag_ref], app_root)
    details.append(_format_command_result(f"git checkout --force -B main {tag_ref}", checkout))
    if checkout.returncode != 0:
        return AppUpdateResult(False, "Git checkout failed. Check the output below.", details, backup_path)

    return AppUpdateResult(True, f"Release {version} installed through git. Restart FRP Gui to run the new code.", details, backup_path)


def create_app_backup(app_root: Path, reason: str = "Manual app backup", comment: str = "") -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_root = _app_backup_root(app_root) / timestamp
    backup_root.mkdir(parents=True, exist_ok=False)

    for directory in PROJECT_DIRS:
        source = app_root / directory
        if source.exists():
            shutil.copytree(source, backup_root / directory, ignore=_ignore_runtime_files)

    for filename in PROJECT_FILES:
        source = app_root / filename
        if source.exists():
            shutil.copy2(source, backup_root / filename)

    _write_backup_meta(backup_root, reason, comment)
    return backup_root


def list_app_backups(app_root: Path) -> list[AppBackupEntry]:
    backup_root = _app_backup_root(app_root)
    if not backup_root.exists():
        return []

    entries: list[AppBackupEntry] = []
    for path in backup_root.iterdir():
        if not path.is_dir():
            continue
        meta = _read_backup_meta(path)
        entries.append(
            AppBackupEntry(
                backup_id=path.name,
                path=path,
                created_at=_format_backup_id(path.name),
                size=_directory_size(path),
                reason=meta["reason"],
                comment=meta["comment"],
            )
        )
    return sorted(entries, key=lambda entry: entry.backup_id, reverse=True)


def restore_app_backup(app_root: Path, backup_id: str) -> AppUpdateResult:
    try:
        backup_path = _app_backup_path(app_root, backup_id)
    except ValueError as exc:
        return AppUpdateResult(False, str(exc))
    if not backup_path.exists() or not backup_path.is_dir():
        return AppUpdateResult(False, f"App update backup not found: {backup_id}")

    safety_backup = create_app_backup(app_root, "Before app backup restore", f"Created automatically before restoring app backup {backup_id}.")
    details = [f"Safety backup created: {safety_backup}", f"Restored from: {backup_path}"]
    try:
        copied = _copy_project_files(backup_path, app_root)
    except (OSError, ValueError) as exc:
        return AppUpdateResult(False, f"App backup restore failed: {exc}", details, safety_backup)
    details.extend(f"Restored: {item}" for item in copied)
    return AppUpdateResult(True, "App backup restored. Restart FRP Gui to run the restored code.", details, safety_backup)


def delete_app_backup(app_root: Path, backup_id: str) -> None:
    backup_path = _app_backup_path(app_root, backup_id)
    if not backup_path.exists() or not backup_path.is_dir():
        raise FileNotFoundError(f"App update backup not found: {backup_id}")
    shutil.rmtree(backup_path)


def _app_backup_root(app_root: Path) -> Path:
    return app_root / "data" / "app-updates" / "backups"


def _app_backup_path(app_root: Path, backup_id: str) -> Path:
    if not backup_id or "/" in backup_id or "\\" in backup_id or backup_id in {".", ".."}:
        raise ValueError("Invalid app backup id.")
    backup_root = _app_backup_root(app_root).resolve()
    backup_path = (backup_root / backup_id).resolve()
    if not backup_path.is_relative_to(backup_root):
        raise ValueError("Invalid app backup path.")
    return backup_path


def _directory_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def _write_backup_meta(path: Path, reason: str, comment: str) -> None:
    content = "\n".join(
        (
            f"reason={_clean_meta_value(reason)}",
            f"comment={_clean_meta_value(comment)}",
        )
    )
    (path / APP_BACKUP_META_FILE).write_text(content + "\n", encoding="utf-8")


def _read_backup_meta(path: Path) -> dict[str, str]:
    meta = {"reason": "Unknown", "comment": ""}
    meta_path = path / APP_BACKUP_META_FILE
    if not meta_path.exists():
        return meta

    for line in meta_path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, separator, value = line.partition("=")
        if separator and key in meta:
            meta[key] = value.strip()
    return meta


def _clean_meta_value(value: str) -> str:
    return " ".join(str(value).split())


def _format_backup_id(backup_id: str) -> str:
    for pattern in ("%Y%m%d-%H%M%S-%f", "%Y%m%d-%H%M%S"):
        try:
            return datetime.strptime(backup_id, pattern).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            continue
    return backup_id


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
