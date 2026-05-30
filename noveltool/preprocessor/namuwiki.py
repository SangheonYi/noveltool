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

_SEED_FIND_TEMPLATE = (
    '"{work}" 나무위키 등장인물 문서입니다.\n\n'
    '나무위키 텍스트:\n{text}\n\n'
    '위 텍스트에서 아래 인물의 나무위키 한국어 이름을 찾으세요.\n'
    '원어 이름: {original}{reading_hint}\n\n'
    '찾으면 한국어 이름만 출력하세요. 찾을 수 없으면 "없음"이라고만 출력하세요.'
)

_LIST_TEMPLATE = (
    '"{work}" 나무위키 등장인물 문서입니다.\n\n'
    '이미 확인된 인물 (출력 제외): {known_korean}\n\n'
    '나무위키 텍스트:\n{text}\n\n'
    '위 텍스트에 언급된 나머지 등장인물 이름을 모두 추출하세요.\n'
    '- "original": 나무위키에 원어 표기가 명시된 경우만 사용, 없으면 한국어 이름 그대로\n'
    '- "korean": 나무위키에서 사용하는 한국어 이름\n\n'
    '형식: [{{"original": "원어이름", "korean": "한국어이름"}}, ...]\n\n'
    'JSON 배열로만 응답:'
)

_PROFILE_TEMPLATE = (
    '작품: {work}\n'
    '캐릭터: {korean} (원어: {original})\n\n'
    '나무위키 텍스트:\n{text}\n\n'
    '위 텍스트에서 이 캐릭터의 프로필을 추출하세요.\n\n'
    '형식: {{"original": "{original}", "korean": "{korean}", '
    '"desc": "성별·나이·역할·성격·주요 능력/특징 등 핵심 정보 2~3문장"}}\n\n'
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


_PLAYWRIGHT_THRESHOLD = 500   # requests 응답이 이 이하면 Playwright로 재시도
_MIN_PAGE_CONTENT = 2000      # 이 이하면 404/오류 페이지로 판단


def _soup_to_text(html: str) -> str:
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'meta', 'link']):
        tag.decompose()
    return soup.get_text(separator='\n', strip=True)


def _fetch_text_requests(url: str) -> str | None:
    try:
        resp = requests.get(
            url,
            headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept-Language': 'ko-KR,ko;q=0.9',
            },
            timeout=10,
        )
        if resp.status_code != 200:
            return None
        return _soup_to_text(resp.text)
    except Exception:
        return None


def _fetch_text_playwright(url: str) -> str | None:
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = browser.new_context(
                user_agent=(
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
                ),
                locale='ko-KR',
            )
            page = ctx.new_page()
            page.goto(url, wait_until='networkidle', timeout=30000)
            html = page.content()
            browser.close()
        return _soup_to_text(html)
    except Exception as e:
        logger.get().error('[나무위키] Playwright 실패 (%s): %s', url, e)
        return None


def _fetch_text(title: str) -> tuple[str, str] | None:
    """(page_text, url) 반환. requests 응답이 너무 짧으면 Playwright로 폴백."""
    url = f'{NAMUWIKI_BASE}/w/{quote(title)}'
    text = _fetch_text_requests(url)
    if not text or len(text) < _PLAYWRIGHT_THRESHOLD:
        logger.get().info('[나무위키] requests 응답 부족 (%d자) → Playwright: %s',
                          len(text) if text else 0, title)
        text = _fetch_text_playwright(url)
    if not text or len(text) < _MIN_PAGE_CONTENT:
        logger.get().warning('[나무위키] 페이지 없음 또는 너무 짧음 (%d자): %s',
                             len(text) if text else 0, title)
        return None
    return text, url


def _parse_name_list(response: str) -> list[dict]:
    start = response.find('[')
    end = response.rfind(']') + 1
    if start == -1 or end == 0:
        return []
    try:
        data = json.loads(response[start:end])
        return [item for item in data if isinstance(item, dict)]
    except Exception:
        return []


def _llm_list_names(
    llm_client,
    page_text: str,
    work: str,
    seed_names: list[str] | None = None,
    chunk_size: int = 5000,
) -> list[dict]:
    """등장인물 문서에서 이름 목록 추출.

    seed_names가 있으면 두 단계로 처리:
      1단계: seed_names → 나무위키 한국어 이름 매핑 (원어 추측 없음)
      2단계: 나머지 인물 전체 추출 (seed에 없는 캐릭터용)
    """
    log = logger.get()
    all_names: dict[str, dict] = {}  # korean → {original, korean}

    # 전체 텍스트를 chunk_size 단위로 분할 (overlap 500자)
    chunks: list[str] = []
    step = chunk_size - 500
    for i in range(0, max(1, len(page_text)), step):
        chunk = page_text[i:i + chunk_size]
        if chunk.strip():
            chunks.append(chunk)
        if not chunk:
            break

    # 1단계: seed_names 원어 이름 → 나무위키 한국어 이름 (1명씩 병렬 호출)
    if seed_names:
        context = page_text[:chunk_size]

        def find_korean(seed: dict[str, str]) -> tuple[str, str] | None:
            original = seed['name']
            reading = seed.get('reading', '')
            reading_hint = f' (읽는 법: {reading})' if reading else ''
            prompt = _SEED_FIND_TEMPLATE.format(
                work=work, text=context, original=original, reading_hint=reading_hint
            )
            try:
                korean = llm_client.chat(
                    [{'role': 'user', 'content': prompt}], temperature=0
                ).strip()
                if not korean or korean == '없음':
                    return None
                return original, korean
            except Exception as e:
                log.error('[나무위키] seed 매핑 실패 (%s): %s', original, e)
                return None

        with ThreadPoolExecutor(max_workers=min(len(seed_names), 8)) as pool:
            futures = {pool.submit(find_korean, s): s['name'] for s in seed_names}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    orig, kor = result
                    # 같은 korean 이름에 여러 seed가 매핑될 때 longest original 유지
                    # 단, 히라가나 별명이 붙은 형태(もっくん 등)보다 한자 표기 우선
                    def _score(name: str) -> tuple[int, int]:
                        kanji_count = sum(1 for c in name if '一' <= c <= '鿿' or '゠' <= c <= 'ヿ')
                        return (kanji_count, len(name.replace(' ', '')))

                    if kor not in all_names or _score(orig) > _score(all_names[kor]['original']):
                        all_names[kor] = {'original': orig, 'korean': kor}

        log.info('[나무위키] seed 매핑 완료: %d명 / %d개 seed', len(all_names), len(seed_names))

    # 2단계: 나머지 인물 추출 (seed 이미 처리된 korean 제외)
    known_korean = ', '.join(all_names.keys()) if all_names else '없음'
    for idx, chunk in enumerate(chunks):
        prompt = _LIST_TEMPLATE.format(work=work, text=chunk, known_korean=known_korean)
        try:
            response = llm_client.chat([{'role': 'user', 'content': prompt}], temperature=0)
            for item in _parse_name_list(response):
                orig = (item.get('original') or '').strip()
                kor = (item.get('korean') or '').strip()
                if orig and kor and kor not in all_names:
                    all_names[kor] = {'original': orig, 'korean': kor}
        except Exception as e:
            log.error('[나무위키] \'%s\' 청크 %d 추출 실패: %s', work, idx + 1, e)

    names = list(all_names.values())
    log.info('[나무위키] \'%s\' 이름 목록 %d명 (청크 %d개): %s',
             work, len(names), len(chunks), [n['korean'] for n in names])
    return names


def _section_for_char(article_text: str, name: str, all_names: list[str], max_chars: int = 3000) -> str:
    """캐릭터 이름 등장 위치부터 다음 알려진 이름 등장 직전까지 추출.
    인접 캐릭터 설명이 섞이는 문제 방지."""
    idx = article_text.find(name)
    if idx == -1:
        return ''
    search_from = idx + len(name) + 1
    end = min(idx + max_chars, len(article_text))
    for other in all_names:
        if other == name:
            continue
        pos = article_text.find(other, search_from)
        if 0 < pos < end:
            end = pos
    return article_text[idx:end]


def _fetch_character_page(korean: str, article: str) -> tuple[str, str] | None:
    """개별 캐릭터 페이지 fetch (requests 전용 — Playwright 없음).
    대량 캐릭터 처리 시 Playwright 브라우저 시작 비용을 피하기 위해 requests만 사용.
    실패 시 메인 문서 텍스트 구간 추출(_section_for_char)으로 폴백.
    """
    work_keyword = article.split('/')[0].split()[0]

    for title in [f'{article}/등장인물/{korean}', korean]:
        url = f'{NAMUWIKI_BASE}/w/{quote(title)}'
        text = _fetch_text_requests(url)
        if text and len(text) >= _MIN_PAGE_CONTENT:
            if title == korean and work_keyword not in text:
                continue  # 타 작품 동명 페이지 방지
            return text, url

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


def _llm_extract(
    llm_client,
    article_text: str,
    article: str,
    work: str,
    seed_names: list[str] | None = None,
) -> list[CharacterProfile]:
    """3단계 파이프라인: 이름목록 → 개별 페이지 fetch → 상세 프로필 (병렬)."""
    log = logger.get()

    # Step 1: 이름 목록 (전체 텍스트 청크 처리 + seed 힌트)
    log.info('[나무위키] Step 1: 이름목록 추출 (seed %d개)', len(seed_names) if seed_names else 0)
    names = _llm_list_names(llm_client, article_text, work, seed_names=seed_names)
    if not names:
        return []

    # Step 2+3: 개별 페이지 fetch + 프로필 추출 (병렬)
    log.info('[나무위키] Step 2+3: %d명 개별 페이지 fetch 및 프로필 추출 (병렬)', len(names))
    log.info('[나무위키] 프로필 프롬프트 템플릿:\n%s',
             _PROFILE_TEMPLATE.replace('{work}', work)
             .replace('{original}', '<원어이름>').replace('{korean}', '<한국어이름>')
             .replace('{text}', '... (캐릭터 페이지 텍스트)'))

    all_known = [n['korean'] for n in names] + [n['original'] for n in names]

    def fetch_and_profile(name_dict: dict) -> CharacterProfile | None:
        original = name_dict['original']
        korean = name_dict['korean']
        result = _fetch_character_page(korean, article)
        if result:
            page_text, page_url = result
            log.info('[나무위키] %s 개별 페이지 로드: %s (%d자)', korean, page_url, len(page_text))
        else:
            # 개별 페이지 없음 → 메인 문서에서 해당 인물 구간만 추출
            log.info('[나무위키] %s 개별 페이지 없음 → 메인 문서 구간 사용', korean)
            section = _section_for_char(article_text, korean, all_known)
            if not section:
                section = _section_for_char(article_text, original, all_known)
            if not section:
                log.warning('[나무위키] %s 메인 문서에서도 이름 미발견 — 스킵', korean)
                return None
            page_text = section
            page_url = f'{NAMUWIKI_BASE}/w/{quote(article)}'
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


def _cached_works(cache_dir: str) -> dict[str, str]:
    """캐시 디렉토리에 있는 작품 목록. {work_name: cache_file_path}"""
    result: dict[str, str] = {}
    if not os.path.isdir(cache_dir):
        return result
    for fname in os.listdir(cache_dir):
        if not fname.endswith('_characters.json'):
            continue
        fpath = os.path.join(cache_dir, fname)
        try:
            with open(fpath, encoding='utf-8') as f:
                data = json.load(f)
            if data:
                work = data[0].get('work', '')
                if work:
                    result[work] = fpath
        except Exception:
            pass
    return result


def _llm_match_cache(llm_client, article: str, cached_works: dict[str, str]) -> str | None:
    """LLM에게 article 이름과 캐시 작품 목록을 주고 일치하는 작품을 고르게 한다."""
    if not cached_works:
        return None
    work_list = '\n'.join(f'- {w}' for w in cached_works)
    prompt = (
        f'현재 번역 중인 작품의 나무위키 문서명: "{article}"\n\n'
        f'아래는 캐시에 저장된 작품 목록입니다:\n{work_list}\n\n'
        f'위 캐시 목록 중 현재 작품과 동일한 작품이 있으면 정확히 그 이름만 출력하세요.\n'
        f'없으면 "없음"이라고만 출력하세요.'
    )
    try:
        response = llm_client.chat(
            [{'role': 'user', 'content': prompt}], temperature=0
        ).strip()
        if response in cached_works:
            return response
    except Exception:
        pass
    return None


def fetch_characters(
    article: str,
    cache_dir: str,
    llm_client=None,
    seed_names: list[dict[str, str]] | None = None,
    force: bool = False,
) -> list[CharacterProfile]:
    log = logger.get()
    cache_file = _cache_path(cache_dir, article)

    # 1. 직접 캐시 hit (force=True면 무시)
    if not force and os.path.exists(cache_file):
        log.info('[나무위키] \'%s\' 캐시 로드: %s', article, cache_file)
        print(f'[나무위키] \'{article}\' 캐시 로드: {cache_file}')
        with open(cache_file, encoding='utf-8') as f:
            data = json.load(f)
        profiles = [CharacterProfile(**d) for d in data]
        for p in profiles:
            log.info('  (캐시) %s (%s): %s', p.original, p.korean, p.desc[:80])
        return profiles

    # 2. 캐시 miss → 다른 캐시 작품 중 LLM으로 매칭 시도 (force=True면 건너뜀)
    if not force and llm_client is not None:
        cached = _cached_works(cache_dir)
        if cached:
            matched = _llm_match_cache(llm_client, article, cached)
            if matched:
                matched_file = cached[matched]
                log.info('[나무위키] \'%s\' 캐시 miss → LLM 매칭: \'%s\'', article, matched)
                print(f'[나무위키] \'{article}\' 캐시 miss → 캐시 매칭: \'{matched}\'')
                with open(matched_file, encoding='utf-8') as f:
                    data = json.load(f)
                profiles = [CharacterProfile(**d) for d in data]
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

    characters = _llm_extract(llm_client, page_text, article, article, seed_names=seed_names)

    if not characters:
        log.warning('[나무위키] \'%s\' 프로필 추출 결과 없음', article)
        print(f'[경고] \'{article}\' LLM 캐릭터 추출 결과 없음')
    else:
        log.info('[나무위키] \'%s\' %d명 → 캐시 저장', article, len(characters))
        print(f'[나무위키] \'{article}\' {len(characters)}명 추출 → 캐시 저장')
        with open(cache_file, 'w', encoding='utf-8') as f:
            json.dump([vars(c) for c in characters], f, ensure_ascii=False, indent=2)

    return characters


def filter_by_raw_chars(
    profiles: list[CharacterProfile],
    raw_chars: set[str],
) -> list[CharacterProfile]:
    """현재 텍스트에서 추출된 이름(raw_chars)에 해당하는 프로필만 반환.
    raw_chars가 비어있으면 전체 반환. 동일 korean 이름은 original이 가장 긴 항목만 유지."""
    if not raw_chars:
        return profiles

    matched: list[CharacterProfile] = []
    for profile in profiles:
        for name in raw_chars:
            n = name.strip()
            if not n:
                continue
            if (
                n == profile.original
                or n == profile.korean
                or n in profile.original
                or profile.original in n
                or n in profile.korean
                or profile.korean in n
            ):
                matched.append(profile)
                break

    # 1차: 같은 korean 이름 중복 → 공백 제거 기준 original이 가장 긴 항목(full name) 우선
    deduped: dict[str, CharacterProfile] = {}
    for p in matched:
        key = p.korean
        if key not in deduped or len(p.original.replace(' ', '')) > len(deduped[key].original.replace(' ', '')):
            deduped[key] = p

    # 2차: fragment 제거 — original·korean 모두 다른 entry의 substring이면 부분명으로 판단
    entries = list(deduped.values())
    originals = [e.original.replace(' ', '') for e in entries]
    koreans = [e.korean for e in entries]
    keep = []
    for i, p in enumerate(entries):
        o = p.original.replace(' ', '')
        k = p.korean
        is_fragment = any(
            j != i
            and o != originals[j]          # 완전히 같은 건 이미 1차에서 처리
            and len(o) < len(originals[j])  # 더 짧은 쪽이 fragment 후보
            and o in originals[j]           # original이 다른 것의 부분 문자열
            and (k in koreans[j] or koreans[j] in k)  # korean도 부분 관계
            for j in range(len(entries))
        )
        if not is_fragment:
            keep.append(p)

    return keep
# | 원문            | 독음            | 한국어 표기    |
# | ------------- | ------------- | --------- |
# | アルディギア国王      | あるでぃぎあこくおう    | 알디기아 국왕   |
# | ルクレティア・ベルトーリ  | るくれてぃあ・べるとーり  | 루크레티아 벨토리 |
# | アラン・ベルトーリ     | あらん・べるとーり     | 아란 벨토리    |
# | エリオット・スタンフォード | えりおっと・すたんふぉーど | 엘리엇 스탠포드  |
# | 원문      | 독음         | 한국어 표기    |
# | ------- | ---------- | --------- |
# | 戦王領域    | せんおうりょういき  | 전왕영역      |
# | 六刃      | ろくじん       | 육인        |
# | 葉瀬夏音監視官 | はせかのんかんしかん | 카논 감시관    |
# | 三聖      | さんせい       | 삼성        |
# | 暁帝人     | あかつき ていじん  | 아카츠키 테이지ン |
# | 원문    | 독음       | 한국어 표기  |
# | ----- | -------- | ------- |
# | 国家攻魔官 | こっかこうまかん | 국가공마관   |
# | 狼牙教官  | ろうがきょうかん | 로가 교관   |
# | 斎木・阿澄 | さいき・あずみ  | 사이키 아즈미 |
# | 御門騎士団 | みかどきしだん  | 미카도 기사단 |
# | 원문    | 독음        | 한국어 표기 |
# | ----- | --------- | ------ |
# | 獄界    | ごっかい      | 옥계     |
# | 観察者   | かんさつしゃ    | 관찰자    |
# | 獄界管理者 | ごっかいかんりしゃ | 옥계 관리자 |
# | 원문            | 독음      | 한국어 표기 |
# | ------------- | ------- | ------ |
# | ケノン           | けのん     | 케논     |
# | No.12（ディセンバー） | でぃせんばー  | 디셈버    |
# | No.IX-4       | ないん・ふぉー | IX-4   |
# | イレブン          | いれぶん    | 일레븐    |
# | 원문           | 독음           | 한국어 표기     |
# | ------------ | ------------ | ---------- |
# | ザナ・ラシュカ      | ざな・らしゅか      | 자나 라슈카     |
# | デイモス・カルソス    | でいもす・かるそす    | 데이모스 칼소스   |
# | バルタザール・ザハリアス | ばるたざーる・ざはりあす | 발타자르 자하리아스 |
# | ルートヴィヒ・ヴェルター | るーとゔぃひ・ゔぇるたー | 루트비히 벨터    |
# | グレンダ・ラルサ     | ぐれんだ・らるさ     | 글렌다 라르사    |
# | 원문     | 독음         | 한국어 표기 |
# | ------ | ---------- | ------ |
# | カインの使徒 | かいんのしと     | 카인의 사도 |
# | 聖殲派    | せいせんは      | 성절파    |
# | 賢者評議会  | けんじゃひょうぎかい | 현자회의   |
# | 원문    | 독음       | 한국어 표기   |
# | ----- | -------- | -------- |
# | 牧瀬文香  | まきせ ふみか  | 마키세 후미카  |
# | 緒方唯千夏 | おがた いちか  | 오가타 이치카  |
# | 白坂沙織  | しらさか さおり | 시라사카 사오리 |
# | 橘佳奈   | たちばな かな  | 타치바나 카나  |
# | 黒崎遥   | くろさき はるか | 쿠로사키 하루카 |


# [
#   {
#     "name_ja": "暁古城",
#     "reading": "あかつき こじょう",
#     "ko": "아카츠키 코죠",
#     "desc": "세계 최강의 흡혈귀인 제4진조의 힘을 계승한 고등학생이다. 12체의 권수를 다루며 이토가미 섬의 수많은 사건의 중심에 선다."
#   },
#   {
#     "name_ja": "姫柊雪菜",
#     "reading": "ひめらぎ ゆきな",
#     "ko": "히메라기 유키나",
#     "desc": "사자왕기관 소속 검무로 제4진조 감시 임무를 맡아 코죠에게 파견된 소녀이다."
#   },
#   {
#     "name_ja": "藍羽浅葱",
#     "reading": "あいば あさぎ",
#     "ko": "아이바 아사기",
#     "desc": "전자 여제로 불리는 천재 해커이며 이토가미 섬의 전산 시스템과 깊게 연결된 인물이다."
#   },
#   {
#     "name_ja": "暁凪沙",
#     "reading": "あかつき なぎさ",
#     "ko": "아카츠키 나기사",
#     "desc": "코죠의 여동생으로 강한 영매 체질과 아브로라와 관련된 비밀을 지닌 소녀이다."
#   },
# ]
