from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from db import BASE_DIR


CONFIG_PATH = BASE_DIR / "config" / "longform.yaml"

DEFAULT_CONFIG: dict[str, Any] = {
    "longform": {
        "enabled": True,
        "duration_minutes": 15,
        "resolution": "1920x1080",
        "fps": 30,
        "schedule": {
            "enabled": True,
            "auto_upload": False,
            "time": "03:10",
            "days": [0, 1, 2, 3, 4, 5, 6],
            "theme_mode": "rotate",
            "skip_when_busy": True,
            "window_minutes": 30,
            "render_preset": "veryfast",
            "render_crf": 20,
            "render_threads": 2,
        },
        "script": {
            "theme": "오늘도 애쓴 당신에게",
            "tone": "calm",
            "intro_seconds": 25,
            "outro_seconds": 30,
        },
        "tts": {
            "provider": "edge",
            "voice": "ko-KR-HyunsuMultilingualNeural",
            "comparison_voice": "ko-KR-HyunsuNeural",
            "speed": 0.85,
            "pitch": 0,
            "sentence_pause": 0.9,
            "paragraph_pause": 1.8,
            "style": "calm",
            "volume": 1.0,
            "elevenlabs": {
                "voice_id": "ksaI0TCD9BstzEzlxj4q",
                "comparison_voice_id": "",
                "saved_voices": {
                    "여자 · 1순위": "ksaI0TCD9BstzEzlxj4q",
                    "남자 · 2순위": "jB1Cifc2UQbq1gR3wnb0",
                    "내 목소리 · 3순위": "5A7p8A1zreJtDuUUDbcb",
                },
                "model_id": "eleven_multilingual_v2",
                "language_code": "ko",
                "output_format": "mp3_44100_128",
                "stability": 0.65,
                "similarity_boost": 0.75,
                "style": 0.05,
                "speed": 0.90,
                "speaker_boost": True,
            },
        },
        "background": {
            "type": "video",
            "source_dir": "./assets/healing/videos",
            "collection": "longform-16x9",
            "mode": "sequence",
            "specific_file": "",
            "transition": "crossfade",
            "transition_seconds": 2.5,
            "random": True,
            "clip_count": 6,
            "intro_seconds": 25,
        },
        "music": {
            "enabled": True,
            "source_dir": "./assets/bgm",
            "nature_source_dir": "./assets/healing/nature",
            "specific_file": "",
            "mode": "music",
            "volume": 0.15,
            "ducking": True,
            "track_count": 3,
            "crossfade_seconds": 4.0,
            "master_lufs": -18.0,
            "true_peak": -2.0,
            "fade_in_seconds": 3,
            "fade_out_seconds": 5,
        },
        "subtitles": {
            "enabled": True,
            "max_lines": 2,
            "position": "bottom",
            "font_size": 16,
        },
        "overlays": {
            "show_title": True,
            "show_key_phrase": True,
            "show_channel": True,
            "show_logo": False,
            "show_subscribe_cta": True,
            "channel_name": "지혜로운 조각들",
            "logo_path": "",
            "title_font_size": 32,
            "key_phrase_font_size": 25,
            "channel_font_size": 21,
            "cta_font_size": 24,
        },
        "output": {
            "format": "mp4",
            "codec": "h264",
            "audio_codec": "aac",
            "directory": "./outputs/longform",
        },
    }
}


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_longform_config(path: Path = CONFIG_PATH) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if path.exists():
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            payload = loaded
    return validate_longform_config(_deep_merge(DEFAULT_CONFIG, payload))


def validate_longform_config(config: dict[str, Any]) -> dict[str, Any]:
    root = config.setdefault("longform", {})
    duration = int(root.get("duration_minutes") or 15)
    root["duration_minutes"] = max(1, min(60, duration))
    width, height = str(root.get("resolution") or "1920x1080").lower().split("x", 1)
    root["resolution"] = f"{max(640, int(width))}x{max(360, int(height))}"
    root["fps"] = max(24, min(60, int(root.get("fps") or 30)))

    schedule = root.setdefault("schedule", {})
    schedule["enabled"] = bool(schedule.get("enabled", True))
    schedule["auto_upload"] = bool(schedule.get("auto_upload", False))
    try:
        hour_text, minute_text = str(schedule.get("time") or "03:10").split(":", 1)
        hour, minute = int(hour_text), int(minute_text)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        schedule["time"] = f"{hour:02d}:{minute:02d}"
    except (TypeError, ValueError):
        schedule["time"] = "03:10"
    raw_days = schedule.get("days") or list(range(7))
    schedule["days"] = sorted({max(0, min(6, int(day))) for day in raw_days}) or list(range(7))
    if str(schedule.get("theme_mode") or "rotate") not in {"rotate", "fixed"}:
        schedule["theme_mode"] = "rotate"
    schedule["window_minutes"] = max(5, min(120, int(schedule.get("window_minutes") or 30)))
    if str(schedule.get("render_preset") or "veryfast") not in {
        "ultrafast", "superfast", "veryfast", "faster", "fast", "medium"
    }:
        schedule["render_preset"] = "veryfast"
    schedule["render_crf"] = max(16, min(28, int(schedule.get("render_crf") or 20)))
    schedule["render_threads"] = max(1, min(8, int(schedule.get("render_threads") or 2)))

    tts = root.setdefault("tts", {})
    if str(tts.get("provider") or "edge") not in {"edge", "elevenlabs"}:
        tts["provider"] = "edge"
    tts["speed"] = max(0.65, min(1.2, float(tts.get("speed") or 0.85)))
    tts["pitch"] = max(-30, min(30, int(tts.get("pitch") or 0)))
    tts["sentence_pause"] = max(0.2, min(3.0, float(tts.get("sentence_pause") or 0.9)))
    tts["paragraph_pause"] = max(0.5, min(5.0, float(tts.get("paragraph_pause") or 1.8)))
    tts["volume"] = max(0.1, min(2.0, float(tts.get("volume") or 1.0)))
    elevenlabs = tts.setdefault("elevenlabs", {})
    if str(elevenlabs.get("model_id") or "eleven_multilingual_v2") not in {
        "eleven_multilingual_v2", "eleven_v3", "eleven_flash_v2_5", "eleven_turbo_v2_5"
    }:
        elevenlabs["model_id"] = "eleven_multilingual_v2"
    elevenlabs["stability"] = max(0.0, min(1.0, float(elevenlabs.get("stability", 0.65))))
    elevenlabs["similarity_boost"] = max(0.0, min(1.0, float(elevenlabs.get("similarity_boost", 0.75))))
    elevenlabs["style"] = max(0.0, min(1.0, float(elevenlabs.get("style", 0.05))))
    elevenlabs["speed"] = max(0.7, min(1.2, float(elevenlabs.get("speed", 0.90))))
    raw_saved_voices = elevenlabs.get("saved_voices") or {}
    elevenlabs["saved_voices"] = {
        str(label).strip(): str(voice_id).strip()
        for label, voice_id in raw_saved_voices.items()
        if str(label).strip() and str(voice_id).strip()
    } if isinstance(raw_saved_voices, dict) else {}

    background = root.setdefault("background", {})
    if str(background.get("mode") or "sequence") not in {
        "hybrid", "sequence", "random", "single_loop", "specific", "image_pan"
    }:
        background["mode"] = "sequence"
    if str(background.get("transition") or "crossfade") not in {"crossfade", "cut"}:
        background["transition"] = "crossfade"
    background["clip_count"] = max(1, min(12, int(background.get("clip_count") or 6)))
    background["intro_seconds"] = max(10, min(60, int(background.get("intro_seconds") or 25)))
    background["transition_seconds"] = max(
        0.0, min(4.0, float(background.get("transition_seconds") or 1.5))
    )

    music = root.setdefault("music", {})
    if str(music.get("mode") or "music") not in {"music", "nature", "none"}:
        music["mode"] = "music"
    music["volume"] = max(0.0, min(1.0, float(music.get("volume") or 0.15)))
    music["track_count"] = max(1, min(5, int(music.get("track_count") or 3)))
    music["crossfade_seconds"] = max(
        0.0, min(10.0, float(music.get("crossfade_seconds", 4.0)))
    )
    music["master_lufs"] = max(-24.0, min(-14.0, float(music.get("master_lufs", -18.0))))
    music["true_peak"] = max(-4.0, min(-1.0, float(music.get("true_peak", -2.0))))
    music["fade_in_seconds"] = max(0.0, min(20.0, float(music.get("fade_in_seconds") or 3)))
    music["fade_out_seconds"] = max(0.0, min(20.0, float(music.get("fade_out_seconds") or 5)))

    subtitles = root.setdefault("subtitles", {})
    subtitles["max_lines"] = max(1, min(2, int(subtitles.get("max_lines") or 2)))
    subtitles["font_size"] = max(10, min(32, int(subtitles.get("font_size") or 16)))

    overlays = root.setdefault("overlays", {})
    for key, default in {
        "title_font_size": 32,
        "key_phrase_font_size": 25,
        "channel_font_size": 21,
        "cta_font_size": 24,
    }.items():
        overlays[key] = max(20, min(72, int(overlays.get(key) or default)))

    output = root.setdefault("output", {})
    if str(output.get("preset") or "medium") not in {
        "ultrafast", "superfast", "veryfast", "faster", "fast", "medium"
    }:
        output["preset"] = "medium"
    output["crf"] = max(14, min(30, int(output.get("crf") or 16)))
    output["threads"] = max(0, min(16, int(output.get("threads") or 0)))
    return config


def save_longform_config(config: dict[str, Any], path: Path = CONFIG_PATH) -> dict[str, Any]:
    validated = validate_longform_config(_deep_merge(DEFAULT_CONFIG, config))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(validated, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    return validated


def config_with_overrides(base: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    return validate_longform_config(_deep_merge(base, {"longform": overrides}))
