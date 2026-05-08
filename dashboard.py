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
from backgrounds import (
    BACKGROUND_DIR,
    ACTIVE_BACKGROUND_LIMIT,
    BackgroundAssetError,
    activate_background_collection,
    get_background_asset_by_id,
    list_background_collections,
    list_background_assets,
    save_background_asset,
    search_pexels_videos,
    update_background_asset,
)
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
from render_video import OUTPUT_DIR, render_video
from telegram_sync import catch_up_recent_messages_sync
from tts import DEFAULT_RATE, DEFAULT_VOICE, RATE_OPTIONS, VOICE_OPTIONS, create_preview_audio
from video_pipelines import enabled_sources, pipeline_for_source, pipeline_payload
from video_script import generate_video_script
from youtube_metadata_ai import generate_tags, generate_title
from youtube_upload import (
    YouTubeUploadError,
    get_video_details,
    get_video_statistics,
    sanitize_youtube_metadata,
    save_youtube_token_from_response,
    update_video_metadata,
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
VIDEO_FILENAME_PATTERN = re.compile(r"wisdom-library-(?P<log_id>\d+)-(?P<stamp>\d{8}-\d{6})(?:-audio)?\.mp4$")
BRAND_NAME = "지혜로운 조각들"
MANUAL_SOURCE = "직접입력"
TELEGRAM_REFRESH_LOCK = threading.Lock()
LONG_VIDEO_LOCK = threading.Lock()
LONG_VIDEO_WIDTH = 1920
LONG_VIDEO_HEIGHT = 1080
LONG_VIDEO_TARGET_SECONDS = 600
LONG_VIDEO_MAX_SOURCE_COUNT = 40
YOUTUBE_STATS_INTERVAL_SECONDS = int(os.getenv("YOUTUBE_STATS_INTERVAL_SECONDS", "3600"))
KST = ZoneInfo("Asia/Seoul")
YOUTUBE_SCHEDULE_WINDOWS = (
    (time(12, 0), time(13, 0)),
    (time(18, 0), time(21, 0)),
)
SETTINGS_KEYS = {
    "TELEGRAM_API_ID",
    "TELEGRAM_API_HASH",
    "TELEGRAM_CATCH_UP_LIMIT",
    "TELEGRAM_CATCH_UP_INTERVAL_SECONDS",
    "PEXELS_API_KEY",
    "DASHBOARD_PASSWORD",
    "DASHBOARD_SECRET_KEY",
    "YOUTUBE_CLIENT_SECRET_JSON",
    "YOUTUBE_TOKEN_JSON",
    "TELEGRAM_SESSION_FILE_B64",
    "YOUTUBE_STATS_INTERVAL_SECONDS",
    "VIDEO_BGM_ENABLED",
    "VIDEO_BGM_TTS_VOLUME",
    "VIDEO_BGM_ONLY_VOLUME",
}
SECRET_KEYS = {
    "TELEGRAM_API_HASH",
    "PEXELS_API_KEY",
    "DASHBOARD_PASSWORD",
    "DASHBOARD_SECRET_KEY",
    "YOUTUBE_CLIENT_SECRET_JSON",
    "YOUTUBE_TOKEN_JSON",
    "TELEGRAM_SESSION_FILE_B64",
}
BGM_SETTING_DEFAULTS = {
    "VIDEO_BGM_ENABLED": "1",
    "VIDEO_BGM_TTS_VOLUME": "0.10",
    "VIDEO_BGM_ONLY_VOLUME": "0.14",
}


def set_setting_value(name: str, value: str) -> None:
    if name not in SETTINGS_KEYS:
        raise ValueError(f"Unsupported setting: {name}")
    set_app_setting(name, value, is_secret=name in SECRET_KEYS)


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
    for source in ("글반장", MANUAL_SOURCE):
        if source not in sources:
            sources.append(source)
    return render_template(
        "dashboard.html",
        db_path=DB_PATH,
        sources=sources,
        voice_options=VOICE_OPTIONS,
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
) -> dict[str, object]:
    stat = path.stat()
    match = VIDEO_FILENAME_PATTERN.match(path.name)
    log_id = int(match.group("log_id")) if match else None
    is_long_video = path.name.startswith("long-wisdom-library-")
    inferred_title = inferred_title_for_log(log_id) if not is_long_video else None
    payload: dict[str, object] = {
        "filename": path.name,
        "video_url": f"/videos/{path.name}",
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
    return payload


def list_generated_videos(limit: int = 20) -> list[dict[str, object]]:
    init_db(DB_PATH)
    jobs_by_name: dict[str, dict[str, object]] = {}
    long_jobs_by_name: dict[str, dict[str, object]] = {}
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

    videos: list[dict[str, object]] = []
    for path in OUTPUT_DIR.glob("*.mp4"):
        videos.append(
            generated_video_payload(
                path,
                jobs_by_name.get(path.name),
                long_jobs_by_name.get(path.name),
            )
        )

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
        rows = conn.execute(
            """
            SELECT title
            FROM long_video_jobs
            WHERE title LIKE '지혜로운조각 10분 시리즈%'
            """
        ).fetchall()
    for row in rows:
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
    )
    payload = json.loads(result.stdout)
    return float(payload["format"]["duration"])


def concat_file_line(path: Path) -> str:
    escaped = path.resolve().as_posix().replace("'", "'\\''")
    return f"file '{escaped}'"


def run_long_video_job(job_id: int, count: int) -> None:
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
            exclude_used=True,
        )
        if len(candidates) < 2:
            raise RuntimeError("긴영상으로 합칠 새 업로드 완료 영상이 2개 이상 필요합니다.")
        source_seconds = sum(float(item.get("duration") or 0) for item in candidates)
        if source_seconds < LONG_VIDEO_TARGET_SECONDS:
            raise RuntimeError(
                f"사용 가능한 새 업로드 완료 영상 길이가 10분 미만입니다. 현재 {int(source_seconds // 60)}분 {int(source_seconds % 60)}초입니다."
            )

        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output = OUTPUT_DIR / f"long-wisdom-library-{stamp}.mp4"
        concat_list = OUTPUT_DIR / f"long-wisdom-library-{stamp}.txt"
        filenames = [str(item["filename"]) for item in candidates]
        concat_list.write_text(
            "\n".join(concat_file_line(Path(item["path"])) for item in candidates),
            encoding="utf-8",
        )
        series_number = next_long_video_series_number()
        title = f"지혜로운조각 10분 시리즈 {series_number}"
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
            "-filter_complex",
            (
                "[0:v]split=2[bgsrc][fgsrc];"
                f"[bgsrc]scale={LONG_VIDEO_WIDTH}:{LONG_VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
                f"crop={LONG_VIDEO_WIDTH}:{LONG_VIDEO_HEIGHT},gblur=sigma=32,eq=brightness=-0.08[bg];"
                f"[fgsrc]scale=-2:{LONG_VIDEO_HEIGHT}[fg];"
                "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
            ),
            "-map",
            "[v]",
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            str(output),
        ]
        subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
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


def youtube_metadata_for_video(filename: str) -> dict[str, object]:
    init_db(DB_PATH)
    path = (OUTPUT_DIR / filename).resolve()
    if path.parent != OUTPUT_DIR.resolve() or not path.exists():
        raise FileNotFoundError(filename)

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
    now_kst = (now or datetime.now(KST)).astimezone(KST)
    search_date = now_kst.date()
    minimum = now_kst + timedelta(minutes=20)

    for day_offset in range(14):
        candidate_date = search_date + timedelta(days=day_offset)
        if candidate_date.weekday() >= 5:
            continue

        windows: list[tuple[datetime, datetime]] = []
        for start_time, end_time in YOUTUBE_SCHEDULE_WINDOWS:
            start_at = datetime.combine(candidate_date, start_time, tzinfo=KST)
            end_at = datetime.combine(candidate_date, end_time, tzinfo=KST)
            start_at = max(start_at, minimum)
            if start_at < end_at:
                windows.append((start_at, end_at))

        if not windows:
            continue

        start_at, end_at = random.choice(windows)
        seconds = max(1, int((end_at - start_at).total_seconds()))
        return start_at + timedelta(seconds=random.randint(0, seconds - 1))

    next_weekday = search_date + timedelta(days=1)
    while next_weekday.weekday() >= 5:
        next_weekday += timedelta(days=1)
    start_time, end_time = random.choice(YOUTUBE_SCHEDULE_WINDOWS)
    start_at = datetime.combine(next_weekday, start_time, tzinfo=KST)
    end_at = datetime.combine(next_weekday, end_time, tzinfo=KST)
    seconds = int((end_at - start_at).total_seconds())
    return start_at + timedelta(seconds=random.randint(0, seconds - 1))


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
) -> int:
    job = video_job_by_filename(filename)
    with closing(connect(DB_PATH)) as conn:
        cursor = conn.execute(
            """
            INSERT INTO youtube_uploads
                (filename, video_job_id, log_id, title, privacy_status, status)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                filename,
                job.get("id") if job else None,
                metadata.get("log_id"),
                metadata.get("title"),
                privacy_status,
                "uploading",
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
        result = upload_video(
            path,
            str(metadata["title"]),
            str(metadata["description"]),
            list(metadata.get("tags") or []),
            privacy_status,
            publish_at=scheduled_publish_at,
        )
    except Exception as exc:
        update_youtube_upload_failure(upload_id, str(exc))
        return
    update_youtube_upload_success(upload_id, result, scheduled_publish_at)


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


def refresh_youtube_upload_stats(limit: int = 50) -> dict[str, object]:
    init_db(DB_PATH)
    with closing(connect(DB_PATH)) as conn:
        rows = conn.execute(
            """
            SELECT id, youtube_video_id
            FROM youtube_uploads
            WHERE status = ?
              AND youtube_video_id IS NOT NULL
              AND view_count IS NULL
              AND datetime(created_at) <= datetime('now', '-24 hours')
              AND datetime(created_at) >= datetime('now', '-72 hours')
            ORDER BY created_at ASC
            LIMIT ?
            """,
            ("uploaded", limit),
        ).fetchall()

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
    return {"checked": len(rows), "updated": updated, "errors": errors}


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
        backgrounds = list_background_assets(limit=ACTIVE_BACKGROUND_LIMIT, active_only=True)
        if backgrounds:
            background_asset_id = int(random.choice(backgrounds)["id"])

    with closing(connect(DB_PATH)) as conn:
        cursor = conn.execute(
            """
            INSERT INTO video_jobs (log_id, background_asset_id, status, stage, progress)
            VALUES (?, ?, ?, ?, ?)
            """,
            (log_id, background_asset_id, "rendering", "대기 중", 0),
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
        output, duration, used_voice = create_preview_audio(text, voice, rate)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500
    return jsonify(
        {
            "audio_url": f"/audio/{output.resolve().relative_to(BASE_DIR).as_posix().removeprefix('outputs/audio/')}",
            "duration": duration,
            "voice": used_voice,
            "requested_voice": voice,
            "rate": rate,
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
    return jsonify({"videos": list_generated_videos(limit)})


@app.get("/api/long-videos/candidates")
@require_auth
def api_long_video_candidates():
    try:
        limit = int(request.args.get("limit", str(LONG_VIDEO_MAX_SOURCE_COUNT)))
    except ValueError:
        limit = LONG_VIDEO_MAX_SOURCE_COUNT
    candidates = uploaded_video_candidates(
        limit,
        target_seconds=LONG_VIDEO_TARGET_SECONDS,
        exclude_used=True,
    )
    total_seconds = sum(float(item.get("duration") or 0) for item in candidates)
    return jsonify(
        {
            "count": len(candidates),
            "duration": total_seconds,
            "target_seconds": LONG_VIDEO_TARGET_SECONDS,
            "ready": total_seconds >= LONG_VIDEO_TARGET_SECONDS,
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

    thread = threading.Thread(target=run_long_video_job, args=(job_id, count), daemon=True)
    thread.start()
    return jsonify({"job": long_video_job_by_id(job_id)}), 202


@app.get("/api/long-videos/jobs/<int:job_id>")
@require_auth
def api_long_video_job(job_id: int):
    job = long_video_job_by_id(job_id)
    if not job:
        return jsonify({"error": "not_found"}), 404
    return jsonify({"job": job})


@app.delete("/api/videos/<path:filename>")
@require_auth
def api_delete_video(filename: str):
    path = (OUTPUT_DIR / filename).resolve()
    if path.parent != OUTPUT_DIR.resolve() or path.suffix.lower() != ".mp4":
        return jsonify({"error": "invalid_filename"}), 400
    if not path.exists():
        return jsonify({"error": "not_found"}), 404

    relative_output = path.resolve().relative_to(BASE_DIR).as_posix()
    path.unlink()
    with closing(connect(DB_PATH)) as conn:
        conn.execute(
            """
            UPDATE video_jobs
            SET status = ?, error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE output_path = ?
            """,
            ("deleted", "file deleted from dashboard", relative_output),
        )
        conn.commit()
    return jsonify({"deleted": filename})


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
    path = (OUTPUT_DIR / filename).resolve()
    if path.parent != OUTPUT_DIR.resolve() or not path.exists():
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
    scheduled_publish_at = random_scheduled_publish_at() if metadata.get("schedule_publish") else None
    if scheduled_publish_at:
        privacy_status = "private"

    successful_upload = latest_successful_youtube_upload(filename)
    if successful_upload:
        return jsonify({"already_uploaded": True, "upload": successful_upload})

    running_upload = active_youtube_upload(filename)
    if running_upload:
        return jsonify({"already_running": True, "upload": running_upload}), 202

    upload_id = create_youtube_upload_row(filename, metadata, privacy_status)
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

    payload = request.get_json() if request.is_json else {}
    current_defaults = {
        "title": upload.get("title") or "",
        "description": "",
        "tags": [],
        "privacy_status": upload.get("privacy_status") or "private",
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
    try:
        per_page = int(request.args.get("per_page", "8"))
    except ValueError:
        per_page = 8
    try:
        results = search_pexels_videos(query, per_page)
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
    return jsonify({"backgrounds": list_background_assets(limit=limit, active_only=active_only)})


@app.get("/api/backgrounds/collections")
@require_auth
def api_background_collections():
    return jsonify({"collections": list_background_collections()})


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


if __name__ == "__main__":
    init_db(DB_PATH)
    mark_interrupted_youtube_uploads()
    start_youtube_stats_loop()
    port = int(os.getenv("DASHBOARD_PORT", "8050"))
    host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=False)
