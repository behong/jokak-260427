from __future__ import annotations

import json
import random
import re
import subprocess
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from backgrounds import BACKGROUND_DIR
from bgm import licensed_longform_bgm_assets
from db import BASE_DIR, DB_PATH, connect, init_db
from longform_script import generate_longform_script
from longform_tts import synthesize_longform_narration, write_srt
from render_video import BODY_BOLD_FONT, BODY_FONT, RENDER_CRF, RENDER_PRESET, ffmpeg_font_path


SUBPROCESS_CREATIONFLAGS = getattr(subprocess, "CREATE_NO_WINDOW", 0)
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"}


def _project_path(value: str, default: Path) -> Path:
    text = str(value or "").strip()
    if not text:
        return default
    path = Path(text)
    return path if path.is_absolute() else BASE_DIR / path


def _relative(path: Path) -> str:
    return path.resolve().relative_to(BASE_DIR).as_posix()


def ffprobe_media(path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration,size:stream=codec_type,codec_name,width,height,sample_rate,channels",
            "-of", "json", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=SUBPROCESS_CREATIONFLAGS,
    )
    return json.loads(result.stdout)


def create_healing_job(config: dict[str, Any], trigger: str = "manual") -> int:
    root = config["longform"]
    tts_config = root["tts"]
    tts_voice = (
        str((tts_config.get("elevenlabs") or {}).get("voice_id") or "")
        if str(tts_config.get("provider") or "edge") == "elevenlabs"
        else str(tts_config.get("voice") or "")
    )
    init_db(DB_PATH)
    with closing(connect(DB_PATH)) as conn:
        cursor = conn.execute(
            """
            INSERT INTO healing_longform_jobs
                (status, stage, progress, theme, duration_minutes, config_json, tts_voice, trigger)
            VALUES ('pending', '대기 중', 0, ?, ?, ?, ?, ?)
            """,
            (
                str(root["script"].get("theme") or ""),
                int(root["duration_minutes"]),
                json.dumps(config, ensure_ascii=False),
                tts_voice,
                trigger if trigger in {"scheduled", "sample", "voice_test"} else "manual",
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def mark_interrupted_healing_jobs() -> int:
    """Mark jobs whose worker disappeared during a dashboard restart."""
    init_db(DB_PATH)
    with closing(connect(DB_PATH)) as conn:
        cursor = conn.execute(
            """
            UPDATE healing_longform_jobs
            SET status = 'failed', stage = '중단됨',
                error = 'dashboard restarted while longform job was running',
                updated_at = CURRENT_TIMESTAMP
            WHERE status IN ('pending', 'running')
            """
        )
        conn.commit()
        return int(cursor.rowcount)


def update_healing_job(job_id: int, **values: Any) -> None:
    allowed = {
        "status", "stage", "progress", "theme", "duration_minutes", "config_json",
        "script_path", "metadata_json", "output_path", "actual_duration", "tts_voice", "error",
    }
    fields = [(key, value) for key, value in values.items() if key in allowed]
    if not fields:
        return
    assignments = ", ".join(f"{key} = ?" for key, _ in fields)
    with closing(connect(DB_PATH)) as conn:
        conn.execute(
            f"UPDATE healing_longform_jobs SET {assignments}, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            [value for _, value in fields] + [job_id],
        )
        conn.commit()


def healing_job(job_id: int) -> dict[str, Any] | None:
    init_db(DB_PATH)
    with closing(connect(DB_PATH)) as conn:
        row = conn.execute(
            "SELECT * FROM healing_longform_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    return dict(row) if row else None


def list_healing_jobs(limit: int = 50) -> list[dict[str, Any]]:
    init_db(DB_PATH)
    with closing(connect(DB_PATH)) as conn:
        rows = conn.execute(
            "SELECT * FROM healing_longform_jobs ORDER BY id DESC LIMIT ?",
            (max(1, min(200, int(limit))),),
        ).fetchall()
    return [dict(row) for row in rows]


THEME_BACKGROUND_TERMS: dict[str, tuple[str, ...]] = {
    "오늘도 애쓴 당신에게": ("theme:comfort", "theme:recovery", "sunset", "meadow", "forest"),
    "잠들기 전 듣는 위로": ("theme:night", "theme:calm", "twilight", "cozy", "sunset"),
    "마음이 복잡한 밤": ("theme:night", "theme:calm", "twilight", "cozy", "water"),
    "인간관계에 지쳤을 때": ("theme:recovery", "forest", "path", "meadow", "flowers"),
    "아무것도 하기 싫은 날": ("theme:comfort", "theme:calm", "theme:recovery", "lake", "forest"),
    "걱정을 내려놓는 시간": ("theme:calm", "water", "lake", "cloud", "ocean"),
    "아침에 듣는 긍정적인 글": ("theme:morning", "sunrise", "morning", "sunny", "flowers", "clear sky"),
    "불안할 때 마음을 안정시키는 글": ("theme:calm", "water", "lake", "cloud", "ocean"),
}


def _background_db_paths(
    collection: str,
    landscape_only: bool = False,
    theme: str = "",
) -> list[Path]:
    with closing(connect(DB_PATH)) as conn:
        rows = conn.execute(
            """
            SELECT local_path, COALESCE(query, '') AS query FROM background_assets
            WHERE enabled = 1 AND (? = '' OR collection = ?)
              AND (
                ? = 0 OR (
                  COALESCE(width, 0) > COALESCE(height, 0)
                  AND width * 10 >= height * 15
                  AND width * 10 <= height * 20
                )
              )
            ORDER BY id DESC
            """,
            (collection, collection, 1 if landscape_only else 0),
        ).fetchall()
    candidates = [
        (BASE_DIR / str(row["local_path"]), str(row["query"] or "").lower())
        for row in rows
        if (BASE_DIR / str(row["local_path"])).exists()
    ]
    terms = THEME_BACKGROUND_TERMS.get(theme, ())
    primary_term = terms[0] if terms else ""
    primary = [path for path, query in candidates if primary_term and primary_term in query]
    related = [
        path for path, query in candidates
        if path not in primary and any(term in query for term in terms[1:])
    ]
    fallback = [
        path for path, _query in candidates
        if path not in primary and path not in related
    ]
    random.shuffle(primary)
    random.shuffle(related)
    random.shuffle(fallback)
    return [*primary, *related, *fallback]


def select_backgrounds(config: dict[str, Any]) -> list[Path]:
    root = config["longform"]
    background = root["background"]
    mode = str(background.get("mode") or "sequence")
    specific = _project_path(str(background.get("specific_file") or ""), BACKGROUND_DIR)
    if mode in {"specific", "single_loop", "image_pan"} and specific.is_file():
        return [specific]

    output_width, output_height = [int(value) for value in str(root.get("resolution") or "1920x1080").split("x", 1)]
    landscape_output = output_width > output_height

    source_dir = _project_path(str(background.get("source_dir") or ""), BASE_DIR / "assets" / "healing" / "videos")
    files = [
        path for path in source_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS | IMAGE_EXTENSIONS
    ] if source_dir.exists() and not landscape_output else []
    theme = str(root.get("script", {}).get("theme") or "")
    files.extend(
        _background_db_paths(
            str(background.get("collection") or ""),
            landscape_only=landscape_output,
            theme=theme,
        )
    )
    unique = list(dict.fromkeys(path.resolve() for path in files if path.exists()))
    if not unique:
        raise RuntimeError("사용할 16:9 가로 배경이 없습니다. 롱폼 배경 관리 화면에서 가로 영상을 먼저 가져와 주세요.")
    if bool(background.get("random", True)) and not theme:
        random.shuffle(unique)
    count = 1 if mode in {"random", "single_loop", "image_pan"} else int(background.get("clip_count") or 6)
    return unique[: max(1, min(count, len(unique)))]


def prepare_hybrid_backgrounds(
    sources: list[Path],
    config: dict[str, Any],
    work_dir: Path,
) -> list[Path]:
    """Use one video for the intro and extracted stills for the long body."""
    background = config["longform"]["background"]
    if str(background.get("mode") or "hybrid") != "hybrid":
        return sources

    videos = [path for path in sources if path.suffix.lower() in VIDEO_EXTENSIONS]
    images = [path for path in sources if path.suffix.lower() in IMAGE_EXTENSIONS]
    intro = videos[0] if videos else images[0]
    if bool(config["longform"].get("sample_mode", False)):
        return [intro]
    still_sources = videos[1:] + images + videos[:1]
    still_count = max(1, int(background.get("clip_count") or 6))
    still_dir = work_dir / "background-stills"
    still_dir.mkdir(parents=True, exist_ok=True)
    stills: list[Path] = []
    for index in range(still_count):
        source = still_sources[index % len(still_sources)]
        if source.suffix.lower() in IMAGE_EXTENSIONS:
            stills.append(source)
            continue
        output = still_dir / f"still-{index + 1:02d}.jpg"
        command = [
            "ffmpeg", "-y", "-loglevel", "error", "-ss", "1.0",
            "-i", str(source), "-frames:v", "1", "-q:v", "2", str(output),
        ]
        try:
            subprocess.run(
                command, check=True, capture_output=True,
                creationflags=SUBPROCESS_CREATIONFLAGS,
            )
        except subprocess.CalledProcessError:
            subprocess.run(
                [
                    "ffmpeg", "-y", "-loglevel", "error", "-i", str(source),
                    "-frames:v", "1", "-q:v", "2", str(output),
                ],
                check=True,
                capture_output=True,
                creationflags=SUBPROCESS_CREATIONFLAGS,
            )
        stills.append(output)
    return [intro, *stills]


def background_segment_durations(
    backgrounds: list[Path],
    duration: float,
    config: dict[str, Any],
) -> list[float]:
    if len(backgrounds) == 1:
        return [duration]
    background = config["longform"]["background"]
    if str(background.get("mode") or "hybrid") == "hybrid":
        min_body_total = 0.1 * (len(backgrounds) - 1)
        intro = min(float(background.get("intro_seconds") or 25), max(0.1, duration - min_body_total))
        body_segment = max(0.1, duration - intro) / (len(backgrounds) - 1)
        return [intro, *([body_segment] * (len(backgrounds) - 1))]
    return [duration / len(backgrounds)] * len(backgrounds)


def _music_record(path: Path, **values: object) -> dict[str, Any]:
    return {"path": path.resolve(), "title": path.stem, **values}


def select_music(config: dict[str, Any]) -> list[dict[str, Any]]:
    music = config["longform"]["music"]
    if not bool(music.get("enabled", True)) or str(music.get("mode") or "music") == "none":
        return []
    specific_text = str(music.get("specific_file") or "").strip()
    if specific_text:
        specific = _project_path(specific_text, BASE_DIR / "assets" / "bgm")
        if specific.is_file():
            return [_music_record(specific, source_type="specific")]
    default_dir = BASE_DIR / "assets" / "healing" / "nature" if str(music.get("mode")) == "nature" else BASE_DIR / "assets" / "bgm"
    configured_dir = str(music.get("nature_source_dir") or "") if str(music.get("mode")) == "nature" else str(music.get("source_dir") or "")
    source_dir = _project_path(configured_dir, default_dir)
    files = [path for path in source_dir.rglob("*") if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS]
    track_count = 1 if bool(config["longform"].get("sample_mode", False)) else int(music.get("track_count") or 3)
    if str(music.get("mode") or "music") == "music":
        licensed = licensed_longform_bgm_assets()
        mood = str(config["longform"].get("script", {}).get("tone") or "calm")
        matching = [asset for asset in licensed if str(asset.get("mood") or "calm") == mood]
        remaining = [asset for asset in licensed if asset not in matching]
        random.shuffle(matching)
        random.shuffle(remaining)
        selected = (matching + remaining)[:track_count]
        if selected:
            return [
                _music_record(
                    BASE_DIR / str(asset["local_path"]),
                    id=asset.get("id"),
                    title=str(asset.get("title") or Path(str(asset["local_path"])).stem),
                    source_type=asset.get("source_type"),
                    source_url=asset.get("source_url"),
                    license_type=asset.get("license_type"),
                    attribution_text=asset.get("attribution_text"),
                    mood=asset.get("mood"),
                )
                for asset in selected
            ]
        generated = [path for path in files if path.parent.name == "generated"]
        if generated:
            theme = str(config["longform"].get("script", {}).get("theme") or "")
            preferred_terms = (
                ("night-lamp", "deep-calm", "slow-breath", "still-water")
                if theme in {"잠들기 전 듣는 위로", "마음이 복잡한 밤"}
                else ("warm-dawn", "clear-sky", "soft-pad")
                if theme == "아침에 듣는 긍정적인 글"
                else ("still-water", "slow-breath", "deep-calm", "soft-pad", "glass-cloud")
            )
            preferred = [path for path in generated if any(term in path.stem for term in preferred_terms)]
            files = preferred or generated
    random.shuffle(files)
    return [
        _music_record(path, source_type="procedural" if path.parent.name == "generated" else "nature")
        for path in files[: max(1, min(track_count, len(files)))]
    ]


def _escape_filter_path(path: Path) -> str:
    relative = _relative(path)
    return relative.replace("\\", "/").replace(":", "\\:").replace("'", "\\'")


def _escape_drawtext(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:").replace("%", "\\%")


def _sample_script(script: dict[str, Any], sentence_limit: int = 4) -> dict[str, Any]:
    sentences = [str(item).strip() for item in script.get("sentences") or [] if str(item).strip()]
    selected = sentences[:max(1, sentence_limit)]
    if not selected:
        return script
    sample = dict(script)
    narration = " ".join(selected)
    sample["paragraphs"] = selected
    sample["narration"] = narration
    sample["sentences"] = selected
    sample["character_count"] = len(narration)
    return sample


def _visual_filter(
    backgrounds: list[Path],
    duration: float,
    width: int,
    height: int,
    fps: int,
    fade: float,
    subtitle_path: Path | None,
    config: dict[str, Any],
    script: dict[str, Any],
) -> tuple[str, str, list[float]]:
    count = len(backgrounds)
    fade = 0.0 if count == 1 else max(0.0, min(float(fade), 4.0))
    segment_durations = background_segment_durations(backgrounds, duration, config)
    hybrid = str(config["longform"]["background"].get("mode") or "hybrid") == "hybrid"
    filters: list[str] = []
    for index, path in enumerate(backgrounds):
        segment = segment_durations[index]
        if path.suffix.lower() in IMAGE_EXTENSIONS:
            zoom_step = 0.06 / max(1, int(segment * fps))
            filters.append(
                f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},zoompan=z='min(zoom+{zoom_step:.10f},1.06)':d={int(segment*fps)}:"
                f"s={width}x{height}:fps={fps},trim=duration={segment:.3f},"
                f"setpts=PTS-STARTPTS,settb=1/{fps},format=yuv420p,setsar=1,fps={fps}[base{index}]"
            )
        else:
            timing = "setpts=1.25*(PTS-STARTPTS)" if hybrid and index == 0 else "setpts=PTS-STARTPTS"
            filters.append(
                f"[{index}:v]split=2[bg{index}][fg{index}];"
                f"[bg{index}]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},boxblur=12:1,eq=brightness=-0.08:saturation=0.88[b{index}];"
                f"[fg{index}]scale={width}:{height}:force_original_aspect_ratio=decrease[f{index}];"
                f"[b{index}][f{index}]overlay=(W-w)/2:(H-h)/2,"
                f"{timing},trim=duration={segment:.3f},settb=1/{fps},"
                f"format=yuv420p,setsar=1,fps={fps}[base{index}]"
            )

        fades: list[str] = []
        half_fade = min(fade / 2.0, segment / 4.0)
        if half_fade > 0 and index > 0:
            fades.append(f"fade=t=in:st=0:d={half_fade:.3f}")
        if half_fade > 0 and index < count - 1:
            fades.append(f"fade=t=out:st={max(0.0, segment-half_fade):.3f}:d={half_fade:.3f}")
        fade_chain = ",".join(fades) if fades else "null"
        filters.append(f"[base{index}]{fade_chain}[v{index}]")

    if count > 1:
        filters.append(
            "".join(f"[v{index}]" for index in range(count))
            + f"concat=n={count}:v=1:a=0[joined]"
        )
        current = "joined"
    else:
        current = "v0"

    chain: list[str] = []
    subtitles = config["longform"]["subtitles"]
    if subtitle_path and bool(subtitles.get("enabled", True)):
        font_size = int(subtitles.get("font_size") or 16)
        margin_v = 55 if str(subtitles.get("position") or "bottom") == "bottom" else 380
        chain.append(
            f"subtitles='{_escape_filter_path(subtitle_path)}':"
            f"force_style='FontName=Noto Serif CJK KR,FontSize={font_size},PrimaryColour=&H006BC8F2,"
            f"OutlineColour=&H70000000,BorderStyle=1,Outline=0.7,Shadow=1,Spacing=0,"
            f"Alignment=2,MarginL=55,MarginR=55,MarginV={margin_v}'"
        )
    overlays = config["longform"].get("overlays") or {}
    title_font_file = ffmpeg_font_path(BODY_BOLD_FONT)
    body_font_file = ffmpeg_font_path(BODY_FONT)
    logo_text = str(overlays.get("logo_path") or "").strip()
    logo_rendered = False
    if bool(overlays.get("show_logo", False)) and logo_text:
        logo_path = _project_path(logo_text, BASE_DIR / "assets" / "logo.png")
        if logo_path.is_file():
            filters.append(
                f"movie='{_escape_filter_path(logo_path)}',scale=150:-1[logo];"
                f"[{current}][logo]overlay=44:36:format=auto[withlogo]"
            )
            current = "withlogo"
            logo_rendered = True
    if bool(overlays.get("show_title", True)):
        title_font_size = int(overlays.get("title_font_size") or 32)
        title_x = 210 if logo_rendered else 60
        chain.append(
            "drawtext="
            f"fontfile='{body_font_file}':text='{_escape_drawtext(script.get('display_title') or script['title'])}':"
            f"fontsize={title_font_size}:fontcolor=white@0.92:borderw=1:bordercolor=black@0.35:"
            f"x={title_x}:y=62"
        )
    if bool(overlays.get("show_key_phrase", True)):
        key_phrase_font_size = int(overlays.get("key_phrase_font_size") or 25)
        chain.append(
            "drawtext="
            f"fontfile='{title_font_file}':text='{_escape_drawtext(script['thumbnail_text'])}':"
            f"fontsize={key_phrase_font_size}:fontcolor=white@0.86:borderw=1:bordercolor=black@0.35:"
            "x=(w-text_w)/2:y=h-108"
        )
    if bool(overlays.get("show_channel", True)):
        channel_font_size = int(overlays.get("channel_font_size") or 21)
        channel_chars = list(str(overlays.get("channel_name") or "지혜로운 조각들").replace(" ", ""))
        channel_tokens = [token for index, char in enumerate(channel_chars) for token in ((char, "·") if index < len(channel_chars) - 1 else (char,))]
        for index, token in enumerate(channel_tokens):
            chain.append(
                "drawtext="
                f"fontfile='{body_font_file}':text='{_escape_drawtext(token)}':"
                f"fontsize={channel_font_size}:fontcolor=white@0.78:borderw=1:bordercolor=black@0.3:"
                f"x=w-text_w-55:y={58 + index * 27}"
            )
    if bool(overlays.get("show_subscribe_cta", True)) and not bool(config["longform"].get("sample_mode", False)):
        cta_start = max(0.0, duration - 12.0)
        cta_font_size = int(overlays.get("cta_font_size") or 30)
        chain.append(
            "drawtext="
            f"fontfile='{body_font_file}':text='편안한 시간이 되셨다면 구독과 좋아요로 함께해 주세요':"
            f"fontsize={cta_font_size}:fontcolor=white@0.9:borderw=1:bordercolor=black@0.4:"
            f"x=(w-text_w)/2:y=h*0.78:enable='between(t,{cta_start:.3f},{duration:.3f})'"
        )
    if chain:
        filters.append(f"[{current}]{','.join(chain)}[vout]")
        current = "vout"
    return ";".join(filters), current, segment_durations


def render_healing_longform(
    job_id: int,
    config: dict[str, Any],
    progress: Callable[[str, int], None] | None = None,
) -> dict[str, Any]:
    root = config["longform"]
    duration_minutes = int(root["duration_minutes"])
    sample_mode = bool(root.get("sample_mode", False))
    sample_seconds = max(10, min(60, int(root.get("sample_seconds") or 25)))
    theme = str(root["script"].get("theme") or "오늘도 애쓴 당신에게")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    work_dir = BASE_DIR / "outputs" / "longform" / f"job-{job_id}-{stamp}"
    work_dir.mkdir(parents=True, exist_ok=True)

    def report(stage: str, value: int) -> None:
        update_healing_job(job_id, stage=stage, progress=value)
        if progress:
            progress(stage, value)

    report("샘플 원고 준비 중" if sample_mode else "주제형 원고 생성 중", 5)
    script_override = root.get("script_override")
    if isinstance(script_override, dict) and script_override.get("paragraphs"):
        script = dict(script_override)
    else:
        script = generate_longform_script(
            theme,
            1 if sample_mode else duration_minutes,
            str(root["script"].get("tone") or "calm"),
            root["tts"],
        )
    if sample_mode:
        script = _sample_script(script)
    script_path = work_dir / "script.json"
    script_path.write_text(json.dumps(script, ensure_ascii=False, indent=2), encoding="utf-8")
    update_healing_job(job_id, script_path=_relative(script_path))

    report("롱폼 TTS 생성 중", 10)
    narration, captions, narration_duration = synthesize_longform_narration(
        script, root["tts"], work_dir / "audio", report
    )
    final_duration = min(narration_duration, float(sample_seconds)) if sample_mode else narration_duration
    subtitle_path = None
    if bool(root["subtitles"].get("enabled", True)):
        subtitle_path = write_srt(
            captions,
            work_dir / "subtitles.srt",
            int(root["subtitles"].get("max_lines") or 2),
        )

    report("배경 영상 구성 중", 45)
    backgrounds = select_backgrounds(config)
    backgrounds = prepare_hybrid_backgrounds(backgrounds, config, work_dir)
    music_tracks = select_music(config)
    width, height = [int(value) for value in str(root["resolution"]).split("x", 1)]
    fps = int(root["fps"])
    visual_filter, video_label, segment_durations = _visual_filter(
        backgrounds,
        final_duration,
        width,
        height,
        fps,
        (
            float(root["background"].get("transition_seconds") or 1.5)
            if str(root["background"].get("transition") or "crossfade") == "crossfade"
            else 0.0
        ),
        subtitle_path,
        config,
        script,
    )

    output_dir = _project_path(str(root["output"].get("directory") or ""), BASE_DIR / "outputs" / "longform")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_config = root["output"]
    render_preset = str(output_config.get("preset") or RENDER_PRESET)
    render_crf = str(int(output_config.get("crf") or int(RENDER_CRF)))
    render_threads = max(0, int(output_config.get("threads") or 0))
    slug = re.sub(r"[^0-9A-Za-z가-힣]+", "-", theme).strip("-")[:36] or "healing"
    voice_test_label = re.sub(r"[^0-9A-Za-z_-]+", "-", str(root.get("voice_test_label") or "")).strip("-")
    output_prefix = (
        "sample"
        if sample_mode
        else f"voice-test-{voice_test_label or 'voice'}"
        if bool(root.get("voice_test_mode"))
        else "healing"
    )
    output = output_dir / f"{output_prefix}-{slug}-{stamp}.mp4"
    command = ["ffmpeg", "-y", "-loglevel", "error", "-nostats", "-progress", "pipe:1"]
    for path, segment_duration in zip(backgrounds, segment_durations):
        if path.suffix.lower() in IMAGE_EXTENSIONS:
            command.extend(["-loop", "1", "-t", f"{segment_duration:.3f}", "-i", str(path)])
        else:
            command.extend(["-stream_loop", "-1", "-t", f"{segment_duration:.3f}", "-i", str(path)])
    narration_index = len(backgrounds)
    command.extend(["-i", str(narration)])
    music_indices: list[int] = []
    music_cfg = root["music"]
    crossfade_seconds = min(
        float(music_cfg.get("crossfade_seconds", 4.0)),
        final_duration / max(4.0, len(music_tracks) * 4.0),
    ) if len(music_tracks) > 1 else 0.0
    music_segment_duration = (
        (final_duration + crossfade_seconds * (len(music_tracks) - 1)) / len(music_tracks)
        if music_tracks else 0.0
    )
    for offset, track in enumerate(music_tracks):
        music_indices.append(narration_index + 1 + offset)
        input_duration = final_duration if len(music_tracks) == 1 else music_segment_duration
        command.extend(
            ["-stream_loop", "-1", "-t", f"{input_duration:.3f}", "-i", str(track["path"])]
        )

    # Edge/ElevenLabs synthesis already applies the selected narration volume.
    audio_filters: list[str] = []
    if music_indices:
        fade_out_start = max(0.0, final_duration - float(music_cfg.get("fade_out_seconds") or 5))
        for track_number, music_index in enumerate(music_indices):
            track_duration = final_duration if len(music_indices) == 1 else music_segment_duration
            audio_filters.append(
                f"[{music_index}:a]atrim=0:{track_duration:.3f},asetpts=PTS-STARTPTS,"
                "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"
                f"[musictrack{track_number}]"
            )
        if len(music_indices) == 1:
            music_base = "musictrack0"
        elif crossfade_seconds > 0:
            music_base = "musictrack0"
            for track_number in range(1, len(music_indices)):
                output_label = f"musicjoin{track_number}"
                audio_filters.append(
                    f"[{music_base}][musictrack{track_number}]"
                    f"acrossfade=d={crossfade_seconds:.3f}:c1=tri:c2=tri[{output_label}]"
                )
                music_base = output_label
        else:
            music_base = "musicjoined"
            audio_filters.append(
                "".join(f"[musictrack{index}]" for index in range(len(music_indices)))
                + f"concat=n={len(music_indices)}:v=0:a=1[{music_base}]"
            )
        audio_filters.append(
            f"[{music_base}]atrim=0:{final_duration:.3f},asetpts=PTS-STARTPTS,"
            f"volume={float(music_cfg.get('volume') or 0.15):.3f},"
            f"afade=t=in:st=0:d={float(music_cfg.get('fade_in_seconds') or 3):.3f},"
            f"afade=t=out:st={fade_out_start:.3f}:d={float(music_cfg.get('fade_out_seconds') or 5):.3f}[music]"
        )
        if bool(music_cfg.get("ducking", True)):
            audio_filters.append(f"[{narration_index}:a]asplit=2[narrside][narrmix]")
            audio_filters.append(
                "[music][narrside]sidechaincompress=threshold=0.025:ratio=8:attack=25:release=650[ducked]"
            )
            audio_filters.append("[narrmix][ducked]amix=inputs=2:duration=first:normalize=0[aout]")
        else:
            audio_filters.append(f"[{narration_index}:a][music]amix=inputs=2:duration=first:normalize=0[aout]")
    else:
        audio_filters.append(f"[{narration_index}:a]anull[aout]")

    master_lufs = float(music_cfg.get("master_lufs", -18.0))
    true_peak = float(music_cfg.get("true_peak", -2.0))
    audio_filters.append(
        f"[aout]loudnorm=I={master_lufs:.1f}:TP={true_peak:.1f}:LRA=7[master]"
    )

    report("1080p 롱폼 렌더링 중", 55)
    if render_threads > 0:
        command.extend(["-filter_complex_threads", str(render_threads)])
    command.extend(
        [
            "-filter_complex", f"{visual_filter};{';'.join(audio_filters)}",
            "-map", f"[{video_label}]", "-map", "[master]", "-t", f"{final_duration:.3f}",
            "-map_metadata", "-1", "-map_chapters", "-1", "-dn",
            "-c:v", "libx264", "-preset", render_preset, "-crf", render_crf,
            "-pix_fmt", "yuv420p", "-r", str(fps), "-colorspace", "bt709",
            "-color_primaries", "bt709", "-color_trc", "bt709",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
            "-movflags", "+faststart", str(output),
        ]
    )
    if render_threads > 0:
        command[-1:-1] = ["-threads", str(render_threads)]
    process = subprocess.Popen(
        command,
        cwd=BASE_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=SUBPROCESS_CREATIONFLAGS,
    )
    last_progress = 55
    if process.stdout is not None:
        for raw_line in process.stdout:
            key, separator, value = raw_line.strip().partition("=")
            if not separator or key not in {"out_time_us", "out_time_ms"}:
                continue
            try:
                rendered_seconds = int(value) / 1_000_000
            except ValueError:
                continue
            progress_value = 55 + int(min(1.0, rendered_seconds / max(1.0, final_duration)) * 39)
            if progress_value >= last_progress + 2:
                last_progress = progress_value
                report(f"1080p 롱폼 렌더링 중 ({progress_value}%)", progress_value)
    error_output = process.stderr.read() if process.stderr is not None else ""
    return_code = process.wait()
    if return_code != 0:
        raise RuntimeError((error_output or "ffmpeg longform render failed")[-5000:])

    report("출력 검증 중", 95)
    probe = ffprobe_media(output)
    actual_duration = float((probe.get("format") or {}).get("duration") or 0)
    music_credits = [
        str(track.get("attribution_text") or "").strip()
        for track in music_tracks
        if str(track.get("attribution_text") or "").strip()
    ]
    description = str(script["description"])
    if music_credits:
        description += "\n\n음악 출처\n" + "\n".join(dict.fromkeys(music_credits))
    metadata = {
        "title": script["title"],
        "title_options": script.get("title_options") or [script["title"]],
        "description": description,
        "thumbnail_text": script["thumbnail_text"],
        "hashtags": script["hashtags"],
        "tags": script.get("tags") or script["hashtags"],
        "duration_minutes": round(actual_duration / 60, 2),
        "sample": sample_mode,
        "voice_test_label": str(root.get("voice_test_label") or ""),
        "tts_provider": root["tts"].get("provider"),
        "tts_voice": (
            (root["tts"].get("elevenlabs") or {}).get("voice_id")
            if root["tts"].get("provider") == "elevenlabs"
            else root["tts"].get("voice")
        ),
        "music": ", ".join(str(track.get("title") or "") for track in music_tracks) or "없음",
        "music_tracks": [
            {
                key: track.get(key)
                for key in ("id", "title", "source_type", "source_url", "license_type", "attribution_text", "mood")
                if track.get(key) not in {None, ""}
            }
            for track in music_tracks
        ],
        "music_crossfade_seconds": crossfade_seconds,
        "audio_mastering": {
            "target_lufs": master_lufs,
            "true_peak": true_peak,
            "loudness_range": 7,
        },
        "backgrounds": [path.name for path in backgrounds],
        "probe": probe,
    }
    metadata_path = work_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "output": output,
        "script_path": script_path,
        "metadata_path": metadata_path,
        "metadata": metadata,
        "actual_duration": actual_duration,
    }


def run_healing_longform_job(job_id: int, config: dict[str, Any]) -> None:
    try:
        update_healing_job(job_id, status="running", stage="시작", progress=1, error=None)
        result = render_healing_longform(job_id, config)
        update_healing_job(
            job_id,
            status="ready",
            stage="완료",
            progress=100,
            script_path=_relative(result["script_path"]),
            metadata_json=json.dumps(result["metadata"], ensure_ascii=False),
            output_path=_relative(result["output"]),
            actual_duration=float(result["actual_duration"]),
            error=None,
        )
    except Exception as exc:
        update_healing_job(job_id, status="failed", stage="실패", error=str(exc)[-5000:])
