import json
import re
import time
from dataclasses import dataclass
from urllib.parse import quote_plus, unquote

import requests
from bs4 import BeautifulSoup

from .. import logger
from .identifier import WorkCandidate


@dataclass
class VerifiedWork:
    work: str
    characters: list[str]
    namuwiki_article: str


_NAMUWIKI_SKIP_PREFIXES = ('틀:', '분류:', '템플릿:', '파일:', '분류:파일/')
_NAMUWIKI_SKIP_SUFFIXES = ('채널', '갤러리', '마이너 갤러리')


def _namuwiki_search(work_name: str, result_count: int) -> tuple[list[str], list[str]]:
    """나무위키 직접 검색. 스니펫 없이 문서 목록만 반환."""
    log = logger.get()
    articles: list[str] = []
    query = f'{work_name} 등장인물'
    log.debug('[검색] 나무위키 쿼리: %s', query)
    try:
        url = f'https://namu.wiki/Search?q={quote_plus(query)}'
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'Accept-Language': 'ko-KR,ko;q=0.9',
        }
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, 'html.parser')
        for a in soup.find_all('a', href=True):
            href = a['href']
            if not href.startswith('/w/'):
                continue
            title = unquote(href[3:]).replace('+', ' ')
            if any(title.startswith(p) for p in _NAMUWIKI_SKIP_PREFIXES):
                continue
            if any(title.endswith(s) for s in _NAMUWIKI_SKIP_SUFFIXES):
                continue
            if title not in articles:
                articles.append(title)
        log.info('[검색] 나무위키 결과: 문서 %d개', len(articles))
        if articles:
            log.debug('[검색] 나무위키 문서 목록:\n%s', '\n'.join(f'  - {a}' for a in articles))
    except Exception as e:
        log.error('[검색] 나무위키 검색 실패 (%s): %s', query, e)
        print(f'[경고] 나무위키 검색 실패 ({query}): {e}')
    return [], articles[:result_count * 3]  # LLM 판단용으로 넉넉하게


def _extract_namuwiki_title(href: str) -> str | None:
    """DDG redirect href에서 namu.wiki 문서명 추출."""
    match = re.search(r'uddg=([^&]+)', href)
    if not match:
        return None
    decoded = unquote(match.group(1))
    if 'namu.wiki/w/' not in decoded:
        return None
    path = decoded.split('namu.wiki/w/', 1)[1].split('?')[0]
    title = unquote(path).replace('+', ' ')
    if title.startswith('분류:'):
        return None
    return title


def _duckduckgo_search(query: str, result_count: int) -> tuple[list[str], list[str]]:
    """DuckDuckGo Lite 검색. (snippets, namu_articles) 반환."""
    log = logger.get()
    snippets: list[str] = []
    namu_articles: list[str] = []
    log.debug('[검색] DuckDuckGo 쿼리: %s', query)
    try:
        url = f'https://lite.duckduckgo.com/lite/?q={quote_plus(query)}&kl=kr-kr'
        headers = {
            'User-Agent': (
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) '
                'Chrome/124.0.0.0 Safari/537.36'
            ),
            'Accept-Language': 'ko-KR,ko;q=0.9',
        }
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, 'html.parser')

        for a in soup.find_all('a', href=True):
            title = _extract_namuwiki_title(a['href'])
            if title and title not in namu_articles:
                namu_articles.append(title)

        for td in soup.select('td.result-snippet'):
            text = td.get_text(strip=True)
            if text and text not in snippets:
                snippets.append(text)
            if len(snippets) >= result_count:
                break

        if not snippets:
            for td in soup.select('tr > td:nth-child(2)'):
                text = td.get_text(' ', strip=True)
                if len(text) > 30 and text not in snippets:
                    snippets.append(text)
                if len(snippets) >= result_count:
                    break

        log.info('[검색] DDG 결과: 스니펫 %d개, 나무위키 링크 %d개', len(snippets), len(namu_articles))
        if snippets:
            log.debug('[검색] 스니펫:\n%s', '\n'.join(f'  - {s}' for s in snippets))
        if namu_articles:
            log.debug('[검색] 나무위키 링크:\n%s', '\n'.join(f'  - {a}' for a in namu_articles))

    except Exception as e:
        log.error('[검색] DuckDuckGo 실패 (%s): %s', query, e)
        print(f'[경고] DuckDuckGo 검색 실패 ({query}): {e}')
    return snippets[:result_count], namu_articles


def _playwright_google_search(query: str, headless: bool, result_count: int, debug_dir: str = '.cache') -> tuple[list[str], list[str]]:
    """Playwright 기반 Google 검색 fallback."""
    log = logger.get()
    snippets: list[str] = []
    log.debug('[검색] Google(Playwright) 쿼리: %s', query)
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(
                user_agent=(
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/124.0.0.0 Safari/537.36'
                ),
                locale='ko-KR',
                extra_http_headers={'Accept-Language': 'ko-KR,ko;q=0.9'},
            )
            page = context.new_page()
            url = f'https://www.google.com/search?q={quote_plus(query)}&hl=ko&gl=KR'
            page.goto(url, wait_until='domcontentloaded', timeout=20000)
            time.sleep(2)

            for selector in ('div.VwiC3b', 'div.IsZvec', 'span.aCOpRe', 'div[data-sncf]', 'div.kb0PBd'):
                elements = page.query_selector_all(selector)
                for el in elements:
                    text = el.inner_text().strip()
                    if text and text not in snippets:
                        snippets.append(text)
                    if len(snippets) >= result_count:
                        break
                if len(snippets) >= result_count:
                    break

            if not snippets:
                import os
                os.makedirs(debug_dir, exist_ok=True)
                screenshot_path = os.path.join(debug_dir, 'google_debug.png')
                page.screenshot(path=screenshot_path, full_page=False)
                log.warning('[검색] Google 결과 없음 (봇 차단 의심) → 스크린샷: %s', screenshot_path)
                print(f'[디버그] 구글 결과 없음 → 스크린샷: {screenshot_path}')
            else:
                log.info('[검색] Google 결과: 스니펫 %d개', len(snippets))
                log.debug('[검색] 스니펫:\n%s', '\n'.join(f'  - {s}' for s in snippets))

            browser.close()
    except Exception as e:
        log.error('[검색] Google(Playwright) 실패 (%s): %s', query, e)
        print(f'[경고] 구글 검색 실패 ({query}): {e}')
    return snippets[:result_count], []


def _search(work_name: str, engine: str, headless: bool, result_count: int, debug_dir: str) -> tuple[list[str], list[str]]:
    if engine == 'namuwiki':
        return _namuwiki_search(work_name, result_count)
    if engine == 'duckduckgo':
        query = f'{work_name} 등장인물 나무위키'
        return _duckduckgo_search(query, result_count)
    query = f'{work_name} 등장인물 나무위키'
    return _playwright_google_search(query, headless, result_count, debug_dir)


def _best_namuwiki_article(articles: list[str]) -> str | None:
    """등장인물 하위 문서를 우선 선택, 없으면 첫 번째 반환."""
    for a in articles:
        if '/등장인물' in a:
            return a.split('/등장인물')[0]
    return articles[0] if articles else None


def _verify_single(
    llm_client,
    candidate: WorkCandidate,
    engine: str,
    headless: bool,
    result_count: int,
    debug_dir: str = '.cache',
) -> 'VerifiedWork | None':
    log = logger.get()
    # 괄호 안 영문 부제 제거 ("붕괴: 스타레일 (Honkai: Star Rail)" → "붕괴: 스타레일")
    work_name = re.sub(r'\s*\(.*?\)', '', candidate.work).strip()
    snippets, namu_articles = _search(work_name, engine, headless, result_count, debug_dir)

    if not snippets and not namu_articles:
        log.warning('[검증] \'%s\' 검색 결과 없음 — 검증 실패', candidate.work)
        print(f'[전처리] \'{candidate.work}\' 검색 결과 없음 — 검증 실패')
        return None

    # LLM에게 스니펫 + 나무위키 링크 목록을 함께 넘겨 정확한 문서명 판단
    snippets_text = '\n'.join(f'- {s}' for s in snippets) if snippets else '(없음)'
    namu_hint = (
        '\n\n검색에서 발견된 나무위키 문서 목록 (이 중 해당 작품 문서가 있으면 선택):\n'
        + '\n'.join(f'- {a}' for a in namu_articles)
        if namu_articles else ''
    )
    log.debug('[검증] 발견된 나무위키 문서 목록: %s', namu_articles)
    messages = [
        {
            'role': 'user',
            'content': (
                f'아래는 "{candidate.work}"에 대한 검색 결과입니다.\n'
                '검색 결과를 바탕으로 이 작품이 실존하는지, 그리고 나무위키 문서가 있는지 판단하세요.\n\n'
                f'검색 결과:\n{snippets_text}'
                f'{namu_hint}\n\n'
                '판단 기준:\n'
                '- 나무위키, 위키백과, 공식 사이트 등 신뢰할 수 있는 출처에서 작품이 확인되면 verified\n'
                '- 검색 결과가 없거나 무관하면 unverified\n'
                '- 나무위키 문서 목록이 있어도 해당 작품과 무관한 문서는 선택하지 마세요\n\n'
                'JSON으로만 응답하세요:\n'
                '{"verified": true/false, "reason": "판단 근거", "namuwiki_article": "나무위키 한국어 문서명 (없으면 null)"}'
            ),
        }
    ]
    log.debug('[검증] LLM 검증 프롬프트:\n%s', messages[0]['content'])

    try:
        response = llm_client.chat(messages, temperature=0)
        log.debug('[검증] LLM 응답:\n%s', response)
        start = response.find('{')
        end = response.rfind('}') + 1
        if start == -1 or end == 0:
            log.warning('[검증] \'%s\' LLM 응답 JSON 파싱 실패', candidate.work)
            return None
        data = json.loads(response[start:end])

        if not data.get('verified', False):
            log.info('[검증] \'%s\' 검증 실패: %s', candidate.work, data.get('reason', ''))
            print(f'[전처리] \'{candidate.work}\' 검증 실패: {data.get("reason", "")}')
            return None

        namuwiki_article = data.get('namuwiki_article') or candidate.work
        log.info('[검증] \'%s\' 검증 성공 → 나무위키: %s', candidate.work, namuwiki_article)
        print(f'[전처리] \'{candidate.work}\' 검증 성공 → 나무위키: {namuwiki_article}')
        return VerifiedWork(
            work=candidate.work,
            characters=candidate.characters,
            namuwiki_article=namuwiki_article,
        )
    except Exception as e:
        log.error('[검증] \'%s\' LLM 검증 실패: %s', candidate.work, e)
        print(f'[경고] \'{candidate.work}\' LLM 검증 실패: {e}')
        return None


def verify_works(
    llm_client,
    candidates: list[WorkCandidate],
    engine: str = 'duckduckgo',
    headless: bool = True,
    result_count: int = 5,
    debug_dir: str = '.cache',
) -> list[VerifiedWork]:
    log = logger.get()
    log.info('[검증] %d개 후보 검증 시작 (엔진: %s)', len(candidates), engine)
    verified: list[VerifiedWork] = []
    for candidate in candidates:
        if candidate.confidence < 0.4:
            log.info('[검증] \'%s\' confidence %.2f → 검색 스킵', candidate.work, candidate.confidence)
            print(f'[전처리] \'{candidate.work}\' confidence {candidate.confidence:.2f} — 검색 스킵')
            continue
        result = _verify_single(llm_client, candidate, engine, headless, result_count, debug_dir)
        if result:
            verified.append(result)
    log.info('[검증] 완료: %d/%d 검증 성공', len(verified), len(candidates))
    return verified
