from __future__ import annotations

import secrets
import os
import re
import threading
from contextlib import closing
from functools import wraps
from pathlib import Path

from flask import Flask, jsonify, redirect, render_template, request, send_from_directory, session, url_for

from backup import cleanup_backups, create_backup, list_backup_files, storage_summary
from backgrounds import (
    BACKGROUND_DIR,
    BackgroundAssetError,
    get_background_asset_by_id,
    list_background_assets,
    save_background_asset,
    search_pexels_videos,
)
from db import BASE_DIR, DB_PATH, connect, init_db
from render_video import OUTPUT_DIR, render_video
from tts import DEFAULT_RATE, DEFAULT_VOICE, RATE_OPTIONS, create_preview_audio
from video_script import generate_video_script
from youtube_upload import (
    YouTubeUploadError,
    get_video_details,
    save_youtube_token_from_response,
    update_video_metadata,
    upload_video,
    youtube_authorization_url,
    youtube_config_status,
)


app = Flask(__name__)
app.secret_key = os.getenv("DASHBOARD_SECRET_KEY") or secrets.token_hex(32)
VIDEO_FILENAME_PATTERN = re.compile(r"wisdom-library-(?P<log_id>\d+)-(?P<stamp>\d{8}-\d{6})\.mp4$")
BRAND_NAME = "지혜로운 조각들"


def dashboard_password() -> str | None:
    return os.getenv("DASHBOARD_PASSWORD")


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
    has_image = request.args.get("has_image", "").strip()
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
    if has_image == "1":
        where.append("media_kind = ?")
        params.append("image")

    sql = """
        SELECT id, source, msg_id, content, created_at, saved_at,
               media_path, media_kind, group_key
        FROM telegram_logs
    """
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
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
                "media_path": None,
                "media_kind": None,
                "media_paths": [],
                "media_count": 0,
            }
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

        media_path = row.get("media_path")
        if row.get("media_kind") == "image" and media_path:
            media_paths = item["media_paths"]
            if media_path not in media_paths:
                media_paths.append(media_path)
            item["media_path"] = item["media_path"] or media_path
            item["media_kind"] = "image"
            item["media_count"] = len(media_paths)

        if str(row["created_at"]) > str(item["created_at"]):
            item["created_at"] = row["created_at"]
        if str(row["saved_at"]) > str(item["saved_at"]):
            item["saved_at"] = row["saved_at"]

    return [grouped[key] for key in ordered_keys[:limit]]


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
    return render_template(
        "dashboard.html",
        db_path=DB_PATH,
        sources=sources,
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


@app.get("/api/stats")
@require_auth
def api_stats():
    init_db(DB_PATH)
    image_count_sql = """
        SELECT COUNT(*) AS count
        FROM telegram_logs
        WHERE media_kind = 'image'
    """
    with closing(connect(DB_PATH)) as conn:
        total = conn.execute("SELECT COUNT(*) AS count FROM telegram_logs").fetchone()
        image_total = conn.execute(image_count_sql).fetchone()
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
            "image_total": image_total["count"] if image_total else 0,
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
    if row["source"] != "글반장":
        return jsonify({"error": "unsupported_source"}), 400
    if not row["content"].strip():
        return jsonify({"error": "empty_content"}), 400

    script = generate_video_script(row["content"])
    return jsonify(
        {
            "id": row["id"],
            "source": row["source"],
            "created_at": row["created_at"],
            "script": script,
        }
    )


def latest_video_for_log(log_id: int) -> dict[str, object] | None:
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT id, log_id, background_asset_id, status, output_path, title,
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


def generated_video_payload(path: Path, job: dict[str, object] | None = None) -> dict[str, object]:
    stat = path.stat()
    match = VIDEO_FILENAME_PATTERN.match(path.name)
    log_id = int(match.group("log_id")) if match else None
    payload: dict[str, object] = {
        "filename": path.name,
        "video_url": f"/videos/{path.name}",
        "size": stat.st_size,
        "modified_at": stat.st_mtime,
        "log_id": log_id,
        "source": "file",
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
    return payload


def list_generated_videos(limit: int = 20) -> list[dict[str, object]]:
    init_db(DB_PATH)
    jobs_by_name: dict[str, dict[str, object]] = {}
    with closing(connect(DB_PATH)) as conn:
        for row in conn.execute(
            """
            SELECT id, log_id, background_asset_id, status, output_path, title,
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

    videos: list[dict[str, object]] = []
    for path in OUTPUT_DIR.glob("*.mp4"):
        videos.append(generated_video_payload(path, jobs_by_name.get(path.name)))

    videos.sort(key=lambda item: float(item["modified_at"]), reverse=True)
    return videos[: max(1, min(int(limit), 100))]


def video_job_by_filename(filename: str) -> dict[str, object] | None:
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT id, log_id, background_asset_id, status, output_path, title,
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
            SELECT id, log_id, background_asset_id, status, output_path, title,
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
            SELECT id, log_id, background_asset_id, status, output_path, title,
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
                SET status = ?, stage = ?, progress = ?, output_path = ?, title = ?,
                    error = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                ("ready", "완료", 100, relative_output, str(script["title"]), job_id),
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


def youtube_title(script: dict[str, object]) -> str:
    title = str(script.get("title") or "오늘의 문장").strip()
    suffix = f" | {BRAND_NAME}"
    max_title_len = 100 - len(suffix)
    if len(title) > max_title_len:
        title = title[: max_title_len - 3].rstrip() + "..."
    return f"{title}{suffix}"


def youtube_metadata_for_video(filename: str) -> dict[str, object]:
    init_db(DB_PATH)
    path = (OUTPUT_DIR / filename).resolve()
    if path.parent != OUTPUT_DIR.resolve() or not path.exists():
        raise FileNotFoundError(filename)

    job = video_job_by_filename(filename)
    log = log_row_for_video(filename, job)
    if not log:
        return {
            "filename": filename,
            "title": f"{BRAND_NAME} - 오늘의 문장",
            "description": f"{BRAND_NAME}\n\n천천히 읽는 문장 영상입니다.",
            "tags": [BRAND_NAME, "좋은글", "명언", "짧은글", "인생문장"],
            "hashtags": ["#지혜로운조각들", "#좋은글", "#명언", "#짧은글"],
        }

    script = generate_video_script(str(log["content"]))
    body_text = "\n".join(str(item) for item in script.get("narration", []))
    source = str(script.get("source") or "").strip()
    background_lines: list[str] = []
    if job and job.get("background_asset_id"):
        asset = get_background_asset_by_id(int(job["background_asset_id"]))
        if asset:
            author = asset.get("author") or "Pexels"
            source_url = asset.get("source_url") or ""
            background_lines.append(f"배경 영상: Pexels / {author}")
            if source_url:
                background_lines.append(str(source_url))

    description_parts = [
        f"{BRAND_NAME}",
        "",
        body_text,
    ]
    if source:
        description_parts.extend(["", f"글 출처: {source}"])
    if background_lines:
        description_parts.extend(["", *background_lines])
    description_parts.extend(
        [
            "",
            "천천히 읽고 마음에 남는 문장을 전합니다.",
            "",
            "#지혜로운조각들 #좋은글 #명언 #짧은글 #인생문장",
        ]
    )

    return {
        "filename": filename,
        "log_id": log["id"],
        "title": youtube_title(script),
        "description": "\n".join(description_parts).strip(),
        "tags": [BRAND_NAME, "좋은글", "명언", "짧은글", "인생문장", "마음글"],
        "hashtags": ["#지혜로운조각들", "#좋은글", "#명언", "#짧은글", "#인생문장"],
    }


def latest_youtube_upload(filename: str) -> dict[str, object] | None:
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT id, filename, video_job_id, log_id, youtube_video_id, youtube_url,
                   title, privacy_status, status, error, created_at, updated_at
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
                   title, privacy_status, status, error, created_at, updated_at
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
                   title, privacy_status, status, error, created_at, updated_at
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


def update_youtube_upload_success(upload_id: int, result: dict[str, object]) -> None:
    with closing(connect(DB_PATH)) as conn:
        conn.execute(
            """
            UPDATE youtube_uploads
            SET status = ?, youtube_video_id = ?, youtube_url = ?, error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            ("uploaded", result["youtube_video_id"], result["youtube_url"], upload_id),
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
    metadata["privacy_status"] = str(payload.get("privacy_status") or defaults.get("privacy_status") or "private")
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

    metadata = parse_youtube_payload(payload, {**metadata, "privacy_status": "private"})
    privacy_status = str(metadata["privacy_status"])

    successful_upload = latest_successful_youtube_upload(filename)
    if successful_upload:
        return jsonify({"already_uploaded": True, "upload": successful_upload})

    running_upload = active_youtube_upload(filename)
    if running_upload:
        return jsonify({"error": "upload_already_running", "upload": running_upload}), 409

    upload_id = create_youtube_upload_row(filename, metadata, privacy_status)
    try:
        result = upload_video(
            path,
            str(metadata["title"]),
            str(metadata["description"]),
            list(metadata.get("tags") or []),
            privacy_status,
        )
    except YouTubeUploadError as exc:
        update_youtube_upload_failure(upload_id, str(exc))
        return jsonify({"error": str(exc), "upload_id": upload_id}), 400
    except Exception as exc:
        update_youtube_upload_failure(upload_id, str(exc))
        return jsonify({"error": str(exc), "upload_id": upload_id}), 500

    update_youtube_upload_success(upload_id, result)
    upload = latest_youtube_upload(filename)
    return jsonify({"upload": upload})


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
    return jsonify({"backgrounds": list_background_assets()})


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
    port = int(os.getenv("DASHBOARD_PORT", "8050"))
    host = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    app.run(host=host, port=port, debug=False)
