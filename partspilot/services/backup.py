"""数据备份：SQLite 在线备份（WAL 安全）+ 每日自动 + 保留份数清理。"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path

KEEP_BACKUPS = 30


def create_backup(db_path: Path, backup_dir: Path) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    # 精确到毫秒，避免同一秒内连续备份互相覆盖
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
    target = backup_dir / f"partspilot-{stamp}.db"
    source = sqlite3.connect(str(db_path))
    try:
        dest = sqlite3.connect(str(target))
        try:
            source.backup(dest)  # 在线备份，WAL 模式下也一致
        finally:
            dest.close()
    finally:
        source.close()
    return target


def list_backups(backup_dir: Path) -> list[dict]:
    if not backup_dir.exists():
        return []
    files = sorted(backup_dir.glob("partspilot-*.db"), reverse=True)
    return [
        {
            "name": f.name,
            "size": f.stat().st_size,
            "created_at": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
        }
        for f in files
    ]


def prune_backups(backup_dir: Path, keep: int = KEEP_BACKUPS) -> None:
    files = sorted(backup_dir.glob("partspilot-*.db"))
    for old in files[:-keep] if len(files) > keep else []:
        old.unlink()


def has_backup_today(backup_dir: Path) -> bool:
    today = datetime.now().strftime("%Y%m%d")
    return any(backup_dir.glob(f"partspilot-{today}-*.db"))
