from __future__ import annotations

import random
import json
import sqlite3
import subprocess
from contextlib import closing
from pathlib import Path
from urllib.parse import urlparse

from db import BASE_DIR, DB_PATH, get_app_setting, init_db


BGM_DIR = BASE_DIR / "assets" / "bgm"
APPROVED_BGM_DIR = BGM_DIR / "approved"
SUPPORTED_BGM_EXTENSIONS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg"}
APPROVED_LICENSE_TYPES = {"youtube_standard", "youtube_cc", "pixabay", "owned"}
APPROVED_MOODS = {"calm", "warm", "meditative", "gentle"}
RECENT_BGM_EXCLUDE_COUNT = 20
BUILTIN_BGM_FILENAME = "generated-soft-pad.wav"
BUILTIN_BGM_DURATION_SECONDS = 180


def _allow_unverified_bgm() -> bool:
    return get_app_setting("VIDEO_BGM_ALLOW_UNVERIFIED", "0").strip().lower() in {
        "1", "true", "yes", "on"
    }


def _is_verified_auto_bgm(asset: dict[str, object]) -> bool:
    if bool(asset.get("approved")):
        return True
    local_path = Path(str(asset.get("local_path") or ""))
    parts = {part.lower() for part in local_path.parts}
    return local_path.name == BUILTIN_BGM_FILENAME or (
        "generated" in parts and "bgm" in parts
    )


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
                INSERT OR IGNORE INTO bgm_assets
                    (local_path, title, enabled, source_type, approved, usage_scope)
                VALUES (?, ?, 1, ?, ?, ?)
                """,
                (
                    relative_path,
                    path.stem,
                    "procedural" if path.parent.name == "generated" or path.name == BUILTIN_BGM_FILENAME else "unknown",
                    1 if path.parent.name == "generated" or path.name == BUILTIN_BGM_FILENAME else 0,
                    "all",
                ),
            )
        conn.commit()
        rows = conn.execute(
            """
            SELECT id, local_path, title, enabled, source_type, source_url,
                   license_type, attribution_text, approved, usage_scope, mood, duration
            FROM bgm_assets
            WHERE enabled = 1
            ORDER BY id
            """
        ).fetchall()
    return [dict(row) for row in rows if (BASE_DIR / str(row["local_path"])).exists()]


def _probe_audio_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "a:0",
            "-show_entries", "stream=codec_type:format=duration", "-of", "json", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    payload = json.loads(result.stdout)
    if not payload.get("streams"):
        raise ValueError("오디오 스트림이 없는 파일입니다.")
    duration = float((payload.get("format") or {}).get("duration") or 0)
    if duration <= 0:
        raise ValueError("재생 길이를 확인할 수 없는 음원입니다.")
    return duration


def register_approved_bgm(
    path: Path,
    *,
    title: str,
    license_type: str,
    source_url: str = "",
    attribution_text: str = "",
    mood: str = "calm",
) -> dict[str, object]:
    init_db(DB_PATH)
    resolved = path.resolve()
    approved_root = APPROVED_BGM_DIR.resolve()
    try:
        resolved.relative_to(approved_root)
    except ValueError as exc:
        raise ValueError("승인 BGM 폴더 밖의 파일은 등록할 수 없습니다.") from exc
    if resolved.suffix.lower() not in SUPPORTED_BGM_EXTENSIONS:
        raise ValueError("지원하지 않는 음원 형식입니다.")
    normalized_license = str(license_type or "").strip()
    if normalized_license not in APPROVED_LICENSE_TYPES:
        raise ValueError("지원하지 않는 라이선스 유형입니다.")
    normalized_source_url = str(source_url or "").strip()
    normalized_attribution = str(attribution_text or "").strip()
    if normalized_source_url and urlparse(normalized_source_url).scheme not in {"http", "https"}:
        raise ValueError("원본 음원 주소는 http 또는 https 주소여야 합니다.")
    if normalized_license in {"youtube_cc", "pixabay"} and not normalized_source_url:
        raise ValueError("이 라이선스는 원본 음원 주소가 필요합니다.")
    if normalized_license == "youtube_cc" and not normalized_attribution:
        raise ValueError("YouTube CC 음원은 저작자 표시 문구가 필요합니다.")
    normalized_mood = str(mood or "calm").strip()
    if normalized_mood not in APPROVED_MOODS:
        normalized_mood = "calm"
    duration = _probe_audio_duration(resolved)
    relative_path = resolved.relative_to(BASE_DIR).as_posix()
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            """
            INSERT INTO bgm_assets
                (local_path, title, enabled, source_type, source_url, license_type,
                 attribution_text, approved, usage_scope, mood, duration)
            VALUES (?, ?, 1, 'licensed', ?, ?, ?, 1, 'longform', ?, ?)
            ON CONFLICT(local_path) DO UPDATE SET
                title = excluded.title,
                enabled = 1,
                source_type = excluded.source_type,
                source_url = excluded.source_url,
                license_type = excluded.license_type,
                attribution_text = excluded.attribution_text,
                approved = 1,
                usage_scope = 'longform',
                mood = excluded.mood,
                duration = excluded.duration
            """,
            (
                relative_path,
                str(title or resolved.stem).strip()[:200],
                normalized_source_url[:1000],
                normalized_license,
                normalized_attribution[:2000],
                normalized_mood,
                duration,
            ),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT id, local_path, title, enabled, source_type, source_url,
                   license_type, attribution_text, approved, usage_scope, mood, duration
            FROM bgm_assets WHERE local_path = ?
            """,
            (relative_path,),
        ).fetchone()
    return dict(row)


def licensed_longform_bgm_assets(*, include_disabled: bool = False) -> list[dict[str, object]]:
    init_db(DB_PATH)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT id, local_path, title, enabled, source_type, source_url,
                   license_type, attribution_text, approved, usage_scope, mood, duration
            FROM bgm_assets
            WHERE approved = 1
              AND source_type = 'licensed'
              AND usage_scope IN ('all', 'longform')
              AND (? = 1 OR enabled = 1)
            ORDER BY id DESC
            """,
            (1 if include_disabled else 0,),
        ).fetchall()
    return [dict(row) for row in rows if (BASE_DIR / str(row["local_path"])).is_file()]


def set_bgm_asset_enabled(asset_id: int, enabled: bool) -> dict[str, object] | None:
    init_db(DB_PATH)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(
            "UPDATE bgm_assets SET enabled = ? WHERE id = ? AND source_type = 'licensed'",
            (1 if enabled else 0, asset_id),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT id, local_path, title, enabled, source_type, source_url,
                   license_type, attribution_text, approved, usage_scope, mood, duration
            FROM bgm_assets WHERE id = ? AND source_type = 'licensed'
            """,
            (asset_id,),
        ).fetchone()
    return dict(row) if row else None


def random_bgm_asset() -> dict[str, object] | None:
    assets = discover_bgm_assets()
    assets = [
        asset for asset in assets
        if str(asset.get("usage_scope") or "all") in {"all", "short"}
    ]
    if not _allow_unverified_bgm():
        assets = [asset for asset in assets if _is_verified_auto_bgm(asset)]
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
