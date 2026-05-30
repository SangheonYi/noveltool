"""
번역 검수 (Review) 모듈

N줄 원문-번역 쌍을 LLM에 보내 인물명·어조·존댓말 일관성을 검사하고,
오번역 발견 시 피드백과 함께 재번역을 요청한다.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import logger
from .llm_client import LLMClient

_REVIEW_INSTRUCTION = """
[번역 검수]
위에 명시된 번역 규칙 및 등장인물 프로필을 기준으로 아래 원문-번역 쌍을 검수하세요.

확인 항목:
1. 등장인물 이름 — [이름 표기] 규칙과 일치하는지, 동일 인물이 다르게 표기되지 않았는지
2. 인물 말투·어조 — 캐릭터 성격과 일치하는지 (반말/경어, 특유의 말버릇 등)
3. 존댓말/반말 — 인물 관계·상황에 맞게 일관적으로 사용되는지
4. 오역·누락 — 원문 내용이 빠지거나 의미가 왜곡되지 않았는지

판정:
- 문제 없으면 정확히 "OK" 한 단어만 출력
- 문제 있으면 각 문제를 번호로 나열하고, 해당 번역문과 올바른 수정 제안을 포함
"""

_RETRANSLATE_PREFIX = """
[재번역 요청]
이전 번역에서 다음 문제가 발견되었습니다. 반드시 수정하여 재번역하세요:

{feedback}

위 문제를 반영해 아래 원문을 다시 번역하세요. 출력 형식은 기존과 동일하게 번역 결과 한 줄만 출력합니다.
"""


@dataclass
class ReviewResult:
    ok: bool
    feedback: str = ''   # 재번역 시 system prompt에 삽입할 피드백


def review_batch(
    llm_client: LLMClient,
    pairs: list[tuple[str, str]],  # (원문, 번역) 쌍 (빈 줄 제외)
    system_prompt: str,            # 번역에 쓰던 system prompt (캐릭터 프로필 + 요약 포함)
) -> ReviewResult:
    """N줄 원문-번역 쌍을 LLM에 보내 검수한다."""
    log = logger.get()
    if not pairs:
        return ReviewResult(ok=True)

    pair_text = '\n'.join(
        f'[원문] {src}\n[번역] {tgt}'
        for src, tgt in pairs
    )
    review_system = system_prompt + '\n\n' + _REVIEW_INSTRUCTION
    messages = [
        {'role': 'system', 'content': review_system},
        {'role': 'user',   'content': pair_text},
    ]

    try:
        response = llm_client.chat(messages).strip()
    except Exception as e:
        log.warning('[검수] LLM 호출 실패: %s — 검수 건너뜀', e)
        return ReviewResult(ok=True)  # 검수 실패 시 번역 계속 진행

    ok = response.upper() == 'OK'
    log.info('[검수] 결과: %s', 'OK' if ok else f'문제 발견\n{response}')
    return ReviewResult(ok=ok, feedback=response if not ok else '')


def build_retranslate_prompt(system_prompt: str, feedback: str) -> str:
    """재번역용 system prompt — 원래 프롬프트에 피드백 지시를 앞에 주입."""
    prefix = _RETRANSLATE_PREFIX.format(feedback=feedback)
    return prefix + '\n\n' + system_prompt
