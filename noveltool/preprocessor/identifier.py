import json
from dataclasses import dataclass

from .. import logger

_PROMPT_TEMPLATE = (
    '아래 등장인물 이름들을 보고, 이들이 등장하는 원작 작품(만화, 소설, 게임, 영화, 드라마 등)을 추론해 주세요.\n'
    '크로스오버/패러디일 경우 여러 작품을 포함하세요.\n'
    '확신하기 어려운 인물은 포함하지 말고 확실한 작품만 응답하세요.\n'
    'JSON으로만 응답하세요.\n\n'
    '등장인물: {characters}\n\n'
    '응답 형식:\n'
    '[\n'
    '  {{\n'
    '    "work": "작품명",\n'
    '    "characters": ["해당 작품 소속으로 추정되는 인물 이름들"],\n'
    '    "confidence": 0.0~1.0,\n'
    '    "reason": "추론 근거"\n'
    '  }}\n'
    ']'
)

# 실제 작품이 아닌 catch-all 분류를 나타내는 패턴 (소문자 매칭)
_CATCH_ALL_PATTERNS = {
    'unknown', 'other', 'miscellaneous', 'misc',
    '기타', '단역', '미상', '불명', '엑스트라',
}


def _is_catch_all(work: str) -> bool:
    w = work.lower()
    return any(p in w for p in _CATCH_ALL_PATTERNS)


@dataclass
class WorkCandidate:
    work: str
    characters: list[str]
    confidence: float
    reason: str


def identify_works(llm_client, characters: set[str]) -> list[WorkCandidate]:
    log = logger.get()
    if not characters:
        return []

    character_list = ', '.join(sorted(characters))
    messages = [{'role': 'user', 'content': _PROMPT_TEMPLATE.format(characters=character_list)}]
    log.info('[세계관] 프롬프트 템플릿:\n%s', _PROMPT_TEMPLATE.replace('{characters}', '... (캐릭터 목록)'))
    log.debug('[세계관] 입력 캐릭터 목록: %s', character_list)

    try:
        response = llm_client.chat(messages, temperature=0)
        log.debug('[세계관] LLM 응답:\n%s', response)
        start = response.find('[')
        end = response.rfind(']') + 1
        if start == -1 or end == 0:
            log.warning('[세계관] JSON 파싱 실패 (응답에 배열 없음)')
            return []
        data = json.loads(response[start:end])

        candidates = []
        skipped = []
        for item in data:
            work = item.get('work', '')
            if not work:
                continue
            if _is_catch_all(work):
                skipped.append(work)
                continue
            candidates.append(WorkCandidate(
                work=work,
                characters=item.get('characters', []),
                confidence=float(item.get('confidence', 0.0)),
                reason=item.get('reason', ''),
            ))

        if skipped:
            log.info('[세계관] catch-all 후보 필터링: %s', skipped)

        log.info('[세계관] 원작 후보 %d개 (필터 후):', len(candidates))
        for c in candidates:
            log.info('  - %s (confidence=%.2f): %s', c.work, c.confidence, c.reason)
            log.info('    캐릭터: %s', c.characters)

        print(f'[전처리] 원작 후보: {[c.work for c in candidates]}')
        return candidates
    except Exception as e:
        log.error('[세계관] 추론 실패: %s', e)
        print(f'[경고] 세계관 추론 실패: {e}')
        return []
