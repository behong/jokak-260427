import os
import sqlite3
import base64
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent


def load_env_file(path: Path = BASE_DIR / ".env", override: bool = False) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        name, value = line.split("=", 1)
        name = name.strip()
        value = value.strip().strip('"').strip("'")
        if name and (override or name not in os.environ):
            os.environ[name] = value


load_env_file()

DB_PATH = Path(os.getenv("TELEGRAM_LOG_DB", BASE_DIR / "telegram_logs.sqlite3"))

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS telegram_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    msg_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at DATETIME NOT NULL,
    saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source, msg_id)
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
    enabled INTEGER NOT NULL DEFAULT 1,
    collection TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(provider, provider_id)
)
"""

CREATE_BGM_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS bgm_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    local_path TEXT NOT NULL UNIQUE,
    title TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
    view_count INTEGER,
    like_count INTEGER,
    comment_count INTEGER,
    stats_checked_at TIMESTAMP,
    scheduled_publish_at TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

CREATE_LONG_VIDEO_JOB_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS long_video_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status TEXT NOT NULL,
    output_path TEXT,
    title TEXT,
    source_count INTEGER NOT NULL DEFAULT 0,
    source_filenames TEXT,
    stage TEXT,
    progress INTEGER,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

CREATE_APP_STATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

CREATE_APP_SETTINGS_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT,
    is_secret INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

DEFAULT_SETTING_KEYS = {
    "TELEGRAM_API_ID": False,
    "TELEGRAM_API_HASH": True,
    "TELEGRAM_SESSION": False,
    "TELEGRAM_LOG_DB": False,
    "TELEGRAM_CATCH_UP_LIMIT": False,
    "LOG_LEVEL": False,
    "DASHBOARD_PASSWORD": True,
    "DASHBOARD_SECRET_KEY": True,
    "DASHBOARD_HOST": False,
    "DASHBOARD_PORT": False,
    "PEXELS_API_KEY": True,
    "YOUTUBE_CLIENT_SECRET_JSON": True,
    "YOUTUBE_TOKEN_JSON": True,
    "TELEGRAM_SESSION_FILE_B64": True,
    "YOUTUBE_STATS_INTERVAL_SECONDS": False,
    "VIDEO_BGM_ENABLED": False,
    "VIDEO_BGM_TTS_VOLUME": False,
    "VIDEO_BGM_ONLY_VOLUME": False,
}


TELEGRAM_LOG_COLUMNS = (
    "id",
    "source",
    "msg_id",
    "content",
    "created_at",
    "saved_at",
    "media_path",
    "media_kind",
    "group_key",
)


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def has_msg_id_only_unique(conn: sqlite3.Connection) -> bool:
    for row in conn.execute("PRAGMA index_list(telegram_logs)").fetchall():
        index_name = row[1]
        is_unique = bool(row[2])
        if not is_unique:
            continue
        columns = [
            column_row[2]
            for column_row in conn.execute(f"PRAGMA index_info({index_name})").fetchall()
        ]
        if columns == ["msg_id"]:
            return True
    return False


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def ensure_telegram_log_optional_columns(
    conn: sqlite3.Connection,
    table_name: str = "telegram_logs",
) -> None:
    columns = {
        row[1]
        for row in conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    }
    if "media_path" not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN media_path TEXT")
    if "media_kind" not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN media_kind TEXT")
    if "group_key" not in columns:
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN group_key TEXT")


def migrate_telegram_logs_unique_key(conn: sqlite3.Connection) -> None:
    old_table_exists = table_exists(conn, "telegram_logs_old")
    if not old_table_exists and not has_msg_id_only_unique(conn):
        return

    if not old_table_exists:
        conn.execute("ALTER TABLE telegram_logs RENAME TO telegram_logs_old")
        conn.execute(CREATE_TABLE_SQL)
    elif not table_exists(conn, "telegram_logs"):
        conn.execute(CREATE_TABLE_SQL)

    ensure_telegram_log_optional_columns(conn)
    existing_columns = {
        row[1]
        for row in conn.execute("PRAGMA table_info(telegram_logs_old)").fetchall()
    }
    select_columns = [
        column if column in existing_columns else "NULL"
        for column in TELEGRAM_LOG_COLUMNS
    ]
    conn.execute(
        f"""
        INSERT OR IGNORE INTO telegram_logs ({", ".join(TELEGRAM_LOG_COLUMNS)})
        SELECT {", ".join(select_columns)}
        FROM telegram_logs_old
        ORDER BY id
        """
    )
    conn.execute("DROP TABLE telegram_logs_old")


def init_db(db_path: Path = DB_PATH) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(CREATE_TABLE_SQL)
        migrate_telegram_logs_unique_key(conn)
        conn.execute(CREATE_VIDEO_TABLE_SQL)
        conn.execute(CREATE_BACKGROUND_TABLE_SQL)
        conn.execute(CREATE_BGM_TABLE_SQL)
        conn.execute(CREATE_YOUTUBE_UPLOAD_TABLE_SQL)
        conn.execute(CREATE_LONG_VIDEO_JOB_TABLE_SQL)
        conn.execute(CREATE_APP_STATE_TABLE_SQL)
        conn.execute(CREATE_APP_SETTINGS_TABLE_SQL)
        migrate_env_to_app_settings(conn)
        ensure_telegram_log_optional_columns(conn)
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
        if "bgm_asset_id" not in video_columns:
            conn.execute("ALTER TABLE video_jobs ADD COLUMN bgm_asset_id INTEGER")
        background_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(background_assets)").fetchall()
        }
        if "enabled" not in background_columns:
            conn.execute(
                "ALTER TABLE background_assets "
                "ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"
            )
        if "collection" not in background_columns:
            conn.execute("ALTER TABLE background_assets ADD COLUMN collection TEXT")
        youtube_upload_columns = {
            row[1]
            for row in conn.execute("PRAGMA table_info(youtube_uploads)").fetchall()
        }
        if "view_count" not in youtube_upload_columns:
            conn.execute("ALTER TABLE youtube_uploads ADD COLUMN view_count INTEGER")
        if "like_count" not in youtube_upload_columns:
            conn.execute("ALTER TABLE youtube_uploads ADD COLUMN like_count INTEGER")
        if "comment_count" not in youtube_upload_columns:
            conn.execute("ALTER TABLE youtube_uploads ADD COLUMN comment_count INTEGER")
        if "stats_checked_at" not in youtube_upload_columns:
            conn.execute("ALTER TABLE youtube_uploads ADD COLUMN stats_checked_at TIMESTAMP")
        if "scheduled_publish_at" not in youtube_upload_columns:
            conn.execute("ALTER TABLE youtube_uploads ADD COLUMN scheduled_publish_at TEXT")
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
            "CREATE INDEX IF NOT EXISTS idx_background_assets_enabled "
            "ON background_assets(enabled, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_bgm_assets_enabled "
            "ON bgm_assets(enabled, id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_youtube_uploads_filename "
            "ON youtube_uploads(filename)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_long_video_jobs_status "
            "ON long_video_jobs(status, id)"
        )
        conn.commit()


def migrate_env_to_app_settings(conn: sqlite3.Connection) -> None:
    for key, is_secret in DEFAULT_SETTING_KEYS.items():
        value = os.getenv(key)
        if value is None:
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO app_settings (key, value, is_secret, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (key, value, 1 if is_secret else 0),
        )
    file_settings = {
        "YOUTUBE_CLIENT_SECRET_JSON": BASE_DIR / "client_secret.json",
        "YOUTUBE_TOKEN_JSON": BASE_DIR / "youtube_token.json",
    }
    for key, path in file_settings.items():
        if not path.exists():
            continue
        conn.execute(
            """
            INSERT OR IGNORE INTO app_settings (key, value, is_secret, updated_at)
            VALUES (?, ?, 1, CURRENT_TIMESTAMP)
            """,
            (key, path.read_text(encoding="utf-8")),
        )
    session_path = BASE_DIR / "telegram_monitor.session"
    if session_path.exists():
        conn.execute(
            """
            INSERT OR IGNORE INTO app_settings (key, value, is_secret, updated_at)
            VALUES (?, ?, 1, CURRENT_TIMESTAMP)
            """,
            (
                "TELEGRAM_SESSION_FILE_B64",
                base64.b64encode(session_path.read_bytes()).decode("ascii"),
            ),
        )


def get_app_setting(key: str, default: str = "") -> str:
    try:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            row = conn.execute(
                "SELECT value FROM app_settings WHERE key = ?",
                (key,),
            ).fetchone()
    except sqlite3.Error:
        return os.getenv(key, default)
    if row is None or row[0] is None:
        return os.getenv(key, default)
    return str(row[0])


def set_app_setting(key: str, value: str, is_secret: bool = False) -> None:
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(CREATE_APP_SETTINGS_TABLE_SQL)
        conn.execute(
            """
            INSERT INTO app_settings (key, value, is_secret, updated_at)
            VALUES (?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(key) DO UPDATE SET
                value = excluded.value,
                is_secret = excluded.is_secret,
                updated_at = CURRENT_TIMESTAMP
            """,
            (key, value, 1 if is_secret else 0),
        )
        conn.commit()
    os.environ[key] = value


def list_app_settings() -> dict[str, dict[str, object]]:
    try:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            conn.execute(CREATE_APP_SETTINGS_TABLE_SQL)
            rows = conn.execute(
                "SELECT key, value, is_secret, updated_at FROM app_settings ORDER BY key"
            ).fetchall()
    except sqlite3.Error:
        return {}
    return {
        str(row[0]): {
            "value": "" if row[1] is None else str(row[1]),
            "is_secret": bool(row[2]),
            "updated_at": row[3],
        }
        for row in rows
    }


def apply_db_settings_to_env() -> None:
    for key, item in list_app_settings().items():
        value = str(item["value"])
        if key.endswith("_B64") or len(value) > 30000:
            continue
        os.environ[key] = value


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
                  AND source = ?
                  AND media_path IS NULL
                """,
                (media_path, media_kind, group_key, msg_id, source),
            )
            conn.commit()
            return "updated" if update_cursor.rowcount > 0 else "skipped"

        conn.commit()
        return "skipped"
