from .history import HistoryManager
from .llm_client import LLMClient


def summarize(llm_client: LLMClient, history: HistoryManager, prior_summary: str | None) -> str:
    context = history.get_summary_context(has_prior_summary=prior_summary is not None)

    if prior_summary:
        system_content = (
            f'[이전 요약]\n{prior_summary}\n\n'
            '위 요약과 아래 추가된 번역 내용을 합쳐 전체 줄거리를 업데이트하여 요약해 주세요. '
            '중요한 인물 관계와 사건을 포함하고, 간결하게 작성하세요.'
        )
    else:
        system_content = (
            '아래 번역 내용을 바탕으로 지금까지의 중요한 줄거리와 인물 관계를 간결하게 요약해 주세요.'
        )

    messages = [{'role': 'system', 'content': system_content}] + context

    print('[요약] 줄거리 요약 생성 중...')
    result = llm_client.chat(messages, temperature=0.3)
    print(f'[요약] 완료:\n{result}\n')
    return result
