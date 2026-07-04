import asyncio
import atexit
import base64
import contextlib
import logging
import os
import platform
import sqlite3
import signal
from pathlib import Path
from datetime import datetime, timezone

from telethon import TelegramClient, events

from auto_upload import run_auto_upload_once
from channels import load_channels
from db import DB_PATH, apply_db_settings_to_env, connect, get_app_setting, init_db, normalize_message, save_message

BASE_DIR = Path(__file__).resolve().parent
SESSION_NAME = os.getenv("TELEGRAM_SESSION", str(BASE_DIR / "telegram_monitor"))

DEFAULT_CATCH_UP_LIMIT = 20
DEFAULT_CATCH_UP_INTERVAL_SECONDS = 3600
DEFAULT_AUTO_UPLOAD_INTERVAL_SECONDS = 60
HEARTBEAT_INTERVAL_SECONDS = 30
LOCK_PATH = BASE_DIR / ".monitor.lock"
_LOCK_HANDLE = None


def acquire_single_instance_lock() -> None:
    global _LOCK_HANDLE
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    handle = LOCK_PATH.open("a+b")
    try:
        if platform.system() == "Windows":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        handle.close()
        raise RuntimeError("monitor.py is already running; exiting") from exc
    _LOCK_HANDLE = handle
    atexit.register(release_single_instance_lock)


def release_single_instance_lock() -> None:
    global _LOCK_HANDLE
    if _LOCK_HANDLE is None:
        return
    try:
        _LOCK_HANDLE.seek(0)
        if platform.system() == "Windows":
            import msvcrt

            msvcrt.locking(_LOCK_HANDLE.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(_LOCK_HANDLE.fileno(), fcntl.LOCK_UN)
    except OSError:
        pass
    finally:
        with contextlib.suppress(OSError):
            _LOCK_HANDLE.close()
        _LOCK_HANDLE = None


def int_setting(name: str, default: int) -> int:
    value = get_app_setting(name, os.getenv(name, str(default)))
    try:
        return int(value)
    except (TypeError, ValueError):
        logging.warning("Invalid integer setting %s=%r. Using %s", name, value, default)
        return default

def get_required_env(name: str) -> str:
    apply_db_settings_to_env()
    value = get_app_setting(name, os.getenv(name, ""))
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def materialize_telegram_session_from_settings() -> None:
    session_value = get_app_setting("TELEGRAM_SESSION", os.getenv("TELEGRAM_SESSION", str(BASE_DIR / "telegram_monitor")))
    session_path = Path(session_value)
    if not session_path.is_absolute():
        session_path = BASE_DIR / session_path
    if session_path.suffix != ".session":
        session_path = session_path.with_suffix(".session")
    if session_path.exists():
        return
    encoded = get_app_setting("TELEGRAM_SESSION_FILE_B64", "")
    if not encoded:
        return
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_bytes(base64.b64decode(encoded))


def write_monitor_state(key: str, value: str) -> None:
    with connect(DB_PATH) as conn:
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


def write_monitor_heartbeat(status: str = "running") -> None:
    now = datetime.now(timezone.utc).isoformat()
    write_monitor_state("monitor_heartbeat_at", now)
    write_monitor_state("monitor_status", status)


async def main() -> None:
    log_level = os.getenv("LOG_LEVEL", "INFO").upper()
    log_format = "%(asctime)s %(levelname)s %(message)s"
    logging.basicConfig(
        level=log_level,
        format=log_format,
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(BASE_DIR / "monitor.log", encoding="utf-8"),
        ],
    )

    api_id = int(get_required_env("TELEGRAM_API_ID"))
    api_hash = get_required_env("TELEGRAM_API_HASH")

    init_db(DB_PATH)
    materialize_telegram_session_from_settings()
    target_chats = load_channels()

    client = TelegramClient(
        SESSION_NAME,
        api_id,
        api_hash,
        auto_reconnect=True,
        connection_retries=None,
        retry_delay=5,
    )
    stop_event = asyncio.Event()

    def request_shutdown() -> None:
        logging.info("Shutdown requested")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_shutdown)
        except NotImplementedError:
            signal.signal(sig, lambda *_: request_shutdown())

    async def save_incoming_message(chat_id: int | None, message) -> str:
        msg_id = message.id
        text = message.raw_text
        content = normalize_message(text)
        if not content:
            return "skipped"

        source = target_chats.get(chat_id, str(chat_id))
        group_key = (
            f"{chat_id}:{message.grouped_id}"
            if getattr(message, "grouped_id", None)
            else None
        )
        result = save_message(
            DB_PATH,
            source,
            msg_id,
            content,
            message.date,
            group_key=group_key,
        )

        if result in {"inserted", "updated"}:
            logging.info(
                "Saved message source=%s chat_id=%s msg_id=%s result=%s",
                source,
                chat_id,
                msg_id,
                result,
            )
        else:
            logging.debug("Skipped duplicate source=%s chat_id=%s msg_id=%s", source, chat_id, msg_id)
        return result

    async def catch_up_recent_messages() -> None:
        catch_up_limit = int_setting("TELEGRAM_CATCH_UP_LIMIT", DEFAULT_CATCH_UP_LIMIT)
        if catch_up_limit <= 0:
            logging.info("Catch-up skipped because TELEGRAM_CATCH_UP_LIMIT=%s", catch_up_limit)
            return

        total = 0
        for chat_id, source in target_chats.items():
            count = 0
            try:
                async for message in client.iter_messages(chat_id, limit=catch_up_limit):
                    if await save_incoming_message(chat_id, message) in {"inserted", "updated"}:
                        count += 1
            except Exception:
                logging.exception("Catch-up failed source=%s chat_id=%s", source, chat_id)
                continue
            total += count
            logging.info("Catch-up source=%s chat_id=%s saved=%s", source, chat_id, count)
        logging.info("Catch-up complete saved=%s", total)

    async def catch_up_periodically() -> None:
        while not stop_event.is_set():
            interval = int_setting(
                "TELEGRAM_CATCH_UP_INTERVAL_SECONDS",
                DEFAULT_CATCH_UP_INTERVAL_SECONDS,
            )
            if interval <= 0:
                logging.info(
                    "Periodic catch-up disabled because TELEGRAM_CATCH_UP_INTERVAL_SECONDS=%s",
                    interval,
                )
                return
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                logging.info("Periodic catch-up started")
                await catch_up_recent_messages()
            else:
                return

    async def auto_upload_periodically() -> None:
        while not stop_event.is_set():
            interval = int_setting(
                "AUTO_UPLOAD_POLL_INTERVAL_SECONDS",
                DEFAULT_AUTO_UPLOAD_INTERVAL_SECONDS,
            )
            if interval <= 0:
                logging.info(
                    "Auto upload disabled because AUTO_UPLOAD_POLL_INTERVAL_SECONDS=%s",
                    interval,
                )
                return
            try:
                processed = await asyncio.to_thread(run_auto_upload_once)
                if processed:
                    logging.info("Auto upload processed jobs=%s", processed)
            except Exception:
                logging.exception("Auto upload check failed")

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=interval)
            except asyncio.TimeoutError:
                continue
            else:
                return

    async def heartbeat_periodically() -> None:
        while not stop_event.is_set():
            try:
                write_monitor_heartbeat()
            except Exception:
                logging.exception("Monitor heartbeat update failed")

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=HEARTBEAT_INTERVAL_SECONDS)
            except asyncio.TimeoutError:
                continue
            else:
                return

    @client.on(events.NewMessage(chats=list(target_chats.keys())))
    async def handle_new_message(event: events.NewMessage.Event) -> None:
        await save_incoming_message(event.chat_id, event.message)

    while not stop_event.is_set():
        try:
            async with client:
                logging.info("Monitoring %s chats. DB=%s", len(target_chats), DB_PATH)
                write_monitor_state("monitor_started_at", datetime.now(timezone.utc).isoformat())
                write_monitor_heartbeat()
                await catch_up_recent_messages()
                periodic_task = asyncio.create_task(catch_up_periodically())
                auto_upload_task = asyncio.create_task(auto_upload_periodically())
                heartbeat_task = asyncio.create_task(heartbeat_periodically())
                try:
                    await stop_event.wait()
                finally:
                    write_monitor_heartbeat("stopping")
                    periodic_task.cancel()
                    auto_upload_task.cancel()
                    heartbeat_task.cancel()
                    await asyncio.gather(periodic_task, auto_upload_task, heartbeat_task, return_exceptions=True)
            break
        except sqlite3.OperationalError as exc:
            if "database is locked" not in str(exc).lower():
                raise
            logging.warning("Telegram session database is locked; retrying in 30 seconds")
            try:
                await client.disconnect()
            except sqlite3.OperationalError as disconnect_exc:
                if "database is locked" not in str(disconnect_exc).lower():
                    raise
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=30)
            except asyncio.TimeoutError:
                continue
            break


if __name__ == "__main__":
    try:
        acquire_single_instance_lock()
        asyncio.run(main())
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
