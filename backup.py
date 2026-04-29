from __future__ import annotations

import json
import re
import sqlite3
import zipfile
from contextlib import closing
from datetime import datetime
from pathlib import Path

from db import BASE_DIR, DB_PATH, init_db


BACKUP_DIR = BASE_DIR / "backups"
MEDIA_DIR = BASE_DIR / "static" / "media"
BACKUP_STAMP_RE = re.compile(r"^(?:backup|telegram_logs|telegram_media)-(\d{8}-\d{6})")


def directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(file.stat().st_size for file in path.rglob("*") if file.is_file())


def backup_database(destination: Path) -> None:
    init_db(DB_PATH)
    with closing(sqlite3.connect(DB_PATH)) as source:
        with closing(sqlite3.connect(destination)) as target:
            source.backup(target)


def backup_media(destination: Path) -> int:
    count = 0
    with zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        if not MEDIA_DIR.exists():
            return count
        for file in MEDIA_DIR.rglob("*"):
            if file.is_file():
                archive.write(file, file.relative_to(BASE_DIR))
                count += 1
    return count


def create_backup() -> dict[str, object]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")

    db_backup = BACKUP_DIR / f"telegram_logs-{stamp}.sqlite3"
    media_backup = BACKUP_DIR / f"telegram_media-{stamp}.zip"
    manifest = BACKUP_DIR / f"backup-{stamp}.json"

    backup_database(db_backup)
    media_count = backup_media(media_backup)

    result = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "backup_dir": str(BACKUP_DIR),
        "database": {
            "path": str(db_backup),
            "size": db_backup.stat().st_size,
        },
        "media": {
            "path": str(media_backup),
            "size": media_backup.stat().st_size,
            "file_count": media_count,
        },
        "source": {
            "database": str(DB_PATH),
            "media_dir": str(MEDIA_DIR),
            "media_size": directory_size(MEDIA_DIR),
        },
    }
    manifest.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    result["manifest"] = {"path": str(manifest), "size": manifest.stat().st_size}
    return result


def list_backup_files() -> list[dict[str, object]]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    return [
        {
            "name": path.name,
            "path": str(path),
            "size": path.stat().st_size,
            "modified_at": path.stat().st_mtime,
        }
        for path in sorted(
            BACKUP_DIR.glob("*"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        if path.is_file()
    ]


def backup_stamps() -> list[str]:
    stamps = set()
    for file in BACKUP_DIR.glob("*"):
        if not file.is_file():
            continue
        match = BACKUP_STAMP_RE.match(file.name)
        if match:
            stamps.add(match.group(1))
    return sorted(stamps, reverse=True)


def cleanup_backups(keep: int = 10) -> dict[str, object]:
    keep = max(1, min(int(keep), 100))
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    stamps_to_delete = set(backup_stamps()[keep:])
    deleted: list[dict[str, object]] = []

    for file in BACKUP_DIR.glob("*"):
        match = BACKUP_STAMP_RE.match(file.name)
        if not match or match.group(1) not in stamps_to_delete:
            continue
        size = file.stat().st_size
        file.unlink()
        deleted.append({"name": file.name, "size": size})

    return {
        "keep": keep,
        "deleted_count": len(deleted),
        "deleted_size": sum(item["size"] for item in deleted),
        "deleted": deleted,
        "remaining_sets": len(backup_stamps()),
    }


def storage_summary() -> dict[str, object]:
    db_size = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    media_size = directory_size(MEDIA_DIR)
    backup_size = directory_size(BACKUP_DIR)
    return {
        "database": {"path": str(DB_PATH), "size": db_size},
        "media": {"path": str(MEDIA_DIR), "size": media_size},
        "backups": {
            "path": str(BACKUP_DIR),
            "size": backup_size,
            "file_count": len(list_backup_files()),
            "set_count": len(backup_stamps()),
        },
        "total": db_size + media_size + backup_size,
    }


if __name__ == "__main__":
    print(json.dumps(create_backup(), ensure_ascii=False, indent=2))
