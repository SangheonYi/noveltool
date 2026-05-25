import json
import os
import re
from dataclasses import dataclass
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup


NAMUWIKI_BASE = 'https://namu.wiki'


@dataclass
class CharacterProfile:
    original: str
    korean: str
    work: str
    desc: str


def _slug(text: str) -> str:
    return re.sub(r'[^\w가-힣]', '_', text).strip('_')


def _cache_path(cache_dir: str, article: str) -> str:
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, f'{_slug(article)}_characters.json')


def _fetch_text(title: str) -> str | None:
    url = f'{NAMUWIKI_BASE}/w/{quote(title)}'
    try:
        resp = requests.get(
            url,
            headers={'User-Agent': 'Mozilla/5.0 (compatible; translator-bot/1.0)'},
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        soup = BeautifulSoup(resp.text, 'html.parser')
        # 스크립트·스타일 제거 후 텍스트 추출
        for tag in soup(['script', 'style', 'meta', 'link']):
            tag.decompose()
        return soup.get_text(separator='\n', strip=True)
    except Exception as e:
        print(f'[경고] 나무위키 요청 실패 ({title}): {e}')
        return None


def _llm_extract(llm_client, page_text: str, work: str) -> list[CharacterProfile]:
    """LLM으로 나무위키 텍스트에서 캐릭터 프로필 추출."""
    # 토큰 절약: 앞 6000자만 전달
    truncated = page_text[:6000]
    messages = [
        {
            'role': 'user',
            'content': (
                f'아래는 "{work}" 나무위키 문서의 텍스트입니다.\n'
                '이 페이지가 등장인물 프로필을 담고 있다면, 등장인물 목록을 JSON 배열로 추출하세요.\n'
                '등장인물이 없거나 관련 없는 페이지라면 빈 배열 []을 반환하세요.\n\n'
                '각 항목 형식:\n'
                '{"original": "원어 이름 (일본어/중국어/영어)", "korean": "한국어 이름", "desc": "한 줄 설명"}\n\n'
                '규칙:\n'
                '- original은 원작 언어 표기, korean은 나무위키에서 사용하는 한국어 이름\n'
                '- 같은 인물이 여러 이름으로 나와도 한 번만 포함\n'
                '- desc는 인물의 핵심 특징 한 줄 (성별·직책·관계 등)\n'
                '- 최대 30명\n\n'
                f'나무위키 텍스트:\n{truncated}\n\n'
                'JSON 배열로만 응답하세요. 다른 설명 없이.'
            ),
        }
    ]
    try:
        response = llm_client.chat(messages, temperature=0)
        start = response.find('[')
        end = response.rfind(']') + 1
        if start == -1 or end == 0:
            return []
        data = json.loads(response[start:end])
        profiles = []
        for item in data:
            if not isinstance(item, dict):
                continue
            original = (item.get('original') or '').strip()
            korean = (item.get('korean') or '').strip()
            desc = (item.get('desc') or '').strip()
            if original and korean:
                profiles.append(CharacterProfile(original=original, korean=korean, work=work, desc=desc))
        return profiles
    except Exception as e:
        print(f'[경고] LLM 캐릭터 추출 실패 ({work}): {e}')
        return []


def fetch_characters(article: str, cache_dir: str, llm_client=None) -> list[CharacterProfile]:
    cache_file = _cache_path(cache_dir, article)

    if os.path.exists(cache_file):
        print(f'[나무위키] \'{article}\' 캐시 로드: {cache_file}')
        with open(cache_file, encoding='utf-8') as f:
            data = json.load(f)
        return [CharacterProfile(**d) for d in data]

    # /등장인물 하위 페이지 우선, 없으면 본문 페이지
    candidates = [f'{article}/등장인물', article]
    page_text: str | None = None

    for title in candidates:
        text = _fetch_text(title)
        if text:
            page_text = text
            print(f'[나무위키] 페이지 로드: {title} ({len(text)}자)')
            break

    if not page_text:
        print(f'[경고] \'{article}\' 나무위키 페이지 접근 실패')
        return []

    if llm_client is None:
        print(f'[경고] \'{article}\' llm_client 없음 — 캐릭터 추출 불가')
        return []

    characters = _llm_extract(llm_client, page_text, article)

    if not characters:
        print(f'[경고] \'{article}\' LLM 캐릭터 추출 결과 없음')
    else:
        print(f'[나무위키] \'{article}\' {len(characters)}명 추출 → 캐시 저장')
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump([vars(c) for c in characters], f, ensure_ascii=False, indent=2)

    return characters
