import json
import re
import time
from dataclasses import dataclass
from urllib.parse import quote_plus, unquote, urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

from .identifier import WorkCandidate


@dataclass
class VerifiedWork:
    work: str
    characters: list[str]
    namuwiki_article: str


def _extract_namuwiki_title(href: str) -> str | None:
    """DDG redirect href에서 namu.wiki 문서명 추출."""
    # //duckduckgo.com/l/?uddg=https%3A%2F%2Fnamu.wiki%2Fw%2F{title}&rut=...
    match = re.search(r'uddg=([^&]+)', href)
    if not match:
        return None
    decoded = unquote(match.group(1))
    if 'namu.wiki/w/' not in decoded:
        return None
    path = decoded.split('namu.wiki/w/', 1)[1]
    # ?from=... 파라미터 제거
    path = path.split('?')[0]
    title = unquote(path).replace('+', ' ')
    # 분류: 접두어 제외
    if title.startswith('분류:'):
        return None
    return title


def _duckduckgo_search(query: str, result_count: int) -> tuple[list[str], list[str]]:
    """DuckDuckGo Lite 검색. (snippets, namu_articles) 반환."""
    snippets: list[str] = []
    namu_articles: list[str] = []
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

        # 나무위키 링크 추출
        for a in soup.find_all('a', href=True):
            title = _extract_namuwiki_title(a['href'])
            if title and title not in namu_articles:
                namu_articles.append(title)

        # 스니펫 수집
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

    except Exception as e:
        print(f'[경고] DuckDuckGo 검색 실패 ({query}): {e}')
    return snippets[:result_count], namu_articles


def _playwright_google_search(query: str, headless: bool, result_count: int, debug_dir: str = '.cache') -> tuple[list[str], list[str]]:
    """Playwright 기반 Google 검색 fallback."""
    snippets: list[str] = []
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
                print(f'[디버그] 구글 결과 없음 → 스크린샷: {screenshot_path}')

            browser.close()
    except Exception as e:
        print(f'[경고] 구글 검색 실패 ({query}): {e}')
    return snippets[:result_count], []


def _search(query: str, engine: str, headless: bool, result_count: int, debug_dir: str) -> tuple[list[str], list[str]]:
    if engine == 'duckduckgo':
        return _duckduckgo_search(query, result_count)
    return _playwright_google_search(query, headless, result_count, debug_dir)


def _best_namuwiki_article(articles: list[str]) -> str | None:
    """등장인물 하위 문서를 우선 선택, 없으면 첫 번째 반환."""
    for a in articles:
        if '/등장인물' in a:
            # 등장인물 서브페이지 → 부모 문서명 반환 (fetch_characters가 /등장인물 시도)
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
    query = f'{candidate.work} 등장인물 나무위키'
    snippets, namu_articles = _search(query, engine, headless, result_count, debug_dir)

    if not snippets and not namu_articles:
        print(f'[전처리] \'{candidate.work}\' 검색 결과 없음 — 검증 실패')
        return None

    # DDG에서 나무위키 링크를 직접 찾은 경우 LLM 검증 생략
    if namu_articles:
        namuwiki_article = _best_namuwiki_article(namu_articles)
        print(f'[전처리] \'{candidate.work}\' 검색에서 나무위키 문서 발견 → {namuwiki_article}')
        return VerifiedWork(
            work=candidate.work,
            characters=candidate.characters,
            namuwiki_article=namuwiki_article,
        )

    # 스니펫만 있는 경우 LLM 검증
    snippets_text = '\n'.join(f'- {s}' for s in snippets)
    messages = [
        {
            'role': 'user',
            'content': (
                f'아래는 "{candidate.work}"에 대한 검색 결과입니다.\n'
                '검색 결과를 바탕으로 이 작품이 실존하는지, 그리고 나무위키 문서가 있는지 판단하세요.\n\n'
                f'검색 결과:\n{snippets_text}\n\n'
                '판단 기준:\n'
                '- 나무위키, 위키백과, 공식 사이트 등 신뢰할 수 있는 출처에서 작품이 확인되면 verified\n'
                '- 검색 결과가 없거나 무관하면 unverified\n\n'
                'JSON으로만 응답하세요:\n'
                '{"verified": true/false, "reason": "판단 근거", "namuwiki_article": "나무위키 한국어 문서명 (없으면 null)"}'
            ),
        }
    ]

    try:
        response = llm_client.chat(messages, temperature=0)
        start = response.find('{')
        end = response.rfind('}') + 1
        if start == -1 or end == 0:
            return None
        data = json.loads(response[start:end])

        if not data.get('verified', False):
            print(f'[전처리] \'{candidate.work}\' 검증 실패: {data.get("reason", "")}')
            return None

        namuwiki_article = data.get('namuwiki_article') or candidate.work
        print(f'[전처리] \'{candidate.work}\' 검증 성공 → 나무위키: {namuwiki_article}')
        return VerifiedWork(
            work=candidate.work,
            characters=candidate.characters,
            namuwiki_article=namuwiki_article,
        )
    except Exception as e:
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
    verified: list[VerifiedWork] = []
    for candidate in candidates:
        if candidate.confidence < 0.4:
            print(f'[전처리] \'{candidate.work}\' confidence {candidate.confidence:.2f} — 검색 스킵')
            continue
        result = _verify_single(llm_client, candidate, engine, headless, result_count, debug_dir)
        if result:
            verified.append(result)
    return verified
