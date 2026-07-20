from __future__ import annotations

import argparse
import json
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from db import BASE_DIR, DB_PATH, connect, init_db
from render_video import OUTPUT_DIR


@dataclass
class CleanupCandidate:
    path: Path
    log_id: int | None
    size: int
    modified_at: datetime
    mark_deleted: bool = False


def used_long_video_source_filenames() -> set[str]:
    init_db(DB_PATH)
    used: set[str] = set()
    with closing(connect(DB_PATH)) as conn:
        rows = conn.execute(
            """
            SELECT source_filenames
            FROM long_video_jobs
            WHERE source_filenames IS NOT NULL
              AND COALESCE(status, '') != 'deleted'
            """
        ).fetchall()
    for row in rows:
        try:
            filenames = json.loads(str(row["source_filenames"] or "[]"))
        except json.JSONDecodeError:
            continue
        if isinstance(filenames, list):
            used.update(str(filename) for filename in filenames if filename)
    return used


def successfully_uploaded_youtube_filenames() -> set[str]:
    init_db(DB_PATH)
    with closing(connect(DB_PATH)) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT filename
            FROM youtube_uploads
            WHERE status = 'uploaded'
              AND COALESCE(filename, '') != ''
            """
        ).fetchall()
    return {str(row["filename"]) for row in rows}


def log_id_from_filename(filename: str) -> int | None:
    if not filename.startswith("wisdom-library-") or not filename.endswith(".mp4"):
        return None
    parts = filename.removesuffix(".mp4").split("-")
    if len(parts) < 4:
        return None
    try:
        return int(parts[2])
    except ValueError:
        return None


def cleanup_candidates(retention_days: int) -> list[CleanupCandidate]:
    cutoff = datetime.now() - timedelta(days=retention_days)
    uploaded = successfully_uploaded_youtube_filenames()
    used_in_long = used_long_video_source_filenames()
    candidates: list[CleanupCandidate] = []

    for path in OUTPUT_DIR.glob("*.mp4"):
        if path.name.startswith("long-wisdom-library-"):
            continue
        if path.name not in uploaded:
            continue
        if path.name in used_in_long:
            continue
        stat = path.stat()
        modified_at = datetime.fromtimestamp(stat.st_mtime)
        if modified_at >= cutoff:
            continue
        candidates.append(
            CleanupCandidate(
                path=path,
                log_id=log_id_from_filename(path.name),
                size=stat.st_size,
                modified_at=modified_at,
                mark_deleted=True,
            )
        )

    candidates.sort(key=lambda item: item.modified_at)
    return candidates


def cleanup_output_candidates(retention_days: int) -> list[CleanupCandidate]:
    cutoff = datetime.now() - timedelta(days=retention_days)
    uploaded_candidates = {item.path.resolve(): item for item in cleanup_candidates(retention_days)}
    candidates = dict(uploaded_candidates)

    for path in OUTPUT_DIR.rglob("*"):
        if not path.is_file():
            continue
        resolved = path.resolve()
        if OUTPUT_DIR.resolve() not in resolved.parents:
            continue
        stat = path.stat()
        modified_at = datetime.fromtimestamp(stat.st_mtime)
        if modified_at >= cutoff:
            continue
        candidates.setdefault(
            resolved,
            CleanupCandidate(
                path=path,
                log_id=log_id_from_filename(path.name),
                size=stat.st_size,
                modified_at=modified_at,
                mark_deleted=False,
            ),
        )

    result = list(candidates.values())
    result.sort(key=lambda item: item.modified_at)
    return result


def mark_video_deleted(path: Path) -> None:
    relative_output = path.resolve().relative_to(BASE_DIR).as_posix()
    with closing(connect(DB_PATH)) as conn:
        conn.execute(
            """
            UPDATE video_jobs
            SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE output_path = ?
            """,
            ("deleted", "file deleted by cleanup_videos.py", relative_output),
        )
        conn.execute(
            """
            UPDATE healing_longform_jobs
            SET status = ?, stage = ?, error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE output_path = ?
            """,
            ("deleted", "보관 기간 만료", "file deleted by cleanup_videos.py", relative_output),
        )
        conn.execute(
            """
            UPDATE long_video_jobs
            SET status = ?, stage = ?, error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE output_path = ?
            """,
            ("deleted", "보관 기간 만료", "file deleted by cleanup_videos.py", relative_output),
        )
        conn.commit()


def reconcile_missing_output_jobs() -> dict[str, int]:
    init_db(DB_PATH)
    targets = (
        ("video_jobs", True),
        ("long_video_jobs", True),
        ("healing_longform_jobs", True),
    )
    reconciled: dict[str, int] = {}
    with closing(connect(DB_PATH)) as conn:
        for table, has_stage in targets:
            rows = conn.execute(
                f"""
                SELECT id, output_path
                FROM {table}
                WHERE status = 'ready'
                  AND COALESCE(output_path, '') != ''
                """
            ).fetchall()
            missing_ids = [
                int(row["id"])
                for row in rows
                if not (BASE_DIR / str(row["output_path"])).exists()
            ]
            reconciled[table] = len(missing_ids)
            if not missing_ids:
                continue
            placeholders = ", ".join("?" for _ in missing_ids)
            stage_assignment = ", stage = '보관 파일 없음'" if has_stage else ""
            conn.execute(
                f"""
                UPDATE {table}
                SET status = 'deleted'{stage_assignment},
                    error = 'output file is missing from storage',
                    updated_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})
                """,
                missing_ids,
            )
        conn.commit()
    return reconciled


def remove_empty_output_dirs() -> int:
    removed = 0
    for path in sorted(
        (item for item in OUTPUT_DIR.rglob("*") if item.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        try:
            path.rmdir()
            removed += 1
        except OSError:
            pass
    return removed


def cleanup_output_files(retention_days: int, apply: bool = False) -> dict[str, object]:
    candidates = cleanup_output_candidates(retention_days)
    deleted: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []

    for candidate in candidates:
        try:
            relative_path = candidate.path.resolve().relative_to(BASE_DIR).as_posix()
        except ValueError:
            errors.append(
                {
                    "filename": candidate.path.name,
                    "log_id": candidate.log_id,
                    "size": candidate.size,
                    "modified_at": candidate.modified_at.isoformat(timespec="seconds"),
                    "error": "path outside project root",
                }
            )
            continue
        item = {
            "filename": candidate.path.name,
            "path": relative_path,
            "log_id": candidate.log_id,
            "size": candidate.size,
            "modified_at": candidate.modified_at.isoformat(timespec="seconds"),
        }
        if apply:
            try:
                candidate.path.unlink()
                mark_video_deleted(candidate.path)
                deleted.append(item)
            except OSError as exc:
                errors.append({**item, "error": str(exc)})
        else:
            deleted.append(item)

    total_size = sum(item["size"] for item in deleted)
    removed_dirs = remove_empty_output_dirs() if apply else 0
    return {
        "applied": apply,
        "retention_days": retention_days,
        "candidate_count": len(candidates),
        "deleted_count": len(deleted) if apply else 0,
        "candidate_size": total_size,
        "candidate_size_gb": round(total_size / 1024**3, 3),
        "empty_dirs_removed": removed_dirs,
        "errors": errors,
        "items": deleted[:200],
    }


def cleanup_uploaded_videos(retention_days: int, apply: bool = False) -> dict[str, object]:
    return cleanup_output_files(retention_days, apply=apply)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Clean local mp4 files that were already uploaded to YouTube."
    )
    parser.add_argument("--retention-days", type=int, default=7)
    parser.add_argument("--apply", action="store_true", help="Actually delete files. Omit for dry-run.")
    args = parser.parse_args()
    if args.retention_days < 1:
        raise SystemExit("--retention-days must be at least 1")

    result = cleanup_output_files(args.retention_days, apply=args.apply)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
