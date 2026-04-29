from __future__ import annotations

import json
from pathlib import Path

from db import BASE_DIR


CHANNELS_PATH = BASE_DIR / "channels.json"
DEFAULT_CHANNELS = [
    {"id": -1001047477854, "alias": "뽐질"},
    {"id": -1002381848987, "alias": "퍼나정"},
    {"id": -1001038361551, "alias": "글반장"},
]


def ensure_channels_file(path: Path = CHANNELS_PATH) -> None:
    if path.exists():
        return
    path.write_text(
        json.dumps(DEFAULT_CHANNELS, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_channels(path: Path = CHANNELS_PATH) -> dict[int, str]:
    ensure_channels_file(path)

    try:
        raw_channels = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Invalid channels config: {path}") from exc

    if not isinstance(raw_channels, list):
        raise RuntimeError("channels.json must contain a list")

    channels: dict[int, str] = {}
    for index, item in enumerate(raw_channels, start=1):
        if not isinstance(item, dict):
            raise RuntimeError(f"channels.json item #{index} must be an object")

        try:
            chat_id = int(item["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(f"channels.json item #{index} has invalid id") from exc

        alias = str(item.get("alias") or chat_id).strip()
        if not alias:
            raise RuntimeError(f"channels.json item #{index} has empty alias")
        if chat_id in channels:
            raise RuntimeError(f"Duplicate channel id in channels.json: {chat_id}")

        channels[chat_id] = alias

    if not channels:
        raise RuntimeError("channels.json must contain at least one channel")

    return channels
