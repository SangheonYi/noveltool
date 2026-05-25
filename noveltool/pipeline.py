import os

from .config import Config
from .history import HistoryManager
from .llm_client import LLMClient
from .preprocessor.extractor import extract_characters
from .preprocessor.identifier import identify_works
from .preprocessor.namuwiki import CharacterProfile, fetch_characters
from .preprocessor.verifier import verify_works
from .prompt import build_system_prompt, update_summary
from .summarizer import summarize


def run(config: Config, preprocess_only: bool = False, no_cache: bool = False) -> None:
    with open(config.input, encoding='utf-8') as f:
        lines = [line.rstrip('\n') for line in f]

    total = len(lines)
    print(f'[파이프라인] 입력: {config.input} ({total}줄)')

    print('\n[Phase 1] 전처리 시작')
    characters = _preprocess(config, lines, no_cache)

    if preprocess_only:
        print(f'\n[--preprocess-only] 검증된 캐릭터 수: {len(characters)}')
        for c in characters:
            print(f'  {c.original} ({c.korean}) [{c.work}]: {c.desc[:50]}')
        return

    print('\n[Phase 2] System Prompt 빌드')
    system_prompt = build_system_prompt(config, characters)

    print('\n[Phase 3] 번역 시작')
    _translate(config, lines, system_prompt, total)


def _preprocess(config: Config, lines: list[str], no_cache: bool) -> list[CharacterProfile]:
    if no_cache:
        _clear_cache(config.preprocessing.cache_dir)

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
        headless=config.search.headless,
        result_count=config.search.result_count,
    )
    if not verified:
        print('[전처리] 검증된 원작 없음 — 캐릭터 없이 진행')
        return []

    all_characters: list[CharacterProfile] = []
    for work in verified:
        chars = fetch_characters(work.namuwiki_article, config.preprocessing.cache_dir)
        all_characters.extend(chars)

    return all_characters


def _clear_cache(cache_dir: str) -> None:
    if not os.path.isdir(cache_dir):
        return
    for fname in os.listdir(cache_dir):
        if fname.endswith('_characters.json'):
            os.remove(os.path.join(cache_dir, fname))
    print(f'[캐시] {cache_dir} 초기화 완료')


def _translate(config: Config, lines: list[str], system_prompt: str, total: int) -> None:
    llm_client = LLMClient(config)
    history = HistoryManager(config.translation.history_window, config.translation.summary_overlap)
    prior_summary: str | None = None

    out_dir = os.path.dirname(os.path.abspath(config.output))
    os.makedirs(out_dir, exist_ok=True)

    with open(config.output, 'w', encoding='utf-8') as out:
        for i, line in enumerate(lines, 1):
            if not line.strip():
                out.write('\n')
                out.flush()
                continue

            messages = [{'role': 'system', 'content': system_prompt}]
            messages.extend(history.to_messages())
            messages.append({'role': 'user', 'content': line})

            try:
                translated = llm_client.chat(messages)
            except Exception as e:
                print(f'[경고] {i}번 줄 번역 실패: {e} — 원문 유지')
                translated = line

            history.add_turn(line, translated)
            out.write(translated + '\n')
            out.flush()

            src_preview = line[:30] + ('...' if len(line) > 30 else '')
            tgt_preview = translated[:30] + ('...' if len(translated) > 30 else '')
            print(f'[번역] {i}/{total}: {src_preview} → {tgt_preview}')

            if history.should_summarize():
                new_summary = summarize(llm_client, history, prior_summary)
                prior_summary = new_summary
                system_prompt = update_summary(system_prompt, new_summary)
                history.trim_to_overlap()
