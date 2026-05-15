from __future__ import annotations

import logging
import os
import random
from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from backgrounds import ACTIVE_BACKGROUND_LIMIT, list_background_assets
from db import BASE_DIR, DB_PATH, connect, get_app_setting, init_db
from render_video import OUTPUT_DIR, render_video
from saramro_quotes import import_saramro_quotes
from tts import DEFAULT_RATE, DEFAULT_VOICE
from video_script import generate_video_script
from youtube_metadata_ai import generate_tags, generate_title
from youtube_upload import sanitize_youtube_metadata, upload_video


KST = ZoneInfo("Asia/Seoul")
BRAND_NAME = "지혜로운 조각들"
DEFAULT_SOURCE = "글반장,사람로"
DEFAULT_SCHEDULE_WINDOWS = "07:30-09:00,12:00-13:30,18:00-23:00"


def setting(name: str, default: str) -> str:
    return get_app_setting(name, os.getenv(name, default)).strip()


def setting_bool(name: str, default: str = "0") -> bool:
    return setting(name, default).lower() not in {"0", "false", "no", "off", ""}


def setting_int(name: str, default: int, minimum: int = 0, maximum: int | None = None) -> int:
    try:
        value = int(setting(name, str(default)))
    except (TypeError, ValueError):
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(value, maximum)
    return value


def parse_time(value: str) -> time:
    hour, minute = value.strip().split(":", 1)
    return time(int(hour), int(minute))


def schedule_windows() -> list[tuple[time, time]]:
    raw = setting("AUTO_UPLOAD_SCHEDULE_WINDOWS", DEFAULT_SCHEDULE_WINDOWS)
    windows: list[tuple[time, time]] = []
    for item in raw.split(","):
        if "-" not in item:
            continue
        start_text, end_text = item.split("-", 1)
        try:
            start_at = parse_time(start_text)
            end_at = parse_time(end_text)
        except (TypeError, ValueError):
            logging.warning("Invalid AUTO_UPLOAD_SCHEDULE_WINDOWS item: %s", item)
            continue
        if start_at < end_at:
            windows.append((start_at, end_at))
    return windows or [(time(18, 0), time(23, 0))]


def next_peak_publish_at(now: datetime | None = None) -> datetime:
    now_kst = (now or datetime.now(KST)).astimezone(KST)
    minimum = now_kst + timedelta(
        minutes=setting_int("AUTO_UPLOAD_MIN_LEAD_MINUTES", 30, minimum=5, maximum=240)
    )
    windows = schedule_windows()

    for day_offset in range(14):
        candidate_date = now_kst.date() + timedelta(days=day_offset)
        candidates: list[tuple[datetime, datetime]] = []
        for start_time, end_time in windows:
            start_at = datetime.combine(candidate_date, start_time, tzinfo=KST)
            end_at = datetime.combine(candidate_date, end_time, tzinfo=KST)
            start_at = max(start_at, minimum)
            if start_at < end_at:
                candidates.append((start_at, end_at))
        if candidates:
            start_at, end_at = random.choice(candidates)
            seconds = max(1, int((end_at - start_at).total_seconds()))
            return start_at + timedelta(seconds=random.randint(0, seconds - 1))

    start_time, end_time = random.choice(windows)
    start_at = datetime.combine(now_kst.date() + timedelta(days=14), start_time, tzinfo=KST)
    end_at = datetime.combine(now_kst.date() + timedelta(days=14), end_time, tzinfo=KST)
    seconds = max(1, int((end_at - start_at).total_seconds()))
    return start_at + timedelta(seconds=random.randint(0, seconds - 1))


def youtube_title(script: dict[str, object], quote_text: str = "") -> str:
    suffix = f" | {BRAND_NAME}"
    source_text = quote_text or " ".join(str(item) for item in script.get("narration", []))
    title = generate_title(source_text or str(script.get("title") or "오늘의 문장")).strip()
    max_title_len = 100 - len(suffix)
    if len(title) > max_title_len:
        title = title[: max_title_len - 3].rstrip() + "..."
    return f"{title}{suffix}"


def youtube_metadata_for_log(log: dict[str, object], filename: str) -> dict[str, object]:
    script = generate_video_script(str(log["content"]))
    body_text = "\n".join(str(item) for item in script.get("narration", []))
    source = str(script.get("source") or "").strip()
    description_parts = [
        BRAND_NAME,
        "",
        body_text,
    ]
    if source:
        description_parts.extend(["", f"글 출처: {source}"])
    description_parts.extend(
        [
            "",
            "천천히 읽고 마음에 담는 문장을 전합니다.",
            "",
            "#지혜로운조각들 #좋은글 #명언 #짧은글 #인생문장 #쇼츠",
        ]
    )
    title, description, tags = sanitize_youtube_metadata(
        youtube_title(script, body_text),
        "\n".join(description_parts).strip(),
        [BRAND_NAME, "좋은글", "명언", "짧은글", "인생문장", "마음글", "쇼츠"],
    )
    title, description, tags = sanitize_youtube_metadata(
        title,
        description,
        generate_tags(body_text, title),
    )
    return {
        "filename": filename,
        "log_id": log["log_id"],
        "title": title,
        "description": description,
        "tags": tags,
    }


def scheduled_publish_payload(publish_at: datetime | None) -> str | None:
    if not publish_at:
        return None
    return publish_at.astimezone(KST).isoformat(timespec="seconds")


def auto_upload_sources() -> list[str]:
    raw = setting("AUTO_UPLOAD_SOURCE", DEFAULT_SOURCE) or DEFAULT_SOURCE
    sources = [item.strip() for item in raw.split(",") if item.strip()]
    return sources or ["글반장"]


def daily_upload_count(now: datetime | None = None) -> int:
    now_kst = (now or datetime.now(KST)).astimezone(KST)
    day_start = datetime.combine(now_kst.date(), time.min, tzinfo=KST)
    day_end = day_start + timedelta(days=1)
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM auto_upload_jobs
            WHERE status IN ('pending', 'rendering', 'uploading', 'uploaded')
              AND datetime(created_at) >= datetime(?)
              AND datetime(created_at) < datetime(?)
            """,
            (
                day_start.astimezone(timezone.utc).isoformat(),
                day_end.astimezone(timezone.utc).isoformat(),
            ),
        ).fetchone()
    return int(row["count"] if row else 0)


def remaining_daily_slots() -> int:
    daily_limit = setting_int("AUTO_UPLOAD_DAILY_LIMIT", 10, minimum=1, maximum=50)
    return max(0, daily_limit - daily_upload_count())


def quota_pause_key(now: datetime | None = None) -> str:
    now_kst = (now or datetime.now(KST)).astimezone(KST)
    return f"auto_upload_youtube_quota_paused:{now_kst.date().isoformat()}"


def youtube_quota_paused() -> bool:
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = ?",
            (quota_pause_key(),),
        ).fetchone()
    return bool(row and row["value"] == "1")


def mark_youtube_quota_paused() -> None:
    with closing(connect(DB_PATH)) as conn:
        conn.execute(
            """
            INSERT INTO app_state (key, value, updated_at)
            VALUES (?, '1', CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (quota_pause_key(),),
        )
        conn.commit()


def is_youtube_quota_error(error: Exception | str) -> bool:
    message = str(error).lower()
    return "quotaexceeded" in message or "exceeded your" in message and "quota" in message


def update_auto_job(job_id: int, **values: object) -> None:
    allowed = {
        "status",
        "video_job_id",
        "youtube_upload_id",
        "output_path",
        "error",
        "scheduled_publish_at",
    }
    fields = [key for key in values if key in allowed]
    if not fields:
        return
    assignments = ", ".join(f"{field} = ?" for field in fields)
    params = [values[field] for field in fields]
    with closing(connect(DB_PATH)) as conn:
        conn.execute(
            f"""
            UPDATE auto_upload_jobs
            SET {assignments}, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (*params, job_id),
        )
        conn.commit()


def auto_upload_start_at(conn) -> str:
    if setting_bool("AUTO_UPLOAD_INCLUDE_EXISTING", "0"):
        return "1970-01-01T00:00:00+00:00"

    row = conn.execute(
        "SELECT value FROM app_state WHERE key = 'auto_upload_start_at'"
    ).fetchone()
    if row and row["value"]:
        return str(row["value"])

    start_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO app_state (key, value, updated_at)
        VALUES ('auto_upload_start_at', ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE SET
            value = excluded.value,
            updated_at = CURRENT_TIMESTAMP
        """,
        (start_at,),
    )
    return start_at


def claim_pending_jobs(limit: int) -> list[dict[str, object]]:
    init_db(DB_PATH)
    sources = auto_upload_sources()
    retry_failed = setting_bool("AUTO_UPLOAD_RETRY_FAILED", "0")
    min_length = setting_int("AUTO_UPLOAD_MIN_CONTENT_LENGTH", 10, minimum=0, maximum=1000)
    failed_filter = "" if retry_failed else "AND COALESCE(auj.status, '') != 'failed'"
    with closing(connect(DB_PATH)) as conn:
        start_at = auto_upload_start_at(conn)
        placeholders = ", ".join("?" for _ in sources)
        rows = conn.execute(
            f"""
            SELECT tl.id AS log_id, tl.source, tl.content, tl.created_at,
                   auj.id AS auto_job_id, auj.status AS auto_status
            FROM telegram_logs tl
            LEFT JOIN auto_upload_jobs auj ON auj.log_id = tl.id
            WHERE tl.source IN ({placeholders})
              AND datetime(tl.saved_at) >= datetime(?)
              AND LENGTH(TRIM(tl.content)) >= ?
              AND COALESCE(auj.status, '') NOT IN ('rendering', 'uploading', 'uploaded')
              {failed_filter}
              AND NOT EXISTS (
                  SELECT 1
                  FROM youtube_uploads yu
                  WHERE yu.log_id = tl.id
                    AND yu.status IN ('uploading', 'uploaded')
              )
              AND NOT EXISTS (
                  SELECT 1
                  FROM video_jobs vj
                  WHERE vj.log_id = tl.id
                    AND vj.status = 'rendering'
              )
            ORDER BY tl.created_at ASC, tl.id ASC
            LIMIT ?
            """,
            (*sources, start_at, min_length, max(1, limit)),
        ).fetchall()
        claimed: list[dict[str, object]] = []
        for row in rows:
            item = dict(row)
            if item.get("auto_job_id"):
                conn.execute(
                    """
                    UPDATE auto_upload_jobs
                    SET status = 'pending', error = NULL, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (item["auto_job_id"],),
                )
                job_id = int(item["auto_job_id"])
            else:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO auto_upload_jobs (log_id, status)
                    VALUES (?, 'pending')
                    """,
                    (item["log_id"],),
                )
                job_id = int(cursor.lastrowid)
                if not job_id:
                    existing = conn.execute(
                        "SELECT id FROM auto_upload_jobs WHERE log_id = ?",
                        (item["log_id"],),
                    ).fetchone()
                    if not existing:
                        continue
                    job_id = int(existing["id"])
            item["auto_job_id"] = job_id
            claimed.append(item)
        conn.commit()
    return claimed


def latest_ready_video(log_id: int) -> dict[str, object] | None:
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT id, output_path, title
            FROM video_jobs
            WHERE log_id = ?
              AND status = 'ready'
              AND output_path IS NOT NULL
            ORDER BY id DESC
            LIMIT 1
            """,
            (log_id,),
        ).fetchone()
    return dict(row) if row else None


def create_video_job(log_id: int, background_asset_id: int | None) -> int:
    with closing(connect(DB_PATH)) as conn:
        cursor = conn.execute(
            """
            INSERT INTO video_jobs (log_id, background_asset_id, status, stage, progress)
            VALUES (?, ?, 'rendering', '대기 중', 0)
            """,
            (log_id, background_asset_id),
        )
        conn.commit()
        return int(cursor.lastrowid)


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


def mark_video_ready(job_id: int, output: Path, script: dict[str, object]) -> None:
    relative_output = output.resolve().relative_to(BASE_DIR).as_posix()
    with closing(connect(DB_PATH)) as conn:
        conn.execute(
            """
            UPDATE video_jobs
            SET status = 'ready', stage = '완료', progress = 100, output_path = ?,
                title = ?, bgm_asset_id = ?, error = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (relative_output, str(script["title"]), script.get("bgm_asset_id"), job_id),
        )
        conn.commit()


def mark_video_failed(job_id: int, error: str) -> None:
    with closing(connect(DB_PATH)) as conn:
        conn.execute(
            """
            UPDATE video_jobs
            SET status = 'failed', stage = '실패', error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (error, job_id),
        )
        conn.commit()


def create_youtube_upload_row(
    filename: str,
    video_job_id: int | None,
    log_id: int,
    title: str,
    privacy_status: str,
) -> int:
    with closing(connect(DB_PATH)) as conn:
        cursor = conn.execute(
            """
            INSERT INTO youtube_uploads
                (filename, video_job_id, log_id, title, privacy_status, status)
            VALUES (?, ?, ?, ?, ?, 'uploading')
            """,
            (filename, video_job_id, log_id, title, privacy_status),
        )
        conn.commit()
        return int(cursor.lastrowid)


def update_youtube_upload_success(
    upload_id: int,
    youtube_video_id: str,
    youtube_url: str,
    publish_at: datetime,
) -> None:
    with closing(connect(DB_PATH)) as conn:
        conn.execute(
            """
            UPDATE youtube_uploads
            SET status = 'uploaded', youtube_video_id = ?, youtube_url = ?, error = NULL,
                scheduled_publish_at = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (youtube_video_id, youtube_url, scheduled_publish_payload(publish_at), upload_id),
        )
        conn.commit()


def update_youtube_upload_failure(upload_id: int, error: str) -> None:
    with closing(connect(DB_PATH)) as conn:
        conn.execute(
            """
            UPDATE youtube_uploads
            SET status = 'failed', error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (error, upload_id),
        )
        conn.commit()


def pick_background_asset_id() -> int | None:
    backgrounds = list_background_assets(limit=ACTIVE_BACKGROUND_LIMIT, active_only=True)
    if not backgrounds:
        return None
    return int(random.choice(backgrounds)["id"])


def render_or_reuse_video(auto_job: dict[str, object]) -> tuple[int | None, Path]:
    log_id = int(auto_job["log_id"])
    ready = latest_ready_video(log_id)
    if ready:
        output_path = BASE_DIR / str(ready["output_path"])
        if output_path.exists():
            return int(ready["id"]), output_path

    background_asset_id = pick_background_asset_id()
    video_job_id = create_video_job(log_id, background_asset_id)
    update_auto_job(int(auto_job["auto_job_id"]), status="rendering", video_job_id=video_job_id)
    try:
        output, script = render_video(
            log_id,
            background_asset_id,
            True,
            DEFAULT_VOICE,
            DEFAULT_RATE,
            lambda stage, progress: update_video_job_progress(video_job_id, stage, progress),
        )
    except Exception as exc:
        mark_video_failed(video_job_id, str(exc))
        raise
    mark_video_ready(video_job_id, output, script)
    return video_job_id, output


def process_auto_upload_job(auto_job: dict[str, object]) -> None:
    auto_job_id = int(auto_job["auto_job_id"])
    log_id = int(auto_job["log_id"])
    try:
        video_job_id, output = render_or_reuse_video(auto_job)
        relative_output = output.resolve().relative_to(BASE_DIR).as_posix()
        update_auto_job(auto_job_id, status="uploading", output_path=relative_output)

        filename = output.name
        metadata = youtube_metadata_for_log(auto_job, filename)
        publish_at = next_peak_publish_at()
        privacy_status = "private"
        configured_privacy = setting("AUTO_UPLOAD_PRIVACY_STATUS", "private")
        if configured_privacy in {"private", "unlisted", "public"} and not publish_at:
            privacy_status = configured_privacy

        upload_id = create_youtube_upload_row(
            filename,
            video_job_id,
            log_id,
            str(metadata["title"]),
            privacy_status,
        )
        update_auto_job(auto_job_id, youtube_upload_id=upload_id, scheduled_publish_at=scheduled_publish_payload(publish_at))
        try:
            result = upload_video(
                output,
                str(metadata["title"]),
                str(metadata["description"]),
                list(metadata.get("tags") or []),
                privacy_status,
                publish_at=publish_at,
            )
        except Exception as exc:
            update_youtube_upload_failure(upload_id, str(exc))
            if is_youtube_quota_error(exc):
                mark_youtube_quota_paused()
            raise

        update_youtube_upload_success(
            upload_id,
            str(result["youtube_video_id"]),
            str(result["youtube_url"]),
            publish_at,
        )
        update_auto_job(auto_job_id, status="uploaded", error=None)
        logging.info(
            "Auto-uploaded log_id=%s filename=%s publish_at=%s",
            log_id,
            filename,
            scheduled_publish_payload(publish_at),
        )
    except Exception as exc:
        logging.exception("Auto upload failed log_id=%s", log_id)
        update_auto_job(auto_job_id, status="failed", error=str(exc)[:4000])


def run_auto_upload_once() -> int:
    if not setting_bool("AUTO_UPLOAD_ENABLED", "1"):
        return 0
    if youtube_quota_paused():
        logging.info("Auto upload paused because YouTube quota is exhausted today")
        return 0
    remaining = remaining_daily_slots()
    if remaining <= 0:
        logging.info("Auto upload daily limit reached")
        return 0
    if setting_bool("SARAMRO_QUOTES_ENABLED", "0") and "사람로" in auto_upload_sources():
        import_saramro_quotes(
            limit=min(remaining, setting_int("SARAMRO_QUOTES_IMPORT_LIMIT", 10, minimum=1, maximum=50)),
            max_pages=setting_int("SARAMRO_QUOTES_MAX_PAGES", 5, minimum=1, maximum=50),
        )
    limit = min(remaining, setting_int("AUTO_UPLOAD_MAX_PER_RUN", 1, minimum=1, maximum=10))
    jobs = claim_pending_jobs(limit)
    for job in jobs:
        process_auto_upload_job(job)
    return len(jobs)
