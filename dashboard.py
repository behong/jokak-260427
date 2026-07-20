from __future__ import annotations

import secrets
import os
import random
import re
import json
import subprocess
import threading
from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from functools import wraps
from pathlib import Path
from zoneinfo import ZoneInfo

from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, session, url_for

from backup import cleanup_backups, create_backup, list_backup_files, storage_summary
from auto_upload import daily_upload_count, mark_stale_short_video_jobs_failed, next_peak_publish_at
from backgrounds import (
    BACKGROUND_DIR,
    ACTIVE_BACKGROUND_LIMIT,
    BackgroundAssetError,
    activate_background_collection,
    get_background_asset_by_id,
    list_background_collections,
    list_background_assets,
    list_background_assets_for_collection,
    save_background_asset,
    search_pexels_videos,
    update_background_asset,
)
from bgm import (
    APPROVED_BGM_DIR,
    BGM_DIR,
    SUPPORTED_BGM_EXTENSIONS,
    licensed_longform_bgm_assets,
    random_bgm_asset,
    register_approved_bgm,
    set_bgm_asset_enabled,
)
from cleanup_videos import cleanup_candidates, cleanup_uploaded_videos, reconcile_missing_output_jobs
from healing_longform import (
    create_healing_job,
    healing_job,
    list_healing_jobs,
    mark_interrupted_healing_jobs,
    run_healing_longform_job,
)
from longform_config import config_with_overrides, load_longform_config, save_longform_config
from longform_scheduler import start_longform_scheduler
from longform_script import available_themes, generate_longform_script
from longform_tts import create_longform_voice_preview
from db import (
    BASE_DIR,
    DB_PATH,
    apply_db_settings_to_env,
    connect,
    get_app_setting,
    init_db,
    list_app_settings,
    load_env_file,
    set_app_setting,
)
from render_video import (
    BODY_BOLD_FONT,
    OUTPUT_DIR,
    RENDER_CRF,
    RENDER_PRESET,
    audit_video_before_upload,
    ffmpeg_font_path,
    mix_bgm,
    random_background_asset_id,
    render_video,
)
from telegram_sync import catch_up_recent_messages_sync
from tts import (
    DEFAULT_RATE,
    DEFAULT_VOICE,
    RATE_OPTIONS,
    SHORT_ELEVENLABS_VOICES,
    VOICE_OPTIONS,
    create_elevenlabs_long_narration_audio,
    create_preview_audio,
    create_short_elevenlabs_preview,
    elevenlabs_subscription_usage,
    short_elevenlabs_model,
    short_tts_provider,
)
from video_pipelines import enabled_sources, pipeline_for_source, pipeline_payload
from video_script import generate_video_script, split_source
from youtube_metadata_ai import generate_tags, generate_title
from youtube_upload import (
    YouTubeUploadError,
    add_video_to_playlist,
    find_or_create_playlist,
    get_video_details,
    get_video_statistics,
    post_top_level_comment,
    sanitize_youtube_metadata,
    save_youtube_token_from_response,
    update_video_metadata,
    upload_korean_caption,
    upload_video,
    youtube_authorization_url,
    youtube_config_status,
)


def dashboard_secret_key() -> str:
    value = get_app_setting("DASHBOARD_SECRET_KEY", os.getenv("DASHBOARD_SECRET_KEY", "")).strip()
    if value:
        return value
    value = secrets.token_hex(32)
    set_app_setting("DASHBOARD_SECRET_KEY", value, is_secret=True)
    return value


app = Flask(__name__)
app.secret_key = dashboard_secret_key()
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.jinja_env.auto_reload = True
VIDEO_FILENAME_PATTERN = re.compile(r"wisdom-library-(?P<log_id>\d+)-(?P<stamp>\d{8}-\d{6})(?:-audio)?\.mp4$")
BRAND_NAME = "지혜로운 조각들"
MANUAL_SOURCE = "직접입력"
TELEGRAM_REFRESH_LOCK = threading.Lock()
LONG_VIDEO_LOCK = threading.Lock()
YOUTUBE_POSTPROCESS_LOCK = threading.Lock()
LONG_VIDEO_WIDTH = 1920
LONG_VIDEO_HEIGHT = 1080
LONG_VIDEO_TARGET_SECONDS = 600
LONG_VIDEO_MAX_SOURCE_COUNT = 40
LONG_VIDEO_DEFAULT_HEALING_TEMPO = 0.90
SUBPROCESS_CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
YOUTUBE_STATS_INTERVAL_SECONDS = int(os.getenv("YOUTUBE_STATS_INTERVAL_SECONDS", "3600"))
KST = ZoneInfo("Asia/Seoul")
YOUTUBE_SCHEDULE_WINDOWS = (
    (time(12, 0), time(13, 0)),
    (time(18, 0), time(21, 0)),
)
MONITOR_HEARTBEAT_STALE_SECONDS = 120
SETTINGS_KEYS = {
    "TELEGRAM_API_ID",
    "TELEGRAM_API_HASH",
    "TELEGRAM_CATCH_UP_LIMIT",
    "TELEGRAM_CATCH_UP_INTERVAL_SECONDS",
    "PEXELS_API_KEY",
    "ELEVENLABS_API_KEY",
    "ELEVENLABS_VOICE_ID",
    "SHORT_TTS_PROVIDER",
    "SHORT_ELEVENLABS_MODEL_ID",
    "DASHBOARD_PASSWORD",
    "DASHBOARD_SECRET_KEY",
    "YOUTUBE_CLIENT_SECRET_JSON",
    "YOUTUBE_TOKEN_JSON",
    "TELEGRAM_SESSION_FILE_B64",
    "YOUTUBE_STATS_INTERVAL_SECONDS",
    "AUTO_UPLOAD_ENABLED",
    "AUTO_UPLOAD_SOURCE",
    "AUTO_UPLOAD_BACKFILL_SOURCE",
    "AUTO_UPLOAD_POLL_INTERVAL_SECONDS",
    "AUTO_UPLOAD_MAX_PER_RUN",
    "AUTO_UPLOAD_DAILY_LIMIT",
    "AUTO_UPLOAD_PRIVACY_STATUS",
    "AUTO_UPLOAD_SCHEDULE_WINDOWS",
    "AUTO_UPLOAD_SCHEDULE_TIMES",
    "LONGFORM_UPLOAD_SCHEDULE_TIMES",
    "LONGFORM_YOUTUBE_PLAYLIST_NAME",
    "AUTO_UPLOAD_MIN_LEAD_MINUTES",
    "AUTO_UPLOAD_MIN_CONTENT_LENGTH",
    "AUTO_UPLOAD_INCLUDE_EXISTING",
    "AUTO_UPLOAD_RETRY_FAILED",
    "LONG_VIDEO_HEALING_TEMPO",
    "LONG_VIDEO_BACKGROUND_COLLECTION",
    "SARAMRO_QUOTES_ENABLED",
    "SARAMRO_QUOTES_IMPORT_LIMIT",
    "SARAMRO_QUOTES_MAX_PAGES",
    "VIDEO_BGM_ENABLED",
    "VIDEO_BGM_ALLOW_UNVERIFIED",
    "VIDEO_BGM_TTS_VOLUME",
    "VIDEO_BGM_ONLY_VOLUME",
    "VIDEO_CLEANUP_ENABLED",
    "VIDEO_CLEANUP_RETENTION_DAYS",
    "NAVER_CLIP_AUTO_UPLOAD_ENABLED",
    "NAVER_CLIP_DAILY_LIMIT",
    "NAVER_CLIP_SCHEDULE_WINDOW",
    "NAVER_CLIP_API_URL",
    "NAVER_CLIP_CHANNEL_URL",
    "NAVER_CLIP_HOST_VIDEO_ROOT",
    "NAVER_CLIP_CATEGORY1",
    "NAVER_CLIP_CATEGORY2",
    "NAVER_CLIP_KEEP_OPEN_SECONDS",
    "NAVER_CLIP_TIMEOUT_SECONDS",
}
SECRET_KEYS = {
    "TELEGRAM_API_HASH",
    "PEXELS_API_KEY",
    "ELEVENLABS_API_KEY",
    "DASHBOARD_PASSWORD",
    "DASHBOARD_SECRET_KEY",
    "YOUTUBE_CLIENT_SECRET_JSON",
    "YOUTUBE_TOKEN_JSON",
    "TELEGRAM_SESSION_FILE_B64",
}
BGM_SETTING_DEFAULTS = {
    "AUTO_UPLOAD_ENABLED": "1",
    "AUTO_UPLOAD_SOURCE": "글반장",
    "AUTO_UPLOAD_BACKFILL_SOURCE": "글반장모음",
    "AUTO_UPLOAD_POLL_INTERVAL_SECONDS": "60",
    "AUTO_UPLOAD_MAX_PER_RUN": "1",
    "AUTO_UPLOAD_DAILY_LIMIT": "4",
    "AUTO_UPLOAD_PRIVACY_STATUS": "private",
    "AUTO_UPLOAD_SCHEDULE_WINDOWS": "07:00-08:00,19:00-20:00",
    "AUTO_UPLOAD_SCHEDULE_TIMES": "07:00,07:30,19:00,19:30",
    "LONGFORM_UPLOAD_SCHEDULE_TIMES": "20:30,21:00",
    "LONGFORM_YOUTUBE_PLAYLIST_NAME": "잠들기 전 듣는 힐링 낭독",
    "AUTO_UPLOAD_MIN_LEAD_MINUTES": "30",
    "AUTO_UPLOAD_MIN_CONTENT_LENGTH": "10",
    "AUTO_UPLOAD_INCLUDE_EXISTING": "0",
    "AUTO_UPLOAD_RETRY_FAILED": "0",
    "LONG_VIDEO_HEALING_TEMPO": "0.90",
    "LONG_VIDEO_BACKGROUND_COLLECTION": "healing-meditation",
    "SARAMRO_QUOTES_ENABLED": "0",
    "SARAMRO_QUOTES_IMPORT_LIMIT": "10",
    "SARAMRO_QUOTES_MAX_PAGES": "5",
    "VIDEO_BGM_ENABLED": "1",
    "VIDEO_BGM_ALLOW_UNVERIFIED": "0",
    "VIDEO_BGM_TTS_VOLUME": "0.10",
    "VIDEO_BGM_ONLY_VOLUME": "0.14",
    "SHORT_TTS_PROVIDER": "elevenlabs",
    "SHORT_ELEVENLABS_MODEL_ID": "eleven_flash_v2_5",
    "VIDEO_CLEANUP_ENABLED": "1",
    "VIDEO_CLEANUP_RETENTION_DAYS": "7",
    "NAVER_CLIP_AUTO_UPLOAD_ENABLED": "0",
    "NAVER_CLIP_DAILY_LIMIT": "4",
    "NAVER_CLIP_SCHEDULE_WINDOW": "06:00-09:00",
    "NAVER_CLIP_API_URL": "http://host.docker.internal:8383/upload_clip",
    "NAVER_CLIP_CHANNEL_URL": "https://creator.tv.naver.com/channel/wisearchive/content/video",
    "NAVER_CLIP_HOST_VIDEO_ROOT": "",
    "NAVER_CLIP_CATEGORY1": "인문, 교양",
    "NAVER_CLIP_CATEGORY2": "인문, 교양",
    "NAVER_CLIP_KEEP_OPEN_SECONDS": "8",
    "NAVER_CLIP_TIMEOUT_SECONDS": "900",
}


def set_setting_value(name: str, value: str) -> None:
    if name not in SETTINGS_KEYS:
        raise ValueError(f"Unsupported setting: {name}")
    set_app_setting(name, value, is_secret=name in SECRET_KEYS)


def setting_bool(name: str, default: str = "1") -> bool:
    value = get_app_setting(name, os.getenv(name, default))
    return value.strip().lower() not in {"0", "false", "no", "off"}


def setting_float(name: str, default: float, minimum: float, maximum: float) -> float:
    raw = get_app_setting(name, os.getenv(name, str(default)))
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, value))


def setting_int(name: str, default: int, minimum: int = 0, maximum: int | None = None) -> int:
    raw = get_app_setting(name, os.getenv(name, str(default)))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def app_state_value(key: str) -> str | None:
    init_db(DB_PATH)
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row and row["value"] is not None else None


def masked_env_value(name: str) -> str:
    value = get_app_setting(name, os.getenv(name, BGM_SETTING_DEFAULTS.get(name, "")))
    if not value:
        return ""
    if name not in SECRET_KEYS:
        return value
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:3]}{'*' * (len(value) - 6)}{value[-3:]}"


def setting_payload() -> dict[str, object]:
    apply_db_settings_to_env()
    youtube = youtube_config_status()
    stored = list_app_settings()
    return {
        "settings": {name: masked_env_value(name) for name in sorted(SETTINGS_KEYS)},
        "db_settings_count": len(stored),
        "secret_keys": sorted(SECRET_KEYS),
        "status": {
            "env_exists": (BASE_DIR / ".env").exists(),
            "telegram_session_exists": (BASE_DIR / "telegram_monitor.session").exists(),
            "youtube_client_exists": youtube["client_secrets_exists"],
            "youtube_token_exists": youtube["token_exists"],
            "youtube_token_valid": youtube["token_has_required_scopes"],
            "pexels_key_exists": bool(os.getenv("PEXELS_API_KEY", "").strip()),
            "db_exists": DB_PATH.exists(),
            "db_path": str(DB_PATH),
        },
    }


def parse_utc_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def age_payload(value: str | None, now: datetime | None = None) -> dict[str, object]:
    parsed = parse_utc_timestamp(value)
    if parsed is None:
        return {"at": value, "age_seconds": None}
    reference = now or datetime.now(timezone.utc)
    return {
        "at": parsed.isoformat(),
        "age_seconds": max(0, int((reference - parsed).total_seconds())),
    }


def file_status(path: Path, now: datetime) -> dict[str, object]:
    if not path.exists():
        return {"path": str(path), "exists": False, "modified_at": None, "age_seconds": None}
    modified = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
    return {
        "path": str(path),
        "exists": True,
        "modified_at": modified.isoformat(),
        "age_seconds": max(0, int((now - modified).total_seconds())),
    }


def latest_table_row(conn, table: str, order_column: str = "updated_at") -> dict[str, object] | None:
    row = conn.execute(f"SELECT * FROM {table} ORDER BY {order_column} DESC, id DESC LIMIT 1").fetchone()
    return dict(row) if row else None


def operational_status_payload() -> dict[str, object]:
    init_db(DB_PATH)
    apply_db_settings_to_env()
    now = datetime.now(timezone.utc)
    with closing(connect(DB_PATH)) as conn:
        state_rows = {
            row["key"]: dict(row)
            for row in conn.execute(
                "SELECT key, value, updated_at FROM app_state WHERE key IN (?, ?, ?)",
                ("monitor_heartbeat_at", "monitor_started_at", "monitor_status"),
            )
        }
        latest_log = conn.execute(
            """
            SELECT id, source, msg_id, created_at, saved_at
            FROM telegram_logs
            ORDER BY saved_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        latest_video = latest_table_row(conn, "video_jobs")
        latest_auto = latest_table_row(conn, "auto_upload_jobs")
        latest_youtube = latest_table_row(conn, "youtube_uploads", "created_at")
        active_video_jobs = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM video_jobs
            WHERE status IN ('pending', 'rendering', 'running')
              AND updated_at >= datetime('now', '-1 hour')
            """
        ).fetchone()["count"]
        active_auto_jobs = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM auto_upload_jobs
            WHERE status IN ('pending', 'rendering', 'uploading')
              AND updated_at >= datetime('now', '-1 hour')
            """
        ).fetchone()["count"]
        active_youtube_uploads = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM youtube_uploads
            WHERE status = 'uploading'
              AND updated_at >= datetime('now', '-1 hour')
            """
        ).fetchone()["count"]

    heartbeat_row = state_rows.get("monitor_heartbeat_at")
    heartbeat = age_payload(heartbeat_row["value"] if heartbeat_row else None, now)
    heartbeat_age = heartbeat["age_seconds"]
    monitor_ok = isinstance(heartbeat_age, int) and heartbeat_age <= MONITOR_HEARTBEAT_STALE_SECONDS

    latest_saved = age_payload(dict(latest_log)["saved_at"] if latest_log else None, now)
    latest_video_age = age_payload(latest_video.get("updated_at") if latest_video else None, now)
    latest_auto_age = age_payload(latest_auto.get("updated_at") if latest_auto else None, now)
    latest_youtube_age = age_payload(latest_youtube.get("updated_at") if latest_youtube else None, now)
    active_jobs = active_video_jobs + active_auto_jobs + active_youtube_uploads

    return {
        "checked_at": now.isoformat(),
        "monitor": {
            "ok": monitor_ok,
            "status": state_rows.get("monitor_status", {}).get("value") or "unknown",
            "heartbeat": heartbeat,
            "started": age_payload(state_rows.get("monitor_started_at", {}).get("value"), now),
            "stale_after_seconds": MONITOR_HEARTBEAT_STALE_SECONDS,
        },
        "collection": {
            "latest_log": dict(latest_log) if latest_log else None,
            "latest_saved": latest_saved,
        },
        "generation": {
            "latest_video": latest_video,
            "latest_updated": latest_video_age,
            "active_jobs": active_video_jobs,
        },
        "auto_upload": {
            "enabled": setting_bool("AUTO_UPLOAD_ENABLED", "1"),
            "latest_job": latest_auto,
            "latest_updated": latest_auto_age,
            "active_jobs": active_auto_jobs,
            "daily_limit": get_app_setting("AUTO_UPLOAD_DAILY_LIMIT", os.getenv("AUTO_UPLOAD_DAILY_LIMIT", "4")),
            "daily_completed": daily_upload_count(now.astimezone(KST)),
        },
        "youtube": {
            "latest_upload": latest_youtube,
            "latest_updated": latest_youtube_age,
            "active_uploads": active_youtube_uploads,
            "config": youtube_config_status(),
        },
        "logs": {
            "monitor_runner": file_status(BASE_DIR / "logs" / "monitor-runner.log", now),
            "monitor": file_status(BASE_DIR / "monitor.log", now),
            "dashboard_runner": file_status(BASE_DIR / "logs" / "dashboard-runner.log", now),
        },
        "overall": {
            "ok": monitor_ok and active_jobs >= 0,
            "active_jobs": active_jobs,
        },
    }


def dashboard_password() -> str | None:
    return get_app_setting("DASHBOARD_PASSWORD", os.getenv("DASHBOARD_PASSWORD", ""))


def auth_enabled() -> bool:
    return bool(dashboard_password())


def is_authenticated() -> bool:
    return not auth_enabled() or bool(session.get("authenticated"))


def require_auth(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if is_authenticated():
            return view(*args, **kwargs)
        if request.path.startswith("/api/"):
            return jsonify({"error": "authentication_required"}), 401
        return redirect(url_for("login", next=request.full_path))

    return wrapper


@app.after_request
def add_no_cache_headers(response):
    if response.content_type.startswith("text/html"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


def clamp_limit(value: str | None, default: int = 100) -> int:
    try:
        limit = int(value or default)
    except ValueError:
        return default
    return max(10, min(limit, 500))


def build_log_query() -> tuple[str, list[object]]:
    source = request.args.get("source", "").strip()
    keyword = request.args.get("q", "").strip()
    date_from = request.args.get("date_from", "").strip()
    date_to = request.args.get("date_to", "").strip()
    limit = clamp_limit(request.args.get("limit"))
    row_limit = limit * 8

    where = []
    params: list[object] = []

    if source:
        where.append("source = ?")
        params.append(source)
    if keyword:
        where.append("content LIKE ?")
        params.append(f"%{keyword}%")
    if date_from:
        where.append("created_at >= ?")
        params.append(date_from)
    if date_to:
        where.append("created_at < ?")
        params.append(date_to)
    sql = """
        SELECT id, source, msg_id, content, created_at, saved_at,
               group_key,
               EXISTS (
                   SELECT 1
                   FROM youtube_uploads yu
                   WHERE yu.log_id = telegram_logs.id
                     AND yu.status = 'uploaded'
                     AND yu.youtube_url IS NOT NULL
               ) AS youtube_uploaded,
               (
                   SELECT yu.youtube_url
                   FROM youtube_uploads yu
                   WHERE yu.log_id = telegram_logs.id
                     AND yu.status = 'uploaded'
                     AND yu.youtube_url IS NOT NULL
                   ORDER BY yu.id DESC
                   LIMIT 1
               ) AS youtube_url,
               (
                   SELECT COUNT(DISTINCT vj.output_path)
                   FROM video_jobs vj
                   WHERE vj.log_id = telegram_logs.id
                     AND vj.status = 'ready'
                     AND vj.output_path IS NOT NULL
               ) AS video_count
        FROM telegram_logs
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY saved_at DESC, id DESC LIMIT ?"
    params.append(row_limit)

    return sql, params


def group_log_rows(rows: list[dict[str, object]], limit: int) -> list[dict[str, object]]:
    grouped: dict[str, dict[str, object]] = {}
    ordered_keys: list[str] = []

    for row in rows:
        key = (
            str(row.get("group_key"))
            if row.get("group_key")
            else f"{row['source']}|{row['created_at']}"
        )
        item = grouped.get(key)

        if item is None:
            item = {
                "id": row["id"],
                "source": row["source"],
                "msg_id": row["msg_id"],
                "msg_ids": [],
                "content": "",
                "created_at": row["created_at"],
                "saved_at": row["saved_at"],
                "delivery_statuses": ["수집"],
                "video_count": 0,
                "youtube_url": row.get("youtube_url"),
            }
            if row.get("youtube_uploaded"):
                item["delivery_statuses"].append("유")
            grouped[key] = item
            ordered_keys.append(key)

        item["msg_ids"].append(row["msg_id"])

        content = str(row.get("content") or "").strip()
        current_content = str(item.get("content") or "")
        if content and content not in current_content:
            if not current_content:
                item["id"] = row["id"]
                item["msg_id"] = row["msg_id"]
            item["content"] = (
                f"{current_content}\n\n{content}" if current_content else content
            )

        if str(row["created_at"]) > str(item["created_at"]):
            item["created_at"] = row["created_at"]
        if str(row["saved_at"]) > str(item["saved_at"]):
            item["saved_at"] = row["saved_at"]
        row_video_count = int(row.get("video_count") or 0)
        if row_video_count:
            item["video_count"] = int(item.get("video_count") or 0) + row_video_count
            item["delivery_statuses"] = [
                status
                for status in item["delivery_statuses"]
                if not str(status).startswith("영")
            ]
            item["delivery_statuses"].insert(1, f"영{item['video_count']}")
        if row.get("youtube_uploaded") and "유" not in item["delivery_statuses"]:
            item["delivery_statuses"].append("유")
            item["youtube_url"] = row.get("youtube_url")
    items = [grouped[key] for key in ordered_keys]
    items.sort(
        key=lambda item: (str(item.get("saved_at") or ""), int(item.get("id") or 0)),
        reverse=True,
    )
    total = len(items)
    for index, item in enumerate(items, start=1):
        item["sequence"] = total - index + 1
    return items[:limit]


@app.get("/health")
def health():
    try:
        init_db(DB_PATH)
        with closing(connect(DB_PATH)) as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception as exc:
        return jsonify({"status": "error", "database": "unavailable", "error": str(exc)}), 500
    return jsonify({"status": "ok", "database": "ok", "db_path": str(DB_PATH)})


@app.get("/")
@require_auth
def index():
    init_db(DB_PATH)
    with closing(connect(DB_PATH)) as conn:
        sources = [
            row["source"]
            for row in conn.execute(
                "SELECT DISTINCT source FROM telegram_logs ORDER BY source"
            )
        ]
    for source in ("글반장", "글반장모음", MANUAL_SOURCE):
        if source not in sources:
            sources.append(source)
    return render_template(
        "dashboard.html",
        db_path=DB_PATH,
        sources=sources,
        voice_options=VOICE_OPTIONS,
        short_tts_provider=short_tts_provider(),
        short_elevenlabs_model=short_elevenlabs_model(),
        short_elevenlabs_voices=SHORT_ELEVENLABS_VOICES,
        auth_enabled=auth_enabled(),
    )


@app.get("/settings")
@require_auth
def settings_page():
    init_db(DB_PATH)
    return render_template(
        "settings.html",
        db_path=DB_PATH,
        auth_enabled=auth_enabled(),
    )


@app.get("/manual")
@require_auth
def manual_page():
    init_db(DB_PATH)
    return render_template(
        "manual.html",
        db_path=DB_PATH,
        auth_enabled=auth_enabled(),
    )


@app.get("/generated-videos")
@require_auth
def generated_videos_page():
    init_db(DB_PATH)
    return render_template(
        "generated_videos.html",
        db_path=DB_PATH,
        auth_enabled=auth_enabled(),
    )


@app.get("/long-videos")
@require_auth
def long_videos_page():
    init_db(DB_PATH)
    return render_template(
        "long_videos.html",
        db_path=DB_PATH,
        auth_enabled=auth_enabled(),
    )


@app.get("/longform-backgrounds")
@require_auth
def longform_backgrounds_page():
    init_db(DB_PATH)
    return render_template(
        "longform_backgrounds.html",
        db_path=DB_PATH,
        auth_enabled=auth_enabled(),
    )


@app.get("/background-library")
@require_auth
def background_library_page():
    init_db(DB_PATH)
    return render_template(
        "background_library.html",
        db_path=DB_PATH,
        auth_enabled=auth_enabled(),
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    if not auth_enabled():
        return redirect(url_for("index"))

    error = ""
    if request.method == "POST":
        password = request.form.get("password", "")
        if secrets.compare_digest(password, dashboard_password() or ""):
            session["authenticated"] = True
            return redirect(request.args.get("next") or url_for("index"))
        error = "비밀번호가 맞지 않습니다"

    return render_template("login.html", error=error)


@app.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.get("/api/logs")
@require_auth
def api_logs():
    init_db(DB_PATH)
    sql, params = build_log_query()
    limit = clamp_limit(request.args.get("limit"))
    with closing(connect(DB_PATH)) as conn:
        rows = [dict(row) for row in conn.execute(sql, params)]
    return jsonify({"logs": group_log_rows(rows, limit)})


@app.post("/api/logs/refresh")
@require_auth
def api_refresh_logs():
    if not TELEGRAM_REFRESH_LOCK.acquire(blocking=False):
        return jsonify({"error": "refresh_already_running"}), 409
    try:
        limit = clamp_limit(request.args.get("limit"), default=50)
        result = catch_up_recent_messages_sync(limit=limit, copy_session=True)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    finally:
        TELEGRAM_REFRESH_LOCK.release()
    status = 207 if result.get("errors") else 200
    return jsonify({"refresh": result}), status


@app.get("/api/stats")
@require_auth
def api_stats():
    init_db(DB_PATH)
    with closing(connect(DB_PATH)) as conn:
        total = conn.execute("SELECT COUNT(*) AS count FROM telegram_logs").fetchone()
        latest = conn.execute(
            "SELECT created_at FROM telegram_logs ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        by_source = [
            dict(row)
            for row in conn.execute(
                """
                SELECT source, COUNT(*) AS count
                FROM telegram_logs
                GROUP BY source
                ORDER BY count DESC, source
                """
            )
        ]

    return jsonify(
        {
            "total": total["count"] if total else 0,
            "latest_created_at": latest["created_at"] if latest else None,
            "by_source": by_source,
        }
    )


@app.get("/api/operation-status")
@require_auth
def api_operation_status():
    return jsonify(operational_status_payload())


@app.post("/api/backup")
@require_auth
def api_backup():
    result = create_backup()
    return jsonify(result)


@app.get("/api/backups")
@require_auth
def api_backups():
    return jsonify({"backups": list_backup_files()[:20]})


@app.get("/api/storage")
@require_auth
def api_storage():
    return jsonify(storage_summary())


def video_cleanup_payload(retention_days: int | None = None) -> dict[str, object]:
    retention_days = (
        setting_int("VIDEO_CLEANUP_RETENTION_DAYS", 7, minimum=1, maximum=365)
        if retention_days is None
        else max(1, min(365, int(retention_days)))
    )
    mp4_files = list(OUTPUT_DIR.glob("*.mp4"))
    total_size = sum(path.stat().st_size for path in mp4_files if path.exists())
    candidates = cleanup_candidates(retention_days)
    candidate_size = sum(candidate.size for candidate in candidates)
    return {
        "enabled": setting_bool("VIDEO_CLEANUP_ENABLED", "1"),
        "retention_days": retention_days,
        "last_run_date": app_state_value("video_cleanup_last_run_date"),
        "total_count": len(mp4_files),
        "total_size": total_size,
        "total_size_gb": round(total_size / 1024**3, 3),
        "candidate_count": len(candidates),
        "candidate_size": candidate_size,
        "candidate_size_gb": round(candidate_size / 1024**3, 3),
        "protected_note": "긴영상 재료와 롱영상 파일은 정리 대상에서 제외됩니다.",
        "items": [
            {
                "filename": candidate.path.name,
                "log_id": candidate.log_id,
                "size": candidate.size,
                "modified_at": candidate.modified_at.isoformat(timespec="seconds"),
            }
            for candidate in candidates[:50]
        ],
    }


@app.get("/api/videos/cleanup")
@require_auth
def api_video_cleanup_status():
    retention_days = request.args.get("retention_days", type=int)
    return jsonify(video_cleanup_payload(retention_days))


@app.post("/api/videos/cleanup")
@require_auth
def api_video_cleanup_run():
    payload = request.get_json(silent=True) or {}
    retention_days = int(payload.get("retention_days") or setting_int("VIDEO_CLEANUP_RETENTION_DAYS", 7, minimum=1, maximum=365))
    retention_days = max(1, min(365, retention_days))
    set_setting_value("VIDEO_CLEANUP_RETENTION_DAYS", str(retention_days))
    set_setting_value("VIDEO_CLEANUP_ENABLED", "1")
    result = cleanup_uploaded_videos(retention_days, apply=True)
    return jsonify({"result": result, "summary": video_cleanup_payload(retention_days)})


@app.get("/api/settings")
@require_auth
def api_settings():
    return jsonify(setting_payload())


@app.post("/api/settings")
@require_auth
def api_update_settings():
    payload = request.get_json(silent=True) or {}
    settings = payload.get("settings") or {}
    updated: list[str] = []

    for name, value in settings.items():
        if name not in SETTINGS_KEYS:
            continue
        if name in SECRET_KEYS and not str(value or "").strip():
            continue
        set_setting_value(name, str(value or "").strip())
        updated.append(name)

    client_secret_json = str(payload.get("youtube_client_secret_json") or "").strip()
    if client_secret_json:
        try:
            parsed = json.loads(client_secret_json)
        except json.JSONDecodeError as exc:
            return jsonify({"error": f"invalid_youtube_client_secret_json: {exc.msg}"}), 400
        if not isinstance(parsed, dict) or not ("installed" in parsed or "web" in parsed):
            return jsonify({"error": "invalid_youtube_client_secret_json"}), 400
        (BASE_DIR / "client_secret.json").write_text(
            json.dumps(parsed, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        set_setting_value("YOUTUBE_CLIENT_SECRET_JSON", json.dumps(parsed, ensure_ascii=False))
        updated.append("client_secret.json")

    return jsonify({"updated": updated, **setting_payload()})


@app.post("/api/portable-backup")
@require_auth
def api_portable_backup():
    script = BASE_DIR / "scripts" / "export_portable.ps1"
    if not script.exists():
        return jsonify({"error": "export_script_missing"}), 404
    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        timeout=900,
        creationflags=SUBPROCESS_CREATIONFLAGS,
    )
    if result.returncode != 0:
        return jsonify({"error": "portable_backup_failed", "stderr": result.stderr[-4000:]}), 500
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        data = {"raw": result.stdout.strip()}
    return jsonify(data)


@app.post("/api/backups/cleanup")
@require_auth
def api_cleanup_backups():
    keep = request.json.get("keep", 10) if request.is_json else request.form.get("keep", 10)
    return jsonify(cleanup_backups(int(keep)))


@app.get("/api/video-script/<int:log_id>")
@require_auth
def api_video_script(log_id: int):
    init_db(DB_PATH)
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT id, source, content, created_at
            FROM telegram_logs
            WHERE id = ?
            """,
            (log_id,),
        ).fetchone()

    if row is None:
        return jsonify({"error": "not_found"}), 404
    if not row["content"].strip():
        return jsonify({"error": "empty_content"}), 400
    pipeline = pipeline_for_source(row["source"])
    profile = pipeline_payload(row["source"])
    if pipeline is None or not pipeline.enabled or pipeline.script_generator is None:
        return jsonify({"error": "pipeline_not_ready", "pipeline": profile}), 400

    script = pipeline.script_generator(row["content"])
    return jsonify(
        {
            "id": row["id"],
            "source": row["source"],
            "created_at": row["created_at"],
            "pipeline": profile,
            "script": script,
        }
    )


@app.post("/api/manual-logs")
@require_auth
def api_create_manual_log():
    payload = request.get_json(silent=True) or {}
    content = str(payload.get("content") or "").strip()
    if len(content) < 10:
        return jsonify({"error": "content_too_short"}), 400

    now = datetime.now(timezone.utc)
    msg_id = -(int(now.timestamp() * 1000) * 1000 + random.randint(0, 999))
    with closing(connect(DB_PATH)) as conn:
        cursor = conn.execute(
            """
            INSERT INTO telegram_logs (source, msg_id, content, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (MANUAL_SOURCE, msg_id, content, now.isoformat()),
        )
        conn.commit()
        log_id = int(cursor.lastrowid)

    return jsonify(
        {
            "id": log_id,
            "source": MANUAL_SOURCE,
            "msg_id": msg_id,
            "created_at": now.isoformat(),
        }
    )


def latest_video_for_log(log_id: int) -> dict[str, object] | None:
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT id, log_id, background_asset_id, bgm_asset_id, status, output_path, title,
                   stage, progress, error, created_at, updated_at
            FROM video_jobs
            WHERE log_id = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (log_id,),
        ).fetchone()
    return dict(row) if row else None


def video_job_payload(row: dict[str, object]) -> dict[str, object]:
    payload = dict(row)
    voice_id = str(payload.get("tts_voice") or "")
    selected_voice = next(
        (voice for voice in SHORT_ELEVENLABS_VOICES if voice["voice_id"] == voice_id),
        None,
    )
    if selected_voice:
        payload["tts_voice_name"] = selected_voice["name"]
        payload["tts_voice_gender"] = selected_voice["gender"]
        payload["tts_provider"] = "elevenlabs"
        payload["tts_model"] = short_elevenlabs_model()
    output_path = payload.get("output_path")
    if output_path:
        path = Path(str(output_path))
        payload["video_url"] = f"/videos/{path.name}"
    else:
        payload["video_url"] = None
    return payload


def inferred_title_for_log(log_id: int | None) -> str | None:
    if not log_id:
        return None
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            "SELECT content FROM telegram_logs WHERE id = ?",
            (int(log_id),),
        ).fetchone()
    if not row:
        return None
    try:
        script = generate_video_script(str(row["content"]))
    except Exception:
        return None
    return str(script.get("title") or "").strip() or None


def generated_video_payload(
    path: Path,
    job: dict[str, object] | None = None,
    long_job: dict[str, object] | None = None,
    healing_job: dict[str, object] | None = None,
) -> dict[str, object]:
    stat = path.stat()
    match = VIDEO_FILENAME_PATTERN.match(path.name)
    log_id = int(match.group("log_id")) if match else None
    relative_name = path.resolve().relative_to(OUTPUT_DIR.resolve()).as_posix()
    is_long_video = bool(
        long_job
        or healing_job
        or path.name.startswith("long-wisdom-library-")
        or relative_name.startswith("longform/")
    )
    inferred_title = inferred_title_for_log(log_id) if not is_long_video else None
    payload: dict[str, object] = {
        "filename": relative_name,
        "video_url": f"/videos/{relative_name}",
        "size": stat.st_size,
        "modified_at": stat.st_mtime,
        "log_id": log_id,
        "source": "file",
        "is_long_video": is_long_video,
        "kind_label": "롱" if is_long_video else "",
        "title": inferred_title,
    }
    if job:
        payload.update(
            {
                "id": job.get("id"),
                "log_id": job.get("log_id") or log_id,
                "background_asset_id": job.get("background_asset_id"),
                "status": job.get("status"),
                "title": job.get("title"),
                "created_at": job.get("created_at"),
                "stage": job.get("stage"),
                "progress": job.get("progress"),
                "updated_at": job.get("updated_at"),
                "source": "job",
            }
        )
    if long_job:
        payload.update(
            {
                "id": long_job.get("id"),
                "status": long_job.get("status"),
                "title": long_job.get("title"),
                "created_at": long_job.get("created_at"),
                "stage": long_job.get("stage"),
                "progress": long_job.get("progress"),
                "updated_at": long_job.get("updated_at"),
                "source": "long_job",
                "source_count": long_job.get("source_count"),
            }
        )
    if healing_job:
        try:
            metadata = json.loads(str(healing_job.get("metadata_json") or "{}"))
        except json.JSONDecodeError:
            metadata = {}
        payload.update(
            {
                "id": healing_job.get("id"),
                "status": healing_job.get("status"),
                "title": metadata.get("title") or healing_job.get("theme"),
                "created_at": healing_job.get("created_at"),
                "stage": healing_job.get("stage"),
                "progress": healing_job.get("progress"),
                "updated_at": healing_job.get("updated_at"),
                "source": "healing_longform_job",
            }
        )
    return payload


def list_generated_videos(
    limit: int = 20,
    video_type: str = "all",
    query: str = "",
) -> list[dict[str, object]]:
    init_db(DB_PATH)
    jobs_by_name: dict[str, dict[str, object]] = {}
    long_jobs_by_name: dict[str, dict[str, object]] = {}
    healing_jobs_by_name: dict[str, dict[str, object]] = {}
    with closing(connect(DB_PATH)) as conn:
        for row in conn.execute(
            """
            SELECT id, log_id, background_asset_id, bgm_asset_id, status, output_path, title,
                   stage, progress, error, created_at, updated_at
            FROM video_jobs
            WHERE output_path IS NOT NULL
            ORDER BY id DESC
            LIMIT 200
            """
        ):
            job = dict(row)
            filename = Path(str(job.get("output_path") or "")).name
            if filename:
                jobs_by_name[filename] = job
        for row in conn.execute(
            """
            SELECT id, status, output_path, title, source_count,
                   stage, progress, error, created_at, updated_at
            FROM long_video_jobs
            WHERE output_path IS NOT NULL
            ORDER BY id DESC
            LIMIT 200
            """
        ):
            job = dict(row)
            filename = Path(str(job.get("output_path") or "")).name
            if filename:
                long_jobs_by_name[filename] = job
        for row in conn.execute(
            """
            SELECT id, status, output_path, theme, metadata_json,
                   stage, progress, error, created_at, updated_at
            FROM healing_longform_jobs
            WHERE status = 'ready' AND output_path IS NOT NULL
            ORDER BY id DESC
            LIMIT 200
            """
        ):
            job = dict(row)
            output_path = Path(str(job.get("output_path") or ""))
            try:
                relative_name = (BASE_DIR / output_path).resolve().relative_to(OUTPUT_DIR.resolve()).as_posix()
            except ValueError:
                continue
            healing_jobs_by_name[relative_name] = job

    videos: list[dict[str, object]] = []
    for path in OUTPUT_DIR.glob("*.mp4"):
        try:
            videos.append(
                generated_video_payload(
                    path,
                    jobs_by_name.get(path.name),
                    long_jobs_by_name.get(path.name),
                )
            )
        except FileNotFoundError:
            continue
    for relative_name, healing_job in healing_jobs_by_name.items():
        path = OUTPUT_DIR / relative_name
        if not path.is_file() or path.stat().st_size <= 0:
            continue
        try:
            videos.append(generated_video_payload(path, healing_job=healing_job))
        except FileNotFoundError:
            continue

    videos.sort(key=lambda item: float(item["modified_at"]), reverse=True)
    visible_videos: list[dict[str, object]] = []
    seen_log_ids: set[int] = set()
    for video in videos:
        log_id = video.get("log_id")
        if log_id and not video.get("is_long_video"):
            normalized_log_id = int(log_id)
            if normalized_log_id in seen_log_ids:
                continue
            seen_log_ids.add(normalized_log_id)
        visible_videos.append(video)
    normalized_type = video_type if video_type in {"all", "long", "short"} else "all"
    if normalized_type == "long":
        visible_videos = [video for video in visible_videos if video.get("is_long_video")]
    elif normalized_type == "short":
        visible_videos = [video for video in visible_videos if not video.get("is_long_video")]

    normalized_query = query.strip().casefold()
    if normalized_query:
        visible_videos = [
            video
            for video in visible_videos
            if normalized_query
            in " ".join(
                str(video.get(key) or "")
                for key in ("filename", "title", "status", "stage", "kind_label")
            ).casefold()
        ]
    return visible_videos[: max(1, min(int(limit), 100))]


def used_long_video_source_filenames() -> set[str]:
    init_db(DB_PATH)
    used: set[str] = set()
    with closing(connect(DB_PATH)) as conn:
        rows = conn.execute(
            """
            SELECT source_filenames
            FROM long_video_jobs
            WHERE status IN ('running', 'ready')
              AND source_filenames IS NOT NULL
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


def next_long_video_series_number() -> int:
    init_db(DB_PATH)
    pattern = re.compile(r"지혜로운조각 10분 시리즈\s+(\d+)")
    latest = 0
    with closing(connect(DB_PATH)) as conn:
        title_rows = conn.execute(
            """
            SELECT title
            FROM long_video_jobs
            WHERE status = 'ready'
              AND output_path IS NOT NULL
              AND title LIKE '지혜로운조각 10분 시리즈%'
            UNION ALL
            SELECT title
            FROM youtube_uploads
            WHERE status = 'uploaded'
              AND youtube_url IS NOT NULL
              AND filename LIKE 'long-wisdom-library-%'
              AND title LIKE '지혜로운조각 10분 시리즈%'
            """
        ).fetchall()
    for row in title_rows:
        match = pattern.search(str(row["title"] or ""))
        if match:
            latest = max(latest, int(match.group(1)))
    return latest + 1


def uploaded_video_candidates(
    limit: int = LONG_VIDEO_MAX_SOURCE_COUNT,
    target_seconds: float | None = None,
    exclude_used: bool = True,
) -> list[dict[str, object]]:
    init_db(DB_PATH)
    candidates: list[dict[str, object]] = []
    seen: set[str] = set()
    used = used_long_video_source_filenames() if exclude_used else set()
    total_seconds = 0.0
    with closing(connect(DB_PATH)) as conn:
        rows = conn.execute(
            """
            SELECT yu.id, yu.filename, yu.log_id, yu.title, yu.youtube_url,
                   yu.updated_at, yu.created_at
            FROM youtube_uploads yu
            WHERE yu.status = 'uploaded'
              AND yu.youtube_url IS NOT NULL
              AND yu.filename LIKE 'wisdom-library-%.mp4'
            ORDER BY yu.updated_at DESC, yu.id DESC
            LIMIT 200
            """
        ).fetchall()
    for row in rows:
        item = dict(row)
        filename = str(item.get("filename") or "")
        path = OUTPUT_DIR / filename
        if not filename or filename in seen or filename in used or not path.exists():
            continue
        seen.add(filename)
        duration = ffprobe_seconds(path)
        item["path"] = path
        item["video_url"] = f"/videos/{filename}"
        item["size"] = path.stat().st_size
        item["duration"] = duration
        candidates.append(item)
        total_seconds += duration
        max_count = max(1, min(int(limit), LONG_VIDEO_MAX_SOURCE_COUNT))
        if len(candidates) >= max_count:
            break
        if target_seconds is not None and total_seconds >= target_seconds:
            break
    return list(reversed(candidates))


def long_video_job_by_id(job_id: int) -> dict[str, object] | None:
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT id, status, output_path, title, source_count, source_filenames,
                   stage, progress, error, created_at, updated_at
            FROM long_video_jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
    if not row:
        return None
    payload = dict(row)
    if payload.get("source_filenames"):
        try:
            payload["source_filenames"] = json.loads(str(payload["source_filenames"]))
        except json.JSONDecodeError:
            payload["source_filenames"] = []
    else:
        payload["source_filenames"] = []
    output_path = payload.get("output_path")
    payload["video_url"] = f"/videos/{Path(str(output_path)).name}" if output_path else None
    return payload


def long_video_job_by_filename(filename: str) -> dict[str, object] | None:
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT id, status, output_path, title, source_count, source_filenames,
                   stage, progress, error, created_at, updated_at
            FROM long_video_jobs
            WHERE output_path LIKE ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (f"%{filename}",),
        ).fetchone()
    return long_video_job_by_id(int(row["id"])) if row else None


def update_long_video_job(job_id: int, **values: object) -> None:
    allowed = {"status", "output_path", "title", "source_count", "source_filenames", "stage", "progress", "error"}
    fields = [key for key in values if key in allowed]
    if not fields:
        return
    assignments = ", ".join(f"{field} = ?" for field in fields)
    params = [values[field] for field in fields]
    with closing(connect(DB_PATH)) as conn:
        conn.execute(
            f"""
            UPDATE long_video_jobs
            SET {assignments}, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (*params, job_id),
        )
        conn.commit()


def ffprobe_seconds(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        creationflags=SUBPROCESS_CREATIONFLAGS,
    )
    payload = json.loads(result.stdout)
    return float(payload["format"]["duration"])


def concat_file_line(path: Path) -> str:
    escaped = path.resolve().as_posix().replace("'", "'\\''")
    return f"file '{escaped}'"


def long_video_background_collection() -> str:
    return get_app_setting("LONG_VIDEO_BACKGROUND_COLLECTION", "healing-meditation").strip()


def long_video_background_assets(collection: str) -> list[dict[str, object]]:
    if not collection:
        return []
    with closing(connect(DB_PATH)) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, provider, provider_id, query, author, preview_url,
                       local_path, width, height, duration
                FROM background_assets
                WHERE collection = ?
                  AND COALESCE(height, 0) >= 1080
                  AND COALESCE(duration, 0) >= 6
                ORDER BY id DESC
                LIMIT 80
                """,
                (collection,),
            )
        ]
    assets = [row for row in rows if (BASE_DIR / str(row.get("local_path") or "")).exists()]
    random.shuffle(assets)
    return assets


def long_video_background_asset_payload(asset: dict[str, object]) -> dict[str, object]:
    local_path = str(asset.get("local_path") or "")
    return {
        **asset,
        "video_url": f"/backgrounds/{Path(local_path).name}" if local_path else None,
    }


def write_long_video_background_concat(
    assets: list[dict[str, object]],
    raw_seconds: float,
    stamp: str,
) -> Path | None:
    if not assets:
        return None
    lines: list[str] = []
    total_seconds = 0.0
    index = 0
    while total_seconds < raw_seconds and index < len(assets) * 20:
        asset = assets[index % len(assets)]
        lines.append(concat_file_line(BASE_DIR / str(asset["local_path"])))
        total_seconds += max(6.0, float(asset.get("duration") or 0))
        index += 1
    if not lines:
        return None
    concat_list = OUTPUT_DIR / f"long-wisdom-library-bg-{stamp}.txt"
    concat_list.write_text("\n".join(lines), encoding="utf-8")
    return concat_list


def long_video_tts_provider() -> str:
    load_env_file(override=True)
    return os.getenv("LONG_VIDEO_TTS_PROVIDER", "").strip().lower()


def long_video_source_texts(candidates: list[dict[str, object]]) -> list[str]:
    text_by_id = long_video_source_text_map(candidates)
    texts: list[str] = []
    for item in candidates:
        text = long_video_candidate_text(item, text_by_id)
        if text:
            texts.append(text)
    return texts


def long_video_source_text_map(candidates: list[dict[str, object]]) -> dict[int, str]:
    log_ids = [int(item["log_id"]) for item in candidates if item.get("log_id")]
    if not log_ids:
        return {}
    placeholders = ",".join("?" for _ in log_ids)
    with closing(connect(DB_PATH)) as conn:
        rows = conn.execute(
            f"""
            SELECT id, content
            FROM telegram_logs
            WHERE id IN ({placeholders})
            """,
            log_ids,
        ).fetchall()
    text_by_id: dict[int, str] = {}
    for row in rows:
        body, _source = split_source(str(row["content"] or ""))
        text_by_id[int(row["id"])] = body.strip()
    return text_by_id


def long_video_candidate_text(
    candidate: dict[str, object],
    text_by_id: dict[int, str],
) -> str:
    if candidate.get("log_id"):
        text = text_by_id.get(int(candidate["log_id"]), "")
        if text:
            return text
    return str(candidate.get("title") or "").strip()


def escape_drawtext_text(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
    )


def wrap_caption_lines(text: str, max_chars: int = 24) -> list[str]:
    words = [word for word in re.split(r"\s+", text.strip()) if word]
    if not words:
        return []
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and len(candidate) > max_chars:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def caption_chunks_for_text(text: str, max_chunks: int = 12) -> list[list[str]]:
    paragraphs = [
        line.strip()
        for line in re.split(r"[\r\n]+", text)
        if line.strip()
    ]
    cleaned: list[str] = []
    for paragraph in paragraphs:
        if paragraph.startswith("-") and len(paragraph) <= 32:
            continue
        cleaned.extend(
            part.strip()
            for part in re.split(r"(?<=[.!?。！？])\s+", paragraph)
            if part.strip()
        )
    lines: list[str] = []
    for paragraph in cleaned or paragraphs:
        lines.extend(wrap_caption_lines(paragraph))
    chunks = [lines[index : index + 2] for index in range(0, len(lines), 2)]
    return [chunk for chunk in chunks if chunk][:max_chunks]


def long_video_caption_events(
    candidates: list[dict[str, object]],
    segment_durations: list[float],
) -> list[tuple[float, float, list[str]]]:
    text_by_id = long_video_source_text_map(candidates)
    events: list[tuple[float, float, list[str]]] = []
    cursor = 0.0
    for candidate, segment_duration in zip(candidates, segment_durations):
        text = long_video_candidate_text(candidate, text_by_id)
        chunks = caption_chunks_for_text(text)
        segment_duration = max(0.1, float(segment_duration))
        if chunks:
            chunk_duration = segment_duration / len(chunks)
            for index, chunk in enumerate(chunks):
                start = cursor + index * chunk_duration + 0.35
                end = cursor + (index + 1) * chunk_duration - 0.25
                if end > start:
                    events.append((start, end, chunk))
        cursor += segment_duration
    return events


def long_video_caption_filter(
    input_label: str,
    output_label: str,
    events: list[tuple[float, float, list[str]]],
) -> str:
    if not events:
        return f"[{input_label}]null[{output_label}]"
    font_file = ffmpeg_font_path(BODY_BOLD_FONT)
    filters: list[str] = []
    for start, end, lines in events:
        y_positions = ["h*0.70-text_h", "h*0.70+58"]
        for line, y_position in zip(lines[:2], y_positions):
            filters.append(
                "drawtext="
                f"fontfile='{font_file}':text='{escape_drawtext_text(line)}':"
                "fontsize=42:fontcolor=white@0.94:"
                "borderw=3:bordercolor=black@0.58:"
                "shadowx=2:shadowy=3:shadowcolor=black@0.35:"
                f"x=(w-text_w)/2:y={y_position}:"
                f"enable='between(t\\,{start:.3f}\\,{end:.3f})'"
            )
    return f"[{input_label}]" + ",".join(filters) + f"[{output_label}]"


def run_long_video_job(job_id: int, count: int, exclude_used: bool = True) -> None:
    if not LONG_VIDEO_LOCK.acquire(blocking=False):
        update_long_video_job(
            job_id,
            status="failed",
            stage="실패",
            error="long_video_already_running",
        )
        return
    try:
        update_long_video_job(job_id, stage="업로드 완료 영상 선택 중", progress=10)
        candidates = uploaded_video_candidates(
            max(count, LONG_VIDEO_MAX_SOURCE_COUNT),
            target_seconds=LONG_VIDEO_TARGET_SECONDS,
            exclude_used=exclude_used,
        )
        if len(candidates) < 2:
            raise RuntimeError("긴영상으로 합칠 새 업로드 완료 영상이 2개 이상 필요합니다.")
        source_seconds = sum(float(item.get("duration") or 0) for item in candidates)
        healing_tempo = setting_float(
            "LONG_VIDEO_HEALING_TEMPO",
            LONG_VIDEO_DEFAULT_HEALING_TEMPO,
            minimum=0.80,
            maximum=1.00,
        )
        effective_seconds = source_seconds / healing_tempo
        if effective_seconds < LONG_VIDEO_TARGET_SECONDS:
            raise RuntimeError(
                f"사용 가능한 새 업로드 완료 영상 길이가 10분 미만입니다. 현재 힐링 템포 적용 후 {int(effective_seconds // 60)}분 {int(effective_seconds % 60)}초입니다."
            )

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output = OUTPUT_DIR / f"long-wisdom-library-{stamp}.mp4"
        concat_list = OUTPUT_DIR / f"long-wisdom-library-{stamp}.txt"
        filenames = [str(item["filename"]) for item in candidates]
        concat_list.write_text(
            "\n".join(concat_file_line(Path(item["path"])) for item in candidates),
            encoding="utf-8",
        )
        background_collection = long_video_background_collection()
        background_concat_list = write_long_video_background_concat(
            long_video_background_assets(background_collection),
            source_seconds,
            stamp,
        )
        series_number = next_long_video_series_number()
        title = f"지혜로운조각 힐링 10분 시리즈 {series_number}"
        elevenlabs_narration_path: Path | None = None
        elevenlabs_narration_seconds = 0.0
        elevenlabs_segment_durations: list[float] = []
        if long_video_tts_provider() == "elevenlabs":
            try:
                update_long_video_job(job_id, stage="ElevenLabs TTS 생성 중", progress=25)
                elevenlabs_narration_path, elevenlabs_segment_durations = create_elevenlabs_long_narration_audio(
                    long_video_source_texts(candidates),
                    f"long-{job_id}-{stamp}",
                )
                elevenlabs_narration_seconds = ffprobe_seconds(elevenlabs_narration_path)
            except Exception as exc:
                elevenlabs_narration_path = None
                elevenlabs_narration_seconds = 0.0
                update_long_video_job(
                    job_id,
                    stage="ElevenLabs 실패, 기존 오디오로 진행",
                    error=f"ElevenLabs fallback: {exc}",
                    progress=30,
                )
        update_long_video_job(
            job_id,
            title=title,
            source_count=len(candidates),
            source_filenames=json.dumps(filenames, ensure_ascii=False),
            stage="긴영상 합치는 중",
            progress=35,
        )
        command = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
        ]
        if background_concat_list:
            command.extend(
                [
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(background_concat_list),
                ]
            )
        if elevenlabs_narration_path:
            video_tempo = max(0.25, min(4.0, elevenlabs_narration_seconds / source_seconds))
            segment_durations = (
                elevenlabs_segment_durations
                if len(elevenlabs_segment_durations) == len(candidates)
                else [float(item.get("duration") or 0) * video_tempo for item in candidates]
            )
            caption_filter = long_video_caption_filter(
                "basev",
                "v",
                long_video_caption_events(candidates, segment_durations),
            )
            if background_concat_list:
                narration_input_index = 2
                filter_complex = (
                    f"[1:v]scale={LONG_VIDEO_WIDTH}:{LONG_VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
                    f"crop={LONG_VIDEO_WIDTH}:{LONG_VIDEO_HEIGHT},"
                    f"eq=brightness=-0.03:saturation=0.92,setsar=1,setpts={video_tempo:.6f}*PTS[basev];"
                    f"{caption_filter}"
                )
            else:
                narration_input_index = 1
                filter_complex = (
                    "[0:v]split=2[bgsrc][fgsrc];"
                    f"[bgsrc]scale={LONG_VIDEO_WIDTH}:{LONG_VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
                    f"crop={LONG_VIDEO_WIDTH}:{LONG_VIDEO_HEIGHT},gblur=sigma=32,eq=brightness=-0.08[bg];"
                    f"[fgsrc]scale=-2:{LONG_VIDEO_HEIGHT}[fg];"
                    f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,setpts={video_tempo:.6f}*PTS[basev];"
                    f"{caption_filter}"
                )
            command.extend(
                [
                    "-i",
                    str(elevenlabs_narration_path),
                    "-filter_complex",
                    filter_complex,
                    "-map",
                    "[v]",
                    "-map",
                    f"{narration_input_index}:a:0",
                    "-shortest",
                ]
            )
        else:
            video_tempo = 1 / healing_tempo
            segment_durations = [
                float(item.get("duration") or 0) / healing_tempo
                for item in candidates
            ]
            caption_filter = long_video_caption_filter(
                "basev",
                "v",
                long_video_caption_events(candidates, segment_durations),
            )
            if background_concat_list:
                filter_complex = (
                    f"[1:v]scale={LONG_VIDEO_WIDTH}:{LONG_VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
                    f"crop={LONG_VIDEO_WIDTH}:{LONG_VIDEO_HEIGHT},"
                    f"eq=brightness=-0.03:saturation=0.92,setsar=1,setpts={video_tempo:.6f}*PTS[basev];"
                    f"{caption_filter};"
                    f"[0:a:0]atempo={healing_tempo:.6f}[a]"
                )
            else:
                filter_complex = (
                    "[0:v]split=2[bgsrc][fgsrc];"
                    f"[bgsrc]scale={LONG_VIDEO_WIDTH}:{LONG_VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
                    f"crop={LONG_VIDEO_WIDTH}:{LONG_VIDEO_HEIGHT},gblur=sigma=32,eq=brightness=-0.08[bg];"
                    f"[fgsrc]scale=-2:{LONG_VIDEO_HEIGHT}[fg];"
                    f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,setpts={video_tempo:.6f}*PTS[basev];"
                    f"{caption_filter};"
                    f"[0:a:0]atempo={healing_tempo:.6f}[a]"
                )
            command.extend(
                [
                    "-filter_complex",
                    filter_complex,
                    "-map",
                    "[v]",
                    "-map",
                    "[a]",
                ]
            )
        command.extend(
            [
                "-map_metadata",
                "-1",
                "-map_chapters",
                "-1",
                "-c:v",
                "libx264",
                "-preset",
                RENDER_PRESET,
                "-crf",
                RENDER_CRF,
                "-pix_fmt",
                "yuv420p",
                "-colorspace",
                "bt709",
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
                "-movflags",
                "+faststart",
                str(output),
            ]
        )
        subprocess.run(
            command,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=SUBPROCESS_CREATIONFLAGS,
        )
        duration = ffprobe_seconds(output)
        update_long_video_job(
            job_id,
            status="ready",
            output_path=output.resolve().relative_to(BASE_DIR).as_posix(),
            stage=f"완료 ({int(duration // 60)}분 {int(duration % 60)}초)",
            progress=100,
            error=None,
        )
    except subprocess.CalledProcessError as exc:
        update_long_video_job(
            job_id,
            status="failed",
            stage="실패",
            error=(exc.stderr or str(exc))[-4000:],
        )
    except Exception as exc:
        update_long_video_job(job_id, status="failed", stage="실패", error=str(exc))
    finally:
        LONG_VIDEO_LOCK.release()


def video_job_by_filename(filename: str) -> dict[str, object] | None:
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT id, log_id, background_asset_id, bgm_asset_id, status, output_path, title,
                   stage, progress, error, created_at, updated_at
            FROM video_jobs
            WHERE output_path LIKE ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (f"%{filename}",),
        ).fetchone()
    return dict(row) if row else None


def video_job_by_id(job_id: int) -> dict[str, object] | None:
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT id, log_id, background_asset_id, bgm_asset_id, status, output_path, title,
                   stage, progress, error, created_at, updated_at
            FROM video_jobs
            WHERE id = ?
            """,
            (job_id,),
        ).fetchone()
    return dict(row) if row else None


def active_video_job(log_id: int) -> dict[str, object] | None:
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT id, log_id, background_asset_id, bgm_asset_id, status, output_path, title,
                   stage, progress, error, created_at, updated_at
            FROM video_jobs
            WHERE log_id = ? AND status = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (log_id, "rendering"),
        ).fetchone()
    return dict(row) if row else None


def update_video_job_progress(job_id: int, stage: str, progress: int) -> None:
    with closing(connect(DB_PATH)) as conn:
        conn.execute(
            """
            UPDATE video_jobs
            SET stage = ?, progress = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (stage, progress, job_id),
        )
        conn.commit()


def run_video_render_job(
    job_id: int,
    log_id: int,
    background_asset_id: int | None,
    tts_enabled: bool,
    tts_voice: str,
    tts_rate: str,
) -> None:
    try:
        output, script = render_video(
            log_id,
            background_asset_id,
            tts_enabled,
            tts_voice,
            tts_rate,
            lambda stage, progress: update_video_job_progress(job_id, stage, progress),
        )
        relative_output = output.resolve().relative_to(BASE_DIR).as_posix()
        with closing(connect(DB_PATH)) as conn:
            conn.execute(
                """
                UPDATE video_jobs
                SET status = ?, stage = ?, progress = ?, output_path = ?, title = ?, bgm_asset_id = ?,
                    background_asset_ids = ?, tts_voice = ?, tts_rate = ?,
                    error = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    "ready",
                    "완료",
                    100,
                    relative_output,
                    str(script["title"]),
                    script.get("bgm_asset_id"),
                    json.dumps(script.get("background_asset_ids") or []),
                    script.get("tts_voice"),
                    script.get("tts_rate") or tts_rate,
                    job_id,
                ),
            )
            conn.commit()
    except Exception as exc:
        with closing(connect(DB_PATH)) as conn:
            conn.execute(
                """
                UPDATE video_jobs
                SET status = ?, stage = ?, error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                ("failed", "실패", str(exc), job_id),
            )
            conn.commit()


def log_row_for_video(filename: str, job: dict[str, object] | None) -> dict[str, object] | None:
    log_id = job.get("log_id") if job else None
    if not log_id:
        match = VIDEO_FILENAME_PATTERN.match(filename)
        log_id = int(match.group("log_id")) if match else None
    if not log_id:
        return None

    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT id, source, content, created_at
            FROM telegram_logs
            WHERE id = ?
            """,
            (int(log_id),),
        ).fetchone()
    return dict(row) if row else None


def youtube_title(script: dict[str, object], quote_text: str = "") -> str:
    title = str(script.get("title") or "오늘의 문장").strip()
    suffix = f" | {BRAND_NAME}"
    source_text = quote_text or " ".join(str(item) for item in script.get("narration", []))
    title = generate_title(source_text or str(script.get("title") or "오늘의 문장")).strip()
    max_title_len = 100 - len(suffix)
    if len(title) > max_title_len:
        title = title[: max_title_len - 3].rstrip() + "..."
    return f"{title}{suffix}"


def output_video_path(filename: str, *, must_exist: bool = True) -> Path:
    path = (OUTPUT_DIR / filename).resolve()
    try:
        path.relative_to(OUTPUT_DIR.resolve())
    except ValueError as exc:
        raise FileNotFoundError(filename) from exc
    if path.suffix.lower() != ".mp4" or (must_exist and not path.is_file()):
        raise FileNotFoundError(filename)
    return path


def youtube_metadata_for_video(filename: str) -> dict[str, object]:
    init_db(DB_PATH)
    output_video_path(filename)

    relative_output = (Path("outputs") / Path(filename)).as_posix()
    with closing(connect(DB_PATH)) as conn:
        healing_row = conn.execute(
            """
            SELECT metadata_json, theme
            FROM healing_longform_jobs
            WHERE output_path = ? AND status = 'ready'
            ORDER BY id DESC LIMIT 1
            """,
            (relative_output,),
        ).fetchone()
    if healing_row:
        try:
            longform_metadata = json.loads(str(healing_row["metadata_json"] or "{}"))
        except json.JSONDecodeError:
            longform_metadata = {}
        longform_tags = longform_metadata.get("tags")
        if not isinstance(longform_tags, list) or not longform_tags:
            longform_tags = [
                BRAND_NAME,
                "힐링낭독",
                "마음위로",
                "긴영상",
                *longform_metadata.get("hashtags", []),
            ]
        title, description, tags = sanitize_youtube_metadata(
            str(longform_metadata.get("title") or healing_row["theme"] or "힐링 롱폼"),
            str(longform_metadata.get("description") or "천천히 듣는 힐링 롱폼 영상입니다."),
            [str(tag) for tag in longform_tags],
        )
        title_options = []
        for candidate in longform_metadata.get("title_options") or [title]:
            clean_title, _, _ = sanitize_youtube_metadata(str(candidate), description, tags)
            if clean_title not in title_options:
                title_options.append(clean_title)
        return {
            "filename": filename,
            "title": title,
            "title_options": title_options[:3],
            "description": description,
            "tags": tags,
            "hashtags": [f"#{str(tag).lstrip('#')}" for tag in longform_metadata.get("hashtags", [])],
        }

    if filename.startswith("long-wisdom-library-"):
        job = long_video_job_by_filename(filename)
        source_count = int(job.get("source_count") or 10) if job else 10
        long_title = str(job.get("title") or "") if job else ""
        if not long_title:
            long_title = f"지혜로운조각 10분 시리즈 {next_long_video_series_number()}"
        title, description, tags = sanitize_youtube_metadata(
            long_title,
            "\n".join(
                [
                    f"{BRAND_NAME}",
                    "",
                    f"짧은 좋은 글 영상 {source_count}편을 한 번에 볼 수 있도록 묶은 긴영상입니다.",
                    "조용히 틀어두고 생각을 정리할 때 보기 좋게 구성했습니다.",
                    "",
                    "#지혜로운조각들 #좋은글 #명언 #글귀 #인생문장 #긴영상",
                ]
            ),
            [BRAND_NAME, "좋은글", "명언", "글귀", "인생문장", "긴영상", "좋은글모음"],
        )
        return {
            "filename": filename,
            "title": title,
            "description": description,
            "tags": tags,
            "hashtags": ["#지혜로운조각들", "#좋은글", "#명언", "#글귀", "#긴영상"],
        }

    job = video_job_by_filename(filename)
    log = log_row_for_video(filename, job)
    if not log:
        title, description, tags = sanitize_youtube_metadata(
            f"{BRAND_NAME} - 오늘의 문장",
            f"{BRAND_NAME}\n\n천천히 읽는 문장 영상입니다.",
            [BRAND_NAME, "좋은글", "명언", "짧은글", "인생문장"],
        )
        return {
            "filename": filename,
            "title": title,
            "description": description,
            "tags": tags,
            "hashtags": ["#지혜로운조각들", "#좋은글", "#명언", "#짧은글"],
        }

    script = generate_video_script(str(log["content"]))
    body_text = "\n".join(str(item) for item in script.get("narration", []))
    source = str(script.get("source") or "").strip()
    description_parts = [
        f"{BRAND_NAME}",
        "",
        body_text,
    ]
    if source:
        description_parts.extend(["", f"글 출처: {source}"])
    description_parts.extend(
        [
            "",
            "천천히 읽고 마음에 남는 문장을 전합니다.",
            "",
            "#지혜로운조각들 #좋은글 #명언 #짧은글 #인생문장",
        ]
    )

    title, description, tags = sanitize_youtube_metadata(
        youtube_title(script, body_text),
        "\n".join(description_parts).strip(),
        [BRAND_NAME, "좋은글", "명언", "짧은글", "인생문장", "마음글"],
    )
    title, description, tags = sanitize_youtube_metadata(
        title,
        description,
        generate_tags(body_text, title),
    )
    return {
        "filename": filename,
        "log_id": log["id"],
        "title": title,
        "description": description,
        "tags": tags,
        "hashtags": ["#지혜로운조각들", "#좋은글", "#명언", "#짧은글", "#인생문장"],
    }


def random_scheduled_publish_at(now: datetime | None = None) -> datetime:
    return next_peak_publish_at(now)


def longform_schedule_times() -> list[time]:
    raw = get_app_setting("LONGFORM_UPLOAD_SCHEDULE_TIMES", "20:30,21:00")
    values: list[time] = []
    for item in raw.split(","):
        try:
            hour, minute = (int(part.strip()) for part in item.split(":", 1))
            candidate = time(hour, minute)
        except (TypeError, ValueError):
            continue
        if candidate not in values:
            values.append(candidate)
    return sorted(values) or [time(20, 30), time(21, 0)]


def next_longform_publish_at(now: datetime | None = None) -> datetime:
    now_kst = (now or datetime.now(KST)).astimezone(KST)
    first_date = now_kst.date() + timedelta(days=1)
    with closing(connect(DB_PATH)) as conn:
        for day_offset in range(31):
            candidate_date = first_date + timedelta(days=day_offset)
            rows = conn.execute(
                """
                SELECT scheduled_publish_at
                FROM youtube_uploads
                WHERE scheduled_publish_at LIKE ?
                  AND status NOT IN ('failed', 'deleted')
                """,
                (f"{candidate_date.isoformat()}T%",),
            ).fetchall()
            occupied = {
                str(row["scheduled_publish_at"] or "")[:16]
                for row in rows
                if row["scheduled_publish_at"]
            }
            for slot in longform_schedule_times():
                candidate = datetime.combine(candidate_date, slot, tzinfo=KST)
                if candidate.isoformat(timespec="minutes")[:16] not in occupied:
                    return candidate
    raise RuntimeError("31일 이내에 사용할 수 있는 롱폼 예약 시간이 없습니다.")


def is_longform_video(filename: str) -> bool:
    normalized = filename.replace("\\", "/")
    return normalized.startswith("longform/") or Path(normalized).name.startswith("long-wisdom-library-")


def scheduled_publish_payload(publish_at: datetime | None) -> dict[str, str] | None:
    if not publish_at:
        return None
    return {
        "kst": publish_at.astimezone(KST).isoformat(timespec="seconds"),
        "utc": publish_at.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    }


def latest_youtube_upload(filename: str) -> dict[str, object] | None:
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT id, filename, video_job_id, log_id, youtube_video_id, youtube_url,
                   title, privacy_status, status, error, view_count, like_count,
                   comment_count, stats_checked_at, scheduled_publish_at,
                   comment_thread_id, comment_posted_at, playlist_id, playlist_item_id,
                   caption_id, postprocess_error,
                   created_at, updated_at
            FROM youtube_uploads
            WHERE filename = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (filename,),
        ).fetchone()
    return dict(row) if row else None


def latest_successful_youtube_upload(filename: str) -> dict[str, object] | None:
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT id, filename, video_job_id, log_id, youtube_video_id, youtube_url,
                   title, privacy_status, status, error, view_count, like_count,
                   comment_count, stats_checked_at, scheduled_publish_at,
                   comment_thread_id, comment_posted_at, playlist_id, playlist_item_id,
                   caption_id, postprocess_error,
                   created_at, updated_at
            FROM youtube_uploads
            WHERE filename = ? AND status = ? AND youtube_url IS NOT NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (filename, "uploaded"),
        ).fetchone()
    return dict(row) if row else None


def active_youtube_upload(filename: str) -> dict[str, object] | None:
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT id, filename, video_job_id, log_id, youtube_video_id, youtube_url,
                   title, privacy_status, status, error, view_count, like_count,
                   comment_count, stats_checked_at, scheduled_publish_at,
                   comment_thread_id, comment_posted_at, playlist_id, playlist_item_id,
                   caption_id, postprocess_error,
                   created_at, updated_at
            FROM youtube_uploads
            WHERE filename = ? AND status = ?
              AND datetime(created_at) >= datetime('now', '-20 minutes')
            ORDER BY id DESC
            LIMIT 1
            """,
            (filename, "uploading"),
        ).fetchone()
    return dict(row) if row else None


def create_youtube_upload_row(
    filename: str,
    metadata: dict[str, object],
    privacy_status: str,
    scheduled_publish_at: datetime | None = None,
) -> int:
    job = video_job_by_filename(filename)
    with closing(connect(DB_PATH)) as conn:
        cursor = conn.execute(
            """
            INSERT INTO youtube_uploads
                (filename, video_job_id, log_id, title, privacy_status, status, scheduled_publish_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                filename,
                job.get("id") if job else None,
                metadata.get("log_id"),
                metadata.get("title"),
                privacy_status,
                "uploading",
                scheduled_publish_payload(scheduled_publish_at)["kst"] if scheduled_publish_at else None,
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def update_youtube_upload_success(
    upload_id: int,
    result: dict[str, object],
    scheduled_publish_at: datetime | None = None,
) -> None:
    with closing(connect(DB_PATH)) as conn:
        conn.execute(
            """
            UPDATE youtube_uploads
            SET status = ?, youtube_video_id = ?, youtube_url = ?, error = NULL,
                scheduled_publish_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                "uploaded",
                result["youtube_video_id"],
                result["youtube_url"],
                scheduled_publish_payload(scheduled_publish_at)["kst"] if scheduled_publish_at else None,
                upload_id,
            ),
        )
        conn.commit()


LONGFORM_COMMENT_TEXT = """오늘도 정말 수고 많으셨습니다.

이번 낭독에서 마음에 남은 문장이 있었나요?
다음 영상에서 듣고 싶은 위로의 주제도 댓글로 남겨주세요.

여러분의 댓글을 다음 낭독 제작에 참고하겠습니다."""


def longform_subtitle_path(filename: str) -> Path | None:
    relative_output = (Path("outputs") / Path(filename)).as_posix()
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT script_path
            FROM healing_longform_jobs
            WHERE output_path = ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (relative_output,),
        ).fetchone()
    if not row or not row["script_path"]:
        return None
    path = (BASE_DIR / str(row["script_path"])).parent / "subtitles.srt"
    return path if path.is_file() else None


def update_youtube_upload_postprocess(upload_id: int, **values: object) -> None:
    allowed = {"playlist_id", "playlist_item_id", "caption_id", "postprocess_error"}
    fields = [key for key in values if key in allowed]
    if not fields:
        return
    assignments = ", ".join(f"{field} = ?" for field in fields)
    with closing(connect(DB_PATH)) as conn:
        conn.execute(
            f"""
            UPDATE youtube_uploads
            SET {assignments}, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (*[values[field] for field in fields], upload_id),
        )
        conn.commit()


def youtube_upload_postprocess_state(upload_id: int) -> dict[str, object]:
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT playlist_id, playlist_item_id, caption_id, postprocess_error
            FROM youtube_uploads
            WHERE id = ?
            """,
            (upload_id,),
        ).fetchone()
    if not row:
        raise LookupError(f"youtube upload not found: {upload_id}")
    return dict(row)


def postprocess_longform_upload(upload_id: int, filename: str, video_id: str) -> dict[str, object]:
    with YOUTUBE_POSTPROCESS_LOCK:
        state = youtube_upload_postprocess_state(upload_id)
        completed: dict[str, object] = {
            key: state.get(key)
            for key in ("playlist_id", "playlist_item_id", "caption_id")
            if state.get(key)
        }
        updates: dict[str, object] = {}
        errors: list[str] = []

        if not state.get("playlist_item_id"):
            try:
                playlist_id = str(state.get("playlist_id") or "")
                if not playlist_id:
                    playlist_name = get_app_setting(
                        "LONGFORM_YOUTUBE_PLAYLIST_NAME",
                        "잠들기 전 듣는 힐링 낭독",
                    ).strip() or "잠들기 전 듣는 힐링 낭독"
                    playlist_id = find_or_create_playlist(playlist_name)
                playlist_item_id = add_video_to_playlist(playlist_id, video_id)
                updates.update(playlist_id=playlist_id, playlist_item_id=playlist_item_id)
                completed.update(playlist_id=playlist_id, playlist_item_id=playlist_item_id)
            except Exception as exc:
                errors.append(f"playlist: {exc}")

        if not state.get("caption_id"):
            subtitle_path = longform_subtitle_path(filename)
            if subtitle_path:
                try:
                    caption_id = upload_korean_caption(video_id, subtitle_path)
                    updates["caption_id"] = caption_id
                    completed["caption_id"] = caption_id
                except Exception as exc:
                    errors.append(f"caption: {exc}")
            else:
                errors.append("caption: subtitles.srt not found")

        updates["postprocess_error"] = " | ".join(errors)[:2000] if errors else None
        update_youtube_upload_postprocess(upload_id, **updates)
        return {"upload_id": upload_id, **completed, "postprocess_error": updates["postprocess_error"]}


def process_pending_longform_uploads(limit: int = 5) -> list[dict[str, object]]:
    init_db(DB_PATH)
    with closing(connect(DB_PATH)) as conn:
        rows = conn.execute(
            """
            SELECT id, filename, youtube_video_id
            FROM youtube_uploads
            WHERE status = 'uploaded'
              AND filename LIKE 'longform/%'
              AND youtube_video_id IS NOT NULL
              AND (playlist_item_id IS NULL OR caption_id IS NULL)
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, min(limit, 20)),),
        ).fetchall()
    return [
        postprocess_longform_upload(int(row["id"]), str(row["filename"]), str(row["youtube_video_id"]))
        for row in rows
    ]


def post_due_longform_comments(limit: int = 5) -> list[dict[str, object]]:
    init_db(DB_PATH)
    with closing(connect(DB_PATH)) as conn:
        rows = conn.execute(
            """
            SELECT id, youtube_video_id
            FROM youtube_uploads
            WHERE status = 'uploaded'
              AND filename LIKE 'longform/%'
              AND youtube_video_id IS NOT NULL
              AND comment_thread_id IS NULL
              AND datetime(created_at) >= datetime('now', '-14 days')
              AND (
                    (scheduled_publish_at IS NOT NULL
                     AND datetime(scheduled_publish_at) <= datetime('now', '-5 minutes'))
                    OR (scheduled_publish_at IS NULL AND privacy_status = 'public')
                  )
            ORDER BY id DESC
            LIMIT ?
            """,
            (max(1, min(limit, 20)),),
        ).fetchall()
    posted: list[dict[str, object]] = []
    for row in rows:
        try:
            response = post_top_level_comment(str(row["youtube_video_id"]), LONGFORM_COMMENT_TEXT)
        except Exception:
            continue
        thread_id = str(response.get("id") or "")
        if not thread_id:
            continue
        with closing(connect(DB_PATH)) as conn:
            conn.execute(
                """
                UPDATE youtube_uploads
                SET comment_thread_id = ?, comment_posted_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (thread_id, row["id"]),
            )
            conn.commit()
        posted.append({"upload_id": int(row["id"]), "comment_thread_id": thread_id})
    return posted


def update_youtube_upload_failure(upload_id: int, error: str) -> None:
    with closing(connect(DB_PATH)) as conn:
        conn.execute(
            """
            UPDATE youtube_uploads
            SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            ("failed", error, upload_id),
        )
        conn.commit()


def mark_interrupted_youtube_uploads() -> None:
    init_db(DB_PATH)
    with closing(connect(DB_PATH)) as conn:
        conn.execute(
            """
            UPDATE youtube_uploads
            SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE status = ?
            """,
            ("failed", "server restarted before upload finished", "uploading"),
        )
        conn.commit()


def run_youtube_upload_job(
    upload_id: int,
    path: Path,
    metadata: dict[str, object],
    privacy_status: str,
    scheduled_publish_at: datetime | None,
) -> None:
    try:
        audit_video_before_upload(path, label=f"manual-youtube-upload-{upload_id}")
        result = upload_video(
            path,
            str(metadata["title"]),
            str(metadata["description"]),
            list(metadata.get("tags") or []),
            privacy_status,
            publish_at=scheduled_publish_at,
            contains_synthetic_media=True,
        )
    except Exception as exc:
        update_youtube_upload_failure(upload_id, str(exc))
        return
    update_youtube_upload_success(upload_id, result, scheduled_publish_at)
    filename = path.resolve().relative_to(OUTPUT_DIR.resolve()).as_posix()
    if is_longform_video(filename):
        postprocess_longform_upload(upload_id, filename, str(result["youtube_video_id"]))


def update_youtube_upload_metadata(upload_id: int, title: str, privacy_status: str) -> None:
    with closing(connect(DB_PATH)) as conn:
        conn.execute(
            """
            UPDATE youtube_uploads
            SET title = ?, privacy_status = ?, error = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (title, privacy_status, upload_id),
        )
        conn.commit()


def youtube_stats_due_uploads(limit: int = 50) -> list[dict[str, object]]:
    with closing(connect(DB_PATH)) as conn:
        rows = conn.execute(
            """
            SELECT id, youtube_video_id
            FROM youtube_uploads
            WHERE status = 'uploaded'
              AND youtube_video_id IS NOT NULL
              AND (
                    (scheduled_publish_at IS NOT NULL
                     AND datetime(scheduled_publish_at) <= datetime('now', '-24 hours')
                     AND datetime(scheduled_publish_at) >= datetime('now', '-72 hours'))
                    OR
                    (scheduled_publish_at IS NULL
                     AND privacy_status = 'public'
                     AND datetime(created_at) <= datetime('now', '-24 hours')
                     AND datetime(created_at) >= datetime('now', '-72 hours'))
                  )
              AND (
                    view_count IS NULL
                    OR stats_checked_at IS NULL
                    OR (scheduled_publish_at IS NOT NULL
                        AND datetime(stats_checked_at) < datetime(scheduled_publish_at, '+24 hours'))
                  )
            ORDER BY COALESCE(scheduled_publish_at, created_at) ASC
            LIMIT ?
            """,
            (max(1, min(limit, 100)),),
        ).fetchall()
    return [dict(row) for row in rows]


def refresh_youtube_upload_stats(limit: int = 50) -> dict[str, object]:
    init_db(DB_PATH)
    longform_postprocessed = process_pending_longform_uploads()
    comments_posted = post_due_longform_comments()
    rows = youtube_stats_due_uploads(limit)

    updated: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    for row in rows:
        try:
            stats = get_video_statistics(str(row["youtube_video_id"]))
        except Exception as exc:
            errors.append({"id": row["id"], "error": str(exc)})
            continue
        with closing(connect(DB_PATH)) as conn:
            conn.execute(
                """
                UPDATE youtube_uploads
                SET view_count = ?, like_count = ?, comment_count = ?,
                    stats_checked_at = CURRENT_TIMESTAMP,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    stats["view_count"],
                    stats["like_count"],
                    stats["comment_count"],
                    row["id"],
                ),
            )
            conn.commit()
        updated.append({"id": row["id"], **stats})
    return {
        "checked": len(rows),
        "updated": updated,
        "errors": errors,
        "longform_postprocessed": longform_postprocessed,
        "comments_posted": comments_posted,
    }


def youtube_performance_patterns(limit: int = 10) -> list[dict[str, object]]:
    init_db(DB_PATH)
    with closing(connect(DB_PATH)) as conn:
        rows = conn.execute(
            """
            SELECT
                COALESCE(NULLIF(tl.source, ''), 'unknown') AS source,
                AVG(yu.view_count) AS avg_views,
                COUNT(*) AS cnt,
                MAX(yu.view_count) AS max_views
            FROM youtube_uploads yu
            LEFT JOIN telegram_logs tl ON tl.id = yu.log_id
            WHERE yu.view_count IS NOT NULL
            GROUP BY source
            ORDER BY avg_views DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "source": row["source"],
            "avg_views": round(float(row["avg_views"] or 0), 1),
            "count": int(row["cnt"] or 0),
            "max_views": int(row["max_views"] or 0),
        }
        for row in rows
    ]


def youtube_stats_loop() -> None:
    while True:
        try:
            refresh_youtube_upload_stats()
        except Exception:
            pass
        wait_seconds = max(300, YOUTUBE_STATS_INTERVAL_SECONDS)
        threading.Event().wait(wait_seconds)


def start_youtube_stats_loop() -> None:
    thread = threading.Thread(target=youtube_stats_loop, daemon=True)
    thread.start()


def parse_youtube_payload(payload: dict[str, object], defaults: dict[str, object]) -> dict[str, object]:
    metadata = dict(defaults)
    if payload.get("title"):
        metadata["title"] = str(payload["title"])
    if payload.get("description") is not None:
        metadata["description"] = str(payload["description"])
    if payload.get("tags") is not None:
        raw_tags = payload["tags"]
        if isinstance(raw_tags, str):
            metadata["tags"] = [item.strip() for item in raw_tags.split(",") if item.strip()]
        elif isinstance(raw_tags, list):
            metadata["tags"] = [str(item).strip() for item in raw_tags if str(item).strip()]
    metadata["title"], metadata["description"], metadata["tags"] = sanitize_youtube_metadata(
        str(metadata.get("title") or ""),
        str(metadata.get("description") or ""),
        list(metadata.get("tags") or []),
    )
    metadata["privacy_status"] = str(payload.get("privacy_status") or defaults.get("privacy_status") or "private")
    metadata["schedule_publish"] = bool(payload.get("schedule_publish") or defaults.get("schedule_publish") or False)
    return metadata


def tts_rate_from_payload(value: object) -> str:
    if not value:
        return DEFAULT_RATE
    text = str(value)
    return RATE_OPTIONS.get(text, text)


@app.post("/api/videos/render/<int:log_id>")
@require_auth
def api_render_video(log_id: int):
    init_db(DB_PATH)
    mark_stale_short_video_jobs_failed()
    running_job = active_video_job(log_id)
    if running_job:
        return jsonify({"job": video_job_payload(running_job), "already_running": True}), 409

    background_asset_id = None
    tts_enabled = True
    tts_voice = DEFAULT_VOICE
    tts_rate = DEFAULT_RATE
    if request.is_json:
        payload = request.get_json() or {}
        raw_background_id = payload.get("background_asset_id")
        if raw_background_id:
            try:
                background_asset_id = int(raw_background_id)
            except (TypeError, ValueError):
                return jsonify({"error": "invalid_background_asset_id"}), 400
        tts_enabled = bool(payload.get("tts_enabled", True))
        if payload.get("tts_voice"):
            tts_voice = str(payload["tts_voice"])
        tts_rate = tts_rate_from_payload(payload.get("tts_rate"))

    if background_asset_id is None:
        background_asset_id = random_background_asset_id()

    with closing(connect(DB_PATH)) as conn:
        cursor = conn.execute(
            """
            INSERT INTO video_jobs
                (log_id, background_asset_id, tts_voice, tts_rate, status, stage, progress)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (log_id, background_asset_id, tts_voice, tts_rate, "rendering", "대기 중", 0),
        )
        job_id = cursor.lastrowid
        conn.commit()

    thread = threading.Thread(
        target=run_video_render_job,
        args=(job_id, log_id, background_asset_id, tts_enabled, tts_voice, tts_rate),
        daemon=True,
    )
    thread.start()
    return jsonify({"job": video_job_payload(video_job_by_id(job_id))}), 202


@app.get("/api/videos/jobs/<int:job_id>")
@require_auth
def api_video_job(job_id: int):
    job = video_job_by_id(job_id)
    if not job:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"job": video_job_payload(job)})


@app.post("/api/tts/preview")
@require_auth
def api_tts_preview():
    payload = request.get_json() if request.is_json else {}
    text = str(payload.get("text") or "")
    voice = str(payload.get("voice") or DEFAULT_VOICE)
    rate = tts_rate_from_payload(payload.get("rate"))
    try:
        provider = short_tts_provider()
        if provider == "elevenlabs":
            output, duration, selected_voice = create_short_elevenlabs_preview(text)
            used_voice = str(selected_voice["name"])
            voice_id = str(selected_voice["voice_id"])
            model_id = str(selected_voice["model_id"])
        else:
            output, duration, used_voice = create_preview_audio(text, voice, rate)
            voice_id = used_voice
            model_id = "edge-tts"
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify(
        {
            "audio_url": f"/audio/{output.resolve().relative_to(BASE_DIR).as_posix().removeprefix('outputs/audio/')}",
            "duration": duration,
            "voice": used_voice,
            "voice_id": voice_id,
            "requested_voice": voice,
            "rate": rate,
            "provider": provider,
            "model_id": model_id,
        }
    )


@app.get("/api/videos/latest/<int:log_id>")
@require_auth
def api_latest_video(log_id: int):
    job = latest_video_for_log(log_id)
    if not job:
        return jsonify({"video": None})
    return jsonify({"video": video_job_payload(job)})


@app.get("/api/videos")
@require_auth
def api_videos():
    try:
        limit = int(request.args.get("limit", "12"))
    except ValueError:
        limit = 12
    video_type = request.args.get("type", "all")
    query = request.args.get("q", "")
    return jsonify({"videos": list_generated_videos(limit, video_type, query)})


@app.get("/api/long-videos/backgrounds")
@require_auth
def api_long_video_backgrounds():
    collection = long_video_background_collection()
    assets = [
        long_video_background_asset_payload(asset)
        for asset in long_video_background_assets(collection)
    ]
    return jsonify(
        {
            "collection": collection,
            "count": len(assets),
            "backgrounds": assets,
        }
    )


@app.get("/api/long-videos/candidates")
@require_auth
def api_long_video_candidates():
    try:
        limit = int(request.args.get("limit", str(LONG_VIDEO_MAX_SOURCE_COUNT)))
    except ValueError:
        limit = LONG_VIDEO_MAX_SOURCE_COUNT
    include_used = request.args.get("include_used") == "1"
    candidates = uploaded_video_candidates(
        limit,
        target_seconds=LONG_VIDEO_TARGET_SECONDS,
        exclude_used=not include_used,
    )
    total_seconds = sum(float(item.get("duration") or 0) for item in candidates)
    healing_tempo = setting_float(
        "LONG_VIDEO_HEALING_TEMPO",
        LONG_VIDEO_DEFAULT_HEALING_TEMPO,
        minimum=0.80,
        maximum=1.00,
    )
    effective_seconds = total_seconds / healing_tempo if healing_tempo else total_seconds
    return jsonify(
        {
            "count": len(candidates),
            "duration": total_seconds,
            "total_seconds": total_seconds,
            "effective_seconds": effective_seconds,
            "healing_tempo": healing_tempo,
            "target_seconds": LONG_VIDEO_TARGET_SECONDS,
            "ready": effective_seconds >= LONG_VIDEO_TARGET_SECONDS,
            "include_used": include_used,
            "candidates": [
                {
                    key: value
                    for key, value in item.items()
                    if key not in {"path"}
                }
                for item in candidates
            ],
        }
    )


@app.post("/api/long-videos/create")
@require_auth
def api_create_long_video():
    payload = request.get_json(silent=True) or {}
    try:
        count = int(payload.get("count") or request.args.get("count") or LONG_VIDEO_MAX_SOURCE_COUNT)
    except ValueError:
        count = LONG_VIDEO_MAX_SOURCE_COUNT
    count = max(2, min(count, LONG_VIDEO_MAX_SOURCE_COUNT))
    include_used = bool(payload.get("include_used") or request.args.get("include_used") == "1")

    with closing(connect(DB_PATH)) as conn:
        cursor = conn.execute(
            """
            INSERT INTO long_video_jobs (status, stage, progress, source_count)
            VALUES (?, ?, ?, ?)
            """,
            ("running", "대기 중", 0, count),
        )
        job_id = int(cursor.lastrowid)
        conn.commit()

    thread = threading.Thread(target=run_long_video_job, args=(job_id, count, not include_used), daemon=True)
    thread.start()
    return jsonify({"job": long_video_job_by_id(job_id)}), 202


@app.get("/api/long-videos/jobs/<int:job_id>")
@require_auth
def api_long_video_job(job_id: int):
    job = long_video_job_by_id(job_id)
    if not job:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"job": job})


def healing_job_payload(job: dict[str, object]) -> dict[str, object]:
    payload = dict(job)
    output_path = str(payload.get("output_path") or "")
    payload["video_url"] = f"/videos/{output_path.removeprefix('outputs/')}" if output_path else None
    try:
        payload["metadata"] = json.loads(str(payload.get("metadata_json") or "{}"))
    except json.JSONDecodeError:
        payload["metadata"] = {}
    payload.pop("config_json", None)
    payload.pop("metadata_json", None)
    return payload


@app.get("/api/healing-longform/config")
@require_auth
def api_healing_longform_config():
    elevenlabs_key = str(get_app_setting("ELEVENLABS_API_KEY", os.getenv("ELEVENLABS_API_KEY", "")) or "").strip()
    config = load_longform_config()
    elevenlabs = config["longform"]["tts"].get("elevenlabs") or {}
    if not str(elevenlabs.get("voice_id") or "").strip():
        elevenlabs["voice_id"] = str(
            get_app_setting("ELEVENLABS_VOICE_ID", os.getenv("ELEVENLABS_VOICE_ID", "")) or ""
        ).strip()
    return jsonify({
        "config": config,
        "themes": available_themes(),
        "voices": VOICE_OPTIONS,
        "providers": {"edge": "Microsoft Edge 무료", "elevenlabs": "ElevenLabs"},
        "elevenlabs_configured": bool(elevenlabs_key),
    })


def licensed_bgm_payload(asset: dict[str, object]) -> dict[str, object]:
    payload = dict(asset)
    local_path = Path(str(payload.get("local_path") or ""))
    try:
        relative = (BASE_DIR / local_path).resolve().relative_to(BGM_DIR.resolve()).as_posix()
    except ValueError:
        payload["audio_url"] = None
    else:
        payload["audio_url"] = f"/bgm/{relative}"
    return payload


@app.get("/api/healing-longform/music")
@require_auth
def api_healing_longform_music():
    assets = [
        licensed_bgm_payload(asset)
        for asset in licensed_longform_bgm_assets(include_disabled=True)
    ]
    return jsonify({"music": assets})


@app.post("/api/healing-longform/music/import")
@require_auth
def api_import_healing_longform_music():
    if request.content_length and request.content_length > 100 * 1024 * 1024:
        return jsonify({"error": "음원 파일은 100MB 이하여야 합니다."}), 413
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"error": "등록할 음원 파일을 선택해 주세요."}), 400
    suffix = Path(upload.filename).suffix.lower()
    if suffix not in SUPPORTED_BGM_EXTENSIONS:
        return jsonify({"error": "MP3, M4A, AAC, WAV, FLAC, OGG 음원만 등록할 수 있습니다."}), 400

    title = str(request.form.get("title") or Path(upload.filename).stem).strip()
    license_type = str(request.form.get("license_type") or "youtube_standard").strip()
    source_url = str(request.form.get("source_url") or "").strip()
    attribution_text = str(request.form.get("attribution_text") or "").strip()
    mood = str(request.form.get("mood") or "calm").strip()
    APPROVED_BGM_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"licensed-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(4)}{suffix}"
    output = (APPROVED_BGM_DIR / filename).resolve()
    if output.parent != APPROVED_BGM_DIR.resolve():
        return jsonify({"error": "안전하지 않은 파일 경로입니다."}), 400
    upload.save(output)
    try:
        if output.stat().st_size <= 0:
            raise ValueError("비어 있는 음원 파일입니다.")
        asset = register_approved_bgm(
            output,
            title=title,
            license_type=license_type,
            source_url=source_url,
            attribution_text=attribution_text,
            mood=mood,
        )
    except (OSError, ValueError, subprocess.CalledProcessError, json.JSONDecodeError) as exc:
        output.unlink(missing_ok=True)
        return jsonify({"error": str(exc) or "음원을 확인할 수 없습니다."}), 400
    return jsonify({"music": licensed_bgm_payload(asset)}), 201


@app.patch("/api/healing-longform/music/<int:asset_id>")
@require_auth
def api_update_healing_longform_music(asset_id: int):
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload.get("enabled"), bool):
        return jsonify({"error": "enabled 값은 true 또는 false여야 합니다."}), 400
    asset = set_bgm_asset_enabled(asset_id, payload["enabled"])
    if not asset:
        return jsonify({"error": "등록된 승인 음원을 찾을 수 없습니다."}), 404
    return jsonify({"music": licensed_bgm_payload(asset)})


@app.put("/api/healing-longform/config")
@require_auth
def api_update_healing_longform_config():
    payload = request.get_json(silent=True) or {}
    overrides = payload.get("longform") if isinstance(payload.get("longform"), dict) else payload
    config = save_longform_config(config_with_overrides(load_longform_config(), overrides))
    return jsonify({"config": config})


@app.get("/api/healing-longform/elevenlabs-usage")
@require_auth
def api_healing_longform_elevenlabs_usage():
    return jsonify({"usage": elevenlabs_subscription_usage()})


@app.post("/api/healing-longform/script-preview")
@require_auth
def api_healing_longform_script_preview():
    payload = request.get_json(silent=True) or {}
    config = config_with_overrides(load_longform_config(), payload)
    root = config["longform"]
    script = generate_longform_script(
        str(root["script"].get("theme") or "오늘도 애쓴 당신에게"),
        int(root["duration_minutes"]),
        str(root["script"].get("tone") or "calm"),
        root["tts"],
    )
    return jsonify({"script": script})


@app.post("/api/healing-longform/voice-preview")
@require_auth
def api_healing_longform_voice_preview():
    payload = request.get_json(silent=True) or {}
    config = config_with_overrides(load_longform_config(), payload)
    root = config["longform"]
    tts_config = dict(root["tts"])
    preview_voice = str(payload.get("preview_voice") or "").strip()
    provider = str(tts_config.get("provider") or "edge")
    if provider == "elevenlabs" and preview_voice:
        elevenlabs = dict(tts_config.get("elevenlabs") or {})
        saved_voice_ids = {str(item).strip() for item in (elevenlabs.get("saved_voices") or {}).values()}
        if preview_voice not in saved_voice_ids:
            return jsonify({"error": "등록되지 않은 ElevenLabs 음성입니다."}), 400
        elevenlabs["voice_id"] = preview_voice
        tts_config["elevenlabs"] = elevenlabs
    elif provider == "edge" and preview_voice:
        if preview_voice not in VOICE_OPTIONS or preview_voice == "random":
            return jsonify({"error": "지원하지 않는 Edge 음성입니다."}), 400
        tts_config["voice"] = preview_voice
    if str(tts_config.get("provider") or "edge") == "elevenlabs":
        key = str(get_app_setting("ELEVENLABS_API_KEY", os.getenv("ELEVENLABS_API_KEY", "")) or "").strip()
        voice_id = str((tts_config.get("elevenlabs") or {}).get("voice_id") or "").strip()
        if not key:
            return jsonify({"error": "ElevenLabs API Key를 운영 설정에 입력해 주세요."}), 400
        if not voice_id:
            return jsonify({"error": "ElevenLabs Voice ID를 입력해 주세요."}), 400
    text = str(payload.get("text") or "오늘도 많이 애쓰셨습니다. 지금은 천천히 마음을 쉬게 해도 괜찮습니다.")
    voice_label = (
        str((tts_config.get("elevenlabs") or {}).get("voice_id") or "elevenlabs")
        if provider == "elevenlabs"
        else str(tts_config.get("voice") or "edge")
    )
    output, duration = create_longform_voice_preview(text, tts_config, voice_label)
    relative = output.resolve().relative_to(BASE_DIR / "outputs" / "audio").as_posix()
    return jsonify({
        "audio_url": f"/audio/{relative}",
        "duration": duration,
        "voice": (
            (tts_config.get("elevenlabs") or {}).get("voice_id")
            if tts_config.get("provider") == "elevenlabs"
            else tts_config.get("voice")
        ),
        "provider": provider,
    })


@app.post("/api/healing-longform/create")
@require_auth
def api_create_healing_longform():
    payload = request.get_json(silent=True) or {}
    config = config_with_overrides(load_longform_config(), payload)
    if not bool(config["longform"].get("enabled", True)):
        return jsonify({"error": "longform_disabled"}), 409
    tts_config = config["longform"]["tts"]
    if str(tts_config.get("provider") or "edge") == "elevenlabs":
        key = str(get_app_setting("ELEVENLABS_API_KEY", os.getenv("ELEVENLABS_API_KEY", "")) or "").strip()
        voice_id = str((tts_config.get("elevenlabs") or {}).get("voice_id") or "").strip()
        if not key:
            return jsonify({"error": "ElevenLabs API Key를 운영 설정에 입력해 주세요."}), 400
        if not voice_id:
            return jsonify({"error": "ElevenLabs Voice ID를 입력해 주세요."}), 400
    active = next((item for item in list_healing_jobs(10) if item.get("status") in {"pending", "running"}), None)
    if active:
        return jsonify({"error": "longform_already_running", "job": healing_job_payload(active)}), 409
    job_id = create_healing_job(config)
    thread = threading.Thread(target=run_healing_longform_job, args=(job_id, config), daemon=True)
    thread.start()
    return jsonify({"job": healing_job_payload(healing_job(job_id) or {})}), 202


@app.post("/api/healing-longform/sample")
@require_auth
def api_create_healing_longform_sample():
    payload = request.get_json(silent=True) or {}
    config = config_with_overrides(load_longform_config(), payload)
    root = config["longform"]
    if not bool(root.get("enabled", True)):
        return jsonify({"error": "longform_disabled"}), 409
    tts_config = root["tts"]
    if str(tts_config.get("provider") or "edge") == "elevenlabs":
        key = str(get_app_setting("ELEVENLABS_API_KEY", os.getenv("ELEVENLABS_API_KEY", "")) or "").strip()
        voice_id = str((tts_config.get("elevenlabs") or {}).get("voice_id") or "").strip()
        if not key:
            return jsonify({"error": "ElevenLabs API Key를 운영 설정에 입력해 주세요."}), 400
        if not voice_id:
            return jsonify({"error": "ElevenLabs Voice ID를 입력해 주세요."}), 400
    active = next((item for item in list_healing_jobs(10) if item.get("status") in {"pending", "running"}), None)
    if active:
        return jsonify({"error": "longform_already_running", "job": healing_job_payload(active)}), 409
    root["sample_mode"] = True
    root["sample_seconds"] = 25
    job_id = create_healing_job(config, trigger="sample")
    thread = threading.Thread(target=run_healing_longform_job, args=(job_id, config), daemon=True)
    thread.start()
    return jsonify({"job": healing_job_payload(healing_job(job_id) or {})}), 202


@app.post("/api/healing-longform/backgrounds/import")
@require_auth
def api_import_healing_longform_backgrounds():
    payload = request.get_json(silent=True) or {}
    query = str(payload.get("query") or "bright peaceful nature sunlight").strip()[:80]
    theme_category = str(payload.get("theme_category") or "").strip().lower()
    try:
        count = max(1, min(6, int(payload.get("count") or 4)))
    except (TypeError, ValueError):
        count = 4
    collection = "longform-16x9"
    try:
        candidates = search_pexels_videos(query, per_page=count, orientation="landscape")
        assets = []
        errors = []
        for candidate in candidates:
            try:
                candidate["theme_category"] = theme_category
                assets.append(_save_longform_background(candidate))
            except BackgroundAssetError as exc:
                errors.append(str(exc))
    except BackgroundAssetError as exc:
        return jsonify({"error": str(exc)}), 400
    if not assets:
        return jsonify({"error": errors[0] if errors else "가로 배경 검색 결과가 없습니다."}), 400
    return jsonify({
        "collection": collection,
        "count": len(assets),
        "assets": assets,
        "errors": errors,
    })


def _save_longform_background(candidate: dict[str, object]) -> dict[str, object]:
    item = dict(candidate)
    width, height = int(item.get("width") or 0), int(item.get("height") or 0)
    aspect_ratio = width / max(1, height)
    if width <= height or not 1.5 <= aspect_ratio <= 2.0:
        raise BackgroundAssetError("16:9에 가까운 가로 영상만 롱폼 배경으로 저장할 수 있습니다.")
    provider_id = str(item.get("provider_id") or "").strip().removesuffix("-landscape")
    if not provider_id:
        raise BackgroundAssetError("Pexels 영상 ID가 없습니다.")
    theme_category = str(item.pop("theme_category", "") or "").strip().lower()
    if theme_category in {"morning", "comfort", "night", "recovery", "calm"}:
        query = str(item.get("query") or "healing nature").strip()
        if query.startswith("theme:") and " | " in query:
            query = query.split(" | ", 1)[1]
        item["query"] = f"theme:{theme_category} | {query}"
    item["provider_id"] = f"{provider_id}-landscape"
    item["collection"] = "longform-16x9"
    item["orientation"] = "landscape"
    return save_background_asset(item)


@app.post("/api/healing-longform/backgrounds/import-one")
@require_auth
def api_import_one_healing_longform_background():
    payload = request.get_json(silent=True) or {}
    try:
        asset = _save_longform_background(payload)
    except BackgroundAssetError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"asset": asset}), 201


@app.get("/api/healing-longform/backgrounds")
@require_auth
def api_healing_longform_backgrounds():
    assets = list_background_assets_for_collection(
        "longform-16x9",
        landscape_only=True,
        limit=300,
    )
    return jsonify({
        "collection": "longform-16x9",
        "backgrounds": assets,
        "total": len(assets),
        "active": sum(1 for item in assets if int(item.get("enabled") or 0) == 1),
    })


@app.get("/api/healing-longform/jobs")
@require_auth
def api_healing_longform_jobs():
    return jsonify({"jobs": [healing_job_payload(job) for job in list_healing_jobs(50)]})


@app.get("/api/healing-longform/jobs/<int:job_id>")
@require_auth
def api_healing_longform_job(job_id: int):
    job = healing_job(job_id)
    if not job:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"job": healing_job_payload(job)})


@app.delete("/api/videos/<path:filename>")
@require_auth
def api_delete_video(filename: str):
    try:
        path = output_video_path(filename, must_exist=False)
    except FileNotFoundError:
        return jsonify({"error": "invalid_filename"}), 400

    relative_output = path.resolve().relative_to(BASE_DIR).as_posix()
    deleted_file = False
    if path.exists():
        try:
            path.unlink()
            deleted_file = True
        except PermissionError:
            return jsonify({"error": "video_file_in_use"}), 409
    with closing(connect(DB_PATH)) as conn:
        conn.execute(
            """
            UPDATE video_jobs
            SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE output_path = ?
            """,
            ("deleted", "file deleted from dashboard", relative_output),
        )
        conn.execute(
            """
            UPDATE healing_longform_jobs
            SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE output_path = ?
            """,
            ("deleted", "file deleted from dashboard", relative_output),
        )
        conn.execute(
            """
            UPDATE long_video_jobs
            SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE output_path = ?
            """,
            ("deleted", "file deleted from dashboard", relative_output),
        )
        conn.commit()
    return jsonify({"deleted": filename, "file_deleted": deleted_file})


@app.get("/api/videos/<path:filename>/youtube")
@require_auth
def api_video_youtube_metadata(filename: str):
    try:
        metadata = youtube_metadata_for_video(filename)
    except FileNotFoundError:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"metadata": metadata})


@app.get("/api/youtube/status")
@require_auth
def api_youtube_status():
    return jsonify(youtube_config_status())


@app.post("/api/youtube/stats/refresh")
@require_auth
def api_youtube_stats_refresh():
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        limit = 50
    try:
        result = refresh_youtube_upload_stats(max(1, min(limit, 100)))
    except YouTubeUploadError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@app.get("/api/youtube/stats/patterns")
@require_auth
def api_youtube_stats_patterns():
    try:
        limit = int(request.args.get("limit", "10"))
    except ValueError:
        limit = 10
    return jsonify({"patterns": youtube_performance_patterns(max(1, min(limit, 50)))})


@app.get("/youtube/oauth/start")
@require_auth
def youtube_oauth_start():
    redirect_uri = url_for("youtube_oauth_callback", _external=True)
    try:
        authorization_url, state = youtube_authorization_url(redirect_uri)
    except YouTubeUploadError as exc:
        return str(exc), 400
    session["youtube_oauth_state"] = state
    return redirect(authorization_url)


@app.get("/youtube/oauth/callback")
@require_auth
def youtube_oauth_callback():
    expected_state = session.pop("youtube_oauth_state", None)
    actual_state = request.args.get("state")
    if not expected_state or actual_state != expected_state:
        return "YouTube 인증 상태값이 맞지 않습니다. 다시 인증해 주세요.", 400

    redirect_uri = url_for("youtube_oauth_callback", _external=True)
    try:
        save_youtube_token_from_response(redirect_uri, request.url, expected_state)
    except Exception as exc:
        return f"YouTube 인증 실패: {exc}", 400

    return redirect(url_for("index", youtube_auth="ok"))


@app.get("/api/videos/<path:filename>/youtube/upload")
@require_auth
def api_video_youtube_upload_status(filename: str):
    upload = latest_successful_youtube_upload(filename) or active_youtube_upload(filename) or latest_youtube_upload(filename)
    return jsonify({"upload": upload})


@app.post("/api/videos/<path:filename>/youtube/upload")
@require_auth
def api_video_youtube_upload(filename: str):
    try:
        path = output_video_path(filename)
    except FileNotFoundError:
        return jsonify({"error": "not_found"}), 404

    payload = request.get_json() if request.is_json else {}
    try:
        metadata = youtube_metadata_for_video(filename)
    except FileNotFoundError:
        return jsonify({"error": "not_found"}), 404

    metadata = parse_youtube_payload(
        payload,
        {**metadata, "privacy_status": "private", "schedule_publish": False},
    )
    privacy_status = str(metadata["privacy_status"])
    scheduled_publish_at = None
    if metadata.get("schedule_publish"):
        scheduled_publish_at = (
            next_longform_publish_at()
            if is_longform_video(filename)
            else random_scheduled_publish_at()
        )
    if scheduled_publish_at:
        privacy_status = "private"

    successful_upload = latest_successful_youtube_upload(filename)
    if successful_upload:
        return jsonify({"already_uploaded": True, "upload": successful_upload})

    running_upload = active_youtube_upload(filename)
    if running_upload:
        return jsonify({"already_running": True, "upload": running_upload}), 202

    upload_id = create_youtube_upload_row(filename, metadata, privacy_status, scheduled_publish_at)
    thread = threading.Thread(
        target=run_youtube_upload_job,
        args=(upload_id, path, metadata, privacy_status, scheduled_publish_at),
        daemon=True,
    )
    thread.start()
    upload = latest_youtube_upload(filename)
    return jsonify(
        {
            "upload": upload,
            "upload_started": True,
            "scheduled_publish": scheduled_publish_payload(scheduled_publish_at),
        }
    ), 202


@app.get("/api/videos/<path:filename>/youtube/remote")
@require_auth
def api_video_youtube_remote(filename: str):
    upload = latest_successful_youtube_upload(filename)
    if not upload or not upload.get("youtube_video_id"):
        return jsonify({"error": "not_uploaded"}), 404
    try:
        details = get_video_details(str(upload["youtube_video_id"]))
    except YouTubeUploadError as exc:
        return jsonify({"error": str(exc), "upload": upload}), 400
    except Exception as exc:
        return jsonify({"error": str(exc), "upload": upload}), 500
    return jsonify({"details": details, "upload": upload})


@app.put("/api/videos/<path:filename>/youtube/remote")
@require_auth
def api_update_video_youtube_remote(filename: str):
    upload = latest_successful_youtube_upload(filename)
    if not upload or not upload.get("youtube_video_id"):
        return jsonify({"error": "not_uploaded"}), 404

    try:
        remote = get_video_details(str(upload["youtube_video_id"]))
    except YouTubeUploadError as exc:
        return jsonify({"error": str(exc), "upload": upload}), 400
    except Exception as exc:
        return jsonify({"error": str(exc), "upload": upload}), 500

    payload = request.get_json() if request.is_json else {}
    current_defaults = {
        "title": remote.get("title") or upload.get("title") or "",
        "description": remote.get("description") or "",
        "tags": remote.get("tags") or [],
        "privacy_status": remote.get("privacy_status") or upload.get("privacy_status") or "private",
    }
    metadata = parse_youtube_payload(payload, current_defaults)
    try:
        result = update_video_metadata(
            str(upload["youtube_video_id"]),
            str(metadata["title"]),
            str(metadata["description"]),
            list(metadata.get("tags") or []),
            str(metadata["privacy_status"]),
        )
    except YouTubeUploadError as exc:
        return jsonify({"error": str(exc), "upload": upload}), 400
    except Exception as exc:
        return jsonify({"error": str(exc), "upload": upload}), 500

    update_youtube_upload_metadata(
        int(upload["id"]),
        str(result["title"]),
        str(result["privacy_status"]),
    )
    refreshed_upload = latest_successful_youtube_upload(filename)
    return jsonify({"details": result, "upload": refreshed_upload})


@app.get("/api/backgrounds/search")
@require_auth
def api_search_backgrounds():
    query = request.args.get("q", "calm library")
    orientation = "landscape" if request.args.get("orientation") == "landscape" else "portrait"
    try:
        per_page = int(request.args.get("per_page", "8"))
    except ValueError:
        per_page = 8
    try:
        results = search_pexels_videos(query, per_page, orientation=orientation)
    except BackgroundAssetError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"results": results})


@app.post("/api/backgrounds/download")
@require_auth
def api_download_background():
    if not request.is_json:
        return jsonify({"error": "json_required"}), 400
    try:
        asset = save_background_asset(request.get_json() or {})
    except BackgroundAssetError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"asset": asset})


@app.get("/api/backgrounds")
@require_auth
def api_backgrounds():
    active_only = request.args.get("active_only") == "1"
    try:
        limit = int(request.args.get("limit", str(ACTIVE_BACKGROUND_LIMIT)))
    except ValueError:
        limit = ACTIVE_BACKGROUND_LIMIT
    backgrounds = [
        item for item in list_background_assets(limit=limit, active_only=active_only)
        if str(item.get("collection") or "") != "longform-16x9"
    ]
    return jsonify({"backgrounds": backgrounds})


@app.get("/api/backgrounds/collections")
@require_auth
def api_background_collections():
    collections = [
        item for item in list_background_collections()
        if str(item.get("collection") or "") != "longform-16x9"
    ]
    return jsonify({"collections": collections})


@app.post("/api/backgrounds/collections/activate")
@require_auth
def api_activate_background_collection():
    payload = request.get_json() if request.is_json else {}
    try:
        result = activate_background_collection(str(payload.get("collection") or ""))
    except BackgroundAssetError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(result)


@app.patch("/api/backgrounds/<int:asset_id>")
@require_auth
def api_update_background(asset_id: int):
    payload = request.get_json() if request.is_json else {}
    enabled = payload.get("enabled")
    collection = payload.get("collection") if "collection" in payload else None
    if enabled is not None:
        enabled = bool(enabled)
    asset = update_background_asset(asset_id, enabled=enabled, collection=collection)
    if not asset:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"asset": asset})


@app.get("/videos/<path:filename>")
@require_auth
def serve_video(filename: str):
    return send_from_directory(OUTPUT_DIR, filename)


@app.get("/backgrounds/<path:filename>")
@require_auth
def serve_background(filename: str):
    return send_from_directory(BACKGROUND_DIR, filename)


@app.get("/audio/<path:filename>")
@require_auth
def serve_audio(filename: str):
    return send_from_directory(BASE_DIR / "outputs" / "audio", filename)


@app.get("/bgm/<path:filename>")
@require_auth
def serve_bgm(filename: str):
    return send_from_directory(BGM_DIR, filename)


if __name__ == "__main__":
    init_db(DB_PATH)
    reconcile_missing_output_jobs()
    mark_stale_short_video_jobs_failed()
    mark_interrupted_youtube_uploads()
    mark_interrupted_healing_jobs()
    start_youtube_stats_loop()
    start_longform_scheduler()
    port = int(os.getenv("DASHBOARD_PORT", "8050"))
    host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=False)
