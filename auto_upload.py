from __future__ import annotations

import csv
import json
import logging
import os
import random
import urllib.error
import urllib.request
from contextlib import closing
from datetime import datetime, time, timedelta, timezone
from pathlib import Path, PureWindowsPath
from zoneinfo import ZoneInfo

from backgrounds import ACTIVE_BACKGROUND_LIMIT, list_background_assets
from cleanup_videos import cleanup_uploaded_videos, reconcile_missing_output_jobs
from db import BASE_DIR, DB_PATH, connect, get_app_setting, init_db
from render_video import OUTPUT_DIR, audit_video_before_upload, random_background_asset_id, render_video
from saramro_quotes import import_saramro_quotes
from tts import DEFAULT_RATE, DEFAULT_VOICE, short_tts_provider
from video_script import generate_video_script, remove_tracking_number_lines
from youtube_metadata_ai import generate_tags, generate_title
from youtube_upload import post_top_level_comment, sanitize_youtube_metadata, upload_video


KST = ZoneInfo("Asia/Seoul")
BRAND_NAME = "지혜로운 조각들"
DEFAULT_SOURCE = "글반장,사람로"
DEFAULT_SCHEDULE_WINDOWS = "07:00-08:00,19:00-20:00"
DEFAULT_SCHEDULE_TIMES = "07:00,07:30,19:00,19:30"
CSV_BACKFILL_SOURCE = "글반장모음"
CSV_BACKFILL_PATH = BASE_DIR / "글반장모음" / "filtered_text.csv"
DEFAULT_YOUTUBE_COMMENT_TEXT = "오늘 가장 마음에 남은 문장은 무엇이었나요?\n댓글로 남겨주시면 다음 영상 주제에 참고하겠습니다."
VIDEO_CLEANUP_STATE_KEY = "video_cleanup_last_run_date"
TTS_AB_TEST_STATE_KEY = "tts_ab_test_assignment_count"
TTS_AB_TEST_VOICES = (
    "ko-KR-HyunsuNeural",
    "ko-KR-HyunsuMultilingualNeural",
)


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


def app_state_value(key: str) -> str | None:
    init_db(DB_PATH)
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
    return str(row["value"]) if row and row["value"] is not None else None


def set_app_state_value(key: str, value: str) -> None:
    init_db(DB_PATH)
    with closing(connect(DB_PATH)) as conn:
        conn.execute(
            """
            INSERT INTO app_state (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (key, value),
        )
        conn.commit()


def next_tts_ab_test_voice() -> str:
    """Allocate alternating voices for a bounded automatic-upload experiment."""
    if not setting_bool("TTS_AB_TEST_ENABLED", "0"):
        return DEFAULT_VOICE

    target_per_voice = setting_int(
        "TTS_AB_TEST_TARGET_PER_VOICE",
        12,
        minimum=1,
        maximum=100,
    )
    total_assignments = target_per_voice * len(TTS_AB_TEST_VOICES)
    init_db(DB_PATH)
    with closing(connect(DB_PATH)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT value FROM app_state WHERE key = ?",
            (TTS_AB_TEST_STATE_KEY,),
        ).fetchone()
        try:
            assignment_index = max(0, int(row["value"])) if row else 0
        except (TypeError, ValueError):
            assignment_index = 0
        if assignment_index >= total_assignments:
            conn.commit()
            return DEFAULT_VOICE

        voice = TTS_AB_TEST_VOICES[assignment_index % len(TTS_AB_TEST_VOICES)]
        conn.execute(
            """
            INSERT INTO app_state (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                updated_at = CURRENT_TIMESTAMP
            """,
            (TTS_AB_TEST_STATE_KEY, str(assignment_index + 1)),
        )
        conn.commit()
    logging.info(
        "TTS A/B assignment %s/%s voice=%s",
        assignment_index + 1,
        total_assignments,
        voice,
    )
    return voice


def cleanup_uploaded_videos_once_per_day() -> None:
    reconciled = reconcile_missing_output_jobs()
    if any(reconciled.values()):
        logging.info("Reconciled missing output files: %s", reconciled)
    if not setting_bool("VIDEO_CLEANUP_ENABLED", "1"):
        return
    today = datetime.now(KST).date().isoformat()
    if app_state_value(VIDEO_CLEANUP_STATE_KEY) == today:
        return
    retention_days = setting_int("VIDEO_CLEANUP_RETENTION_DAYS", 7, minimum=1, maximum=365)
    result = cleanup_uploaded_videos(retention_days, apply=True)
    logging.info(
        "Video cleanup finished retention_days=%s deleted=%s size_gb=%s errors=%s",
        retention_days,
        result.get("deleted_count"),
        result.get("candidate_size_gb"),
        len(result.get("errors") or []),
    )
    set_app_state_value(VIDEO_CLEANUP_STATE_KEY, today)


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


def schedule_times() -> list[time]:
    raw = setting("AUTO_UPLOAD_SCHEDULE_TIMES", DEFAULT_SCHEDULE_TIMES)
    times: list[time] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            times.append(parse_time(item))
        except (TypeError, ValueError):
            logging.warning("Invalid AUTO_UPLOAD_SCHEDULE_TIMES item: %s", item)
    return sorted(set(times)) or [time(18, 0)]


def scheduled_slots_for_date(conn, candidate_date) -> set[str]:
    prefix = candidate_date.isoformat()
    slots: set[str] = set()
    for table in ("auto_upload_jobs", "youtube_uploads"):
        for row in conn.execute(
            f"""
            SELECT scheduled_publish_at
            FROM {table}
            WHERE scheduled_publish_at LIKE ?
              AND COALESCE(status, '') NOT IN ('failed', 'deleted')
            """,
            (f"{prefix}T%",),
        ).fetchall():
            value = str(row["scheduled_publish_at"] or "")
            if len(value) >= 16:
                slots.add(value[:16])
    return slots


def next_peak_publish_at(now: datetime | None = None) -> datetime:
    now_kst = (now or datetime.now(KST)).astimezone(KST)
    minimum = now_kst + timedelta(
        minutes=setting_int("AUTO_UPLOAD_MIN_LEAD_MINUTES", 30, minimum=5, maximum=240)
    )
    slots = schedule_times()

    with closing(connect(DB_PATH)) as conn:
        for day_offset in range(14):
            candidate_date = now_kst.date() + timedelta(days=day_offset)
            used_slots = scheduled_slots_for_date(conn, candidate_date)
            for slot_time in slots:
                candidate = datetime.combine(candidate_date, slot_time, tzinfo=KST)
                if candidate < minimum:
                    continue
                if candidate.isoformat(timespec="minutes")[:16] in used_slots:
                    continue
                return candidate

    return datetime.combine(now_kst.date() + timedelta(days=14), slots[0], tzinfo=KST)


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
        "오늘 마음에 남겨둘 짧은 문장을 전합니다.",
        "",
        body_text,
    ]
    if source:
        description_parts.extend(["", f"글 출처: {source}"])
    description_parts.extend(
        [
            "",
            "천천히 읽고 마음에 담는 문장을 전합니다.",
            "가장 와닿은 문장은 댓글로 남겨주세요.",
            "",
            "#지혜로운조각들 #좋은글 #명언 #인생문장 #마음치유 #쇼츠",
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


def csv_backfill_source() -> str:
    return setting("AUTO_UPLOAD_BACKFILL_SOURCE", CSV_BACKFILL_SOURCE) or CSV_BACKFILL_SOURCE


def import_csv_backfill_quotes(path: Path = CSV_BACKFILL_PATH) -> int:
    if not path.exists():
        logging.warning("CSV backfill file not found: %s", path)
        return 0

    init_db(DB_PATH)
    source = csv_backfill_source()
    inserted = 0
    with closing(connect(DB_PATH)) as conn, path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row_number, row in enumerate(reader, start=1):
            text = remove_tracking_number_lines((row.get("text") or "").strip())
            if not text:
                continue
            try:
                msg_id = int(str(row.get("id") or "").strip())
            except ValueError:
                msg_id = row_number
            created_at = (row.get("date") or "").strip() or datetime.now(timezone.utc).isoformat()
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO telegram_logs (source, msg_id, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (source, msg_id, text, created_at),
            )
            inserted += int(cursor.rowcount or 0)
        conn.commit()
    if inserted:
        logging.info("Imported CSV backfill quotes source=%s inserted=%s", source, inserted)
    return inserted


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
    daily_limit = setting_int("AUTO_UPLOAD_DAILY_LIMIT", 4, minimum=1, maximum=50)
    return max(0, daily_limit - daily_upload_count())


def naver_daily_upload_count(now: datetime | None = None) -> int:
    now_kst = (now or datetime.now(KST)).astimezone(KST)
    day_start = datetime.combine(now_kst.date(), time.min, tzinfo=KST)
    day_end = day_start + timedelta(days=1)
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM naver_uploads
            WHERE status IN ('uploading', 'uploaded')
              AND datetime(created_at) >= datetime(?)
              AND datetime(created_at) < datetime(?)
            """,
            (
                day_start.astimezone(timezone.utc).isoformat(),
                day_end.astimezone(timezone.utc).isoformat(),
            ),
        ).fetchone()
    return int(row["count"] if row else 0)


def naver_schedule_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    now_kst = (now or datetime.now(KST)).astimezone(KST)
    raw = setting("NAVER_CLIP_SCHEDULE_WINDOW", "06:00-09:00")
    try:
        start_raw, end_raw = [part.strip() for part in raw.split("-", 1)]
        start_hour, start_minute = [int(part) for part in start_raw.split(":", 1)]
        end_hour, end_minute = [int(part) for part in end_raw.split(":", 1)]
        start = datetime.combine(now_kst.date(), time(start_hour, start_minute), tzinfo=KST)
        end = datetime.combine(now_kst.date(), time(end_hour, end_minute), tzinfo=KST)
    except Exception:
        logging.warning("Invalid NAVER_CLIP_SCHEDULE_WINDOW=%r. Using 06:00-09:00", raw)
        start = datetime.combine(now_kst.date(), time(6, 0), tzinfo=KST)
        end = datetime.combine(now_kst.date(), time(9, 0), tzinfo=KST)
    if end <= start:
        end += timedelta(days=1)
    return start, end


def naver_next_slot_at(now: datetime | None = None) -> datetime:
    now_kst = (now or datetime.now(KST)).astimezone(KST)
    start, end = naver_schedule_window(now_kst)
    daily_limit = setting_int(
        "NAVER_CLIP_DAILY_LIMIT",
        setting_int("AUTO_UPLOAD_DAILY_LIMIT", 8, minimum=1, maximum=50),
        minimum=1,
        maximum=50,
    )
    completed = naver_daily_upload_count(now_kst)
    slot_index = min(completed, daily_limit - 1)
    slot_seconds = max(1, int((end - start).total_seconds() // daily_limit))
    return start + timedelta(seconds=slot_seconds * slot_index)


def naver_schedule_ready(now: datetime | None = None) -> tuple[bool, str]:
    now_kst = (now or datetime.now(KST)).astimezone(KST)
    start, end = naver_schedule_window(now_kst)
    if now_kst < start:
        return False, f"Naver schedule waiting until {start.isoformat()}"
    if now_kst >= end:
        return False, f"Naver schedule window closed until tomorrow {start + timedelta(days=1)}"
    next_slot = naver_next_slot_at(now_kst)
    if now_kst < next_slot:
        return False, f"Naver schedule waiting for next slot {next_slot.isoformat()}"
    return True, ""


def remaining_naver_daily_slots() -> int:
    default_limit = setting_int("AUTO_UPLOAD_DAILY_LIMIT", 8, minimum=1, maximum=50)
    daily_limit = setting_int("NAVER_CLIP_DAILY_LIMIT", default_limit, minimum=1, maximum=50)
    return max(0, daily_limit - naver_daily_upload_count())


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
        "naver_upload_id",
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


def claim_pending_jobs_from_sources(
    conn,
    sources: list[str],
    start_at: str,
    min_length: int,
    retry_failed: bool,
    limit: int,
) -> list[dict[str, object]]:
    if not sources or limit <= 0:
        return []
    failed_filter = "" if retry_failed else "AND COALESCE(auj.status, '') != 'failed'"
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
    return claimed


def claim_pending_jobs(limit: int) -> list[dict[str, object]]:
    init_db(DB_PATH)
    configured_sources = auto_upload_sources()
    fallback_source = csv_backfill_source()
    primary_sources = [source for source in configured_sources if source != fallback_source]
    retry_failed = setting_bool("AUTO_UPLOAD_RETRY_FAILED", "0")
    min_length = setting_int("AUTO_UPLOAD_MIN_CONTENT_LENGTH", 10, minimum=0, maximum=1000)
    with closing(connect(DB_PATH)) as conn:
        start_at = auto_upload_start_at(conn)
        claimed = claim_pending_jobs_from_sources(
            conn,
            primary_sources,
            start_at,
            min_length,
            retry_failed,
            limit,
        )
        remaining = limit - len(claimed)
        if remaining > 0:
            conn.commit()
            import_csv_backfill_quotes()
            claimed.extend(
                claim_pending_jobs_from_sources(
                    conn,
                    [fallback_source],
                    start_at,
                    min_length,
                    retry_failed,
                    remaining,
                )
            )
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


def create_video_job(log_id: int, background_asset_id: int | None, tts_voice: str) -> int:
    with closing(connect(DB_PATH)) as conn:
        cursor = conn.execute(
            """
            INSERT INTO video_jobs
                (log_id, background_asset_id, tts_voice, tts_rate, status, stage, progress)
            VALUES (?, ?, ?, ?, 'rendering', '대기 중', 0)
            """,
            (log_id, background_asset_id, tts_voice, DEFAULT_RATE),
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
                title = ?, bgm_asset_id = ?, background_asset_ids = ?,
                tts_voice = ?, tts_rate = ?,
                error = NULL, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                relative_output,
                str(script["title"]),
                script.get("bgm_asset_id"),
                json.dumps(script.get("background_asset_ids") or []),
                script.get("tts_voice"),
                script.get("tts_rate") or DEFAULT_RATE,
                job_id,
            ),
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


def update_youtube_upload_comment(upload_id: int, comment_thread_id: str) -> None:
    with closing(connect(DB_PATH)) as conn:
        conn.execute(
            """
            UPDATE youtube_uploads
            SET comment_thread_id = ?, comment_posted_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (comment_thread_id, upload_id),
        )
        conn.commit()


def maybe_post_youtube_comment(upload_id: int, video_id: str) -> None:
    if not setting_bool("YOUTUBE_AUTO_COMMENT_ENABLED", "1"):
        return
    comment_text = setting("YOUTUBE_AUTO_COMMENT_TEXT", DEFAULT_YOUTUBE_COMMENT_TEXT)
    if not comment_text:
        return
    try:
        response = post_top_level_comment(video_id, comment_text)
    except Exception:
        logging.exception("YouTube auto comment failed video_id=%s", video_id)
        return
    comment_thread_id = str(response.get("id") or "")
    if comment_thread_id:
        update_youtube_upload_comment(upload_id, comment_thread_id)
    logging.info("YouTube auto-commented video_id=%s comment_thread_id=%s", video_id, comment_thread_id)


def create_naver_upload_row(
    filename: str,
    video_job_id: int | None,
    log_id: int,
    channel_url: str,
    title: str,
) -> int:
    with closing(connect(DB_PATH)) as conn:
        cursor = conn.execute(
            """
            INSERT INTO naver_uploads
                (filename, video_job_id, log_id, channel_url, title, status)
            VALUES (?, ?, ?, ?, ?, 'uploading')
            """,
            (filename, video_job_id, log_id, channel_url, title),
        )
        conn.commit()
        return int(cursor.lastrowid)


def update_naver_upload_success(upload_id: int, response: str) -> None:
    with closing(connect(DB_PATH)) as conn:
        conn.execute(
            """
            UPDATE naver_uploads
            SET status = 'uploaded', response = ?, error = NULL,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (response[:4000], upload_id),
        )
        conn.commit()


def update_naver_upload_failure(upload_id: int, error: str) -> None:
    with closing(connect(DB_PATH)) as conn:
        conn.execute(
            """
            UPDATE naver_uploads
            SET status = 'failed', error = ?, updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (error[:4000], upload_id),
        )
        conn.commit()


def mark_stale_naver_uploads_failed() -> int:
    stale_minutes = setting_int("NAVER_CLIP_STALE_UPLOAD_MINUTES", 60, minimum=10, maximum=1440)
    with closing(connect(DB_PATH)) as conn:
        rows = conn.execute(
            """
            SELECT id
            FROM naver_uploads
            WHERE status = 'uploading'
              AND updated_at < datetime('now', ?)
            """,
            (f"-{stale_minutes} minutes",),
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        for upload_id in ids:
            conn.execute(
                """
                UPDATE naver_uploads
                SET status = 'failed', error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                ("stale uploading job reset for retry", upload_id),
            )
            conn.execute(
                """
                UPDATE auto_upload_jobs
                SET naver_upload_id = NULL, updated_at = CURRENT_TIMESTAMP
                WHERE naver_upload_id = ?
                """,
                (upload_id,),
            )
        conn.commit()
    if ids:
        logging.warning("Marked stale Naver uploads failed for retry: %s", ids)
    return len(ids)


def mark_stale_short_video_jobs_failed() -> int:
    init_db(DB_PATH)
    stale_minutes = setting_int("SHORT_VIDEO_STALE_RENDER_MINUTES", 60, minimum=15, maximum=1440)
    with closing(connect(DB_PATH)) as conn:
        rows = conn.execute(
            """
            SELECT id
            FROM video_jobs
            WHERE status IN ('pending', 'rendering', 'running')
              AND updated_at < datetime('now', ?)
            """,
            (f"-{stale_minutes} minutes",),
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        if ids:
            placeholders = ", ".join("?" for _ in ids)
            error = f"rendering stopped for more than {stale_minutes} minutes"
            conn.execute(
                f"""
                UPDATE video_jobs
                SET status = 'failed', stage = '실패', error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id IN ({placeholders})
                """,
                (error, *ids),
            )
            conn.execute(
                f"""
                UPDATE auto_upload_jobs
                SET status = 'failed', error = ?, updated_at = CURRENT_TIMESTAMP
                WHERE video_job_id IN ({placeholders})
                  AND status IN ('pending', 'rendering')
                """,
                (error, *ids),
            )
        conn.commit()
    if ids:
        logging.warning("Marked stale short video jobs failed: %s", ids)
    return len(ids)


def naver_clip_video_path(video_path: Path) -> str:
    resolved = video_path.resolve()
    host_root = setting("NAVER_CLIP_HOST_VIDEO_ROOT", "")
    if not host_root:
        return str(resolved)
    try:
        relative_path = resolved.relative_to(BASE_DIR)
    except ValueError:
        return str(resolved)
    if "\\" in host_root or ":" in host_root:
        return str(PureWindowsPath(host_root, *relative_path.parts))
    return str(Path(host_root, *relative_path.parts))


def upload_naver_clip(video_path: Path, metadata: dict[str, object]) -> str:
    api_url = setting("NAVER_CLIP_API_URL", "http://host.docker.internal:8383/upload_clip")
    channel_url = setting(
        "NAVER_CLIP_CHANNEL_URL",
        "https://creator.tv.naver.com/channel/wisearchive/content/video",
    )
    timeout = setting_int("NAVER_CLIP_TIMEOUT_SECONDS", 900, minimum=30, maximum=3600)
    keep_open_seconds = setting_int("NAVER_CLIP_KEEP_OPEN_SECONDS", 8, minimum=0, maximum=300)
    payload = {
        "channel_url": channel_url,
        "video_path": naver_clip_video_path(video_path),
        "title": str(metadata["title"]),
        "description": str(metadata["description"]),
        "tags": list(metadata.get("tags") or []),
        "category1": setting("NAVER_CLIP_CATEGORY1", "인문, 교양"),
        "category2": setting("NAVER_CLIP_CATEGORY2", "인문, 교양"),
        "ai_usage": False,
        "keep_open_seconds": keep_open_seconds,
    }
    request = urllib.request.Request(
        api_url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Naver upload failed HTTP {exc.code}: {body_text}") from exc


def maybe_upload_naver_clip(
    auto_job_id: int,
    video_job_id: int | None,
    log_id: int,
    output: Path,
    metadata: dict[str, object],
) -> None:
    if not setting_bool("NAVER_CLIP_AUTO_UPLOAD_ENABLED", "0"):
        return
    if remaining_naver_daily_slots() <= 0:
        logging.info("Naver auto upload daily limit reached")
        return
    ready, reason = naver_schedule_ready()
    if not ready:
        logging.info(reason)
        return
    channel_url = setting(
        "NAVER_CLIP_CHANNEL_URL",
        "https://creator.tv.naver.com/channel/wisearchive/content/video",
    )
    upload_id = create_naver_upload_row(
        output.name,
        video_job_id,
        log_id,
        channel_url,
        str(metadata["title"]),
    )
    update_auto_job(auto_job_id, naver_upload_id=upload_id)
    try:
        response = upload_naver_clip(output, metadata)
    except Exception as exc:
        update_naver_upload_failure(upload_id, str(exc))
        logging.exception("Naver auto upload failed log_id=%s filename=%s", log_id, output.name)
        return
    update_naver_upload_success(upload_id, response)
    logging.info("Naver auto-uploaded log_id=%s filename=%s", log_id, output.name)


def pick_background_asset_id() -> int | None:
    return random_background_asset_id()


def render_or_reuse_video(auto_job: dict[str, object]) -> tuple[int | None, Path]:
    log_id = int(auto_job["log_id"])
    ready = latest_ready_video(log_id)
    if ready:
        output_path = BASE_DIR / str(ready["output_path"])
        if output_path.exists():
            return int(ready["id"]), output_path

    background_asset_id = pick_background_asset_id()
    tts_voice = DEFAULT_VOICE if short_tts_provider() == "elevenlabs" else next_tts_ab_test_voice()
    video_job_id = create_video_job(log_id, background_asset_id, tts_voice)
    update_auto_job(int(auto_job["auto_job_id"]), status="rendering", video_job_id=video_job_id)
    try:
        output, script = render_video(
            log_id,
            background_asset_id,
            True,
            tts_voice,
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
            audit_video_before_upload(output, label=f"auto-youtube-log-{log_id}")
            result = upload_video(
                output,
                str(metadata["title"]),
                str(metadata["description"]),
                list(metadata.get("tags") or []),
                privacy_status,
                publish_at=publish_at,
                contains_synthetic_media=True,
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
        maybe_post_youtube_comment(upload_id, str(result["youtube_video_id"]))
        maybe_upload_naver_clip(auto_job_id, video_job_id, log_id, output, metadata)
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


def missing_naver_upload_jobs(limit: int = 1) -> list[dict[str, object]]:
    if not setting_bool("NAVER_CLIP_AUTO_UPLOAD_ENABLED", "0"):
        return []
    with closing(connect(DB_PATH)) as conn:
        rows = conn.execute(
            """
            SELECT au.id AS auto_job_id, au.log_id, au.video_job_id, au.output_path,
                   tl.source, tl.msg_id, tl.content, tl.created_at, tl.saved_at
            FROM auto_upload_jobs au
            JOIN telegram_logs tl ON tl.id = au.log_id
            WHERE au.status = 'uploaded'
              AND au.output_path IS NOT NULL
              AND au.naver_upload_id IS NULL
            ORDER BY au.id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 10)),),
        ).fetchall()
    return [dict(row) for row in rows]


def backfill_missing_naver_uploads(limit: int = 1) -> int:
    mark_stale_naver_uploads_failed()
    remaining = remaining_naver_daily_slots()
    if remaining <= 0:
        logging.info("Naver backfill daily limit reached")
        return 0
    ready, reason = naver_schedule_ready()
    if not ready:
        logging.info(reason)
        return 0
    processed = 0
    for job in missing_naver_upload_jobs(min(limit, remaining)):
        output = BASE_DIR / str(job["output_path"])
        if not output.exists():
            logging.warning("Missing video file for Naver backfill auto_job_id=%s path=%s", job["auto_job_id"], output)
            continue
        metadata = youtube_metadata_for_log(job, output.name)
        logging.info("Naver backfill started auto_job_id=%s filename=%s", job["auto_job_id"], output.name)
        maybe_upload_naver_clip(
            int(job["auto_job_id"]),
            int(job["video_job_id"]) if job.get("video_job_id") is not None else None,
            int(job["log_id"]),
            output,
            metadata,
        )
        processed += 1
    return processed


def run_auto_upload_once() -> int:
    mark_stale_short_video_jobs_failed()
    try:
        cleanup_uploaded_videos_once_per_day()
    except Exception:
        logging.exception("Daily video cleanup failed")
    if not setting_bool("AUTO_UPLOAD_ENABLED", "1"):
        return 0
    naver_backfilled = backfill_missing_naver_uploads(
        setting_int("NAVER_CLIP_BACKFILL_PER_RUN", 1, minimum=1, maximum=10)
    )
    if youtube_quota_paused():
        logging.info("Auto upload paused because YouTube quota is exhausted today")
        return naver_backfilled
    remaining = remaining_daily_slots()
    if remaining <= 0:
        logging.info("Auto upload daily limit reached")
        return naver_backfilled
    if setting_bool("SARAMRO_QUOTES_ENABLED", "0") and "사람로" in auto_upload_sources():
        import_saramro_quotes(
            limit=min(remaining, setting_int("SARAMRO_QUOTES_IMPORT_LIMIT", 10, minimum=1, maximum=50)),
            max_pages=setting_int("SARAMRO_QUOTES_MAX_PAGES", 5, minimum=1, maximum=50),
        )
    limit = min(remaining, setting_int("AUTO_UPLOAD_MAX_PER_RUN", 1, minimum=1, maximum=10))
    jobs = claim_pending_jobs(limit)
    for job in jobs:
        process_auto_upload_job(job)
    return naver_backfilled + len(jobs)
