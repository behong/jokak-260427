from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from render_video import render_video
from video_script import generate_video_script


@dataclass(frozen=True)
class VideoPipeline:
    source: str
    label: str
    description: str
    enabled: bool
    script_generator: Callable[[str], dict[str, object]] | None = None
    renderer: Callable | None = None


PIPELINES: dict[str, VideoPipeline] = {
    "글반장": VideoPipeline(
        source="글반장",
        label="문장형 쇼츠",
        description="짧은 문장을 천천히 읽는 감성형 영상",
        enabled=True,
        script_generator=generate_video_script,
        renderer=render_video,
    ),
    "글반장모음": VideoPipeline(
        source="글반장모음",
        label="문장형 쇼츠",
        description="CSV로 보충한 글반장 모음 글을 영상으로 제작",
        enabled=True,
        script_generator=generate_video_script,
        renderer=render_video,
    ),
    "직접입력": VideoPipeline(
        source="직접입력",
        label="수동 좋은글 쇼츠",
        description="직접 입력한 좋은글을 글반장과 같은 문장형 영상으로 제작",
        enabled=True,
        script_generator=generate_video_script,
        renderer=render_video,
    ),
}


def pipeline_for_source(source: str) -> VideoPipeline | None:
    return PIPELINES.get(source)


def pipeline_payload(source: str) -> dict[str, object]:
    pipeline = pipeline_for_source(source)
    if pipeline is None:
        return {
            "source": source,
            "label": "지원 예정",
            "description": "아직 영상 제작 스타일이 등록되지 않았습니다.",
            "enabled": False,
        }
    return {
        "source": pipeline.source,
        "label": pipeline.label,
        "description": pipeline.description,
        "enabled": pipeline.enabled,
    }


def enabled_sources() -> set[str]:
    return {source for source, pipeline in PIPELINES.items() if pipeline.enabled}
