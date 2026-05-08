from __future__ import annotations

import asyncio
import base64
import os
import shutil
from pathlib import Path

from telethon import TelegramClient

from channels import load_channels
from db import DB_PATH, apply_db_settings_to_env, get_app_setting, init_db, normalize_message, save_message


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CATCH_UP_LIMIT = 20
DEFAULT_CATCH_UP_INTERVAL_SECONDS = 3600


def int_setting(name: str, default: int) -> int:
    value = get_app_setting(name, os.getenv(name, str(default)))
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def get_required_env(name: str) -> str:
    apply_db_settings_to_env()
    value = get_app_setting(name, os.getenv(name, ""))
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def session_file_path() -> Path:
    session_value = get_app_setting(
        "TELEGRAM_SESSION",
        os.getenv("TELEGRAM_SESSION", str(BASE_DIR / "telegram_monitor")),
    )
    session_path = Path(session_value)
    if not session_path.is_absolute():
        session_path = BASE_DIR / session_path
    if session_path.suffix != ".session":
        session_path = session_path.with_suffix(".session")
    return session_path


def materialize_telegram_session_from_settings() -> Path:
    session_path = session_file_path()
    if session_path.exists():
        return session_path
    encoded = get_app_setting("TELEGRAM_SESSION_FILE_B64", "")
    if not encoded:
        return session_path
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_bytes(base64.b64decode(encoded))
    return session_path


def refresh_session_name() -> str:
    source = materialize_telegram_session_from_settings()
    target = BASE_DIR / "telegram_dashboard_refresh.session"
    if source.exists():
        shutil.copy2(source, target)
    return str(target.with_suffix(""))


async def save_incoming_message(target_chats: dict[int, str], chat_id: int | None, message) -> str:
    content = normalize_message(message.raw_text)
    if not content:
        return "skipped"

    source = target_chats.get(chat_id, str(chat_id))
    group_key = (
        f"{chat_id}:{message.grouped_id}"
        if getattr(message, "grouped_id", None)
        else None
    )
    return save_message(
        DB_PATH,
        source,
        message.id,
        content,
        message.date,
        group_key=group_key,
    )


async def catch_up_recent_messages(limit: int | None = None, session_name: str | None = None) -> dict[str, object]:
    init_db(DB_PATH)
    api_id = int(get_required_env("TELEGRAM_API_ID"))
    api_hash = get_required_env("TELEGRAM_API_HASH")
    target_chats = load_channels()
    catch_up_limit = limit if limit is not None else int_setting("TELEGRAM_CATCH_UP_LIMIT", DEFAULT_CATCH_UP_LIMIT)
    if catch_up_limit <= 0:
        return {"saved": 0, "inserted": 0, "updated": 0, "skipped": 0, "errors": []}

    stats = {"saved": 0, "inserted": 0, "updated": 0, "skipped": 0, "errors": []}
    client = TelegramClient(
        session_name or str(materialize_telegram_session_from_settings().with_suffix("")),
        api_id,
        api_hash,
        auto_reconnect=True,
        connection_retries=2,
        retry_delay=2,
    )

    async with client:
        for chat_id, source in target_chats.items():
            try:
                async for message in client.iter_messages(chat_id, limit=catch_up_limit):
                    result = await save_incoming_message(target_chats, chat_id, message)
                    if result in {"inserted", "updated"}:
                        stats["saved"] += 1
                    if result in stats:
                        stats[result] += 1
            except Exception as exc:
                stats["errors"].append({"source": source, "chat_id": chat_id, "error": str(exc)})
    return stats


def catch_up_recent_messages_sync(limit: int | None = None, copy_session: bool = False) -> dict[str, object]:
    session_name = refresh_session_name() if copy_session else None
    return asyncio.run(catch_up_recent_messages(limit=limit, session_name=session_name))
