import os

from .config import Config
from .llm_client import LLMClient
from .preprocessor.extractor import extract_characters
from .preprocessor.identifier import identify_works
from .preprocessor.namuwiki import CharacterProfile, fetch_characters
from .preprocessor.verifier import verify_works
from .prompt import build_recovery_system_prompt


def run(
    config: Config,
    before_file: str,
    after_file: str,
    missing_lines: int,
    summary: str | None = None,
    output: str | None = None,
    no_cache: bool = False,
) -> None:
    before_lines = _read_lines(before_file)
    after_lines = _read_lines(after_file)

    # Slice to configured window sizes
    before_ctx = before_lines[-config.recovery.before_lines:]
    after_ctx = after_lines[:config.recovery.after_lines]

    print(f'[복구] before: {len(before_ctx)}줄 / after: {len(after_ctx)}줄 / 복구 대상: {missing_lines}줄')

    if no_cache:
        _clear_cache(config.preprocessing.cache_dir)

    print('\n[Phase 1] 전처리 — 캐릭터/세계관 식별')
    characters = _preprocess(config, before_lines)

    print('\n[Phase 2] System Prompt 빌드')
    system_prompt = build_recovery_system_prompt(
        config=config,
        characters=characters,
        missing_lines=missing_lines,
        after_context=after_ctx,
        summary=summary,
    )

    out_path = output or 'recovered.txt'
    print(f'\n[Phase 3] 복구 시작 → {out_path}')
    _recover(config, system_prompt, before_ctx, missing_lines, out_path)


def _read_lines(path: str) -> list[str]:
    with open(path, encoding='utf-8') as f:
        return [line.rstrip('\n') for line in f]


def _preprocess(config: Config, lines: list[str]) -> list[CharacterProfile]:
    llm_client = LLMClient(config)

    raw_chars = extract_characters(llm_client, lines, config.preprocessing.chunk_tokens, config.llm.model)
    if not raw_chars:
        print('[전처리] 추출된 캐릭터 없음 — 캐릭터 없이 진행')
        return []

    candidates = identify_works(llm_client, raw_chars)
    if not candidates:
        print('[전처리] 원작 추론 결과 없음 — 캐릭터 없이 진행')
        return []

    verified = verify_works(
        llm_client,
        candidates,
        engine=config.search.engine,
        headless=config.search.headless,
        result_count=config.search.result_count,
        debug_dir=config.preprocessing.cache_dir,
    )
    if not verified:
        print('[전처리] 검증된 원작 없음 — 캐릭터 없이 진행')
        return []

    all_characters: list[CharacterProfile] = []
    for work in verified:
        chars = fetch_characters(work.namuwiki_article, config.preprocessing.cache_dir, llm_client)
        all_characters.extend(chars)

    return all_characters


def _recover(
    config: Config,
    system_prompt: str,
    before_ctx: list[str],
    missing_lines: int,
    out_path: str,
) -> None:
    llm_client = LLMClient(config)

    # Seed history with before context
    messages: list[dict] = [{'role': 'system', 'content': system_prompt}]
    for line in before_ctx:
        if not line.strip():
            continue
        messages.append({'role': 'user', 'content': '계속'})
        messages.append({'role': 'assistant', 'content': line})

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)

    with open(out_path, 'w', encoding='utf-8') as out:
        for i in range(missing_lines):
            messages.append({'role': 'user', 'content': '계속'})

            try:
                recovered = llm_client.chat(messages)
            except Exception as e:
                print(f'[경고] {i + 1}번 줄 복구 실패: {e} — 빈 줄 출력')
                recovered = ''

            messages.append({'role': 'assistant', 'content': recovered})
            out.write(recovered + '\n')
            out.flush()

            preview = recovered[:40] + ('...' if len(recovered) > 40 else '')
            print(f'[복구] {i + 1}/{missing_lines}: {preview}')


def _clear_cache(cache_dir: str) -> None:
    if not os.path.isdir(cache_dir):
        return
    for fname in os.listdir(cache_dir):
        if fname.endswith('_characters.json'):
            os.remove(os.path.join(cache_dir, fname))
    print(f'[캐시] {cache_dir} 초기화 완료')
