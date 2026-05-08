from __future__ import annotations

import asyncio
import json
import random
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from db import BASE_DIR


VENDOR_DIR = BASE_DIR / "vendor"
if VENDOR_DIR.exists():
    sys.path.insert(0, str(VENDOR_DIR))


AUDIO_DIR = BASE_DIR / "outputs" / "audio"
DEFAULT_VOICE = "ko-KR-HyunsuMultilingualNeural"
RANDOM_VOICE = "random"
DEFAULT_RATE = "-12%"
PAGE_PAUSE_SECONDS = 0.8
VOICE_OPTIONS = {
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


def concat_audio(parts: list[Path], output: Path) -> float:
    output.parent.mkdir(parents=True, exist_ok=True)
    concat_file = output.with_suffix(".txt")
    concat_file.write_text(
        "\n".join(f"file '{part.resolve().as_posix()}'" for part in parts),
        encoding="utf-8",
    )
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
            "-c",
            "copy",
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


def create_preview_audio(text: str, voice: str, rate: str) -> tuple[Path, float, str]:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = AUDIO_DIR / "previews" / f"tts-preview-{stamp}.mp3"
    preview_text = text.strip() or "안녕하세요. 지혜로운 조각들입니다. 오늘의 문장을 천천히 읽어드립니다."
    duration, used_voice = synthesize_speech(preview_text[:180], output, voice, rate)
    return output, duration, used_voice
