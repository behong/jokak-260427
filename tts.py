from __future__ import annotations

import asyncio
import json
import os
import random
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from db import BASE_DIR, get_app_setting


VENDOR_DIR = BASE_DIR / "vendor"
if VENDOR_DIR.exists():
    sys.path.insert(0, str(VENDOR_DIR))


AUDIO_DIR = BASE_DIR / "outputs" / "audio"
DEFAULT_VOICE = "ko-KR-HyunsuMultilingualNeural"
RANDOM_VOICE = "random"
DEFAULT_RATE = "-12%"
PAGE_PAUSE_SECONDS = 0.8
ELEVENLABS_API_URL = "https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
ELEVENLABS_SUBSCRIPTION_URL = "https://api.elevenlabs.io/v1/user/subscription"
ELEVENLABS_DEFAULT_MODEL = "eleven_multilingual_v2"
SHORT_ELEVENLABS_MODEL = "eleven_flash_v2_5"
SHORT_ELEVENLABS_VOICES = (
    {
        "voice_id": "uyVNoMrnUku1dZyVEXwD",
        "name": "Anna Kim",
        "gender": "female",
        "rank": 1,
    },
    {
        "voice_id": "z6Kj0hecH20CdetSElRT",
        "name": "Jennie",
        "gender": "female",
        "rank": 2,
    },
    {
        "voice_id": "ZJCNdZEjYwkOElxugmW2",
        "name": "Hyuk",
        "gender": "male",
        "rank": 1,
    },
    {
        "voice_id": "PDoCXqBQFGsvfO0hNkEs",
        "name": "Chris",
        "gender": "male",
        "rank": 2,
    },
)
VOICE_OPTIONS = {
    "ko-KR-HyunsuNeural": "현수 한국어 전용 남성 음성",
    RANDOM_VOICE: "랜덤 한국어 음성",
    "ko-KR-HyunsuMultilingualNeural": "차분한 기본 음성",
    "ko-KR-InJoonNeural": "차분한 남성 음성",
    "ko-KR-SunHiNeural": "차분한 여성 음성",
    "en-US-AvaMultilingualNeural": "Ava 멀티링구얼 여성",
    "en-US-EmmaMultilingualNeural": "Emma 멀티링구얼 여성",
    "en-US-BrianMultilingualNeural": "Brian 멀티링구얼 남성",
    "fr-FR-VivienneMultilingualNeural": "Vivienne 멀티링구얼 여성",
    "fr-FR-RemyMultilingualNeural": "Remy 멀티링구얼 남성",
    "de-DE-SeraphinaMultilingualNeural": "Seraphina 멀티링구얼 여성",
    "de-DE-FlorianMultilingualNeural": "Florian 멀티링구얼 남성",
}
RANDOM_KOREAN_VOICES = [
    voice for voice in VOICE_OPTIONS
    if voice != RANDOM_VOICE
]
RATE_OPTIONS = {
    "slow": "-18%",
    "normal": "-10%",
    "fast": "+0%",
}
KST = timezone(timedelta(hours=9))


def elevenlabs_subscription_usage() -> dict[str, object]:
    api_key = str(
        get_app_setting("ELEVENLABS_API_KEY", os.getenv("ELEVENLABS_API_KEY", "")) or ""
    ).strip()
    if not api_key:
        return {
            "configured": False,
            "available": False,
            "error_code": "not_configured",
            "message": "ElevenLabs API Key가 설정되지 않았습니다.",
        }

    request = urllib.request.Request(
        ELEVENLABS_SUBSCRIPTION_URL,
        headers={"xi-api-key": api_key, "Accept": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            subscription = json.load(response)
    except urllib.error.HTTPError as exc:
        detail_text = exc.read().decode("utf-8", errors="replace")
        try:
            detail_payload = json.loads(detail_text)
        except json.JSONDecodeError:
            detail_payload = {}
        detail = detail_payload.get("detail") if isinstance(detail_payload, dict) else {}
        detail = detail if isinstance(detail, dict) else {}
        missing_user_read = (
            exc.code in {401, 403}
            and detail.get("code") == "unauthorized"
            and "user_read" in str(detail.get("message") or "")
        )
        if missing_user_read:
            return {
                "configured": True,
                "available": False,
                "error_code": "missing_user_read",
                "message": "현재 API Key에 사용량 조회 권한(user_read)이 없습니다.",
            }
        return {
            "configured": True,
            "available": False,
            "error_code": f"http_{exc.code}",
            "message": "ElevenLabs 사용량을 조회하지 못했습니다.",
        }
    except (urllib.error.URLError, TimeoutError, OSError):
        return {
            "configured": True,
            "available": False,
            "error_code": "connection_failed",
            "message": "ElevenLabs 사용량 서버에 연결하지 못했습니다.",
        }

    used = max(0, int(subscription.get("character_count") or 0))
    limit = max(0, int(subscription.get("character_limit") or 0))
    remaining = max(0, limit - used)
    reset_unix = subscription.get("next_character_count_reset_unix")
    reset_at = None
    if reset_unix:
        try:
            reset_at = datetime.fromtimestamp(int(reset_unix), timezone.utc).astimezone(KST).isoformat()
        except (TypeError, ValueError, OSError):
            reset_at = None
    overage = subscription.get("current_overage")
    return {
        "configured": True,
        "available": True,
        "tier": str(subscription.get("tier") or ""),
        "status": str(subscription.get("status") or ""),
        "used": used,
        "limit": limit,
        "remaining": remaining,
        "usage_percent": round((used / limit * 100) if limit else 0.0, 1),
        "reset_at_kst": reset_at,
        "billing_period": str(subscription.get("billing_period") or ""),
        "current_overage": overage if isinstance(overage, dict) else None,
    }


def short_tts_provider() -> str:
    provider = str(
        get_app_setting("SHORT_TTS_PROVIDER", os.getenv("SHORT_TTS_PROVIDER", "elevenlabs"))
        or "elevenlabs"
    ).strip().lower()
    return provider if provider in {"edge", "elevenlabs"} else "elevenlabs"


def short_elevenlabs_model() -> str:
    model = str(
        get_app_setting(
            "SHORT_ELEVENLABS_MODEL_ID",
            os.getenv("SHORT_ELEVENLABS_MODEL_ID", SHORT_ELEVENLABS_MODEL),
        )
        or SHORT_ELEVENLABS_MODEL
    ).strip()
    return model if model in {"eleven_flash_v2_5", "eleven_multilingual_v2"} else SHORT_ELEVENLABS_MODEL


def random_short_elevenlabs_voice() -> dict[str, object]:
    return dict(random.choice(SHORT_ELEVENLABS_VOICES))


def resolve_voice(voice: str | None) -> str:
    if not voice or voice == RANDOM_VOICE:
        return random.choice(RANDOM_KOREAN_VOICES)
    return voice


def ffprobe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)
    return float(payload["format"]["duration"])


async def _synthesize_edge(text: str, output: Path, voice: str, rate: str) -> None:
    try:
        import edge_tts
    except ImportError as exc:
        raise RuntimeError(
            "edge-tts를 불러오지 못했습니다. requirements 설치 또는 vendor 폴더를 확인하세요."
        ) from exc

    communicate = edge_tts.Communicate(text=text, voice=voice, rate=rate)
    await communicate.save(str(output))


def synthesize_speech(
    text: str,
    output: Path,
    voice: str = DEFAULT_VOICE,
    rate: str = DEFAULT_RATE,
) -> tuple[float, str]:
    output.parent.mkdir(parents=True, exist_ok=True)
    used_voice = resolve_voice(voice)
    try:
        asyncio.run(_synthesize_edge(text, output, used_voice, rate))
    except Exception:
        if used_voice == DEFAULT_VOICE:
            raise
        used_voice = DEFAULT_VOICE
        asyncio.run(_synthesize_edge(text, output, DEFAULT_VOICE, rate))
    return ffprobe_duration(output), used_voice


def elevenlabs_voice_speed() -> float:
    raw = os.getenv("ELEVENLABS_VOICE_SPEED", "0.90")
    try:
        value = float(raw)
    except ValueError:
        return 0.90
    return max(0.70, min(1.20, value))


def elevenlabs_voice_ids() -> list[str]:
    raw_ids = os.getenv("ELEVENLABS_VOICE_IDS", "").strip()
    voice_ids = [item.strip() for item in raw_ids.split(",") if item.strip()]
    if voice_ids:
        return voice_ids
    single_voice_id = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
    return [single_voice_id] if single_voice_id else []


def synthesize_elevenlabs_speech(
    text: str,
    output: Path,
    voice_id: str | None = None,
    config: dict[str, object] | None = None,
) -> float:
    config = config or {}
    api_key = str(get_app_setting("ELEVENLABS_API_KEY", os.getenv("ELEVENLABS_API_KEY", "")) or "").strip()
    voice_id = str(
        voice_id
        or get_app_setting("ELEVENLABS_VOICE_ID", os.getenv("ELEVENLABS_VOICE_ID", ""))
        or ""
    ).strip()
    if not api_key:
        raise RuntimeError("ELEVENLABS_API_KEY is not set")
    if not voice_id:
        raise RuntimeError("ELEVENLABS_VOICE_ID or ELEVENLABS_VOICE_IDS is not set")

    output.parent.mkdir(parents=True, exist_ok=True)
    output_format = str(config.get("output_format") or "mp3_44100_128")
    url = ELEVENLABS_API_URL.format(voice_id=urllib.parse.quote(voice_id, safe=""))
    url = f"{url}?output_format={urllib.parse.quote(output_format, safe='_')}"
    payload = {
        "text": text.strip(),
        "model_id": str(config.get("model_id") or os.getenv("ELEVENLABS_MODEL_ID", ELEVENLABS_DEFAULT_MODEL)).strip() or ELEVENLABS_DEFAULT_MODEL,
        "language_code": str(config.get("language_code") or os.getenv("ELEVENLABS_LANGUAGE_CODE", "ko")),
        "voice_settings": {
            "stability": float(config.get("stability") if config.get("stability") is not None else os.getenv("ELEVENLABS_STABILITY", "0.55")),
            "similarity_boost": float(config.get("similarity_boost") if config.get("similarity_boost") is not None else os.getenv("ELEVENLABS_SIMILARITY_BOOST", "0.75")),
            "style": float(config.get("style") if config.get("style") is not None else os.getenv("ELEVENLABS_STYLE", "0.10")),
            "use_speaker_boost": bool(config.get("speaker_boost", os.getenv("ELEVENLABS_SPEAKER_BOOST", "1").strip().lower() not in {"0", "false", "no"})),
            "speed": float(config.get("speed") if config.get("speed") is not None else elevenlabs_voice_speed()),
        },
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "xi-api-key": api_key,
        },
        method="POST",
    )
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                output.write_bytes(response.read())
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            if exc.code not in {429, 500, 502, 503, 504} or attempt == 2:
                raise RuntimeError(f"ElevenLabs TTS failed: HTTP {exc.code} {detail[:500]}") from exc
            retry_after = str(exc.headers.get("Retry-After") or "").strip()
            wait_seconds = float(retry_after) if retry_after.replace(".", "", 1).isdigit() else 2 ** attempt
            time.sleep(min(15.0, max(1.0, wait_seconds)))
        except urllib.error.URLError as exc:
            if attempt == 2:
                raise RuntimeError(f"ElevenLabs TTS failed: {exc.reason}") from exc
            time.sleep(2 ** attempt)
    return ffprobe_duration(output)


def silence_file(output: Path, duration: float) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-t",
            f"{duration:.3f}",
            "-q:a",
            "9",
            "-acodec",
            "libmp3lame",
            str(output),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def concat_audio(parts: list[Path], output: Path, reencode: bool = False) -> float:
    output.parent.mkdir(parents=True, exist_ok=True)
    concat_file = output.with_suffix(".txt")
    concat_file.write_text(
        "\n".join(f"file '{part.resolve().as_posix()}'" for part in parts),
        encoding="utf-8",
    )
    codec_args = [
        "-vn",
        "-acodec",
        "libmp3lame",
        "-b:a",
        "128k",
    ] if reencode else ["-c", "copy"]
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_file),
            *codec_args,
            str(output),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return ffprobe_duration(output)


def create_narration_audio(
    pages: list[dict[str, object]],
    job_key: str,
    voice: str = DEFAULT_VOICE,
    rate: str = DEFAULT_RATE,
) -> tuple[Path, list[float]]:
    voice = resolve_voice(voice)
    job_dir = AUDIO_DIR / job_key
    job_dir.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    durations: list[float] = []

    for index, page in enumerate(pages, start=1):
        speech_path = job_dir / f"page-{index:02d}.mp3"
        silence_path = job_dir / f"pause-{index:02d}.mp3"
        duration, _used_voice = synthesize_speech(str(page.get("tts_text") or page.get("text") or ""), speech_path, voice, rate)
        silence_file(silence_path, PAGE_PAUSE_SECONDS)
        parts.extend([speech_path, silence_path])
        durations.append(duration + PAGE_PAUSE_SECONDS)

    output = job_dir / "narration.mp3"
    concat_audio(parts, output)
    return output, durations


def create_short_elevenlabs_narration_audio(
    pages: list[dict[str, object]],
    job_key: str,
) -> tuple[Path, list[float], dict[str, object]]:
    job_dir = AUDIO_DIR / job_key
    job_dir.mkdir(parents=True, exist_ok=True)
    selected_voice = random_short_elevenlabs_voice()
    voice_id = str(selected_voice["voice_id"])
    model_id = short_elevenlabs_model()
    parts: list[Path] = []
    durations: list[float] = []
    config = {
        "model_id": model_id,
        "language_code": "ko",
        "output_format": "mp3_44100_128",
        "stability": 0.65,
        "similarity_boost": 0.75,
        "style": 0.05,
        "speaker_boost": True,
        "speed": 0.96,
    }

    for index, page in enumerate(pages, start=1):
        clean_text = " ".join(str(page.get("tts_text") or page.get("text") or "").split())
        if not clean_text:
            continue
        speech_path = job_dir / f"elevenlabs-short-{index:02d}.mp3"
        silence_path = job_dir / f"pause-{index:02d}.mp3"
        duration = synthesize_elevenlabs_speech(
            clean_text,
            speech_path,
            voice_id=voice_id,
            config=config,
        )
        silence_file(silence_path, PAGE_PAUSE_SECONDS)
        parts.extend([speech_path, silence_path])
        durations.append(duration + PAGE_PAUSE_SECONDS)

    if not parts:
        raise RuntimeError("No text available for ElevenLabs short narration")

    output = job_dir / "elevenlabs-short-narration.mp3"
    concat_audio(parts, output, reencode=True)
    selected_voice["model_id"] = model_id
    return output, durations, selected_voice


def create_short_elevenlabs_preview(text: str) -> tuple[Path, float, dict[str, object]]:
    selected_voice = random_short_elevenlabs_voice()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output = AUDIO_DIR / "previews" / f"elevenlabs-short-preview-{stamp}.mp3"
    preview_text = text.strip() or "오늘 마음에 남겨둘 따뜻한 문장을 전해드립니다."
    model_id = short_elevenlabs_model()
    duration = synthesize_elevenlabs_speech(
        preview_text[:180],
        output,
        voice_id=str(selected_voice["voice_id"]),
        config={
            "model_id": model_id,
            "language_code": "ko",
            "output_format": "mp3_44100_128",
            "stability": 0.65,
            "similarity_boost": 0.75,
            "style": 0.05,
            "speaker_boost": True,
            "speed": 0.96,
        },
    )
    selected_voice["model_id"] = model_id
    return output, duration, selected_voice


def create_elevenlabs_long_narration_audio(texts: list[str], job_key: str) -> tuple[Path, list[float]]:
    job_dir = AUDIO_DIR / job_key
    job_dir.mkdir(parents=True, exist_ok=True)
    parts: list[Path] = []
    durations: list[float] = []
    voice_ids = elevenlabs_voice_ids()
    if not voice_ids:
        raise RuntimeError("ELEVENLABS_VOICE_ID or ELEVENLABS_VOICE_IDS is not set")

    for index, text in enumerate(texts, start=1):
        clean_text = " ".join(str(text or "").split())
        if not clean_text:
            continue
        voice_id = voice_ids[(index - 1) % len(voice_ids)]
        speech_path = job_dir / f"elevenlabs-{index:02d}-{voice_id}.mp3"
        silence_path = job_dir / f"pause-{index:02d}.mp3"
        duration = synthesize_elevenlabs_speech(clean_text[:4500], speech_path, voice_id=voice_id)
        silence_file(silence_path, PAGE_PAUSE_SECONDS)
        parts.extend([speech_path, silence_path])
        durations.append(duration + PAGE_PAUSE_SECONDS)

    if not parts:
        raise RuntimeError("No text available for ElevenLabs narration")

    output = job_dir / "elevenlabs-long-narration.mp3"
    concat_audio(parts, output, reencode=True)
    return output, durations


def create_preview_audio(text: str, voice: str, rate: str) -> tuple[Path, float, str]:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = AUDIO_DIR / "previews" / f"tts-preview-{stamp}.mp3"
    preview_text = text.strip() or "안녕하세요. 지혜로운 조각들입니다. 오늘의 문장을 천천히 읽어드립니다."
    duration, used_voice = synthesize_speech(preview_text[:180], output, voice, rate)
    return output, duration, used_voice
