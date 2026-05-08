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

from db import BASE_DIR, DB_PATH, get_app_setting, connect, init_db


BACKGROUND_DIR = BASE_DIR / "assets" / "backgrounds"
PEXELS_VIDEO_SEARCH_URL = "https://api.pexels.com/videos/search"
ACTIVE_BACKGROUND_LIMIT = 500


class BackgroundAssetError(RuntimeError):
    pass


def pexels_api_key() -> str:
    return get_app_setting("PEXELS_API_KEY", os.getenv("PEXELS_API_KEY", "")).strip()


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
    return min(
        candidates,
        key=lambda item: (
            abs(int(item.get("height") or 0) - 1920),
            0 if int(item.get("height") or 0) >= 1080 else 1,
            0 if item.get("quality") in {"hd", "uhd"} else 1,
            abs(int(item.get("width") or 0) - 1080),
        ),
    )


def search_pexels_videos(query: str, per_page: int = 8, page: int = 1) -> list[dict[str, Any]]:
    api_key = pexels_api_key()
    if not api_key:
        raise BackgroundAssetError(".env에 PEXELS_API_KEY를 추가해야 검색할 수 있습니다.")

    clean_query = (query or "calm library").strip()[:80]
    params = urllib.parse.urlencode(
        {
            "query": clean_query,
            "orientation": "portrait",
            "per_page": max(1, min(int(per_page), 20)),
            "page": max(1, int(page)),
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
                   local_path, width, height, duration, enabled, collection, created_at
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
                   local_path, width, height, duration, enabled, collection, created_at
            FROM background_assets
            WHERE id = ?
            """,
            (asset_id,),
        ).fetchone()
    return _asset_payload(dict(row)) if row else None


def list_background_assets(limit: int = 50, active_only: bool = False) -> list[dict[str, Any]]:
    init_db(DB_PATH)
    where = "WHERE enabled = 1" if active_only else ""
    with closing(connect(DB_PATH)) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT id, provider, provider_id, query, author, source_url, preview_url,
                       local_path, width, height, duration, enabled, collection, created_at
                FROM background_assets
                {where}
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(1, min(int(limit), ACTIVE_BACKGROUND_LIMIT)),),
            )
        ]
    return [_asset_payload(row) for row in rows]


def update_background_asset(
    asset_id: int,
    enabled: bool | None = None,
    collection: str | None = None,
) -> dict[str, Any] | None:
    init_db(DB_PATH)
    updates: list[str] = []
    params: list[Any] = []
    if enabled is not None:
        updates.append("enabled = ?")
        params.append(1 if enabled else 0)
    if collection is not None:
        updates.append("collection = ?")
        params.append(collection.strip()[:80] or None)
    if updates:
        params.append(asset_id)
        with closing(connect(DB_PATH)) as conn:
            conn.execute(
                f"UPDATE background_assets SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            conn.commit()
    return get_background_asset_by_id(asset_id)


def list_background_collections() -> list[dict[str, Any]]:
    init_db(DB_PATH)
    with closing(connect(DB_PATH)) as conn:
        rows = [
            dict(row)
            for row in conn.execute(
                """
                SELECT COALESCE(collection, '') AS collection,
                       COUNT(*) AS count,
                       SUM(enabled) AS active_count
                FROM background_assets
                GROUP BY COALESCE(collection, '')
                ORDER BY collection
                """
            )
        ]
    return rows


def activate_background_collection(collection: str) -> dict[str, Any]:
    clean_collection = collection.strip()[:80]
    if not clean_collection:
        raise BackgroundAssetError("활성화할 분류를 선택하세요.")
    init_db(DB_PATH)
    with closing(connect(DB_PATH)) as conn:
        conn.execute("UPDATE background_assets SET enabled = 0")
        conn.execute(
            """
            UPDATE background_assets
            SET enabled = 1
            WHERE id IN (
                SELECT id
                FROM background_assets
                WHERE collection = ?
                  AND COALESCE(height, 0) >= 1080
                  AND COALESCE(duration, 0) >= 6
                ORDER BY id DESC
                LIMIT ?
            )
            """,
            (clean_collection, ACTIVE_BACKGROUND_LIMIT),
        )
        conn.commit()
    return {
        "collection": clean_collection,
        "active_limit": ACTIVE_BACKGROUND_LIMIT,
        "collections": list_background_collections(),
    }


def count_background_assets() -> int:
    init_db(DB_PATH)
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute("SELECT COUNT(*) AS count FROM background_assets").fetchone()
    return int(row["count"] if row else 0)


def _asset_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = dict(row)
    local_path = str(payload.get("local_path") or "")
    payload["video_url"] = f"/backgrounds/{Path(local_path).name}" if local_path else None
    return payload
