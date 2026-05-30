"""
패러디/팬픽 원작 판별 모듈.

사용법:
    # 1. 파일명 키워드만 (빠름, 무료)
    from noveltool.parody import detect_by_filename
    is_p, source = detect_by_filename("[나루토] 우치하 전생 1-200.txt")

    # 2. 파일명 실패 시 LLM 폴백
    from noveltool.parody import detect
    is_p, source = detect("unknown_title.txt", path=Path("/mnt/d/nov/unknown_title.txt"), llm_client=client)
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# ── 원작 키워드 사전 ──────────────────────────────────────────────────────────
# { 원작명: [판별 키워드] }  — 키워드는 대소문자 무관하게 매칭
PARODY_MAP: dict[str, list[str]] = {
    # 점프/소년만화
    "나루토":       ["나루토", "호카게", "우치하", "이타치", "카구야", "히나타", "사스케",
                    "나뭇잎마을", "木葉", "火影", "오오츠츠키", "오로치마루", "카카시",
                    "닌자 세계", "Shinobi World"],
    "블리치":       ["블리치", "아이젠", "이치고", "소울소사이어티", "사신", "호정십삼대", "켄파치"],
    "원피스":       ["원피스", "화이트비어드", "해적왕", "아오키지", "루피", "大航海", "海贼"],
    "드래곤볼":     ["드래곤볼"],
    "헌터헌터":     ["헌터×헌터", "헌터x헌터", "헌터헌터", "클로로 루실후르", "키르아"],
    "페어리테일":   ["페어리 테일", "페어리테일", "fairy tail", "Fairy Tail",
                    "드래곤 슬레이어", "에스카노르", "妖尾"],
    "나의영웅":     ["히로아카", "나의 영웅 아카데미아", "히어로 아카데미아", "僕のヒーロー"],
    "짱구":         ["짱구", "신노스케", "野原新之助", "노하라신노스케"],
    # 해리포터
    "해리포터":     ["해리포터", "호그와트", "그리핀도르", "헤르미온느", "格沃茨",
                    "슬리데린", "해리 포터"],
    # DC / 마블
    "DC":           ["DC유니버스", "DC 유니버스", "배트맨", "DC에서", "DC宇宙", "蝙蝠俠"],
    "마블":         ["마블", "크립토니안", "어벤저스", "홈랜더", "둠스데이 슈퍼맨", "漫威", "氪星人"],
    # 애니/게임
    "명일방주":     ["명일방주", "아크나이츠", "명방", "明日方舟", "로도스 아일랜드", "켈시", "테라"],
    "붕괴스타레일": ["붕괴 스타레일", "스타레일"],
    "원신":         ["원신", "겐신"],
    "EVA":          ["EVA", "에반게리온", "이카리 신지"],
    "건담":         ["건담", "양산기"],
    "코노스바":     ["코노스바", "카즈마", "아쿠아", "메구밍"],
    "페르소나":     ["페르소나", "心灵怪盗"],
    "포켓몬":       ["포켓몬", "포켓몬스터"],
    # 라이트노벨
    "타입문":       ["타입문", "型月", "型月之"],
    "데이트어라이브": ["데어라", "데이트 어라이브", "데이트어라이브"],
    "스트라이크더블러드": ["Strike the Blood"],
    "장송의프리렌": ["프리렌", "장송의 프리렌"],
    # 명탐정 코난
    "명탐정코난":   ["명탐정 코난", "柯南世界", "柯学", "코난 세계"],
    # 중국 판타지 원작
    "투파창궁":     ["투파창궁", "斗破苍穹"],
    "학사신공":     ["학사신공", "盘龙"],
    "홍황":         ["홍황", "洪荒"],
    # 기타
    "주술회전":     ["주술회전", "고죠 사토루"],
    "워해머":       ["워해머"],
}

_LLM_PROMPT = (
    "다음 소설 텍스트의 앞부분입니다.\n"
    "이 소설이 기존 작품(만화·애니·게임·소설 등)의 팬픽/패러디인지 판단하세요.\n\n"
    "[텍스트]\n{snippet}\n\n"
    "팬픽/패러디라면: {{\"is_parody\": true, \"source\": \"원작명\"}}\n"
    "오리지널이라면: {{\"is_parody\": false, \"source\": \"\"}}\n"
    "JSON으로만 응답:"
)


def detect_by_filename(name: str) -> tuple[bool, str]:
    """파일명 키워드 기반 원작 판별. 크로스오버는 'A x B' 형태로 반환."""
    found: list[str] = []
    name_lower = name.lower()
    for source, keywords in PARODY_MAP.items():
        for kw in keywords:
            if kw.lower() in name_lower:
                found.append(source)
                break
    if found:
        return True, " x ".join(found)
    return False, ""


def detect_by_content(snippet: str, llm_client) -> tuple[bool, str]:
    """텍스트 앞부분 샘플 + LLM으로 원작 판별."""
    messages = [{"role": "user", "content": _LLM_PROMPT.format(snippet=snippet[:1500])}]
    try:
        response = llm_client.chat(messages, temperature=0)
        start, end = response.find("{"), response.rfind("}") + 1
        if start == -1 or end == 0:
            return False, ""
        data = json.loads(response[start:end])
        if data.get("is_parody"):
            return True, data.get("source", "unknown")
    except Exception:
        pass
    return False, ""


def _read_txt_snippet(path: Path, chars: int = 1500) -> str | None:
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read(chars)
    except Exception:
        return None


def detect(
    name: str,
    path: Path | None = None,
    llm_client=None,
) -> tuple[bool, str]:
    """
    통합 판별 함수.
    1) 파일명 키워드 → 감지 시 즉시 반환
    2) txt 파일 + llm_client 있으면 내용 샘플링 후 LLM 폴백
    반환: (is_parody, source_name)
    """
    is_p, src = detect_by_filename(name)
    if is_p:
        return is_p, src

    if llm_client is not None and path is not None and path.suffix.lower() == ".txt":
        snippet = _read_txt_snippet(path)
        if snippet:
            return detect_by_content(snippet, llm_client)

    return False, ""
