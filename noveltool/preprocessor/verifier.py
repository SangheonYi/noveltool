import json
import time
from dataclasses import dataclass
from urllib.parse import quote_plus

from playwright.sync_api import sync_playwright

from .identifier import WorkCandidate


@dataclass
class VerifiedWork:
    work: str
    characters: list[str]
    namuwiki_article: str


def _google_search(query: str, headless: bool, result_count: int) -> list[str]:
    snippets: list[str] = []
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(
                user_agent=(
                    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                    'AppleWebKit/537.36 (KHTML, like Gecko) '
                    'Chrome/120.0.0.0 Safari/537.36'
                )
            )
            page = context.new_page()
            url = f'https://www.google.com/search?q={quote_plus(query)}&hl=ko'
            page.goto(url, wait_until='networkidle', timeout=15000)
            time.sleep(1)

            for selector in ('div.VwiC3b', 'div.IsZvec', 'span.aCOpRe', 'div[data-sncf]'):
                elements = page.query_selector_all(selector)
                for el in elements:
                    text = el.inner_text().strip()
                    if text and text not in snippets:
                        snippets.append(text)
                    if len(snippets) >= result_count:
                        break
                if len(snippets) >= result_count:
                    break

            browser.close()
    except Exception as e:
        print(f'[경고] 구글 검색 실패 ({query}): {e}')
    return snippets[:result_count]


def _verify_single(
    llm_client,
    candidate: WorkCandidate,
    headless: bool,
    result_count: int,
) -> 'VerifiedWork | None':
    probe_char = candidate.characters[0] if candidate.characters else candidate.work
    query = f'"{probe_char}" "{candidate.work}" 등장인물'
    snippets = _google_search(query, headless, result_count)

    if not snippets:
        print(f'[전처리] \'{candidate.work}\' 검색 결과 없음 — 검증 실패')
        return None

    snippets_text = '\n'.join(f'- {s}' for s in snippets)
    messages = [
        {
            'role': 'user',
            'content': (
                f'아래는 "{candidate.work}"과 캐릭터 "{probe_char}"에 대한 구글 검색 결과입니다.\n'
                '검색 결과를 바탕으로 해당 캐릭터가 실제로 그 작품에 등장하는지 판단하세요.\n\n'
                f'검색 결과:\n{snippets_text}\n\n'
                '판단 기준:\n'
                '- 나무위키, 위키백과, 공식 사이트 등 신뢰할 수 있는 출처에 해당 캐릭터가 언급되면 verified\n'
                '- 팬픽이나 비공식 자료만 있으면 unverified\n'
                '- 검색 결과가 없거나 무관하면 unverified\n\n'
                'JSON으로만 응답하세요:\n'
                '{"verified": true/false, "reason": "판단 근거", "namuwiki_article": "나무위키 문서명 (없으면 null)"}'
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
    headless: bool = True,
    result_count: int = 5,
) -> list[VerifiedWork]:
    verified: list[VerifiedWork] = []
    for candidate in candidates:
        if candidate.confidence < 0.4:
            print(f'[전처리] \'{candidate.work}\' confidence {candidate.confidence:.2f} — 검색 스킵')
            continue
        result = _verify_single(llm_client, candidate, headless, result_count)
        if result:
            verified.append(result)
    return verified
