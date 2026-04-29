from __future__ import annotations

import math
import re


SOURCE_PATTERN = re.compile(r"^\s*[-–—]?\s*(출처|Source)\s*[:：]?\s*(.+?)\s*$", re.IGNORECASE)
BRAND_NAME = "지혜로운 조각들"
MAX_LINES_PER_PAGE = 3
MAX_CHARS_PER_LINE = 15
MIN_PAGE_SECONDS = 8
MAX_PAGE_SECONDS = 14
OUTRO_SECONDS = 5
FINAL_PAGE_SECONDS = 6


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def split_source(content: str) -> tuple[str, str]:
    lines = [
        line.strip()
        for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if line.strip()
    ]
    if not lines:
        return "", ""

    match = SOURCE_PATTERN.match(lines[-1])
    if match:
        source = normalize_text(match.group(2))
        body = "\n".join(lines[:-1]).strip()
        return body, source
    if lines[-1].startswith("-") and lines[-1].endswith("-") and len(lines[-1]) <= 50:
        source = normalize_text(lines[-1].strip("- "))
        body = "\n".join(lines[:-1]).strip()
        return body, source
    return content.strip(), ""


def paragraphs_from_content(content: str) -> list[str]:
    blocks = [
        normalize_text(block)
        for block in re.split(r"\n\s*\n", content.replace("\r\n", "\n").replace("\r", "\n"))
        if normalize_text(block)
    ]
    if len(blocks) > 1:
        return blocks

    return [
        normalize_text(line)
        for line in content.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if normalize_text(line)
    ]


def title_from_paragraphs(paragraphs: list[str]) -> str:
    if not paragraphs:
        return "오늘의 문장"

    first = paragraphs[0].strip(" \"'“”‘’")
    if len(first) <= 24:
        return first
    return first[:23].rstrip() + "..."


def wrap_korean_text(text: str, max_chars: int = MAX_CHARS_PER_LINE) -> list[str]:
    words = text.split()
    if not words:
        return []

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            lines.append(current)
        while len(word) > max_chars:
            lines.append(word[:max_chars])
            word = word[max_chars:]
        current = word

    if current:
        lines.append(current)
    return lines


def split_paragraph_for_pages(paragraph: str) -> list[list[str]]:
    wrapped = wrap_korean_text(paragraph)
    return [
        wrapped[index : index + MAX_LINES_PER_PAGE]
        for index in range(0, len(wrapped), MAX_LINES_PER_PAGE)
    ]


def final_page_lines(paragraphs: list[str], source: str) -> list[str]:
    lines: list[str] = []
    for paragraph in paragraphs:
        lines.extend(wrap_korean_text(paragraph, max_chars=16))
    if source:
        lines.extend(wrap_korean_text(f"출처 {source}", max_chars=16))
    return lines


def page_duration(text: str) -> int:
    # TTS will later use the same paragraph text, so keep the visual pace close to calm narration.
    seconds = 4.5 + len(text) * 0.23
    return max(MIN_PAGE_SECONDS, min(MAX_PAGE_SECONDS, int(math.ceil(seconds))))


def paginate_paragraphs(paragraphs: list[str]) -> list[dict[str, object]]:
    pages: list[dict[str, object]] = []
    for paragraph_index, paragraph in enumerate(paragraphs, start=1):
        chunks = split_paragraph_for_pages(paragraph)
        for chunk_index, lines in enumerate(chunks, start=1):
            chunk_text = normalize_text(" ".join(lines))
            pages.append(
                {
                    "number": len(pages) + 1,
                    "paragraph_number": paragraph_index,
                    "paragraph_part": chunk_index,
                    "lines": lines,
                    "text": chunk_text,
                    "tts_text": chunk_text,
                    "duration_seconds": page_duration(chunk_text),
                }
            )
    return pages


def generate_video_script(content: str) -> dict[str, object]:
    body, source = split_source(content)
    paragraphs = paragraphs_from_content(body)
    pages = paginate_paragraphs(paragraphs)
    narration = [str(page["tts_text"]) for page in pages]
    outro_page = None
    if paragraphs or source:
        full_text = " ".join([*paragraphs, f"출처 {source}" if source else ""])
        outro_page = {
            "number": len(pages) + 1,
            "lines": final_page_lines(paragraphs, source),
            "text": normalize_text(full_text),
            "tts_text": f"다시 한번 읽어봅니다. {normalize_text(full_text)}",
            "duration_seconds": max(FINAL_PAGE_SECONDS, min(8, page_duration(full_text))),
            "type": "full_text",
        }

    return {
        "brand": BRAND_NAME,
        "title": title_from_paragraphs(paragraphs),
        "narration": narration,
        "captions": narration,
        "pages": pages,
        "outro_page": outro_page,
        "source": source,
        "safety_note": "원본 이미지 대신 허가된 배경 영상과 텍스트 오버레이를 사용합니다.",
        "style_note": "9:16 세로 영상, 문단 단위 전환, TTS 연결을 고려한 천천히 읽는 속도입니다.",
        "original_line_count": len([line for line in content.splitlines() if line.strip()]),
        "estimated_seconds": sum(page["duration_seconds"] for page in pages)
        + (outro_page["duration_seconds"] if outro_page else 0),
    }
