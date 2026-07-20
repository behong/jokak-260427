from __future__ import annotations

import copy
import json
import threading
from contextlib import closing
from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from db import DB_PATH, connect, init_db
from healing_longform import create_healing_job, list_healing_jobs, run_healing_longform_job
from longform_config import load_longform_config, validate_longform_config
from longform_script import available_themes


KST = ZoneInfo("Asia/Seoul")
SCHEDULER_POLL_SECONDS = 30


def schedule_is_due(config: dict[str, Any], now: datetime | None = None) -> bool:
    now = (now or datetime.now(KST)).astimezone(KST)
    schedule = config["longform"].get("schedule") or {}
    if not bool(schedule.get("enabled", False)) or now.weekday() not in schedule.get("days", []):
        return False
    hour, minute = [int(value) for value in str(schedule.get("time") or "03:10").split(":", 1)]
    scheduled_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    window = timedelta(minutes=int(schedule.get("window_minutes") or 30))
    return scheduled_at <= now < scheduled_at + window


def _kst_day_utc_bounds(now: datetime) -> tuple[str, str]:
    local = now.astimezone(KST)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=1)
    return (
        start.astimezone(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
        end.astimezone(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S"),
    )


def scheduled_job_exists_today(now: datetime | None = None) -> bool:
    now = now or datetime.now(KST)
    start, end = _kst_day_utc_bounds(now)
    init_db(DB_PATH)
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM healing_longform_jobs
            WHERE trigger = 'scheduled' AND created_at >= ? AND created_at < ?
            LIMIT 1
            """,
            (start, end),
        ).fetchone()
    return row is not None


def system_has_active_video_work() -> bool:
    if any(job.get("status") in {"pending", "running"} for job in list_healing_jobs(20)):
        return True
    init_db(DB_PATH)
    with closing(connect(DB_PATH)) as conn:
        video_jobs = conn.execute(
            """
            SELECT COUNT(*) AS count FROM video_jobs
            WHERE status IN ('pending', 'rendering', 'running')
              AND updated_at >= datetime('now', '-2 hours')
            """
        ).fetchone()["count"]
        auto_jobs = conn.execute(
            """
            SELECT COUNT(*) AS count FROM auto_upload_jobs
            WHERE status IN ('pending', 'rendering', 'uploading')
              AND updated_at >= datetime('now', '-2 hours')
            """
        ).fetchone()["count"]
    return int(video_jobs) + int(auto_jobs) > 0


def scheduled_render_config(config: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(KST)).astimezone(KST)
    result = copy.deepcopy(config)
    root = result["longform"]
    schedule = root.get("schedule") or {}
    if str(schedule.get("theme_mode") or "rotate") == "rotate":
        themes = available_themes()
        root["script"]["theme"] = themes[now.date().toordinal() % len(themes)]
    output = root.setdefault("output", {})
    output["preset"] = str(schedule.get("render_preset") or "veryfast")
    output["crf"] = int(schedule.get("render_crf") or 20)
    output["threads"] = int(schedule.get("render_threads") or 2)
    return validate_longform_config(result)


def _record_scheduler_state(status: str, detail: dict[str, Any]) -> None:
    payload = json.dumps({"status": status, **detail}, ensure_ascii=False)
    with closing(connect(DB_PATH)) as conn:
        conn.execute(
            """
            INSERT INTO app_state (key, value, updated_at)
            VALUES ('longform_scheduler', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
            """,
            (payload,),
        )
        conn.commit()


def longform_scheduler_tick(now: datetime | None = None) -> dict[str, Any]:
    now = (now or datetime.now(KST)).astimezone(KST)
    config = load_longform_config()
    if not schedule_is_due(config, now):
        return {"status": "not_due"}
    if scheduled_job_exists_today(now):
        return {"status": "already_created"}
    schedule = config["longform"].get("schedule") or {}
    if bool(schedule.get("skip_when_busy", True)) and system_has_active_video_work():
        detail = {"checked_at": now.isoformat(), "reason": "another_video_job_is_active"}
        _record_scheduler_state("waiting_for_idle", detail)
        return {"status": "waiting_for_idle", **detail}

    job_config = scheduled_render_config(config, now)
    job_id = create_healing_job(job_config, trigger="scheduled")
    thread = threading.Thread(
        target=run_healing_longform_job,
        args=(job_id, job_config),
        daemon=True,
        name=f"scheduled-longform-{job_id}",
    )
    thread.start()
    detail = {
        "job_id": job_id,
        "started_at": now.isoformat(),
        "theme": job_config["longform"]["script"]["theme"],
    }
    _record_scheduler_state("started", detail)
    return {"status": "started", **detail}


def longform_scheduler_loop() -> None:
    while True:
        try:
            longform_scheduler_tick()
        except Exception as exc:
            _record_scheduler_state("error", {"error": str(exc)[-1000:]})
        threading.Event().wait(SCHEDULER_POLL_SECONDS)


def start_longform_scheduler() -> threading.Thread:
    thread = threading.Thread(
        target=longform_scheduler_loop,
        daemon=True,
        name="longform-scheduler",
    )
    thread.start()
    return thread
