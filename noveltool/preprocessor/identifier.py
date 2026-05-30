"""
identifier.py — 원작 세계관 식별

각 캐릭터명을 나무위키에서 검색하여 등장 작품을 빈도 기반으로 집계한다.
LLM 추론 대신 실제 검색 결과를 사용하므로 신뢰도가 높다.
"""
from __future__ import annotations

import collections
import re
import time
from dataclasses import dataclass
from urllib.parse import quote_plus, unquote

import requests
from bs4 import BeautifulSoup

from .. import logger


# 나무위키 URL에서 제외할 프리픽스·서픽스
_SKIP_PREFIXES = ('틀:', '분류:', '템플릿:', '파일:', '분류:파일/')
_SKIP_SUFFIXES = ('채널', '갤러리', '마이너 갤러리', '관련 정보')

# 등장인물 페이지 URL 패턴: /w/{작품}/등장인물/...  또는  /w/{작품}/등장인물
_CHAR_PATH_RE = re.compile(r'^(.+?)(?:/등장인물(?:/.+)?|/캐릭터(?:/.+)?)$')

# catch-all 필터
_CATCH_ALL = {'unknown', 'other', 'misc', '기타', '단역', '미상', '불명', '엑스트라'}


@dataclass
class WorkCandidate:
    work: str
    characters: list[str]
    confidence: float
    reason: str


def _search_character(name: str) -> list[str]:
    """캐릭터명을 나무위키에서 검색 → 등장 작품 목록 반환."""
    log = logger.get()
    works: list[str] = []
    try:
        url = f'https://namu.wiki/Search?q={quote_plus(name)}'
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'Accept-Language': 'ko-KR,ko;q=0.9',
        }
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code != 200:
            return works
        soup = BeautifulSoup(resp.text, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = a['href']
            if not href.startswith('/w/'):
                continue
            title = unquote(href[3:]).replace('+', ' ')
            if any(title.startswith(p) for p in _SKIP_PREFIXES):
                continue
            if any(title.endswith(s) for s in _SKIP_SUFFIXES):
                continue
            # /w/{작품}/등장인물/... 형태 → 작품명 추출
            m = _CHAR_PATH_RE.match(title)
            if m:
                work = m.group(1).strip()
                if work and work not in works:
                    works.append(work)
            # /w/{작품} 형태이면서 제목에 작품명이 포함될 수 있음 — 그대로 후보
            elif '/' not in title and title not in works:
                works.append(title)
    except Exception as e:
        log.debug('[식별] 나무위키 검색 실패 (%s): %s', name, e)
    return works


def identify_works(llm_client, characters: set[str]) -> list[WorkCandidate]:
    """
    캐릭터명 각각을 나무위키에서 검색하여 등장 빈도가 높은 작품을 반환한다.
    llm_client 는 시그니처 호환성 유지용 (사용 안 함).
    """
    log = logger.get()
    if not characters:
        return []

    work_count: dict[str, int] = collections.Counter()
    work_chars: dict[str, list[str]] = collections.defaultdict(list)

    char_list = sorted(characters)
    log.info('[식별] 캐릭터 %d명 나무위키 검색 시작', len(char_list))
    print(f'[전처리] 캐릭터 {len(char_list)}명 나무위키 검색 중...')

    for i, name in enumerate(char_list):
        works = _search_character(name)
        for w in works:
            work_count[w] += 1
            if name not in work_chars[w]:
                work_chars[w].append(name)
        if (i + 1) % 10 == 0:
            print(f'  {i + 1}/{len(char_list)} 검색 완료...', flush=True)
        time.sleep(0.1)  # 나무위키 부하 방지

    if not work_count:
        log.warning('[식별] 어떤 작품도 검색되지 않음')
        print('[전처리] 나무위키 검색에서 작품 미발견')
        return []

    # 빈도 상위 작품 → 후보 목록 (최소 2개 캐릭터가 검색된 작품만)
    MIN_CHARS = max(1, len(char_list) // 20)  # 전체의 5% 이상 등장
    top = [
        (work, cnt) for work, cnt in work_count.most_common(10)
        if cnt >= MIN_CHARS
        and not any(p in work.lower() for p in _CATCH_ALL)
    ]

    log.info('[식별] 작품 빈도 상위:\n%s',
             '\n'.join(f'  {w}: {c}명' for w, c in work_count.most_common(10)))
    log.info('[식별] 최종 후보 (최소 %d명): %s', MIN_CHARS, [w for w, _ in top])
    print(f'[전처리] 원작 후보: {[w for w, _ in top]} (기준: {MIN_CHARS}명 이상 검색됨)')

    candidates = [
        WorkCandidate(
            work=work,
            characters=work_chars[work],
            confidence=min(1.0, cnt / len(char_list)),
            reason=f'나무위키 검색에서 {cnt}명 등장 확인',
        )
        for work, cnt in top
    ]
    return candidates
