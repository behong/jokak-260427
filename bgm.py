from __future__ import annotations

import random
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path

from db import BASE_DIR, DB_PATH, init_db


BGM_DIR = BASE_DIR / "assets" / "bgm"
SUPPORTED_BGM_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg"}
RECENT_BGM_EXCLUDE_COUNT = 10
BUILTIN_BGM_FILENAME = "generated-soft-pad.wav"
BUILTIN_BGM_DURATION_SECONDS = 180


def _scan_bgm_files() -> list[Path]:
    return [
        path
        for path in BGM_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_BGM_EXTENSIONS
    ]


def ensure_builtin_bgm_asset() -> Path | None:
    output_path = BGM_DIR / BUILTIN_BGM_FILENAME
    if output_path.exists() and output_path.stat().st_size > 0:
        return output_path

    tones = [220.00, 277.18, 329.63, 440.00, 554.37]
    command = ["ffmpeg", "-y"]
    for frequency in tones:
        command.extend(
            [
                "-f",
                "lavfi",
                "-i",
                (
                    f"sine=frequency={frequency}:"
                    f"duration={BUILTIN_BGM_DURATION_SECONDS}:sample_rate=44100"
                ),
            ]
        )

    volume_filters = [f"[{index}:a]volume=0.018[a{index}]" for index in range(len(tones))]
    mix_inputs = "".join(f"[a{index}]" for index in range(len(tones)))
    fade_out_start = max(0, BUILTIN_BGM_DURATION_SECONDS - 3)
    filter_complex = (
        ";".join(volume_filters)
        + ";"
        + f"{mix_inputs}amix=inputs={len(tones)}:duration=longest,"
        + "lowpass=f=1200,"
        + "afade=t=in:st=0:d=2,"
        + f"afade=t=out:st={fade_out_start}:d=3[a]"
    )

    try:
        subprocess.run(
            [
                *command,
                "-filter_complex",
                filter_complex,
                "-map",
                "[a]",
                "-ac",
                "2",
                "-c:a",
                "pcm_s16le",
                str(output_path),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return output_path if output_path.exists() and output_path.stat().st_size > 0 else None


def discover_bgm_assets() -> list[dict[str, object]]:
    init_db(DB_PATH)
    BGM_DIR.mkdir(parents=True, exist_ok=True)
    found_paths = _scan_bgm_files()
    if not found_paths and ensure_builtin_bgm_asset():
        found_paths = _scan_bgm_files()
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
