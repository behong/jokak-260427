from __future__ import annotations

import random
import sqlite3
from contextlib import closing
from pathlib import Path

from db import BASE_DIR, DB_PATH, init_db


BGM_DIR = BASE_DIR / "assets" / "bgm"
SUPPORTED_BGM_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg"}
RECENT_BGM_EXCLUDE_COUNT = 10


def discover_bgm_assets() -> list[dict[str, object]]:
    init_db(DB_PATH)
    BGM_DIR.mkdir(parents=True, exist_ok=True)
    found_paths = [
        path
        for path in BGM_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_BGM_EXTENSIONS
    ]
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        for path in found_paths:
            relative_path = path.resolve().relative_to(BASE_DIR).as_posix()
            conn.execute(
                """
                INSERT OR IGNORE INTO bgm_assets (local_path, title, enabled)
                VALUES (?, ?, 1)
                """,
                (relative_path, path.stem),
            )
        conn.commit()
        rows = conn.execute(
            """
            SELECT id, local_path, title, enabled
            FROM bgm_assets
            WHERE enabled = 1
            ORDER BY id
            """
        ).fetchall()
    return [dict(row) for row in rows if (BASE_DIR / str(row["local_path"])).exists()]


def random_bgm_asset() -> dict[str, object] | None:
    assets = discover_bgm_assets()
    if not assets:
        return None

    with closing(sqlite3.connect(DB_PATH)) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT bgm_asset_id
            FROM video_jobs
            WHERE bgm_asset_id IS NOT NULL
            ORDER BY id DESC
            LIMIT ?
            """,
            (RECENT_BGM_EXCLUDE_COUNT,),
        ).fetchall()
    recent_ids = {int(row[0]) for row in rows if row[0] is not None}
    candidates = [asset for asset in assets if int(asset["id"]) not in recent_ids]
    return dict(random.choice(candidates or assets))
