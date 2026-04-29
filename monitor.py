import asyncio
import logging
import os
import signal
from pathlib import Path

from telethon import TelegramClient, events

from channels import load_channels
from db import DB_PATH, init_db, normalize_message, save_message

BASE_DIR = Path(__file__).resolve().parent
MEDIA_DIR = BASE_DIR / "static" / "media"
SESSION_NAME = os.getenv("TELEGRAM_SESSION", str(BASE_DIR / "telegram_monitor"))

CATCH_UP_LIMIT = int(os.getenv("TELEGRAM_CATCH_UP_LIMIT", "20"))

def get_required_env(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


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

    async def download_image(message) -> tuple[str | None, str | None]:
        if not message.media:
            return None, None

        mime_type = getattr(message.file, "mime_type", None)
        is_image = bool(message.photo) or bool(mime_type and mime_type.startswith("image/"))
        if not is_image:
            return None, None

        MEDIA_DIR.mkdir(parents=True, exist_ok=True)
        path = await message.download_media(file=MEDIA_DIR)
        if not path:
            return None, None

        relative_path = Path(path).resolve().relative_to(BASE_DIR).as_posix()
        return relative_path, "image"

    async def save_incoming_message(chat_id: int | None, message) -> str:
        msg_id = message.id
        media_path, media_kind = await download_image(message)
        text = message.raw_text
        content = normalize_message(text)
        if not content and not media_path:
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
            media_path=media_path,
            media_kind=media_kind,
            group_key=group_key,
        )

        if result in {"inserted", "updated"}:
            logging.info(
                "Saved message source=%s chat_id=%s msg_id=%s media=%s result=%s",
                source,
                chat_id,
                msg_id,
                media_path or "-",
                result,
            )
        else:
            logging.debug("Skipped duplicate source=%s chat_id=%s msg_id=%s", source, chat_id, msg_id)
        return result

    async def catch_up_recent_messages() -> None:
        total = 0
        for chat_id, source in target_chats.items():
            count = 0
            try:
                async for message in client.iter_messages(chat_id, limit=CATCH_UP_LIMIT):
                    if await save_incoming_message(chat_id, message) in {"inserted", "updated"}:
                        count += 1
            except Exception:
                logging.exception("Catch-up failed source=%s chat_id=%s", source, chat_id)
                continue
            total += count
            logging.info("Catch-up source=%s chat_id=%s saved=%s", source, chat_id, count)
        logging.info("Catch-up complete saved=%s", total)

    @client.on(events.NewMessage(chats=list(target_chats.keys())))
    async def handle_new_message(event: events.NewMessage.Event) -> None:
        await save_incoming_message(event.chat_id, event.message)

    async with client:
        logging.info("Monitoring %s chats. DB=%s", len(target_chats), DB_PATH)
        await catch_up_recent_messages()
        await stop_event.wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from exc
