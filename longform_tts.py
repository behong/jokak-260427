from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from db import BASE_DIR
from tts import ffprobe_duration, silence_file, synthesize_elevenlabs_speech


SUBPROCESS_CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _edge_rate(speed: float) -> str:
    percent = int(round((float(speed) - 1.0) * 100))
    return f"{percent:+d}%"


def _edge_pitch(pitch: int) -> str:
    return f"{int(pitch):+d}Hz"


def _edge_volume(volume: float) -> str:
    percent = int(round((float(volume) - 1.0) * 100))
    return f"{percent:+d}%"


async def _edge_sentence(text: str, output: Path, voice: str, config: dict[str, Any]) -> None:
    import edge_tts

    style = str(config.get("style") or "calm").lower()
    style_speed, style_pitch = {
        "calm": (1.0, 0),
        "warm": (0.98, 1),
        "meditative": (0.94, -2),
        "gentle": (0.97, 2),
    }.get(style, (1.0, 0))
    communicate = edge_tts.Communicate(
        text=text,
        voice=voice,
        rate=_edge_rate(float(config.get("speed") or 0.85) * style_speed),
        pitch=_edge_pitch(int(config.get("pitch") or 0) + style_pitch),
        volume=_edge_volume(float(config.get("volume") or 1.0)),
    )
    await communicate.save(str(output))


def _sentence_list(paragraph: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"(?<=[.!?])\s+", str(paragraph or "").strip())
        if item.strip()
    ]


def _concat_audio(parts: list[Path], output: Path) -> float:
    concat_path = output.with_suffix(".concat.txt")
    concat_path.write_text(
        "\n".join(f"file '{part.resolve().as_posix()}'" for part in parts),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_path),
            "-vn", "-c:a", "libmp3lame", "-b:a", "128k", str(output),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=SUBPROCESS_CREATIONFLAGS,
    )
    return ffprobe_duration(output)


def synthesize_longform_narration(
    script: dict[str, Any],
    tts_config: dict[str, Any],
    work_dir: Path,
    progress: Callable[[str, int], None] | None = None,
) -> tuple[Path, list[dict[str, Any]], float]:
    work_dir.mkdir(parents=True, exist_ok=True)
    paragraphs = [str(item).strip() for item in script.get("paragraphs") or [] if str(item).strip()]
    sentence_rows: list[tuple[int, str]] = []
    for paragraph_index, paragraph in enumerate(paragraphs):
        sentence_rows.extend((paragraph_index, sentence) for sentence in _sentence_list(paragraph))
    if not sentence_rows:
        raise RuntimeError("롱폼 TTS로 읽을 원고가 없습니다.")

    provider = str(tts_config.get("provider") or "edge").lower()
    voice = str(tts_config.get("voice") or "ko-KR-HyunsuMultilingualNeural")
    elevenlabs_config = dict(tts_config.get("elevenlabs") or {})
    sentence_pause = float(tts_config.get("sentence_pause") or 0.9)
    paragraph_pause = float(tts_config.get("paragraph_pause") or 1.8)
    parts: list[Path] = []
    captions: list[dict[str, Any]] = []
    cursor = 0.0

    for index, (paragraph_index, sentence) in enumerate(sentence_rows, start=1):
        speech = work_dir / f"sentence-{index:04d}.mp3"
        if provider == "elevenlabs":
            duration = synthesize_elevenlabs_speech(
                sentence,
                speech,
                voice_id=str(elevenlabs_config.get("voice_id") or ""),
                config=elevenlabs_config,
            )
        else:
            asyncio.run(_edge_sentence(sentence, speech, voice, tts_config))
            duration = ffprobe_duration(speech)
        parts.append(speech)
        captions.append({"start": cursor, "end": cursor + duration, "text": sentence})
        cursor += duration

        next_paragraph = (
            sentence_rows[index][0]
            if index < len(sentence_rows)
            else None
        )
        pause_seconds = paragraph_pause if next_paragraph != paragraph_index else sentence_pause
        pause = work_dir / f"pause-{index:04d}.mp3"
        silence_file(pause, pause_seconds)
        parts.append(pause)
        cursor += pause_seconds
        if progress and (index == 1 or index % 5 == 0 or index == len(sentence_rows)):
            progress(f"TTS 생성 중 ({index}/{len(sentence_rows)})", 10 + int(index / len(sentence_rows) * 30))

    output = work_dir / "narration.mp3"
    duration = _concat_audio(parts, output)
    return output, captions, duration


def _srt_time(seconds: float) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _wrap_caption(text: str, max_chars: int = 24, max_lines: int = 2) -> str:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) > max_lines:
        merged = " ".join(lines)
        midpoint = min(len(merged), max_chars)
        split_at = merged.rfind(" ", 0, midpoint + 1)
        split_at = split_at if split_at > 0 else midpoint
        lines = [merged[:split_at].strip(), merged[split_at:].strip()]
    return "\n".join(lines[:max_lines])


def write_srt(captions: list[dict[str, Any]], output: Path, max_lines: int = 2) -> Path:
    blocks: list[str] = []
    for index, item in enumerate(captions, start=1):
        blocks.append(
            f"{index}\n{_srt_time(float(item['start']))} --> {_srt_time(float(item['end']))}\n"
            f"{_wrap_caption(str(item['text']), max_lines=max_lines)}"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n\n".join(blocks) + "\n", encoding="utf-8")
    return output


def create_longform_voice_preview(text: str, tts_config: dict[str, Any], label: str) -> tuple[Path, float]:
    output_dir = BASE_DIR / "outputs" / "audio" / "previews"
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_label = re.sub(r"[^A-Za-z0-9_-]+", "-", label).strip("-") or "longform"
    output = output_dir / f"longform-{safe_label}.mp3"
    config = dict(tts_config)
    if str(config.get("provider") or "edge") == "elevenlabs":
        elevenlabs_config = dict(config.get("elevenlabs") or {})
        synthesize_elevenlabs_speech(
            text[:300],
            output,
            voice_id=str(elevenlabs_config.get("voice_id") or ""),
            config=elevenlabs_config,
        )
    else:
        voice = str(config.get("voice") or "ko-KR-HyunsuMultilingualNeural")
        asyncio.run(_edge_sentence(text[:300], output, voice, config))
    return output, ffprobe_duration(output)
