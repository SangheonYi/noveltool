from . import logger
from .history import HistoryManager
from .llm_client import LLMClient

_FIRST_TEMPLATE = (
    '아래 번역 내용을 바탕으로 지금까지의 중요한 줄거리와 인물 관계를 간결하게 요약해 주세요.'
)
_ROLLING_TEMPLATE = (
    '[이전 요약]\n{prior_summary}\n\n'
    '위 요약과 아래 추가된 번역 내용을 합쳐 전체 줄거리를 업데이트하여 요약해 주세요. '
    '중요한 인물 관계와 사건을 포함하고, 간결하게 작성하세요.'
)

_template_logged = {'first': False, 'rolling': False}


def summarize(llm_client: LLMClient, history: HistoryManager, prior_summary: str | None) -> str:
    log = logger.get()
    context = history.get_summary_context(has_prior_summary=prior_summary is not None)

    if prior_summary:
        system_content = _ROLLING_TEMPLATE.format(prior_summary=prior_summary)
        kind = 'rolling'
        log.info('[요약] 롤링 요약 (이전 요약 + 새 history %d턴)', len(context) // 2)
        if not _template_logged['rolling']:
            log.info('[요약] 롤링 요약 프롬프트 템플릿:\n%s',
                     _ROLLING_TEMPLATE.replace('{prior_summary}', '... (이전 요약)'))
            _template_logged['rolling'] = True
    else:
        system_content = _FIRST_TEMPLATE
        kind = 'first'
        log.info('[요약] 첫 요약 (history %d턴)', len(context) // 2)
        if not _template_logged['first']:
            log.info('[요약] 첫 요약 프롬프트 템플릿:\n%s', _FIRST_TEMPLATE)
            _template_logged['first'] = True

    messages = [{'role': 'system', 'content': system_content}] + context

    print('[요약] 줄거리 요약 생성 중...')
    result = llm_client.chat(messages, temperature=0.3)
    log.info('[요약] %s 완료 (%d자)', kind, len(result))
    log.debug('[요약] 생성된 요약문:\n%s', result)
    print(f'[요약] 완료:\n{result}\n')
    return result
