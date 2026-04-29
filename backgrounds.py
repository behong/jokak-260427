from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from contextlib import closing
from pathlib import Path
from typing import Any

from db import BASE_DIR, DB_PATH, connect, init_db


BACKGROUND_DIR = BASE_DIR / "assets" / "backgrounds"
PEXELS_VIDEO_SEARCH_URL = "https://api.pexels.com/videos/search"


class BackgroundAssetError(RuntimeError):
    pass


def pexels_api_key() -> str:
    return os.getenv("PEXELS_API_KEY", "").strip()


def _request_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise BackgroundAssetError(f"Pexels API error {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise BackgroundAssetError(f"Pexels API connection failed: {exc.reason}") from exc


def _best_video_file(video: dict[str, Any]) -> dict[str, Any] | None:
    files = [
        item
        for item in video.get("video_files", [])
        if item.get("file_type") == "video/mp4" and item.get("link")
    ]
    if not files:
        return None

    portrait_files = [
        item
        for item in files
        if int(item.get("height") or 0) >= int(item.get("width") or 0)
    ]
    candidates = portrait_files or files
    return max(
        candidates,
        key=lambda item: (
            int(item.get("height") or 0),
            int(item.get("width") or 0),
            1 if item.get("quality") in {"uhd", "hd"} else 0,
        ),
    )


def search_pexels_videos(query: str, per_page: int = 8) -> list[dict[str, Any]]:
    api_key = pexels_api_key()
    if not api_key:
        raise BackgroundAssetError(".env에 PEXELS_API_KEY를 추가해야 검색할 수 있습니다.")

    clean_query = (query or "calm library").strip()[:80]
    params = urllib.parse.urlencode(
        {
            "query": clean_query,
            "orientation": "portrait",
            "per_page": max(1, min(int(per_page), 20)),
        }
    )
    payload = _request_json(
        f"{PEXELS_VIDEO_SEARCH_URL}?{params}",
        {"Authorization": api_key, "User-Agent": "telegram-dashboard/1.0"},
    )

    results: list[dict[str, Any]] = []
    for video in payload.get("videos", []):
        best_file = _best_video_file(video)
        if not best_file:
            continue
        results.append(
            {
                "provider": "pexels",
                "provider_id": str(video.get("id")),
                "query": clean_query,
                "author": (video.get("user") or {}).get("name") or "Pexels",
                "source_url": video.get("url"),
                "preview_url": video.get("image"),
                "download_url": best_file.get("link"),
                "width": int(best_file.get("width") or video.get("width") or 0),
                "height": int(best_file.get("height") or video.get("height") or 0),
                "duration": float(video.get("duration") or 0),
            }
        )
    return results


def _safe_filename(value: str) -> str:
    name = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return name[:80] or "background"


def _download_file(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "telegram-dashboard/1.0"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("wb") as file:
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    file.write(chunk)
    except urllib.error.URLError as exc:
        raise BackgroundAssetError(f"background download failed: {exc.reason}") from exc


def save_background_asset(candidate: dict[str, Any]) -> dict[str, Any]:
    if candidate.get("provider") != "pexels":
        raise BackgroundAssetError("현재는 Pexels 배경 영상만 지원합니다.")

    provider_id = str(candidate.get("provider_id") or "").strip()
    download_url = str(candidate.get("download_url") or "").strip()
    if not provider_id or not download_url:
        raise BackgroundAssetError("다운로드할 영상 정보가 부족합니다.")

    init_db(DB_PATH)
    filename = f"pexels-{_safe_filename(provider_id)}.mp4"
    target = BACKGROUND_DIR / filename
    relative_path = target.resolve().relative_to(BASE_DIR).as_posix()

    if not target.exists():
        _download_file(download_url, target)

    with closing(connect(DB_PATH)) as conn:
        conn.execute(
            """
            INSERT INTO background_assets
                (provider, provider_id, query, author, source_url, preview_url,
                 local_path, width, height, duration)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider, provider_id) DO UPDATE SET
                query = excluded.query,
                author = excluded.author,
                source_url = excluded.source_url,
                preview_url = excluded.preview_url,
                local_path = excluded.local_path,
                width = excluded.width,
                height = excluded.height,
                duration = excluded.duration
            """,
            (
                "pexels",
                provider_id,
                candidate.get("query"),
                candidate.get("author"),
                candidate.get("source_url"),
                candidate.get("preview_url"),
                relative_path,
                int(candidate.get("width") or 0),
                int(candidate.get("height") or 0),
                float(candidate.get("duration") or 0),
            ),
        )
        conn.commit()

    return get_background_asset("pexels", provider_id) or {}


def get_background_asset(provider: str, provider_id: str) -> dict[str, Any] | None:
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT id, provider, provider_id, query, author, source_url, preview_url,
                   local_path, width, height, duration, created_at
            FROM background_assets
            WHERE provider = ? AND provider_id = ?
            """,
            (provider, provider_id),
        ).fetchone()
    return _asset_payload(dict(row)) if row else None


def get_background_asset_by_id(asset_id: int) -> dict[str, Any] | None:
    init_db(DB_PATH)
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            """
            SELECT id, provider, provider_id, query, author, source_url, preview_url,
                   local_path, width, height, duration, created_at
            FROM background_assets
            WHERE id = ?
            """,
            (asset_id,),
        ).fetchone()
    return _asset_payload(dict(row)) if row else None


def list_background_assets(limit: int = 50) -> list[dict[str, Any]]:
    init_db(DB_PATH)
    with closing(connect(DB_PATH)) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT id, provider, provider_id, query, author, source_url, preview_url,
                       local_path, width, height, duration, created_at
                FROM background_assets
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), 200)),),
            )
        ]
    return [_asset_payload(row) for row in rows]


def _asset_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    local_path = str(payload.get("local_path") or "")
    payload["video_url"] = f"/backgrounds/{Path(local_path).name}" if local_path else None
    return payload
