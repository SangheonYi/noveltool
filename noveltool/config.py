import os
import re
import sys
import yaml
import requests
from dataclasses import dataclass


def _expand_env(value):
    if isinstance(value, str):
        return re.sub(r'\$\{(\w+)\}', lambda m: os.environ.get(m.group(1), m.group(0)), value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


@dataclass
class LLMConfig:
    base_url: str
    api_key: str
    model: str
    temperature: float
    max_completion_tokens: int


@dataclass
class TranslationConfig:
    history_window: int
    summary_overlap: float
    source_language: str
    target_language: str
    max_lines: int | None


@dataclass
class PreprocessingConfig:
    chunk_tokens: int
    cache_dir: str


@dataclass
class SearchConfig:
    engine: str
    headless: bool
    result_count: int


@dataclass
class RecoveryConfig:
    before_lines: int
    after_lines: int


@dataclass
class SystemPromptConfig:
    base: str
    extra_rules: list[str]


@dataclass
class Config:
    llm: LLMConfig
    translation: TranslationConfig
    recovery: RecoveryConfig
    preprocessing: PreprocessingConfig
    search: SearchConfig
    system_prompt: SystemPromptConfig
    input: str
    output: str
    log_dir: str
    log_level: str
    log_translation_step: int


def _resolve_log_dir(log_dir: str | None, output: str) -> str:
    if log_dir:
        return log_dir
    if output:
        return os.path.join(os.path.dirname(os.path.abspath(output)), 'logs')
    return 'logs'


def _fetch_first_model(base_url: str, api_key: str) -> str:
    url = base_url.rstrip('/') + '/models'
    try:
        resp = requests.get(
            url,
            headers={'Authorization': f'Bearer {api_key}'},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        model = data['data'][0]['id']
        print(f'[설정] llm.model 미설정 → API에서 첫 번째 모델 사용: {model}')
        return model
    except Exception as e:
        print(f'오류: llm.model 이 설정되지 않았고 모델 목록 조회도 실패했습니다: {e}', file=sys.stderr)
        sys.exit(1)


def load_config(path: str) -> Config:
    try:
        with open(path, encoding='utf-8') as f:
            raw = yaml.safe_load(f)
    except FileNotFoundError:
        print(f"오류: 설정 파일을 찾을 수 없습니다: {path}", file=sys.stderr)
        sys.exit(1)

    raw = _expand_env(raw)

    llm_raw = raw.get('llm', {})
    for key in ('base_url', 'api_key'):
        if not llm_raw.get(key):
            print(f"오류: llm.{key} 가 설정 파일에 없습니다.", file=sys.stderr)
            sys.exit(1)

    model = llm_raw.get('model') or _fetch_first_model(llm_raw['base_url'], llm_raw['api_key'])

    translation_raw = raw.get('translation', {})
    recovery_raw = raw.get('recovery', {})
    preprocessing_raw = raw.get('preprocessing', {})
    search_raw = raw.get('search', {})
    system_prompt_raw = raw.get('system_prompt', {})

    return Config(
        llm=LLMConfig(
            base_url=llm_raw['base_url'],
            api_key=llm_raw['api_key'],
            model=model,
            temperature=float(llm_raw.get('temperature', 0.3)),
            max_completion_tokens=int(llm_raw.get('max_completion_tokens', 4096)),
        ),
        translation=TranslationConfig(
            history_window=int(translation_raw.get('history_window', 20)),
            summary_overlap=float(translation_raw.get('summary_overlap', 0.5)),
            source_language=translation_raw.get('source_language', 'auto'),
            target_language=translation_raw.get('target_language', 'ko'),
            max_lines=int(translation_raw['max_lines']) if translation_raw.get('max_lines') else None,
        ),
        recovery=RecoveryConfig(
            before_lines=int(recovery_raw.get('before_lines', 20)),
            after_lines=int(recovery_raw.get('after_lines', 10)),
        ),
        preprocessing=PreprocessingConfig(
            chunk_tokens=int(preprocessing_raw.get('chunk_tokens', 6000)),
            cache_dir=preprocessing_raw.get('cache_dir', '.cache'),
        ),
        search=SearchConfig(
            engine=search_raw.get('engine', 'playwright'),
            headless=bool(search_raw.get('headless', True)),
            result_count=int(search_raw.get('result_count', 5)),
        ),
        system_prompt=SystemPromptConfig(
            base=system_prompt_raw.get('base', ''),
            extra_rules=system_prompt_raw.get('extra_rules', []),
        ),
        input=raw.get('input', ''),
        output=raw.get('output', ''),
        log_dir=_resolve_log_dir(raw.get('log_dir'), raw.get('output', '')),
        log_level=str(raw.get('log_level', 'INFO')).upper(),
        log_translation_step=int(raw.get('log_translation_step', 100)),
    )
