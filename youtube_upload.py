from __future__ import annotations

import os
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from db import BASE_DIR, get_app_setting, set_app_setting


DEFAULT_CLIENT_SECRETS_FILE = BASE_DIR / "client_secrets.json"
ALT_CLIENT_SECRET_FILE = BASE_DIR / "client_secret.json"
CLIENT_SECRETS_FILE = Path(os.getenv("YOUTUBE_CLIENT_SECRETS", DEFAULT_CLIENT_SECRETS_FILE))
TOKEN_FILE = Path(os.getenv("YOUTUBE_TOKEN_FILE", BASE_DIR / "youtube_token.json"))
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl",
]
MAX_YOUTUBE_TITLE_LENGTH = 100
MAX_YOUTUBE_DESCRIPTION_LENGTH = 5000
MAX_YOUTUBE_TAG_LENGTH = 500


def materialize_youtube_files_from_settings() -> None:
    client_json = get_app_setting("YOUTUBE_CLIENT_SECRET_JSON", "")
    if client_json and not ALT_CLIENT_SECRET_FILE.exists() and not DEFAULT_CLIENT_SECRETS_FILE.exists():
        ALT_CLIENT_SECRET_FILE.write_text(client_json, encoding="utf-8")
    token_json = get_app_setting("YOUTUBE_TOKEN_JSON", "")
    if token_json and not TOKEN_FILE.exists():
        TOKEN_FILE.write_text(token_json, encoding="utf-8")


class YouTubeUploadError(RuntimeError):
    pass


def clear_youtube_token() -> None:
    if TOKEN_FILE.exists():
        TOKEN_FILE.unlink()
    set_app_setting("YOUTUBE_TOKEN_JSON", "", is_secret=True)


def is_invalid_grant_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "invalid_grant" in message or "expired or revoked" in message


def _clean_youtube_text(value: str, max_length: int) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = []
    for char in normalized:
        if char in "\n\t":
            cleaned.append(char)
            continue
        category = unicodedata.category(char)
        if category.startswith("C"):
            continue
        if char in "<>":
            cleaned.append(" ")
            continue
        cleaned.append(char)
    text = "".join(cleaned)
    lines = [" ".join(line.split()) for line in text.split("\n")]
    text = "\n".join(lines).strip()
    if len(text) <= max_length:
        return text
    return text[: max_length - 3].rstrip() + "..."


def sanitize_youtube_metadata(
    title: str,
    description: str,
    tags: list[str],
) -> tuple[str, str, list[str]]:
    clean_title = _clean_youtube_text(title, MAX_YOUTUBE_TITLE_LENGTH) or "오늘의 문장"
    clean_description = _clean_youtube_text(description, MAX_YOUTUBE_DESCRIPTION_LENGTH)
    clean_tags: list[str] = []
    total_tag_length = 0
    for tag in tags:
        clean_tag = _clean_youtube_text(str(tag), 60).replace(",", " ").strip()
        if not clean_tag or clean_tag in clean_tags:
            continue
        next_total = total_tag_length + len(clean_tag)
        if next_total > MAX_YOUTUBE_TAG_LENGTH:
            break
        clean_tags.append(clean_tag)
        total_tag_length = next_total
    return clean_title, clean_description, clean_tags


def youtube_config_status() -> dict[str, Any]:
    materialize_youtube_files_from_settings()
    client_file = resolved_client_secrets_file()
    token_has_required_scopes = False
    token_needs_reauth = False
    if TOKEN_FILE.exists():
        try:
            Credentials, _Flow, _InstalledAppFlow, Request, _HttpError, _build, _media = _import_google_clients()
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
            token_has_required_scopes = creds.has_scopes(SCOPES)
            if token_has_required_scopes and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                    token_json = creds.to_json()
                    TOKEN_FILE.write_text(token_json, encoding="utf-8")
                    set_app_setting("YOUTUBE_TOKEN_JSON", token_json, is_secret=True)
                except Exception as exc:
                    if is_invalid_grant_error(exc):
                        clear_youtube_token()
                        token_needs_reauth = True
                    token_has_required_scopes = False
        except Exception:
            token_has_required_scopes = False
    return {
        "client_secrets_path": str(client_file),
        "client_secrets_exists": client_file.exists(),
        "token_path": str(TOKEN_FILE),
        "token_exists": TOKEN_FILE.exists(),
        "token_has_required_scopes": token_has_required_scopes,
        "token_needs_reauth": token_needs_reauth,
        "scopes": SCOPES,
    }


def resolved_client_secrets_file() -> Path:
    materialize_youtube_files_from_settings()
    if CLIENT_SECRETS_FILE.exists():
        return CLIENT_SECRETS_FILE
    if "YOUTUBE_CLIENT_SECRETS" not in os.environ and ALT_CLIENT_SECRET_FILE.exists():
        return ALT_CLIENT_SECRET_FILE
    return CLIENT_SECRETS_FILE


def _import_google_clients():
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import Flow, InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.errors import HttpError
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
    except ImportError as exc:
        raise YouTubeUploadError(
            "YouTube 업로드 패키지가 설치되지 않았습니다. pip install -r requirements.txt 를 실행하세요."
        ) from exc
    return Credentials, Flow, InstalledAppFlow, Request, HttpError, build, MediaFileUpload


def youtube_credentials(interactive: bool = True):
    materialize_youtube_files_from_settings()
    Credentials, _Flow, InstalledAppFlow, Request, _HttpError, _build, _media = _import_google_clients()
    client_secrets_file = resolved_client_secrets_file()
    if not client_secrets_file.exists():
        raise YouTubeUploadError(
            f"Google OAuth 클라이언트 파일이 없습니다: {client_secrets_file}"
        )

    creds = None
    if TOKEN_FILE.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if creds and not creds.has_scopes(SCOPES):
        if not interactive:
            raise YouTubeUploadError("YouTube 인증 권한이 부족합니다. 유튜브 인증을 다시 진행하세요.")
        creds = None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception as exc:
            if is_invalid_grant_error(exc):
                clear_youtube_token()
                raise YouTubeUploadError(
                    "YouTube 인증 토큰이 만료되었거나 취소되었습니다. YouTube 인증을 다시 진행해 주세요."
                ) from exc
            raise

    if (not creds or not creds.valid) and not interactive:
        raise YouTubeUploadError("YouTube 인증이 필요합니다. 유튜브 인증을 먼저 진행하세요.")

    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets_file), SCOPES)
        creds = flow.run_local_server(port=0)

    token_json = creds.to_json()
    TOKEN_FILE.write_text(token_json, encoding="utf-8")
    set_app_setting("YOUTUBE_TOKEN_JSON", token_json, is_secret=True)
    return creds


def youtube_auth_flow(redirect_uri: str, state: str | None = None):
    materialize_youtube_files_from_settings()
    _Credentials, Flow, _InstalledAppFlow, _Request, _HttpError, _build, _media = _import_google_clients()
    client_secrets_file = resolved_client_secrets_file()
    if not client_secrets_file.exists():
        raise YouTubeUploadError(
            f"Google OAuth 클라이언트 파일이 없습니다: {client_secrets_file}"
        )
    return Flow.from_client_secrets_file(
        str(client_secrets_file),
        scopes=SCOPES,
        redirect_uri=redirect_uri,
        state=state,
    )


def youtube_authorization_url(redirect_uri: str) -> tuple[str, str]:
    flow = youtube_auth_flow(redirect_uri)
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    return authorization_url, state


def save_youtube_token_from_response(redirect_uri: str, authorization_response: str, state: str):
    os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")
    flow = youtube_auth_flow(redirect_uri, state=state)
    flow.fetch_token(authorization_response=authorization_response)
    token_json = flow.credentials.to_json()
    TOKEN_FILE.write_text(token_json, encoding="utf-8")
    set_app_setting("YOUTUBE_TOKEN_JSON", token_json, is_secret=True)
    return flow.credentials


def upload_video(
    video_path: Path,
    title: str,
    description: str,
    tags: list[str],
    privacy_status: str = "private",
    category_id: str = "22",
    publish_at: datetime | None = None,
) -> dict[str, Any]:
    if privacy_status not in {"private", "unlisted", "public"}:
        raise YouTubeUploadError("privacy_status must be private, unlisted, or public")
    if not video_path.exists():
        raise YouTubeUploadError(f"Video file not found: {video_path}")
    title, description, tags = sanitize_youtube_metadata(title, description, tags)

    _credentials, _flow, _installed_flow, _request, _HttpError, build, MediaFileUpload = _import_google_clients()
    credentials = youtube_credentials(interactive=False)
    youtube = build("youtube", "v3", credentials=credentials)
    status_body = {
        "privacyStatus": privacy_status,
        "selfDeclaredMadeForKids": False,
    }
    if publish_at:
        if publish_at.tzinfo is None:
            publish_at = publish_at.replace(tzinfo=timezone.utc)
        publish_at_utc = publish_at.astimezone(timezone.utc)
        status_body["privacyStatus"] = "private"
        status_body["publishAt"] = publish_at_utc.isoformat().replace("+00:00", "Z")

    body = {
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": category_id,
        },
        "status": status_body,
    }
    media = MediaFileUpload(str(video_path), chunksize=-1, resumable=True)
    request = youtube.videos().insert(
        part=",".join(body.keys()),
        body=body,
        media_body=media,
    )
    response = None
    try:
        while response is None:
            _status, response = request.next_chunk()
    except Exception as exc:
        _raise_youtube_api_error(exc)
    video_id = response["id"]
    return {
        "youtube_video_id": video_id,
        "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
        "scheduled_publish_at": status_body.get("publishAt"),
        "response": response,
    }


def youtube_service():
    _credentials, _flow, _installed_flow, _request, _HttpError, build, _media = _import_google_clients()
    return build("youtube", "v3", credentials=youtube_credentials(interactive=False))


def _raise_youtube_api_error(exc: Exception) -> None:
    message = str(exc)
    if is_invalid_grant_error(exc):
        clear_youtube_token()
        raise YouTubeUploadError(
            "YouTube 인증 토큰이 만료되었거나 취소되었습니다. YouTube 인증을 다시 진행해 주세요."
        ) from exc
    if "insufficient" in message.lower() and "scope" in message.lower():
        raise YouTubeUploadError("YouTube 인증 권한이 부족합니다. 유튜브 인증을 다시 진행하세요.") from exc
    if "invaliddescription" in message.lower() or "invalid video description" in message.lower():
        raise YouTubeUploadError("YouTube 설명란에 허용되지 않는 문자가 있거나 길이가 너무 깁니다. 설명을 정리한 뒤 다시 시도하세요.") from exc
    raise YouTubeUploadError(message) from exc


def get_video_details(video_id: str) -> dict[str, Any]:
    youtube = youtube_service()
    try:
        response = youtube.videos().list(
            part="snippet,status",
            id=video_id,
        ).execute()
    except Exception as exc:
        _raise_youtube_api_error(exc)
    items = response.get("items") or []
    if not items:
        raise YouTubeUploadError(f"YouTube 영상을 찾을 수 없습니다: {video_id}")
    item = items[0]
    snippet = item.get("snippet") or {}
    status = item.get("status") or {}
    return {
        "youtube_video_id": video_id,
        "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
        "studio_url": f"https://studio.youtube.com/video/{video_id}/edit",
        "title": snippet.get("title") or "",
        "description": snippet.get("description") or "",
        "tags": snippet.get("tags") or [],
        "category_id": snippet.get("categoryId") or "22",
        "privacy_status": status.get("privacyStatus") or "private",
        "embeddable": status.get("embeddable"),
        "public_stats_viewable": status.get("publicStatsViewable"),
    }


def get_video_statistics(video_id: str) -> dict[str, int]:
    youtube = youtube_service()
    try:
        response = youtube.videos().list(
            part="statistics",
            id=video_id,
        ).execute()
    except Exception as exc:
        _raise_youtube_api_error(exc)
    items = response.get("items") or []
    if not items:
        raise YouTubeUploadError(f"YouTube 영상 정보를 찾을 수 없습니다: {video_id}")
    statistics = items[0].get("statistics") or {}
    return {
        "view_count": int(statistics.get("viewCount") or 0),
        "like_count": int(statistics.get("likeCount") or 0),
        "comment_count": int(statistics.get("commentCount") or 0),
    }


def update_video_metadata(
    video_id: str,
    title: str,
    description: str,
    tags: list[str],
    privacy_status: str,
) -> dict[str, Any]:
    if privacy_status not in {"private", "unlisted", "public"}:
        raise YouTubeUploadError("privacy_status must be private, unlisted, or public")
    title, description, tags = sanitize_youtube_metadata(title, description, tags)

    current = get_video_details(video_id)
    youtube = youtube_service()
    body = {
        "id": video_id,
        "snippet": {
            "title": title,
            "description": description,
            "tags": tags,
            "categoryId": current["category_id"],
        },
        "status": {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": False,
        },
    }
    try:
        response = youtube.videos().update(
            part="snippet,status",
            body=body,
        ).execute()
    except Exception as exc:
        _raise_youtube_api_error(exc)
    snippet = response.get("snippet") or {}
    status = response.get("status") or {}
    return {
        "youtube_video_id": video_id,
        "youtube_url": f"https://www.youtube.com/watch?v={video_id}",
        "studio_url": f"https://studio.youtube.com/video/{video_id}/edit",
        "title": snippet.get("title") or title[:100],
        "description": snippet.get("description") or description,
        "tags": snippet.get("tags") or tags,
        "privacy_status": status.get("privacyStatus") or privacy_status,
        "response": response,
    }
