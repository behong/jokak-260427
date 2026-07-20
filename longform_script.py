from __future__ import annotations

import hashlib
import random
import re
from typing import Any


THEMES: dict[str, dict[str, Any]] = {
    "오늘도 애쓴 당신에게": {
        "title": "오늘도 애쓴 당신에게",
        "thumbnail": "오늘도 정말 수고했어요",
        "focus": [
            "오늘 하루를 버텨 낸 것만으로도 당신은 충분히 많은 일을 해냈습니다.",
            "아무도 알아주지 않은 수고까지 당신의 하루에는 고스란히 남아 있습니다.",
            "잘한 일보다 부족했던 일이 먼저 떠올라도 오늘의 노력을 지울 수는 없습니다.",
            "끝까지 해내지 못한 일이 있어도 여기까지 온 마음은 존중받아야 합니다.",
            "당신이 견딘 피로는 게으름이 아니라 오래 애써 왔다는 신호일 수 있습니다.",
            "오늘의 당신은 완벽하지 않아도 충분히 성실했고 충분히 소중했습니다.",
        ],
    },
    "잠들기 전 듣는 위로": {
        "title": "잠들기 전 듣는 따뜻한 위로",
        "thumbnail": "잠들기 전, 당신에게",
        "focus": [
            "밤이 깊어질수록 낮에는 지나쳤던 생각들이 더 크게 들리곤 합니다.",
            "잠이 오지 않는다고 해서 지금의 휴식이 모두 헛된 것은 아닙니다.",
            "오늘 해결하지 못한 일은 잠시 밤의 바깥에 내려두어도 괜찮습니다.",
            "몸은 이미 쉬고 싶다는 신호를 보내고 있으니 그 뜻을 천천히 따라가 봅니다.",
            "생각을 멈추려 애쓰기보다 생각이 지나가도록 조용히 자리를 내어 줍니다.",
            "이 밤에는 무엇을 증명하지 않아도 당신의 자리가 충분히 안전합니다.",
        ],
    },
    "마음이 복잡한 밤": {
        "title": "마음이 복잡한 밤에 듣는 이야기",
        "thumbnail": "복잡한 마음을 잠시 내려놓아요",
        "focus": [
            "여러 생각이 한꺼번에 몰려오면 무엇부터 바라봐야 할지 알기 어렵습니다.",
            "정리되지 않는 마음은 잘못된 마음이 아니라 지친 마음일 때가 많습니다.",
            "답을 찾으려는 노력 때문에 오히려 마음의 소리가 더 커질 수도 있습니다.",
            "지금 떠오르는 모든 생각이 당장 처리해야 할 일은 아닙니다.",
            "마음속 소란은 밀어낼수록 커지기도 하니 잠시 그대로 바라봐 줍니다.",
            "복잡함 속에서도 숨은 들어오고 나가며 당신을 현재에 머물게 합니다.",
        ],
    },
    "인간관계에 지쳤을 때": {
        "title": "인간관계에 지친 날 듣는 따뜻한 이야기",
        "thumbnail": "마음이 지쳤다면 들어보세요",
        "focus": [
            "사람을 이해하려 애쓰는 동안 정작 내 마음을 놓칠 때가 있습니다.",
            "모든 관계를 잘 지켜 내야 한다는 책임까지 혼자 안을 필요는 없습니다.",
            "상대의 기분을 헤아렸던 만큼 오늘은 내 기분도 살펴봐 주면 좋겠습니다.",
            "거리를 두는 일은 미워하는 일이 아니라 나를 보호하는 선택일 수 있습니다.",
            "누군가의 기대에 맞지 않았다고 해서 당신의 가치가 줄어들지는 않습니다.",
            "좋은 관계는 한 사람의 인내만으로 유지되는 것이 아닙니다.",
        ],
    },
    "아무것도 하기 싫은 날": {
        "title": "아무것도 하기 싫은 날, 조용히 들어보세요",
        "thumbnail": "오늘은 여기까지 해도 괜찮아요",
        "focus": [
            "아무것도 하고 싶지 않은 날에는 몸과 마음이 멈춤을 부탁하는 것일 수 있습니다.",
            "의욕이 없다는 이유로 자신을 몰아붙이면 피로는 더 깊어지기 쉽습니다.",
            "오늘의 속도가 느리다고 해서 삶 전체가 뒤처지는 것은 아닙니다.",
            "작은 일 하나도 버겁게 느껴지는 날이 누구에게나 찾아옵니다.",
            "쉬는 동안 보이지 않게 회복되는 힘도 분명히 존재합니다.",
            "지금 필요한 것은 새로운 계획보다 아무 조건 없는 휴식일지 모릅니다.",
        ],
    },
    "걱정을 내려놓는 시간": {
        "title": "걱정을 내려놓는 시간",
        "thumbnail": "걱정은 잠시 내려놓으세요",
        "focus": [
            "걱정은 아직 오지 않은 시간을 미리 살아내게 만들곤 합니다.",
            "생각을 많이 한다고 모든 위험을 막을 수 있는 것은 아닙니다.",
            "지금 통제할 수 없는 일까지 마음속에 붙잡아 둘 필요는 없습니다.",
            "걱정하는 나를 탓하기보다 안전하고 싶었던 마음을 먼저 이해해 줍니다.",
            "불확실함을 없애지 못해도 오늘 할 수 있는 작은 일은 남아 있습니다.",
            "내일의 문제는 내일의 빛과 힘으로 바라봐도 늦지 않습니다.",
        ],
    },
    "아침에 듣는 긍정적인 글": {
        "title": "아침에 듣는 긍정적인 이야기",
        "thumbnail": "오늘은 좋은 일이 시작됩니다",
        "focus": [
            "새로운 아침은 어제와 다른 선택을 해 볼 수 있는 작은 여백을 건넵니다.",
            "거창한 목표보다 오늘 가능한 한 걸음이면 충분합니다.",
            "아직 정해지지 않은 하루에는 생각보다 많은 가능성이 남아 있습니다.",
            "몸을 일으키고 숨을 고르는 순간부터 오늘은 이미 시작되었습니다.",
            "작은 친절과 차분한 말 한마디가 하루의 방향을 바꾸기도 합니다.",
            "완벽한 준비를 기다리지 않아도 지금 가진 힘으로 출발할 수 있습니다.",
        ],
    },
    "불안할 때 마음을 안정시키는 글": {
        "title": "불안할 때 마음을 안정시키는 이야기",
        "thumbnail": "지금 이 순간은 안전합니다",
        "focus": [
            "불안이 찾아오면 몸은 실제보다 더 큰 위험이 다가온 것처럼 긴장합니다.",
            "가슴이 답답하고 생각이 빨라져도 그 감각은 조금씩 지나갈 수 있습니다.",
            "불안을 없애려 싸우기보다 지금 곁에 머물도록 조용히 바라봐 줍니다.",
            "발이 닿은 바닥과 등을 받치는 의자가 현재의 안전을 알려 줍니다.",
            "한 번에 깊이 쉬려 하지 말고 편안한 만큼만 천천히 숨을 내쉽니다.",
            "불안한 생각은 예언이 아니라 지친 마음이 보내는 신호일 수 있습니다.",
        ],
    },
}

YOUTUBE_PROFILES: dict[str, dict[str, Any]] = {
    "오늘도 애쓴 당신에게": {
        "titles": [
            "오늘도 수고했어요 | 잠들기 전 듣는 힐링 낭독 {minutes}분",
            "지친 마음 쉬어가세요 | 편안한 위로 낭독 {minutes}분",
            "잠들기 전 듣는 위로 | 마음이 편안해지는 힐링 낭독 {minutes}분",
        ],
        "summary": [
            "오늘 하루도 정말 수고하셨습니다.",
            "지친 마음을 편안하게 쉬게 해주는 따뜻한 힐링 낭독입니다.",
        ],
        "recommendations": [
            "잠들기 전 마음을 차분하게 정리하고 싶은 분",
            "하루의 피로와 걱정을 잠시 내려놓고 싶은 분",
            "따뜻한 글과 편안한 목소리가 필요한 분",
            "휴식이나 명상을 위한 낭독을 찾는 분",
        ],
        "hashtags": ["힐링낭독", "잠들기전듣는글", "마음위로", "오늘도수고했어요", "지혜로운조각들"],
        "tip": "🌙 잠들기 전에 듣는 경우 자동재생과 화면 밝기를 조절해 주세요.",
    },
    "잠들기 전 듣는 위로": {
        "titles": [
            "잠들기 전 듣는 따뜻한 위로 | 편안한 힐링 낭독 {minutes}분",
            "오늘 밤, 마음을 내려놓으세요 | 수면 전 위로 {minutes}분",
            "생각이 많은 밤 듣는 이야기 | 마음 안정 낭독 {minutes}분",
        ],
        "summary": [
            "오늘 밤만큼은 해결하지 못한 일들을 잠시 내려놓아도 괜찮습니다.",
            "생각이 많은 밤, 마음을 천천히 가라앉히는 따뜻한 위로 낭독입니다.",
        ],
        "recommendations": [
            "잠들기 전 생각이 많아 쉽게 쉬지 못하는 분",
            "조용한 목소리와 따뜻한 문장이 필요한 분",
            "불안과 긴장을 내려놓고 편안히 잠들고 싶은 분",
            "수면 전 마음을 차분하게 정리하고 싶은 분",
        ],
        "hashtags": ["잠들기전", "수면낭독", "따뜻한위로", "마음안정", "지혜로운조각들"],
        "tip": "🌙 잠들기 전에 듣는 경우 자동재생과 화면 밝기를 조절해 주세요.",
    },
    "마음이 복잡한 밤": {
        "titles": [
            "마음이 복잡한 밤에 듣는 이야기 | 힐링 낭독 {minutes}분",
            "생각이 멈추지 않는 밤 | 마음을 쉬게 하는 위로 {minutes}분",
            "복잡한 마음을 잠시 내려놓아요 | 편안한 밤 낭독 {minutes}분",
        ],
        "summary": [
            "여러 생각이 한꺼번에 몰려오는 밤에는 답보다 휴식이 먼저 필요합니다.",
            "복잡한 마음을 억지로 정리하지 않고 천천히 쉬게 하는 힐링 낭독입니다.",
        ],
        "recommendations": [
            "생각이 많아 마음이 쉽게 가라앉지 않는 분",
            "오늘 해결하지 못한 일을 계속 떠올리는 분",
            "조용히 호흡하며 감정을 정리하고 싶은 분",
            "잠들기 전 편안한 위로가 필요한 분",
        ],
        "hashtags": ["마음정리", "생각이많은밤", "힐링낭독", "잠들기전", "지혜로운조각들"],
        "tip": "🌙 잠들기 전에 듣는 경우 자동재생과 화면 밝기를 조절해 주세요.",
    },
    "인간관계에 지쳤을 때": {
        "titles": [
            "인간관계에 지친 날 듣는 위로 | 힐링 낭독 {minutes}분",
            "모든 사람에게 잘할 필요는 없어요 | 마음 위로 {minutes}분",
            "관계에 지친 마음을 쉬게 하는 이야기 | 편안한 낭독 {minutes}분",
        ],
        "summary": [
            "사람을 이해하려 애쓰는 동안 놓쳤던 내 마음을 오늘은 먼저 살펴봅니다.",
            "인간관계에 지친 마음을 부드럽게 다독이는 따뜻한 힐링 낭독입니다.",
        ],
        "recommendations": [
            "사람에게 맞추느라 마음이 지친 분",
            "관계에서 적당한 거리를 두고 싶은 분",
            "누군가의 기대 때문에 자신을 탓하는 분",
            "내 마음을 보호하는 따뜻한 문장이 필요한 분",
        ],
        "hashtags": ["인간관계", "마음위로", "관계에지쳤을때", "힐링낭독", "지혜로운조각들"],
        "tip": "🍃 조용한 공간에서 호흡을 천천히 고르며 들어보세요.",
    },
    "아무것도 하기 싫은 날": {
        "titles": [
            "아무것도 하기 싫은 날 들어보세요 | 힐링 낭독 {minutes}분",
            "오늘은 여기까지 해도 괜찮아요 | 따뜻한 위로 {minutes}분",
            "지친 마음에 휴식이 필요한 날 | 편안한 낭독 {minutes}분",
        ],
        "summary": [
            "아무것도 하고 싶지 않은 날은 마음과 몸이 휴식을 부탁하는 날일 수 있습니다.",
            "스스로를 몰아붙이지 않고 편안히 쉬도록 도와주는 따뜻한 낭독입니다.",
        ],
        "recommendations": [
            "작은 일도 버겁게 느껴지는 분",
            "의욕이 없어 자신을 탓하고 있는 분",
            "계획보다 조건 없는 휴식이 필요한 분",
            "조용히 누워 마음을 회복하고 싶은 분",
        ],
        "hashtags": ["아무것도하기싫은날", "휴식", "마음회복", "힐링낭독", "지혜로운조각들"],
        "tip": "🍃 아무것도 하지 않아도 괜찮으니 편안한 자세로 들어보세요.",
    },
    "걱정을 내려놓는 시간": {
        "titles": [
            "걱정을 내려놓는 시간 | 마음 안정 힐링 낭독 {minutes}분",
            "아직 오지 않은 일을 미리 걱정하지 마세요 | 위로 {minutes}분",
            "불안한 생각에서 잠시 쉬어가는 이야기 | 편안한 낭독 {minutes}분",
        ],
        "summary": [
            "아직 오지 않은 내일의 걱정을 오늘 모두 해결하지 않아도 괜찮습니다.",
            "통제할 수 없는 생각을 내려놓고 현재로 돌아오는 마음 안정 낭독입니다.",
        ],
        "recommendations": [
            "걱정이 꼬리를 물어 쉽게 쉬지 못하는 분",
            "아직 일어나지 않은 일을 반복해서 생각하는 분",
            "마음을 현재에 머물게 하고 싶은 분",
            "편안한 호흡과 따뜻한 위로가 필요한 분",
        ],
        "hashtags": ["걱정내려놓기", "마음안정", "불안완화", "힐링낭독", "지혜로운조각들"],
        "tip": "🍃 이어폰을 사용하거나 작은 음량으로 들으며 호흡을 천천히 골라보세요.",
    },
    "아침에 듣는 긍정적인 글": {
        "titles": [
            "아침에 듣는 긍정적인 이야기 | 좋은 하루 낭독 {minutes}분",
            "오늘은 좋은 일이 시작됩니다 | 아침 힐링 낭독 {minutes}분",
            "하루를 차분하게 시작하는 긍정 문장 | 편안한 낭독 {minutes}분",
        ],
        "summary": [
            "새로운 아침은 오늘 가능한 한 걸음을 시작할 작은 여백을 건넵니다.",
            "하루를 긍정적이고 차분한 마음으로 열어주는 따뜻한 아침 낭독입니다.",
        ],
        "recommendations": [
            "아침을 긍정적인 문장으로 시작하고 싶은 분",
            "출근이나 하루 준비 중 편안한 낭독을 듣고 싶은 분",
            "새로운 시작을 앞두고 용기가 필요한 분",
            "오늘의 마음가짐을 차분히 정리하고 싶은 분",
        ],
        "hashtags": ["아침긍정", "좋은하루", "긍정적인글", "힐링낭독", "지혜로운조각들"],
        "tip": "☀️ 아침 준비나 산책 시간에 작은 음량으로 편안하게 들어보세요.",
    },
    "불안할 때 마음을 안정시키는 글": {
        "titles": [
            "불안할 때 마음을 안정시키는 이야기 | 힐링 낭독 {minutes}분",
            "지금 이 순간은 안전합니다 | 불안한 마음을 위한 위로 {minutes}분",
            "가슴이 답답하고 생각이 많을 때 | 마음 안정 낭독 {minutes}분",
        ],
        "summary": [
            "불안한 생각은 미래의 예언이 아니라 지친 마음이 보내는 신호일 수 있습니다.",
            "빠르게 움직이는 생각을 늦추고 지금의 안전을 느끼게 하는 마음 안정 낭독입니다.",
        ],
        "recommendations": [
            "가슴이 답답하고 생각이 빠르게 이어지는 분",
            "불안을 없애려 애쓰느라 더욱 지친 분",
            "현재의 감각과 호흡에 집중하고 싶은 분",
            "조용하고 안전한 위로가 필요한 분",
        ],
        "hashtags": ["불안할때", "마음안정", "불안한마음", "힐링낭독", "지혜로운조각들"],
        "tip": "🍃 무리해서 깊게 숨 쉬기보다 편안한 만큼 천천히 내쉬어 보세요.",
    },
}


def build_youtube_metadata(theme: str, duration_minutes: int) -> dict[str, Any]:
    profile = YOUTUBE_PROFILES.get(theme) or YOUTUBE_PROFILES["오늘도 애쓴 당신에게"]
    minutes = max(1, min(60, int(duration_minutes)))
    title_options = [
        re.sub(r"\s*[|｜]\s*", " ｜ ", str(item).format(minutes=minutes))
        for item in profile["titles"]
    ]
    hashtags = list(dict.fromkeys([*profile["hashtags"], "좋은글"]))
    description = "\n".join(
        [
            *profile["summary"],
            "",
            "하루를 시작하거나 마무리하며 마음을 천천히 정리하고 싶은 시간,",
            "편안한 자세로 호흡을 고르고 천천히 들어보세요.",
            "",
            "이런 분께 추천합니다.",
            *(f"- {item}" for item in profile["recommendations"]),
            "",
            "🎧 이어폰을 사용하거나 작은 음량으로 들으시면 더욱 편안합니다.",
            str(profile["tip"]),
            "",
            "마음에 남은 문장이나 오늘의 이야기가 있다면 댓글로 남겨주세요.",
            "여러분의 댓글은 다음 낭독을 만드는 데 참고하겠습니다.",
            "",
            "구독과 좋아요는 더 좋은 글과 이야기를 만드는 데 큰 힘이 됩니다.",
            "",
            " ".join(f"#{tag}" for tag in hashtags),
        ]
    )
    tags = list(
        dict.fromkeys(
            [
                "지혜로운조각들", "힐링낭독", "마음위로", "좋은글", "긴영상",
                *hashtags, theme, "편안한목소리", "마음휴식",
            ]
        )
    )
    return {
        "title": title_options[0],
        "title_options": title_options,
        "description": description,
        "hashtags": hashtags,
        "tags": tags,
    }

REFLECTIONS = [
    "마음은 늘 논리대로 움직이지 않기에 먼저 이해받을 시간이 필요합니다.",
    "힘든 감정을 빨리 없애려 하지 않아도 감정은 제 속도로 옅어질 수 있습니다.",
    "지금의 느낌이 오래갈 것 같아도 모든 감정에는 조금씩 변하는 흐름이 있습니다.",
    "스스로에게 건네는 부드러운 말은 생각보다 오래 마음을 지탱해 줍니다.",
    "다른 사람에게는 쉽게 건넸던 이해를 오늘만큼은 나에게도 나누어 줍니다.",
    "마음이 흔들리는 것은 약해서가 아니라 그동안 중요하게 살아왔기 때문입니다.",
    "잘 버티는 것만이 강함은 아니며 잠시 기대고 쉬는 것도 삶의 힘입니다.",
    "지금 필요한 답이 선명하지 않아도 방향을 잃었다고 단정할 필요는 없습니다.",
    "하루의 한 장면이 당신의 전부를 설명할 수는 없습니다.",
    "스스로를 평가하는 목소리보다 몸이 보내는 작은 신호에 귀를 기울여 봅니다.",
    "오늘의 감정은 존중받아야 하지만 당신의 미래를 결정하지는 않습니다.",
    "조금 부족했던 순간까지도 살아가는 과정 안에 자연스럽게 포함되어 있습니다.",
]

ACTIONS = [
    "어깨에 들어간 힘을 알아차리고 숨을 내쉴 때 조금만 느슨하게 풀어 봅니다.",
    "손끝의 온도와 발바닥의 감각을 느끼며 마음을 지금 이곳으로 데려옵니다.",
    "천천히 숨을 들이마시고 그보다 조금 더 길게 내쉬어 봅니다.",
    "눈앞의 빛과 주변의 소리를 판단하지 말고 있는 그대로 느껴 봅니다.",
    "오늘 가장 힘들었던 순간을 떠올린 뒤 그때의 나에게 수고했다고 말해 줍니다.",
    "해야 할 일들을 마음속 선반에 잠시 올려두는 모습을 천천히 그려 봅니다.",
    "턱과 이마의 힘을 풀고 편안한 자세를 찾을 때까지 몸을 조금 움직여 봅니다.",
    "따뜻한 물 한 모금이 목을 지나가는 감각처럼 작은 편안함에 머물러 봅니다.",
    "한 가지 생각이 떠오르면 붙잡지 않고 흘러가는 구름처럼 보내 줍니다.",
    "지금 들리는 목소리만 따라오며 다른 문제는 잠시 뒤로 미뤄 둡니다.",
    "숨을 세지 않아도 괜찮으니 내쉬는 호흡이 자연스럽게 길어지는지 살펴봅니다.",
    "가슴 위에 손을 얹고 여기까지 잘 왔다고 조용히 마음을 다독여 봅니다.",
]

PERMISSIONS = [
    "오늘은 여기까지 해도 괜찮습니다.",
    "잠시 멈춘다고 해서 소중한 것을 잃는 것은 아닙니다.",
    "누구에게도 설명하지 않고 편안히 쉬어도 괜찮습니다.",
    "지금 당장 나아지지 않아도 이 시간을 그대로 보내도 됩니다.",
    "모든 사람에게 좋은 사람이 되지 않아도 당신은 충분히 좋은 사람입니다.",
    "조금 느리고 서툰 모습까지도 오늘의 나로 받아들여도 괜찮습니다.",
    "아직 답을 모르겠다면 모르는 채로 편안히 머물러도 됩니다.",
    "마음이 원하는 만큼 조용히 있고 말하지 않아도 괜찮습니다.",
    "내일의 힘을 오늘 미리 꺼내 쓰지 않아도 됩니다.",
    "당신의 회복에는 당신만의 시간이 필요할 수 있습니다.",
    "잘 쉬는 일도 오늘 해야 할 중요한 일 가운데 하나입니다.",
    "지금의 당신에게 필요한 것은 채찍보다 따뜻한 편일 수 있습니다.",
]

BRIDGES = [
    "이제 호흡을 한 번 고르며 다음 이야기를 천천히 이어가 보겠습니다.",
    "잠시 아무 말 없이 숨이 오가는 시간을 느껴 봅니다.",
    "그 마음을 그대로 둔 채 조금 더 편안한 쪽으로 시선을 옮겨 봅니다.",
    "여기까지 들었다면 이미 자신을 돌보는 시간을 잘 보내고 있습니다.",
    "서두르지 않고 지금의 속도 그대로 마음 곁에 머물러 봅니다.",
    "조금 전보다 단 한 부분이라도 편안해졌다면 그것으로 충분합니다.",
]


def available_themes() -> list[str]:
    return list(THEMES)


def _sentences(text: str) -> list[str]:
    return [item.strip() for item in re.split(r"(?<=[.!?])\s+", text) if item.strip()]


def _target_character_count(duration_minutes: int, tts_config: dict[str, Any] | None) -> int:
    """Estimate Korean copy length from the selected pace and configured pauses.

    The baseline (6.67 script chars/sec at Edge speed 1.0) is calibrated from
    a complete 15-minute Gyeol job, including Korean spaces and punctuation.
    Sentence and paragraph density values come from this module's structure.
    """
    config = tts_config or {}
    provider = str(config.get("provider") or "edge")
    if provider == "elevenlabs":
        speed = max(0.7, min(1.2, float((config.get("elevenlabs") or {}).get("speed") or 0.90)))
        baseline_chars_per_second = 6.3
    else:
        speed = max(0.65, min(1.2, float(config.get("speed") or 0.85)))
        baseline_chars_per_second = 6.67
    sentence_pause = max(0.2, float(config.get("sentence_pause") or 0.9))
    paragraph_pause = max(sentence_pause, float(config.get("paragraph_pause") or 1.8))
    speech_seconds_per_char = 1.0 / (baseline_chars_per_second * speed)
    pause_seconds_per_char = sentence_pause / 37.0
    paragraph_extra_per_char = (paragraph_pause - sentence_pause) / 150.0
    chars_per_minute = 60.0 / (
        speech_seconds_per_char + pause_seconds_per_char + paragraph_extra_per_char
    )
    # Leave half a paragraph of headroom because paragraphs are appended whole.
    return max(450, int(duration_minutes * chars_per_minute * 0.97))


def generate_longform_script(
    theme: str,
    duration_minutes: int,
    tone: str = "calm",
    tts_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    profile = THEMES.get(theme) or THEMES["오늘도 애쓴 당신에게"]
    duration_minutes = max(1, min(60, int(duration_minutes)))
    target_chars = _target_character_count(duration_minutes, tts_config)
    seed = int(hashlib.sha256(f"{theme}:{duration_minutes}:{tone}".encode("utf-8")).hexdigest()[:16], 16)
    rng = random.Random(seed)

    intro = [
        "오늘 하루도 많이 애쓰셨습니다.",
        "지금 이 시간만큼은 아무것도 해결하려 하지 않아도 괜찮습니다.",
        "몸이 편안한 자리를 찾고 잠시 눈을 감은 채 이야기를 들어보세요.",
    ]
    outro = [
        "오늘 해결하지 못한 일은 내일의 나에게 잠시 맡겨두세요.",
        "지금은 편안히 쉬어도 괜찮습니다.",
        "당신은 오늘도 충분히 애썼고 여기까지 잘 왔습니다.",
        "오늘도 정말 수고 많으셨습니다.",
    ]

    focus = list(profile["focus"])
    reflections = list(REFLECTIONS)
    actions = list(ACTIONS)
    permissions = list(PERMISSIONS)
    rng.shuffle(focus)
    rng.shuffle(reflections)
    rng.shuffle(actions)
    rng.shuffle(permissions)

    paragraphs: list[str] = []
    index = 0
    while len(" ".join([*intro, *paragraphs, *outro])) < target_chars:
        paragraph = " ".join(
            [
                focus[index % len(focus)],
                reflections[(index * 5 + index // len(focus)) % len(reflections)],
                actions[(index * 7 + 2) % len(actions)],
                permissions[(index * 11 + 3) % len(permissions)],
            ]
        )
        if index and index % 4 == 0:
            paragraph = f"{BRIDGES[(index // 4 - 1) % len(BRIDGES)]} {paragraph}"
        if paragraph not in paragraphs:
            paragraphs.append(paragraph)
        index += 1
        if index > 180:
            break

    full_paragraphs = [" ".join(intro), *paragraphs, " ".join(outro)]
    narration = "\n\n".join(full_paragraphs)
    youtube = build_youtube_metadata(theme if theme in THEMES else "오늘도 애쓴 당신에게", duration_minutes)
    sentences = [sentence for paragraph in full_paragraphs for sentence in _sentences(paragraph)]
    config = tts_config or {}
    if str(config.get("provider") or "edge") == "elevenlabs":
        speech_speed = max(0.7, float((config.get("elevenlabs") or {}).get("speed") or 0.90))
        baseline_chars_per_second = 6.3
    else:
        speech_speed = max(0.65, float(config.get("speed") or 0.85))
        baseline_chars_per_second = 6.67
    speech_seconds = len(narration) / (baseline_chars_per_second * speech_speed)
    sentence_pause = float((tts_config or {}).get("sentence_pause") or 0.9)
    paragraph_pause = float((tts_config or {}).get("paragraph_pause") or 1.8)
    estimated_seconds = speech_seconds + len(sentences) * sentence_pause
    estimated_seconds += len(full_paragraphs) * max(0.0, paragraph_pause - sentence_pause)
    return {
        "theme": theme if theme in THEMES else "오늘도 애쓴 당신에게",
        "tone": tone,
        "title": youtube["title"],
        "display_title": str(profile["title"]),
        "title_options": youtube["title_options"],
        "thumbnail_text": str(profile["thumbnail"]),
        "intro": " ".join(intro),
        "body_paragraphs": paragraphs,
        "outro": " ".join(outro),
        "paragraphs": full_paragraphs,
        "narration": narration,
        "sentences": sentences,
        "description": youtube["description"],
        "hashtags": youtube["hashtags"],
        "tags": youtube["tags"],
        "target_minutes": duration_minutes,
        "character_count": len(narration),
        "estimated_duration_seconds": round(estimated_seconds, 1),
    }
