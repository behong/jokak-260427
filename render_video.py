from __future__ import annotations

import argparse
import json
import math
import os
import random
import sqlite3
import subprocess
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Callable

from moviepy import vfx
from moviepy.video.VideoClip import ImageClip
from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip, concatenate_videoclips
from moviepy.video.io.ImageSequenceClip import ImageSequenceClip
from moviepy.video.io.VideoFileClip import VideoFileClip
from PIL import Image, ImageDraw, ImageFont

from backgrounds import get_background_asset_by_id, list_background_assets
from bgm import random_bgm_asset
from db import BASE_DIR, DB_PATH, get_app_setting, init_db
from tts import (
    DEFAULT_RATE,
    DEFAULT_VOICE,
    VOICE_OPTIONS,
    create_narration_audio,
    create_short_elevenlabs_narration_audio,
    resolve_voice,
    short_tts_provider,
)
from video_script import generate_video_script


OUTPUT_DIR = BASE_DIR / "outputs"
FRAME_DIR = OUTPUT_DIR / "frames"
SUBPROCESS_CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
WIDTH = 1080
HEIGHT = 1920
FPS = 24
BACKGROUND_SEGMENT_SECONDS = 10
BACKGROUND_POOL_LIMIT = 500
# Four daily shorts: keep every background segment out of rotation for about a month.
RECENT_BACKGROUND_EXCLUDE_COUNT = 120
BACKGROUND_FADE_SECONDS = 0.7
RENDER_PRESET = os.getenv("VIDEO_RENDER_PRESET", "medium")
RENDER_CRF = os.getenv("VIDEO_RENDER_CRF", "16")
RENDER_THREADS = int(os.getenv("VIDEO_RENDER_THREADS", "4"))
RENDER_ENGINE = os.getenv("VIDEO_RENDER_ENGINE", "ffmpeg")
ENABLE_TTS_DEFAULT = os.getenv("VIDEO_TTS_ENABLED", "1") != "0"
SUPPORTED_SOURCES = {"글반장", "글반장모음", "직접입력", "사람로"}
BACKGROUND = (246, 242, 233)
TEXT = (40, 39, 36)
MUTED = (112, 104, 94)
ACCENT = (122, 93, 62)
OVERLAY_TEXT = (255, 253, 246, 255)
OVERLAY_MUTED = (238, 230, 216, 245)
OVERLAY_ACCENT = (246, 224, 178, 250)
OVERLAY_SHADOW = (0, 0, 0, 230)
FOOTER_LINES = [
    "지혜로운 조각들 · 천천히 읽는 문장",
    "지혜로운 조각들 · 마음에 머무는 문장",
    "지혜로운 조각들 · 오늘의 사색",
    "지혜로운 조각들 · 조용히 건네는 문장",
    "지혜로운 조각들 · 생각이 머무는 곳",
]


def setting_bool(name: str, default: str = "1") -> bool:
    return get_app_setting(name, os.getenv(name, default)).strip().lower() not in {"0", "false", "no", "off"}


def setting_float(name: str, default: str) -> float:
    try:
        return float(get_app_setting(name, os.getenv(name, default)))
    except (TypeError, ValueError):
        return float(default)


def first_existing_font(*paths: str) -> str:
    for path in paths:
        if Path(path).exists():
            return path
    return paths[-1]


TITLE_FONT = first_existing_font(
    "C:/Windows/Fonts/NanumMyeongjoBold.ttf",
    "C:/Windows/Fonts/malgunbd.ttf",
    "C:/Windows/Fonts/malgun.ttf",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)
BODY_FONT = first_existing_font(
    "C:/Windows/Fonts/NanumMyeongjo.ttf",
    "C:/Windows/Fonts/malgun.ttf",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)
BODY_BOLD_FONT = first_existing_font(
    "C:/Windows/Fonts/NanumMyeongjoBold.ttf",
    "C:/Windows/Fonts/malgunbd.ttf",
    "C:/Windows/Fonts/malgun.ttf",
    "/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)
SMALL_FONT = first_existing_font(
    "C:/Windows/Fonts/malgun.ttf",
    "C:/Windows/Fonts/NotoSansCJKkr-Regular.otf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def ffmpeg_font_path(path: str) -> str:
    return path.replace("\\", "/").replace(":", "\\:")


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    try:
        return ImageFont.truetype(path, size=size)
    except OSError:
        return ImageFont.load_default(size=size)


def text_size(draw: ImageDraw.ImageDraw, text: str, text_font: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=text_font)
    return box[2] - box[0], box[3] - box[1]


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    y: int,
    text: str,
    text_font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> list[int]:
    width, _ = text_size(draw, text, text_font)
    draw.text(((WIDTH - width) / 2, y), text, font=text_font, fill=fill)


def draw_centered_text_shadow(
    draw: ImageDraw.ImageDraw,
    y: int,
    text: str,
    text_font: ImageFont.ImageFont,
    fill: tuple[int, ...],
    shadow_fill: tuple[int, ...] = OVERLAY_SHADOW,
) -> None:
    width, _ = text_size(draw, text, text_font)
    x = (WIDTH - width) / 2
    for dx, dy in ((0, 7), (4, 4), (-4, 4)):
        draw.text((x + dx, y + dy), text, font=text_font, fill=shadow_fill)
    draw.text(
        (x, y),
        text,
        font=text_font,
        fill=fill,
        stroke_width=5,
        stroke_fill=(0, 0, 0, 205),
    )


def draw_left_text_shadow(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    text: str,
    text_font: ImageFont.ImageFont,
    fill: tuple[int, ...],
) -> None:
    draw.text((x + 2, y + 3), text, font=text_font, fill=(0, 0, 0, 180))
    draw.text(
        (x, y),
        text,
        font=text_font,
        fill=fill,
        stroke_width=3,
        stroke_fill=(0, 0, 0, 180),
    )


def draw_wrapped_centered_lines(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    y: int,
    text_font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
    line_gap: int = 34,
) -> int:
    current_y = y
    for line in lines:
        width, height = text_size(draw, line, text_font)
        draw.text(((WIDTH - width) / 2, current_y), line, font=text_font, fill=fill)
        current_y += height + line_gap
    return current_y


def draw_wrapped_centered_lines_shadow(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    y: int,
    text_font: ImageFont.ImageFont,
    fill: tuple[int, ...],
    line_gap: int = 34,
) -> int:
    current_y = y
    for line in lines:
        width, height = text_size(draw, line, text_font)
        x = (WIDTH - width) / 2
        for dx, dy in ((0, 8), (4, 5), (-4, 5)):
            draw.text((x + dx, current_y + dy), line, font=text_font, fill=OVERLAY_SHADOW)
        draw.text(
            (x, current_y),
            line,
            font=text_font,
            fill=fill,
            stroke_width=6,
            stroke_fill=(0, 0, 0, 220),
        )
        current_y += height + line_gap
    return current_y


def draw_full_text_lines_shadow(
    draw: ImageDraw.ImageDraw,
    lines: list[str],
    y: int,
    text_font: ImageFont.ImageFont,
    fill: tuple[int, ...],
    line_gap: int,
) -> int:
    current_y = y
    for line in lines:
        width, height = text_size(draw, line, text_font)
        x = (WIDTH - width) / 2
        draw.text((x, current_y + 5), line, font=text_font, fill=OVERLAY_SHADOW)
        draw.text(
            (x, current_y),
            line,
            font=text_font,
            fill=fill,
            stroke_width=4,
            stroke_fill=(0, 0, 0, 215),
        )
        current_y += height + line_gap
    return current_y


def full_text_font_size(line_count: int) -> int:
    if line_count <= 6:
        return 50
    if line_count <= 10:
        return 44
    if line_count <= 14:
        return 38
    if line_count <= 18:
        return 34
    return 30


def draw_background(draw: ImageDraw.ImageDraw) -> None:
    draw.rectangle((0, 0, WIDTH, HEIGHT), fill=BACKGROUND)
    for i in range(0, HEIGHT, 24):
        tone = 238 + int(6 * math.sin(i / 70))
        draw.line((0, i, WIDTH, i), fill=(tone, tone - 2, tone - 8), width=1)
    draw.rectangle((72, 92, WIDTH - 72, HEIGHT - 92), outline=(220, 210, 196), width=2)
    draw.rectangle((96, 116, WIDTH - 96, HEIGHT - 116), outline=(235, 228, 216), width=1)


def draw_overlay_background(draw: ImageDraw.ImageDraw) -> None:
    draw.rectangle((0, 0, WIDTH, HEIGHT), fill=(0, 0, 0, 92))
    draw.rectangle((0, 0, WIDTH, 190), fill=(0, 0, 0, 92))
    draw.rectangle((0, HEIGHT - 300, WIDTH, HEIGHT), fill=(0, 0, 0, 78))


def render_page(
    script: dict[str, object],
    page: dict[str, object],
    page_count: int,
    frame_path: Path,
    footer_text: str,
) -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw_background(draw)

    brand_font = font(TITLE_FONT, 58)
    title_font = font(BODY_BOLD_FONT, 38)
    body_font = font(BODY_FONT, 58)
    source_font = font(BODY_FONT, 48)
    small_font = font(SMALL_FONT, 28)

    draw_centered_text(draw, 190, str(script["brand"]), brand_font, ACCENT)
    draw_centered_text(draw, 285, str(script["title"]), title_font, MUTED)

    lines = list(page["lines"])
    is_source = page.get("type") == "source"
    is_full_text = page.get("type") == "full_text"
    if is_full_text:
        full_font_size = full_text_font_size(len(lines))
        full_font = font(BODY_FONT, full_font_size)
        line_gap = max(18, int(full_font_size * 0.42))
        line_heights = [text_size(draw, line, full_font)[1] for line in lines]
        total_height = sum(line_heights) + max(0, len(lines) - 1) * line_gap
        start_y = max(240, int((HEIGHT - total_height) / 2))
        draw_full_text_lines_shadow(draw, lines, start_y, full_font, OVERLAY_TEXT, line_gap)
        image.save(frame_path, quality=95)
        return

    main_font = source_font if is_source else body_font
    total_height = len(lines) * 80 + max(0, len(lines) - 1) * 28
    start_y = max(560, int((HEIGHT - total_height) / 2))
    draw_wrapped_centered_lines(draw, lines, start_y, main_font, TEXT, line_gap=46)

    draw_centered_text(
        draw,
        HEIGHT - 210,
        footer_text,
        small_font,
        MUTED,
    )

    image.save(frame_path, quality=95)


def render_overlay_page(
    script: dict[str, object],
    page: dict[str, object],
    frame_path: Path,
) -> None:
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    body_font = font(BODY_FONT, 68)
    source_font = font(BODY_FONT, 46)

    lines = list(page["lines"])
    is_source = page.get("type") == "source"
    is_full_text = page.get("type") == "full_text"
    if is_full_text:
        full_font_size = full_text_font_size(len(lines))
        full_font = font(BODY_FONT, full_font_size)
        line_gap = max(18, int(full_font_size * 0.42))
        line_heights = [text_size(draw, line, full_font)[1] for line in lines]
        total_height = sum(line_heights) + max(0, len(lines) - 1) * line_gap
        start_y = max(240, int((HEIGHT - total_height) / 2))
        draw_full_text_lines_shadow(draw, lines, start_y, full_font, OVERLAY_TEXT, line_gap)
        image.save(frame_path)
        return

    main_font = source_font if is_source else body_font
    line_gap = 42 if is_source else 58
    total_height = len(lines) * 82 + max(0, len(lines) - 1) * line_gap
    start_y = max(430, int((HEIGHT - total_height) / 2))
    draw_wrapped_centered_lines_shadow(draw, lines, start_y, main_font, OVERLAY_TEXT, line_gap=line_gap)
    image.save(frame_path)


def render_static_overlay(script: dict[str, object], frame_path: Path, footer_text: str) -> None:
    image = Image.new("RGBA", (WIDTH, HEIGHT), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw_overlay_background(draw)

    brand_font = font(TITLE_FONT, 36)
    small_font = font(SMALL_FONT, 28)
    draw_left_text_shadow(draw, 56, 54, str(script["brand"]), brand_font, OVERLAY_ACCENT)
    draw_centered_text_shadow(draw, HEIGHT - 210, footer_text, small_font, OVERLAY_MUTED)

    image.save(frame_path)


def get_log_content(log_id: int) -> str:
    init_db(DB_PATH)
    with closing(sqlite3.connect(DB_PATH)) as conn:
        row = conn.execute(
            "SELECT source, content FROM telegram_logs WHERE id = ?",
            (log_id,),
        ).fetchone()

    if row is None:
        raise RuntimeError(f"Log not found: {log_id}")
    source, content = row
    if source not in SUPPORTED_SOURCES:
        raise RuntimeError(f"Unsupported source for video rendering: {source}")
    if not content.strip():
        raise RuntimeError("Selected log has empty content")
    return content


def prepared_background_clip(path: Path, duration: float):
    clip = VideoFileClip(str(path), audio=False)
    scale = max(WIDTH / clip.w, HEIGHT / clip.h)
    resized = clip.resized((int(clip.w * scale), int(clip.h * scale)))
    cropped = resized.cropped(
        x_center=resized.w / 2,
        y_center=resized.h / 2,
        width=WIDTH,
        height=HEIGHT,
    )
    return cropped.with_effects([vfx.Loop(duration=duration)]).with_duration(duration)


def recent_background_asset_ids() -> set[int]:
    init_db(DB_PATH)
    recent_ids: set[int] = set()
    with closing(sqlite3.connect(DB_PATH)) as conn:
        rows = conn.execute(
            """
            SELECT background_asset_id, background_asset_ids
            FROM video_jobs
            WHERE background_asset_id IS NOT NULL
            ORDER BY id DESC
            LIMIT ?
            """,
            (RECENT_BACKGROUND_EXCLUDE_COUNT,),
        ).fetchall()
    for primary_id, asset_ids_json in rows:
        if primary_id is not None:
            recent_ids.add(int(primary_id))
        if asset_ids_json:
            try:
                recent_ids.update(int(value) for value in json.loads(asset_ids_json))
            except (TypeError, ValueError, json.JSONDecodeError):
                pass
    return recent_ids


def ordered_background_assets(first_asset_id: int) -> list[dict[str, object]]:
    assets = [
        asset for asset in list_background_assets(limit=BACKGROUND_POOL_LIMIT, active_only=True)
        if str(asset.get("collection") or "") != "longform-16x9"
    ]
    existing = [asset for asset in assets if (BASE_DIR / str(asset.get("local_path"))).exists()]
    selected = [asset for asset in existing if int(asset["id"]) == first_asset_id]
    recent_ids = recent_background_asset_ids()
    others = [
        asset for asset in existing
        if int(asset["id"]) != first_asset_id and int(asset["id"]) not in recent_ids
    ]
    if not others:
        others = [asset for asset in existing if int(asset["id"]) != first_asset_id]
    random.shuffle(others)
    return selected + others if selected else others


def background_clip_for_asset(asset_id: int, duration: float):
    asset = get_background_asset_by_id(asset_id)
    if not asset:
        raise RuntimeError(f"Background asset not found: {asset_id}")
    assets = ordered_background_assets(asset_id) or [asset]
    if len(assets) == 1:
        local_path = BASE_DIR / str(assets[0]["local_path"])
        if not local_path.exists():
            raise RuntimeError(f"Background file not found: {local_path}")
        return prepared_background_clip(local_path, duration), [int(assets[0]["id"])]

    clips = []
    used_asset_ids: list[int] = []
    remaining = duration
    index = 0
    while remaining > 0:
        segment_duration = min(BACKGROUND_SEGMENT_SECONDS, remaining)
        segment_asset = assets[index % len(assets)]
        local_path = BASE_DIR / str(segment_asset["local_path"])
        if not local_path.exists():
            raise RuntimeError(f"Background file not found: {local_path}")
        clips.append(prepared_background_clip(local_path, segment_duration))
        used_asset_ids.append(int(segment_asset["id"]))
        remaining -= segment_duration
        index += 1
    return concatenate_videoclips(clips, method="compose"), used_asset_ids


def background_segment_paths(asset_id: int, duration: float) -> list[tuple[Path, float, int]]:
    asset = get_background_asset_by_id(asset_id)
    if not asset:
        raise RuntimeError(f"Background asset not found: {asset_id}")
    assets = ordered_background_assets(asset_id) or [asset]

    if len(assets) == 1:
        local_path = BASE_DIR / str(assets[0]["local_path"])
        if not local_path.exists():
            raise RuntimeError(f"Background file not found: {local_path}")
        return [(local_path, duration, int(assets[0]["id"]))]

    segments: list[tuple[Path, float, int]] = []
    remaining = duration
    index = 0
    while remaining > 0:
        segment_duration = min(BACKGROUND_SEGMENT_SECONDS, remaining)
        segment_asset = assets[index % len(assets)]
        local_path = BASE_DIR / str(segment_asset["local_path"])
        if not local_path.exists():
            raise RuntimeError(f"Background file not found: {local_path}")
        segments.append((local_path, segment_duration, int(segment_asset["id"])))
        remaining -= segment_duration
        index += 1
    return segments


def random_background_asset_id() -> int | None:
    assets = [
        asset
        for asset in list_background_assets(limit=BACKGROUND_POOL_LIMIT, active_only=True)
        if str(asset.get("collection") or "") != "longform-16x9"
        and (BASE_DIR / str(asset.get("local_path"))).exists()
    ]
    if not assets:
        return None
    recent_ids = recent_background_asset_ids()
    candidates = [asset for asset in assets if int(asset["id"]) not in recent_ids]
    return int(random.choice(candidates or assets)["id"])


def run_ffmpeg_overlay_render(
    background_asset_id: int,
    static_overlay_path: str,
    frame_paths: list[str],
    durations: list[float],
    output: Path,
) -> None:
    total_duration = sum(durations)
    background_segments = background_segment_paths(background_asset_id, total_duration)

    command = ["ffmpeg", "-y"]
    for path, _duration, _asset_id in background_segments:
        command.extend(["-stream_loop", "-1", "-i", str(path)])
    command.extend(["-loop", "1", "-t", f"{total_duration:.3f}", "-i", static_overlay_path])
    for frame_path, duration in zip(frame_paths, durations):
        command.extend(["-loop", "1", "-t", f"{duration:.3f}", "-i", frame_path])

    filters: list[str] = []
    if len(background_segments) == 1:
        filters.append(
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,setsar=1[bg]"
        )
    else:
        segment_labels = []
        for index, (_path, duration, _asset_id) in enumerate(background_segments):
            label = f"bg{index}"
            fade_out_start = max(0.0, duration - BACKGROUND_FADE_SECONDS)
            filters.append(
                f"[{index}:v]scale=1080:1920:force_original_aspect_ratio=increase,"
                f"crop=1080:1920,setsar=1,trim=duration={duration:.3f},"
                f"fade=t=in:st=0:d={BACKGROUND_FADE_SECONDS:.3f},"
                f"fade=t=out:st={fade_out_start:.3f}:d={BACKGROUND_FADE_SECONDS:.3f},"
                f"setpts=PTS-STARTPTS[{label}]"
            )
            segment_labels.append(f"[{label}]")
        filters.append(
            "".join(segment_labels)
            + f"concat=n={len(segment_labels)}:v=1:a=0[bg]"
        )

    static_input_index = len(background_segments)
    filters.append(
        f"[{static_input_index}:v]format=rgba,setpts=PTS-STARTPTS[static]"
    )
    filters.append("[bg][static]overlay=0:0[base]")

    current_label = "base"
    start = 0.0
    overlay_input_offset = len(background_segments) + 1
    for index, duration in enumerate(durations):
        input_index = overlay_input_offset + index
        overlay_label = f"ov{index}"
        next_label = f"v{index}"
        fade_out_start = max(0.0, duration - 0.8)
        filters.append(
            f"[{input_index}:v]format=rgba,"
            f"fade=t=in:st=0:d=1:alpha=1,"
            f"fade=t=out:st={fade_out_start:.3f}:d=0.8:alpha=1,"
            f"setpts=PTS+{start:.3f}/TB[{overlay_label}]"
        )
        filters.append(
            f"[{current_label}][{overlay_label}]overlay=0:0:"
            f"enable='between(t,{start:.3f},{start + duration:.3f})'[{next_label}]"
        )
        current_label = next_label
        start += duration

    command.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            f"[{current_label}]",
            "-t",
            f"{total_duration:.3f}",
            "-r",
            str(FPS),
            "-c:v",
            "libx264",
            "-preset",
            RENDER_PRESET,
            "-crf",
            RENDER_CRF,
            "-threads",
            str(RENDER_THREADS),
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-movflags",
            "+faststart",
            "-an",
            str(output),
        ]
    )
    subprocess.run(
        command,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=SUBPROCESS_CREATIONFLAGS,
    )
    return [asset_id for _path, _duration, asset_id in background_segments]


def mux_audio(video_path: Path, audio_path: Path) -> None:
    temp_output = video_path.with_name(f"{video_path.stem}-audio{video_path.suffix}")
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-i",
            str(audio_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            str(temp_output),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=SUBPROCESS_CREATIONFLAGS,
    )
    temp_output.replace(video_path)


def ffprobe_video_duration(video_path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        creationflags=SUBPROCESS_CREATIONFLAGS,
    )
    return float(result.stdout.strip() or 0)


def ffprobe_video_size(video_path: Path) -> tuple[int, int]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=p=0:s=x",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        creationflags=SUBPROCESS_CREATIONFLAGS,
    )
    width, height = result.stdout.strip().split("x", 1)
    return int(width), int(height)


def ffprobe_video_bitrate(video_path: Path) -> int:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-show_entries",
            "format=bit_rate",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        creationflags=SUBPROCESS_CREATIONFLAGS,
    )
    try:
        return int(float(result.stdout.strip() or 0))
    except ValueError:
        return 0


def ffprobe_has_audio(video_path: Path) -> bool:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "quiet",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=index",
            "-of",
            "csv=p=0",
            str(video_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        creationflags=SUBPROCESS_CREATIONFLAGS,
    )
    return bool(result.stdout.strip())


def extract_audit_frame(video_path: Path, output_path: Path, timestamp: float) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-ss",
            f"{max(0.0, timestamp):.3f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(output_path),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=SUBPROCESS_CREATIONFLAGS,
    )


def audit_video_before_upload(video_path: Path, label: str = "youtube") -> dict[str, object]:
    if not video_path.exists() or video_path.stat().st_size <= 0:
        raise RuntimeError(f"Video audit failed: missing or empty file: {video_path}")

    duration = ffprobe_video_duration(video_path)
    width, height = ffprobe_video_size(video_path)
    bitrate = ffprobe_video_bitrate(video_path)
    has_audio = ffprobe_has_audio(video_path)
    file_size = video_path.stat().st_size
    is_short = height > width
    min_width, min_height = (1080, 1920) if is_short else (1920, 1080)

    errors: list[str] = []
    warnings: list[str] = []
    if duration < 5:
        errors.append(f"duration_too_short:{duration:.2f}s")
    if width < min_width or height < min_height:
        errors.append(f"resolution_too_low:{width}x{height}")
    if bitrate and bitrate < 1_200_000:
        warnings.append(f"low_bitrate:{bitrate}")
    if not has_audio:
        warnings.append("no_audio_stream")

    audit_dir = BASE_DIR / ".tmp" / "video_audit" / "pre_upload" / video_path.stem
    first_frame = audit_dir / "sample_10s.jpg"
    end_frame = audit_dir / "sample_end.jpg"
    sample_times = [
        min(max(duration * 0.25, 1.0), max(duration - 1.0, 1.0)),
        max(duration - 2.0, 0.0),
    ]
    try:
        extract_audit_frame(video_path, first_frame, sample_times[0])
        extract_audit_frame(video_path, end_frame, sample_times[1])
    except subprocess.CalledProcessError as exc:
        errors.append(f"frame_extract_failed:{(exc.stderr or str(exc))[-300:]}")

    for frame in (first_frame, end_frame):
        if not frame.exists() or frame.stat().st_size <= 0:
            errors.append(f"missing_audit_frame:{frame.name}")

    report = {
        "label": label,
        "video_path": str(video_path.resolve()),
        "duration_seconds": round(duration, 3),
        "width": width,
        "height": height,
        "bitrate": bitrate,
        "file_size": file_size,
        "has_audio": has_audio,
        "sample_frames": [
            first_frame.resolve().relative_to(BASE_DIR).as_posix(),
            end_frame.resolve().relative_to(BASE_DIR).as_posix(),
        ],
        "warnings": warnings,
        "errors": errors,
    }
    audit_dir.mkdir(parents=True, exist_ok=True)
    (audit_dir / "audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if errors:
        raise RuntimeError(f"Video audit failed before upload: {', '.join(errors)}")
    return report


def mix_bgm(video_path: Path, bgm_path: Path) -> None:
    duration = ffprobe_video_duration(video_path)
    if duration <= 0:
        return
    has_audio = ffprobe_has_audio(video_path)
    volume = setting_float("VIDEO_BGM_TTS_VOLUME", "0.10") if has_audio else setting_float("VIDEO_BGM_ONLY_VOLUME", "0.14")
    fade_out_start = max(0.0, duration - 1.2)
    temp_output = video_path.with_name(f"{video_path.stem}-bgm{video_path.suffix}")
    bgm_filter = (
        f"[1:a]atrim=0:{duration:.3f},asetpts=PTS-STARTPTS,"
        f"volume={volume:.3f},"
        "afade=t=in:st=0:d=1.0,"
        f"afade=t=out:st={fade_out_start:.3f}:d=1.2[bgm]"
    )
    if has_audio:
        filter_complex = (
            "[0:a:0]volume=1.0[main];"
            f"{bgm_filter};"
            "[main][bgm]amix=inputs=2:duration=first:dropout_transition=0[a]"
        )
    else:
        filter_complex = f"{bgm_filter};[bgm]anull[a]"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video_path),
            "-stream_loop",
            "-1",
            "-i",
            str(bgm_path),
            "-filter_complex",
            filter_complex,
            "-map",
            "0:v:0",
            "-map",
            "[a]",
            "-map_metadata",
            "-1",
            "-map_chapters",
            "-1",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-shortest",
            str(temp_output),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=SUBPROCESS_CREATIONFLAGS,
    )
    temp_output.replace(video_path)


def _escape_drawtext_text(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "\\%")
    )


def add_subscribe_cta_card(
    video_path: Path,
    duration_seconds: float | None = None,
) -> None:
    width, height = ffprobe_video_size(video_path)
    has_audio = ffprobe_has_audio(video_path)
    duration_seconds = SUBSCRIBE_CTA_SECONDS if duration_seconds is None else duration_seconds
    duration_seconds = max(1.0, float(duration_seconds))
    temp_output = video_path.with_name(f"{video_path.stem}-subscribe-cta{video_path.suffix}")
    font_file = ffmpeg_font_path(SMALL_FONT)
    cta_lines = random.choice(load_subscribe_cta_lines()).splitlines()
    cta_lines = [line.strip() for line in cta_lines if line.strip()][:2]
    if len(cta_lines) == 1:
        cta_lines.append("다음 문장도 함께해 주세요")
    card_fade_out_start = max(0.0, duration_seconds - 1.0)
    text_start = 0.5
    text_fade_in_seconds = 1.0
    text_fade_out_seconds = 0.7
    text_fade_in_end = text_start + text_fade_in_seconds
    text_fade_out_start = max(text_fade_in_end, duration_seconds - text_fade_out_seconds)
    text_alpha = (
        f"if(lt(t\\,{text_start:.3f})\\,0\\,"
        f"if(lt(t\\,{text_fade_in_end:.3f})\\,(t-{text_start:.3f})/{text_fade_in_seconds:.3f}\\,"
        f"if(lt(t\\,{text_fade_out_start:.3f})\\,1\\,"
        f"max(0\\,({duration_seconds:.3f}-t)/{text_fade_out_seconds:.3f}))))"
    )
    text_y_positions = ["h/2-72-text_h/2", "h/2+10-text_h/2"]
    drawtext_filters = []
    for line, y_position in zip(cta_lines, text_y_positions):
        text = _escape_drawtext_text(line)
        drawtext_filters.append(
            f"drawtext=fontfile='{font_file}':text='{text}':"
            "fontsize=42:fontcolor=white@0.94:borderw=2:bordercolor=black@0.55:"
            f"x=(w-text_w)/2:y={y_position}:"
            f"alpha='{text_alpha}'"
        )
    cta_filter = (
        f"fade=t=in:st=0:d=0.8,fade=t=out:st={card_fade_out_start:.3f}:d=1.0,"
        + ",".join(drawtext_filters)
    )
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-f",
        "lavfi",
        "-t",
        f"{duration_seconds:.3f}",
        "-i",
        f"color=c=black:s={width}x{height}:r={FPS}",
    ]
    if has_audio:
        command.extend(
            [
                "-f",
                "lavfi",
                "-t",
                f"{duration_seconds:.3f}",
                "-i",
                "anullsrc=channel_layout=stereo:sample_rate=44100",
                "-filter_complex",
                (
                    "[0:v]setsar=1[v0];"
                    f"[1:v]{cta_filter},setsar=1[v1];"
                    "[v0][0:a:0][v1][2:a:0]concat=n=2:v=1:a=1[v][a]"
                ),
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-map_metadata",
                "-1",
                "-map_chapters",
                "-1",
                "-c:a",
                "aac",
                "-b:a",
                "160k",
            ]
        )
    else:
        command.extend(
            [
                "-filter_complex",
                (
                    "[0:v]setsar=1[v0];"
                    f"[1:v]{cta_filter},setsar=1[v1];"
                    "[v0][v1]concat=n=2:v=1:a=0[v]"
                ),
                "-map",
                "[v]",
                "-map_metadata",
                "-1",
                "-map_chapters",
                "-1",
                "-an",
            ]
        )
    command.extend(
        [
            "-c:v",
            "libx264",
            "-preset",
            RENDER_PRESET,
            "-crf",
            RENDER_CRF,
            "-threads",
            str(RENDER_THREADS),
            "-pix_fmt",
            "yuv420p",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-movflags",
            "+faststart",
            str(temp_output),
        ]
    )
    subprocess.run(
        command,
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=SUBPROCESS_CREATIONFLAGS,
    )
    temp_output.replace(video_path)


DEFAULT_SUBSCRIBE_CTA_LINES = [
    "구독하고\n다음 문장도 함께해 주세요",
    "다음 조각은\n구독으로 이어가 주세요",
    "내일도 한 문장\n구독으로 받아보세요",
    "짧은 지혜가 필요할 때\n구독하고 다시 찾아와 주세요",
    "좋은 문장을 계속 만나고 싶다면\n구독으로 함께해 주세요",
]
SUBSCRIBE_CTA_SECONDS = 4.0


def load_subscribe_cta_lines() -> list[str]:
    path = BASE_DIR / "assets" / "cta_lines.json"
    if not path.exists():
        return DEFAULT_SUBSCRIBE_CTA_LINES
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_SUBSCRIBE_CTA_LINES
    active_set = str(payload.get("active_set") or "")
    sets = payload.get("sets")
    if not isinstance(sets, dict):
        return DEFAULT_SUBSCRIBE_CTA_LINES
    lines = sets.get(active_set)
    if not isinstance(lines, list):
        return DEFAULT_SUBSCRIBE_CTA_LINES
    cleaned = [str(line).strip() for line in lines if str(line).strip()]
    return cleaned or DEFAULT_SUBSCRIBE_CTA_LINES


def render_video(
    log_id: int,
    background_asset_id: int | None = None,
    tts_enabled: bool = ENABLE_TTS_DEFAULT,
    tts_voice: str = DEFAULT_VOICE,
    tts_rate: str = DEFAULT_RATE,
    progress_callback: Callable[[str, int], None] | None = None,
) -> tuple[Path, dict[str, object]]:
    def progress(stage: str, value: int) -> None:
        if progress_callback:
            progress_callback(stage, value)

    content = get_log_content(log_id)
    script = generate_video_script(content)
    if background_asset_id is None:
        background_asset_id = random_background_asset_id()
    bgm_asset = random_bgm_asset() if setting_bool("VIDEO_BGM_ENABLED", "1") else None
    pages = list(script["pages"])
    if script["outro_page"]:
        pages.append(script["outro_page"])

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    job_key = f"log-{log_id}-{stamp}"
    frame_root = FRAME_DIR / f"log-{log_id}-{stamp}"
    frame_root.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    frame_paths: list[str] = []
    durations: list[float] = []
    narration_path: Path | None = None
    if tts_enabled:
        provider = short_tts_provider()
        if provider == "elevenlabs":
            try:
                progress("ElevenLabs Flash v2.5 TTS 생성 중", 15)
                narration_path, tts_durations, selected_voice = create_short_elevenlabs_narration_audio(
                    pages,
                    job_key,
                )
                tts_voice = str(selected_voice["voice_id"])
                script["tts_provider"] = "elevenlabs"
                script["tts_model"] = str(selected_voice["model_id"])
                script["tts_voice_name"] = str(selected_voice["name"])
                script["tts_voice_gender"] = str(selected_voice["gender"])
            except Exception as exc:
                script["tts_fallback_error"] = str(exc)[:500]
                provider = "edge"
                progress("ElevenLabs 실패 · Edge 무료 음성으로 전환", 15)
        if provider == "edge":
            edge_voice = tts_voice if tts_voice in VOICE_OPTIONS else DEFAULT_VOICE
            tts_voice = resolve_voice(edge_voice)
            narration_path, tts_durations = create_narration_audio(
                pages,
                job_key,
                voice=tts_voice,
                rate=tts_rate,
            )
            script["tts_provider"] = "edge"
            script["tts_model"] = "edge-tts"
        for page, duration in zip(pages, tts_durations):
            page["duration_seconds"] = max(3.2, duration)
        script["tts_audio_path"] = narration_path.resolve().relative_to(BASE_DIR).as_posix()
        script["tts_voice"] = tts_voice
        script["tts_rate"] = tts_rate
        script["estimated_seconds"] = sum(float(page["duration_seconds"]) for page in pages)
    else:
        progress("프레임 준비 중", 15)
    footer_text = random.choice(FOOTER_LINES)
    static_overlay_path = frame_root / "static-overlay.png"
    if background_asset_id:
        render_static_overlay(script, static_overlay_path, footer_text)

    for page in pages:
        suffix = "png" if background_asset_id else "jpg"
        frame_path = frame_root / f"page-{int(page['number']):02d}.{suffix}"
        if background_asset_id:
            render_overlay_page(script, page, frame_path)
        else:
            render_page(script, page, len(pages), frame_path, footer_text)
        frame_paths.append(str(frame_path))
        durations.append(float(page["duration_seconds"]))

    output = OUTPUT_DIR / f"wisdom-library-{log_id}-{stamp}.mp4"
    progress("배경 합성 중", 45)
    if background_asset_id:
        if RENDER_ENGINE == "ffmpeg":
            used_background_asset_ids = run_ffmpeg_overlay_render(
                background_asset_id,
                str(static_overlay_path),
                frame_paths,
                durations,
                output,
            )
        else:
            total_duration = sum(durations)
            background, used_background_asset_ids = background_clip_for_asset(
                background_asset_id, total_duration
            )
            static_overlay = ImageClip(str(static_overlay_path)).with_duration(total_duration)
            overlays = []
            start = 0.0
            for frame_path, duration in zip(frame_paths, durations):
                overlay = (
                    ImageClip(frame_path)
                    .with_duration(duration)
                    .with_start(start)
                    .with_position(("center", "center"))
                    .with_effects([vfx.FadeIn(1.0), vfx.FadeOut(0.8)])
                )
                overlays.append(overlay)
                start += duration
            clip = CompositeVideoClip([background, static_overlay, *overlays], size=(WIDTH, HEIGHT))
            clip.write_videofile(
                str(output),
                fps=FPS,
                codec="libx264",
                audio=False,
                preset=RENDER_PRESET,
                threads=RENDER_THREADS,
                ffmpeg_params=[
                    "-crf",
                    RENDER_CRF,
                    "-pix_fmt",
                    "yuv420p",
                    "-colorspace",
                    "bt709",
                    "-color_primaries",
                    "bt709",
                    "-color_trc",
                    "bt709",
                    "-movflags",
                    "+faststart",
                ],
                logger=None,
            )
            clip.close()
            background.close()
            static_overlay.close()
            for overlay in overlays:
                overlay.close()
        script["background_asset_ids"] = used_background_asset_ids
    else:
        clip = ImageSequenceClip(frame_paths, durations=durations)
        clip.write_videofile(
            str(output),
            fps=FPS,
            codec="libx264",
            audio=False,
            preset=RENDER_PRESET,
            threads=RENDER_THREADS,
            ffmpeg_params=[
                "-crf",
                RENDER_CRF,
                "-pix_fmt",
                "yuv420p",
                "-colorspace",
                "bt709",
                "-color_primaries",
                "bt709",
                "-color_trc",
                "bt709",
                "-movflags",
                "+faststart",
            ],
            logger=None,
        )
        clip.close()
    if narration_path:
        progress("오디오 합성 중", 85)
        mux_audio(output, narration_path)
    progress("구독 엔딩 추가 중", 92)
    add_subscribe_cta_card(output)
    if bgm_asset:
        progress("BGM 합성 중", 96)
        bgm_path = BASE_DIR / str(bgm_asset["local_path"])
        mix_bgm(output, bgm_path)
        script["bgm_asset_id"] = int(bgm_asset["id"])
        script["bgm_path"] = bgm_path.resolve().relative_to(BASE_DIR).as_posix()
        script["bgm_title"] = str(bgm_asset.get("title") or bgm_path.stem)
    progress("완료", 100)
    return output, script


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a silent 9:16 video for a 글반장 log.")
    parser.add_argument("log_id", type=int, help="telegram_logs.id value")
    parser.add_argument("--background-asset-id", type=int, default=None)
    parser.add_argument("--no-tts", action="store_true")
    parser.add_argument("--tts-voice", default=DEFAULT_VOICE)
    parser.add_argument("--tts-rate", default=DEFAULT_RATE)
    args = parser.parse_args()

    output, _ = render_video(args.log_id, args.background_asset_id, not args.no_tts, args.tts_voice, args.tts_rate)
    print(output)


if __name__ == "__main__":
    main()
