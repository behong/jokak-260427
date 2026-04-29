import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


def load_env_file(path: Path = BASE_DIR / ".env") -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and name not in os.environ:
            os.environ[name] = value


load_env_file()

DB_PATH = Path(os.getenv("TELEGRAM_LOG_DB", BASE_DIR / "telegram_logs.sqlite3"))

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS telegram_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    msg_id INTEGER NOT NULL UNIQUE,
    content TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

CREATE_VIDEO_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS video_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    log_id INTEGER NOT NULL,
    background_asset_id INTEGER,
    status TEXT NOT NULL,
    output_path TEXT,
    title TEXT,
    stage TEXT,
    progress INTEGER,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

CREATE_BACKGROUND_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS background_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider TEXT NOT NULL,
    provider_id TEXT NOT NULL,
    query TEXT,
    author TEXT,
    source_url TEXT,
    preview_url TEXT,
    local_path TEXT NOT NULL,
    width INTEGER,
    height INTEGER,
    duration REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(provider, provider_id)
)
"""

CREATE_YOUTUBE_UPLOAD_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS youtube_uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    video_job_id INTEGER,
    log_id INTEGER,
    youtube_video_id TEXT,
    youtube_url TEXT,
    title TEXT,
    privacy_status TEXT,
    status TEXT NOT NULL,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: Path = DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(CREATE_TABLE_SQL)
        conn.execute(CREATE_VIDEO_TABLE_SQL)
        conn.execute(CREATE_BACKGROUND_TABLE_SQL)
        conn.execute(CREATE_YOUTUBE_UPLOAD_TABLE_SQL)
        columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(telegram_logs)").fetchall()
        }
        if "media_path" not in columns:
            conn.execute("ALTER TABLE telegram_logs ADD COLUMN media_path TEXT")
        if "media_kind" not in columns:
            conn.execute("ALTER TABLE telegram_logs ADD COLUMN media_kind TEXT")
        if "group_key" not in columns:
            conn.execute("ALTER TABLE telegram_logs ADD COLUMN group_key TEXT")
        video_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(video_jobs)").fetchall()
        }
        if "background_asset_id" not in video_columns:
            conn.execute("ALTER TABLE video_jobs ADD COLUMN background_asset_id INTEGER")
        if "stage" not in video_columns:
            conn.execute("ALTER TABLE video_jobs ADD COLUMN stage TEXT")
        if "progress" not in video_columns:
            conn.execute("ALTER TABLE video_jobs ADD COLUMN progress INTEGER")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_telegram_logs_source "
            "ON telegram_logs(source)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_telegram_logs_created_at "
            "ON telegram_logs(created_at)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_telegram_logs_group_key "
            "ON telegram_logs(group_key)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_video_jobs_log_id "
            "ON video_jobs(log_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_background_assets_provider "
            "ON background_assets(provider, provider_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_youtube_uploads_filename "
            "ON youtube_uploads(filename)"
        )
        conn.commit()


def normalize_message(text: str | None) -> str:
    return (text or "").strip()


def to_iso_datetime(value: datetime | None) -> str:
    if value is None:
        value = datetime.now(timezone.utc)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def save_message(
    db_path: Path,
    source: str,
    msg_id: int,
    content: str,
    created_at: datetime | None,
    media_path: str | None = None,
    media_kind: str | None = None,
    group_key: str | None = None,
) -> str:
    with closing(sqlite3.connect(db_path)) as conn:
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO telegram_logs
                (source, msg_id, content, created_at, media_path, media_kind, group_key)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source,
                msg_id,
                content,
                to_iso_datetime(created_at),
                media_path,
                media_kind,
                group_key,
            ),
        )
        if cursor.rowcount > 0:
            conn.commit()
            return "inserted"

        if media_path:
            update_cursor = conn.execute(
                """
                UPDATE telegram_logs
                SET media_path = COALESCE(media_path, ?),
                    media_kind = COALESCE(media_kind, ?),
                    group_key = COALESCE(group_key, ?)
                WHERE msg_id = ?
                  AND media_path IS NULL
                """,
                (media_path, media_kind, group_key, msg_id),
            )
            conn.commit()
            return "updated" if update_cursor.rowcount > 0 else "skipped"

        conn.commit()
        return "skipped"
