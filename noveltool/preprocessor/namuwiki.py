import json
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from urllib.parse import quote

import requests
from bs4 import BeautifulSoup

from .. import logger


NAMUWIKI_BASE = 'https://namu.wiki'

_LIST_TEMPLATE = (
    '"{work}" 나무위키 등장인물 문서입니다.\n'
    '등장인물 이름 목록만 JSON 배열로 추출하세요. 최대 30명.\n\n'
    '형식: [{{"original": "원어이름", "korean": "나무위키에서 사용하는 한국어이름"}}, ...]\n\n'
    '나무위키 텍스트 (앞 4000자):\n{text}\n\n'
    'JSON 배열로만 응답:'
)

_PROFILE_TEMPLATE = (
    '"{korean}" (원어: {original}) 캐릭터의 나무위키 문서입니다. 작품: {work}\n\n'
    '이 캐릭터의 프로필을 JSON으로 추출하세요:\n'
    '{{"original": "{original}", "korean": "{korean}", '
    '"desc": "성별·나이·역할·성격·주요 능력/특징 등 핵심 정보 2~3문장"}}\n\n'
    '나무위키 텍스트 (앞 3000자):\n{text}\n\n'
    'JSON으로만 응답:'
)


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


def _fetch_text(title: str) -> tuple[str, str] | None:
    """(page_text, url) 반환. 실패 시 None."""
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
        for tag in soup(['script', 'style', 'meta', 'link']):
            tag.decompose()
        return soup.get_text(separator='\n', strip=True), url
    except Exception as e:
        logger.get().error('[나무위키] 요청 실패 (%s): %s', title, e)
        return None


def _llm_list_names(llm_client, page_text: str, work: str) -> list[dict]:
    """Step 1: 등장인물 문서에서 이름 목록 추출."""
    log = logger.get()
    prompt = _LIST_TEMPLATE.format(work=work, text=page_text[:4000])
    messages = [{'role': 'user', 'content': prompt}]
    log.debug('[나무위키] 이름목록 프롬프트:\n%s', prompt)
    try:
        response = llm_client.chat(messages, temperature=0)
        log.debug('[나무위키] 이름목록 응답:\n%s', response)
        start = response.find('[')
        end = response.rfind(']') + 1
        if start == -1 or end == 0:
            log.warning('[나무위키] \'%s\' 이름목록 파싱 실패', work)
            return []
        data = json.loads(response[start:end])
        names = [
            {'original': (item.get('original') or '').strip(), 'korean': (item.get('korean') or '').strip()}
            for item in data
            if isinstance(item, dict) and item.get('original') and item.get('korean')
        ]
        log.info('[나무위키] \'%s\' 이름 목록 %d명: %s', work, len(names), [n['korean'] for n in names])
        return names
    except Exception as e:
        log.error('[나무위키] \'%s\' 이름목록 추출 실패: %s', work, e)
        return []


def _fetch_character_page(korean: str, article: str) -> tuple[str, str] | None:
    """개별 캐릭터 페이지 fetch. (text, url) 반환."""
    candidates = [korean, f'{article}/등장인물/{korean}']
    for title in candidates:
        result = _fetch_text(title)
        if result:
            return result
    return None


def _llm_profile_single(llm_client, original: str, korean: str, page_text: str, page_url: str, work: str) -> CharacterProfile | None:
    """Step 2: 단일 캐릭터 상세 프로필 추출."""
    log = logger.get()
    prompt = _PROFILE_TEMPLATE.format(
        korean=korean, original=original, work=work, text=page_text[:3000]
    )
    messages = [{'role': 'user', 'content': prompt}]
    log.debug('[나무위키] %s 프로필 프롬프트 (페이지: %s):\n%s', korean, page_url, prompt[:300])
    try:
        response = llm_client.chat(messages, temperature=0)
        log.debug('[나무위키] %s 프로필 응답:\n%s', korean, response)
        start = response.find('{')
        end = response.rfind('}') + 1
        if start == -1 or end == 0:
            log.warning('[나무위키] %s 프로필 파싱 실패', korean)
            return None
        data = json.loads(response[start:end])
        profile = CharacterProfile(
            original=(data.get('original') or original).strip(),
            korean=(data.get('korean') or korean).strip(),
            work=work,
            desc=(data.get('desc') or '').strip(),
        )
        log.info('[나무위키] %s (%s): %s', profile.korean, profile.original, profile.desc[:80])
        return profile
    except Exception as e:
        log.error('[나무위키] %s 프로필 추출 실패: %s', korean, e)
        return None


def _llm_extract(llm_client, article_text: str, article: str, work: str) -> list[CharacterProfile]:
    """3단계 파이프라인: 이름목록 → 개별 페이지 fetch → 상세 프로필 (병렬)."""
    log = logger.get()

    # Step 1: 이름 목록
    log.info('[나무위키] Step 1: 이름목록 추출 프롬프트 템플릿:\n%s',
             _LIST_TEMPLATE.replace('{work}', work).replace('{text}', '... (페이지 텍스트)'))
    names = _llm_list_names(llm_client, article_text, work)
    if not names:
        return []

    # Step 2+3: 개별 페이지 fetch + 프로필 추출 (병렬)
    log.info('[나무위키] Step 2+3: %d명 개별 페이지 fetch 및 프로필 추출 (병렬)', len(names))
    log.info('[나무위키] 프로필 프롬프트 템플릿:\n%s',
             _PROFILE_TEMPLATE.replace('{work}', work)
             .replace('{original}', '<원어이름>').replace('{korean}', '<한국어이름>')
             .replace('{text}', '... (캐릭터 페이지 텍스트)'))

    def fetch_and_profile(name_dict: dict) -> CharacterProfile | None:
        original = name_dict['original']
        korean = name_dict['korean']
        result = _fetch_character_page(korean, article)
        if result:
            page_text, page_url = result
            log.info('[나무위키] %s 개별 페이지 로드: %s (%d자)', korean, page_url, len(page_text))
        else:
            # 개별 페이지 없음 → 메인 문서 텍스트에서 해당 인물 구간 추출 시도
            log.info('[나무위키] %s 개별 페이지 없음 → 메인 문서 텍스트 사용', korean)
            # 이름 주변 2000자를 잘라 사용
            idx = article_text.find(korean)
            if idx == -1:
                idx = article_text.find(original)
            if idx != -1:
                page_text = article_text[max(0, idx - 200): idx + 1800]
                page_url = f'{NAMUWIKI_BASE}/w/{quote(article)}'
            else:
                log.warning('[나무위키] %s 메인 문서에서도 이름 미발견 — 스킵', korean)
                return None
        return _llm_profile_single(llm_client, original, korean, page_text, page_url, work)

    profiles: list[CharacterProfile] = []
    with ThreadPoolExecutor(max_workers=min(len(names), 6)) as executor:
        futures = {executor.submit(fetch_and_profile, n): n['korean'] for n in names}
        for future in as_completed(futures):
            result = future.result()
            if result:
                profiles.append(result)

    log.info('[나무위키] \'%s\' 최종 프로필 %d명 수집', work, len(profiles))
    return profiles


def fetch_characters(article: str, cache_dir: str, llm_client=None) -> list[CharacterProfile]:
    log = logger.get()
    cache_file = _cache_path(cache_dir, article)

    if os.path.exists(cache_file):
        log.info('[나무위키] \'%s\' 캐시 로드: %s', article, cache_file)
        print(f'[나무위키] \'{article}\' 캐시 로드: {cache_file}')
        with open(cache_file, encoding='utf-8') as f:
            data = json.load(f)
        profiles = [CharacterProfile(**d) for d in data]
        for p in profiles:
            log.info('  (캐시) %s (%s): %s', p.original, p.korean, p.desc[:80])
        return profiles

    candidates = [f'{article}/등장인물', article]
    page_text: str | None = None

    for title in candidates:
        result = _fetch_text(title)
        if result:
            page_text, page_url = result
            log.info('[나무위키] 등장인물 페이지 로드: %s (%d자)', page_url, len(page_text))
            print(f'[나무위키] 페이지 로드: {title} ({len(page_text)}자)')
            break

    if not page_text:
        log.error('[나무위키] \'%s\' 페이지 접근 실패', article)
        print(f'[경고] \'{article}\' 나무위키 페이지 접근 실패')
        return []

    if llm_client is None:
        log.warning('[나무위키] \'%s\' llm_client 없음 — 캐릭터 추출 불가', article)
        print(f'[경고] \'{article}\' llm_client 없음 — 캐릭터 추출 불가')
        return []

    characters = _llm_extract(llm_client, page_text, article, article)

    if not characters:
        log.warning('[나무위키] \'%s\' 프로필 추출 결과 없음', article)
        print(f'[경고] \'{article}\' LLM 캐릭터 추출 결과 없음')
    else:
        log.info('[나무위키] \'%s\' %d명 → 캐시 저장', article, len(characters))
        print(f'[나무위키] \'{article}\' {len(characters)}명 추출 → 캐시 저장')
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump([vars(c) for c in characters], f, ensure_ascii=False, indent=2)

    return characters
