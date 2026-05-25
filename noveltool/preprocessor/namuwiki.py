import json
import os
import re
from dataclasses import dataclass
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

NAMUWIKI_BASE = 'https://namu.wiki'
TARGET_HEADINGS = {'등장인물', '주요 등장인물', '주요 인물', '인물', '캐릭터'}
HEADING_TAGS = {'h2', 'h3', 'h4', 'h5'}


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


def _fetch_html(title: str) -> str | None:
    url = f'{NAMUWIKI_BASE}/w/{quote(title)}'
    try:
        resp = requests.get(
            url,
            headers={'User-Agent': 'Mozilla/5.0 (compatible; translator-bot/1.0)'},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        print(f'[경고] 나무위키 요청 실패 ({title}): {e}')
    return None


def _is_target_heading(tag) -> bool:
    return tag.name in HEADING_TAGS and any(t in tag.get_text(strip=True) for t in TARGET_HEADINGS)


def _parse_characters(html: str, work: str) -> list[CharacterProfile]:
    soup = BeautifulSoup(html, 'html.parser')
    characters: list[CharacterProfile] = []

    for heading in soup.find_all(HEADING_TAGS):
        if not _is_target_heading(heading):
            continue

        heading_level = int(heading.name[1])
        sibling = heading.find_next_sibling()

        while sibling:
            if sibling.name in HEADING_TAGS and int(sibling.name[1]) <= heading_level:
                break

            if sibling.name in ('ul', 'ol'):
                for li in sibling.find_all('li', recursive=False):
                    profile = _parse_li(li, work)
                    if profile:
                        characters.append(profile)

            sibling = sibling.find_next_sibling()

        if characters:
            break

    return characters


def _parse_li(li, work: str) -> CharacterProfile | None:
    text = li.get_text(' ', strip=True)
    if not text:
        return None

    name_el = li.find(['strong', 'b'])
    if name_el:
        name = name_el.get_text(strip=True)
        desc_text = text[len(name):].lstrip(' :/-–').strip()
    else:
        parts = text.split(':', 1)
        name = parts[0].strip()
        desc_text = parts[1].strip() if len(parts) > 1 else ''

    if not name:
        return None

    sentences = re.split(r'(?<=[.!?。！？])\s+', desc_text)
    desc = ' '.join(sentences[:2])

    return CharacterProfile(original=name, korean=name, work=work, desc=desc)


def fetch_characters(article: str, cache_dir: str) -> list[CharacterProfile]:
    cache_file = _cache_path(cache_dir, article)

    if os.path.exists(cache_file):
        print(f'[나무위키] \'{article}\' 캐시 로드: {cache_file}')
        with open(cache_file, encoding='utf-8') as f:
            data = json.load(f)
        return [CharacterProfile(**d) for d in data]

    candidates = [f'{article}/등장인물', article, f'{article} 등장인물']
    characters: list[CharacterProfile] = []

    for title in candidates:
        html = _fetch_html(title)
        if not html:
            continue
        characters = _parse_characters(html, article)
        if characters:
            break

    if not characters:
        print(f'[경고] \'{article}\' 나무위키 캐릭터 파싱 결과 없음')
    else:
        print(f'[나무위키] \'{article}\' {len(characters)}명 수집 → 캐시 저장')
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump([vars(c) for c in characters], f, ensure_ascii=False, indent=2)

    return characters
