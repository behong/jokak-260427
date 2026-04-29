from __future__ import annotations

import argparse
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
from db import BASE_DIR, DB_PATH, init_db
from tts import DEFAULT_RATE, DEFAULT_VOICE, create_narration_audio
from video_script import generate_video_script


OUTPUT_DIR = BASE_DIR / "outputs"
FRAME_DIR = OUTPUT_DIR / "frames"
WIDTH = 1080
HEIGHT = 1920
FPS = 24
BACKGROUND_SEGMENT_SECONDS = 10
BACKGROUND_FADE_SECONDS = 0.7
RENDER_PRESET = os.getenv("VIDEO_RENDER_PRESET", "veryfast")
RENDER_THREADS = int(os.getenv("VIDEO_RENDER_THREADS", "4"))
RENDER_ENGINE = os.getenv("VIDEO_RENDER_ENGINE", "ffmpeg")
ENABLE_TTS_DEFAULT = os.getenv("VIDEO_TTS_ENABLED", "1") != "0"
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


def font(path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(path, size=size)


TITLE_FONT = "C:/Windows/Fonts/NanumMyeongjoBold.ttf"
BODY_FONT = "C:/Windows/Fonts/NanumMyeongjo.ttf"
BODY_BOLD_FONT = "C:/Windows/Fonts/NanumMyeongjoBold.ttf"
SMALL_FONT = "C:/Windows/Fonts/malgun.ttf"


def text_size(draw: ImageDraw.ImageDraw, text: str, text_font: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=text_font)
    return box[2] - box[0], box[3] - box[1]


def draw_centered_text(
    draw: ImageDraw.ImageDraw,
    y: int,
    text: str,
    text_font: ImageFont.ImageFont,
    fill: tuple[int, int, int],
) -> None:
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
    if source != "글반장":
        raise RuntimeError("Only 글반장 logs are supported for video rendering")
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


def ordered_background_assets(first_asset_id: int) -> list[dict[str, object]]:
    assets = list_background_assets(limit=100)
    existing = [asset for asset in assets if (BASE_DIR / str(asset.get("local_path"))).exists()]
    selected = [asset for asset in existing if int(asset["id"]) == first_asset_id]
    others = [asset for asset in existing if int(asset["id"]) != first_asset_id]
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
        return prepared_background_clip(local_path, duration)

    clips = []
    remaining = duration
    index = 0
    while remaining > 0:
        segment_duration = min(BACKGROUND_SEGMENT_SECONDS, remaining)
        segment_asset = assets[index % len(assets)]
        local_path = BASE_DIR / str(segment_asset["local_path"])
        if not local_path.exists():
            raise RuntimeError(f"Background file not found: {local_path}")
        clips.append(prepared_background_clip(local_path, segment_duration))
        remaining -= segment_duration
        index += 1
    return concatenate_videoclips(clips, method="compose")


def background_segment_paths(asset_id: int, duration: float) -> list[tuple[Path, float]]:
    asset = get_background_asset_by_id(asset_id)
    if not asset:
        raise RuntimeError(f"Background asset not found: {asset_id}")
    assets = ordered_background_assets(asset_id) or [asset]

    if len(assets) == 1:
        local_path = BASE_DIR / str(assets[0]["local_path"])
        if not local_path.exists():
            raise RuntimeError(f"Background file not found: {local_path}")
        return [(local_path, duration)]

    segments: list[tuple[Path, float]] = []
    remaining = duration
    index = 0
    while remaining > 0:
        segment_duration = min(BACKGROUND_SEGMENT_SECONDS, remaining)
        segment_asset = assets[index % len(assets)]
        local_path = BASE_DIR / str(segment_asset["local_path"])
        if not local_path.exists():
            raise RuntimeError(f"Background file not found: {local_path}")
        segments.append((local_path, segment_duration))
        remaining -= segment_duration
        index += 1
    return segments


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
    for path, _duration in background_segments:
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
        for index, (_path, duration) in enumerate(background_segments):
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
            "-threads",
            str(RENDER_THREADS),
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(output),
        ]
    )
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


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
    )
    temp_output.replace(video_path)


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
        progress("TTS 생성 중", 15)
        narration_path, tts_durations = create_narration_audio(
            pages,
            job_key,
            voice=tts_voice,
            rate=tts_rate,
        )
        for page, duration in zip(pages, tts_durations):
            page["duration_seconds"] = max(3.2, duration)
        script["tts_audio_path"] = narration_path.resolve().relative_to(BASE_DIR).as_posix()
        script["tts_voice"] = tts_voice
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
            run_ffmpeg_overlay_render(
                background_asset_id,
                str(static_overlay_path),
                frame_paths,
                durations,
                output,
            )
        else:
            total_duration = sum(durations)
            background = background_clip_for_asset(background_asset_id, total_duration)
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
                logger=None,
            )
            clip.close()
            background.close()
            static_overlay.close()
            for overlay in overlays:
                overlay.close()
    else:
        clip = ImageSequenceClip(frame_paths, durations=durations)
        clip.write_videofile(
            str(output),
            fps=FPS,
            codec="libx264",
            audio=False,
            preset=RENDER_PRESET,
            threads=RENDER_THREADS,
            logger=None,
        )
        clip.close()
    if narration_path:
        progress("오디오 합성 중", 85)
        mux_audio(output, narration_path)
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
