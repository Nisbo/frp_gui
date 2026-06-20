from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass
class BackupEntry:
    backup_id: str
    filename: str
    created_at: str
    source_path: str
    comment: str
    size: int


def create_backup(source_path: Path, backup_dir: Path, comment: str = "") -> BackupEntry:
    if not source_path.exists():
        raise FileNotFoundError(f"Config file not found: {source_path}")

    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_id = f"{source_path.name}.{stamp}"
    backup_path = backup_dir / f"{backup_id}.bak"
    meta_path = _meta_path(backup_path)

    shutil.copy2(source_path, backup_path)
    metadata = {
        "backup_id": backup_id,
        "filename": backup_path.name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "source_path": str(source_path),
        "comment": comment.strip(),
    }
    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return _entry_from_paths(backup_path, meta_path)


def list_backups(backup_dir: Path) -> list[BackupEntry]:
    if not backup_dir.exists():
        return []

    entries = []
    for backup_path in backup_dir.glob("*.bak"):
        entries.append(_entry_from_paths(backup_path, _meta_path(backup_path)))
    return sorted(entries, key=lambda item: item.created_at, reverse=True)


def restore_backup(backup_dir: Path, backup_id: str, target_path: Path) -> None:
    backup_path = _find_backup(backup_dir, backup_id)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup_path, target_path)


def delete_backup(backup_dir: Path, backup_id: str) -> None:
    backup_path = _find_backup(backup_dir, backup_id)
    meta_path = _meta_path(backup_path)
    backup_path.unlink()
    if meta_path.exists():
        meta_path.unlink()


def read_backup_content(backup_dir: Path, backup_id: str, limit: int = 200_000) -> str:
    backup_path = _find_backup(backup_dir, backup_id)
    content = backup_path.read_text(encoding="utf-8", errors="replace")
    if len(content) > limit:
        return content[:limit] + "\n\n# Preview truncated."
    return content


def get_backup(backup_dir: Path, backup_id: str) -> BackupEntry:
    backup_path = _find_backup(backup_dir, backup_id)
    return _entry_from_paths(backup_path, _meta_path(backup_path))


def _find_backup(backup_dir: Path, backup_id: str) -> Path:
    candidate = backup_dir / f"{backup_id}.bak"
    if candidate.exists() and candidate.is_file():
        return candidate
    raise FileNotFoundError(f"Backup not found: {backup_id}")


def _meta_path(backup_path: Path) -> Path:
    return backup_path.with_suffix(backup_path.suffix + ".json")


def _entry_from_paths(backup_path: Path, meta_path: Path) -> BackupEntry:
    metadata = {}
    if meta_path.exists():
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            metadata = {}

    backup_id = str(metadata.get("backup_id") or backup_path.name.removesuffix(".bak"))
    stat = backup_path.stat()
    created_at = str(metadata.get("created_at") or datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"))
    return BackupEntry(
        backup_id=backup_id,
        filename=backup_path.name,
        created_at=created_at,
        source_path=str(metadata.get("source_path") or ""),
        comment=str(metadata.get("comment") or ""),
        size=stat.st_size,
    )
