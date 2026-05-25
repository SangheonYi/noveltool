import json
from dataclasses import dataclass


@dataclass
class WorkCandidate:
    work: str
    characters: list[str]
    confidence: float
    reason: str


def identify_works(llm_client, characters: set[str]) -> list[WorkCandidate]:
    if not characters:
        return []

    character_list = ', '.join(sorted(characters))
    messages = [
        {
            'role': 'user',
            'content': (
                '아래 등장인물 이름들을 보고, 이들이 등장하는 원작 작품(만화, 소설, 게임, 영화, 드라마 등)을 추론해 주세요.\n'
                '크로스오버/패러디일 경우 여러 작품을 포함하세요.\n'
                '확신하기 어려운 경우 후보를 포함하되 confidence를 낮게 표시하세요.\n'
                'JSON으로만 응답하세요.\n\n'
                f'등장인물: {character_list}\n\n'
                '응답 형식:\n'
                '[\n'
                '  {\n'
                '    "work": "작품명",\n'
                '    "characters": ["해당 작품 소속으로 추정되는 인물 이름들"],\n'
                '    "confidence": 0.0~1.0,\n'
                '    "reason": "추론 근거"\n'
                '  }\n'
                ']'
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
        candidates = [
            WorkCandidate(
                work=item.get('work', ''),
                characters=item.get('characters', []),
                confidence=float(item.get('confidence', 0.0)),
                reason=item.get('reason', ''),
            )
            for item in data
            if item.get('work')
        ]
        print(f'[전처리] 원작 후보: {[c.work for c in candidates]}')
        return candidates
    except Exception as e:
        print(f'[경고] 세계관 추론 실패: {e}')
        return []
