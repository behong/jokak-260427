from __future__ import annotations

import re


EMOTION_PATTERNS = [
    (["힘들", "지쳐", "버티", "포기"], "지쳐있을 때 읽는 글"),
    (["실패", "틀렸", "실수", "잘못"], "실패한 것 같을 때"),
    (["혼자", "외로", "고독"], "혼자인 것 같을 때"),
    (["자신", "나를", "자존", "나는"], "나 자신에게 필요한 말"),
    (["용서", "이해", "받아들"], "마음이 무거울 때"),
    (["성장", "노력", "꾸준", "루틴"], "조금씩 나아가고 싶을 때"),
    (["관계", "사람", "타인", "친구"], "사람에게 상처받았을 때"),
    (["슬픔", "눈물", "아프"], "슬픔을 느끼는 당신에게"),
    (["행복", "감사", "소중"], "오늘 하루 위로가 필요할 때"),
]

TAG_POOL = {
    "감정/위로": ["힐링", "위로의말", "마음치유", "공감", "따뜻한말"],
    "성장/동기": ["동기부여", "자기계발", "성장명언", "긍정명언", "루틴"],
    "관계": ["인간관계", "사람명언", "관계명언"],
    "자존감": ["자존감명언", "자존감", "나를사랑하기"],
    "일상/힐링": ["잠들기전힐링", "짧은글귀", "좋은글귀", "일상명언"],
}

KEYWORD_TO_CATEGORY = {
    "힘들": "감정/위로",
    "지쳐": "감정/위로",
    "위로": "감정/위로",
    "슬픔": "감정/위로",
    "외로": "감정/위로",
    "아프": "감정/위로",
    "성장": "성장/동기",
    "노력": "성장/동기",
    "루틴": "성장/동기",
    "꾸준": "성장/동기",
    "실패": "성장/동기",
    "도전": "성장/동기",
    "사람": "관계",
    "관계": "관계",
    "타인": "관계",
    "자신": "자존감",
    "자존": "자존감",
    "나를": "자존감",
}

BASE_TAGS = ["명언", "힐링", "좋은글귀", "지혜로운조각들", "짧은영상"]
TITLE_MAX_LENGTH = 70


def _first_sentence(quote: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(quote or "")).strip()
    sentence = re.split(r"[.。,，\n]", text)[0].strip()
    return sentence[:limit].rstrip()


def _clean_title(title: str) -> str:
    title = re.sub(r"\s+", " ", str(title or "")).strip()
    title = title.strip("\"'`“”‘’")
    if len(title) > TITLE_MAX_LENGTH:
        title = title[: TITLE_MAX_LENGTH - 1].rstrip() + "…"
    return title or "오늘 하루 위로가 필요할 때"


def generate_title_local(quote: str) -> str:
    text = str(quote or "")
    for keywords, prefix in EMOTION_PATTERNS:
        if any(keyword in text for keyword in keywords):
            first = _first_sentence(text, 15)
            if first:
                return _clean_title(f"{prefix} | {first}…")
            return _clean_title(prefix)

    return _clean_title(_first_sentence(text, 20))


def generate_title(quote: str) -> str:
    return generate_title_local(quote)


def generate_tags_local(quote: str) -> list[str]:
    text = str(quote or "")
    tags = BASE_TAGS.copy()
    added_categories = set()

    for keyword, category in KEYWORD_TO_CATEGORY.items():
        if keyword in text and category not in added_categories:
            tags.extend(TAG_POOL[category][:2])
            added_categories.add(category)

    tags.extend(TAG_POOL["일상/힐링"][:2])
    return list(dict.fromkeys(tags))[:15]


def generate_tags(quote: str, title: str = "") -> list[str]:
    return generate_tags_local(f"{title} {quote}")
