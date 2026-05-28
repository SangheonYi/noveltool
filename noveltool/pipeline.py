import json
import os

from . import logger
from .config import Config
from .history import HistoryManager
from .llm_client import LLMClient
from .preprocessor.extractor import extract_characters
from .preprocessor.identifier import identify_works
from .preprocessor.namuwiki import CharacterProfile, fetch_characters
from .preprocessor.verifier import verify_works
from .prompt import build_system_prompt, update_summary
from .summarizer import summarize


def _state_path(output: str) -> str:
    return output + '.state.json'


def _save_state(state_path: str, done_lines: int, prior_summary: str | None,
                system_prompt: str, history: HistoryManager) -> None:
    data = {
        'done_lines': done_lines,
        'prior_summary': prior_summary,
        'system_prompt': system_prompt,
        'history_pairs': history._pairs,
    }
    tmp = state_path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, state_path)


def _load_state(state_path: str) -> dict | None:
    if not os.path.exists(state_path):
        return None
    try:
        with open(state_path, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def run(config: Config, preprocess_only: bool = False, no_cache: bool = False) -> None:
    log = logger.get()
    with open(config.input, encoding='utf-8') as f:
        lines = [line.rstrip('\n') for line in f]

    if config.translation.max_lines:
        lines = lines[:config.translation.max_lines]
        print(f'[파이프라인] 입력: {config.input} (max_lines {config.translation.max_lines} 적용 → {len(lines)}줄 처리)')
    else:
        print(f'[파이프라인] 입력: {config.input} ({len(lines)}줄)')
    log.info('[파이프라인] 입력: %s (%d줄)', config.input, len(lines))

    print('\n[Phase 1] 전처리 시작')
    log.info('=== Phase 1: 전처리 ===')
    characters = _preprocess(config, lines, no_cache)

    if preprocess_only:
        print(f'\n[--preprocess-only] 검증된 캐릭터 수: {len(characters)}')
        for c in characters:
            print(f'  {c.original} ({c.korean}) [{c.work}]: {c.desc[:50]}')
        return

    print('\n[Phase 2] System Prompt 빌드')
    log.info('=== Phase 2: System Prompt 빌드 ===')
    system_prompt = build_system_prompt(config, characters)
    log.info('[Phase 2] System Prompt (%d자):\n%s', len(system_prompt), system_prompt)

    print('\n[Phase 3] 번역 시작')
    log.info('=== Phase 3: 번역 시작 (%d줄) ===', len(lines))
    _translate(config, lines, system_prompt, len(lines))


def _preprocess(config: Config, lines: list[str], no_cache: bool) -> list[CharacterProfile]:
    log = logger.get()
    cache_dir = config.preprocessing.cache_dir

    if no_cache:
        _clear_cache(cache_dir)

    llm_client = LLMClient(config)

    raw_chars = extract_characters(llm_client, lines, config.preprocessing.chunk_tokens, config.llm.model)
    if not raw_chars:
        log.warning('[전처리] 추출된 캐릭터 없음')
        print('[전처리] 추출된 캐릭터 없음 — 캐릭터 없이 진행')
        return []

    candidates = identify_works(llm_client, raw_chars)
    if not candidates:
        log.warning('[전처리] 원작 추론 결과 없음')
        print('[전처리] 원작 추론 결과 없음 — 캐릭터 없이 진행')
        return []

    verified = verify_works(
        llm_client,
        candidates,
        engine=config.search.engine,
        headless=config.search.headless,
        result_count=config.search.result_count,
        debug_dir=cache_dir,
    )
    if not verified:
        log.warning('[전처리] 검증된 원작 없음')
        print('[전처리] 검증된 원작 없음 — 캐릭터 없이 진행')
        return []

    all_characters: list[CharacterProfile] = []
    for work in verified:
        chars = fetch_characters(work.namuwiki_article, cache_dir, llm_client)
        all_characters.extend(chars)

    log.info('[전처리] 완료: 총 %d명', len(all_characters))
    return all_characters


def _clear_cache(cache_dir: str) -> None:
    if not os.path.isdir(cache_dir):
        return
    for fname in os.listdir(cache_dir):
        if fname.endswith('_characters.json'):
            os.remove(os.path.join(cache_dir, fname))
    print(f'[캐시] {cache_dir} 초기화 완료')


def _translate(config: Config, lines: list[str], system_prompt: str, total: int) -> None:
    log = logger.get()
    llm_client = LLMClient(config)
    history = HistoryManager(config.translation.history_window, config.translation.summary_overlap)
    prior_summary: str | None = None
    step = config.log_translation_step

    out_dir = os.path.dirname(os.path.abspath(config.output))
    os.makedirs(out_dir, exist_ok=True)

    # Resume from saved state (preserves history/summary context)
    done_lines = 0
    sp = _state_path(config.output)
    state = _load_state(sp)
    if state:
        done_lines = state['done_lines']
        if done_lines >= total:
            print(f'[번역] 이미 완료된 파일 ({done_lines}줄) — 건너뜀')
            return
        prior_summary = state.get('prior_summary')
        system_prompt = state.get('system_prompt', system_prompt)
        history._pairs = [tuple(p) for p in state.get('history_pairs', [])]
        print(f'[이어쓰기] state 복원: {done_lines}줄 완료, history {len(history._pairs)}쌍, 요약문 {"있음" if prior_summary else "없음"}')
        log.info('[이어쓰기] state 복원: %d줄 완료, history %d쌍', done_lines, len(history._pairs))
        lines = lines[done_lines:]
    elif os.path.exists(config.output):
        # 출력 파일만 있고 state가 없는 경우: 줄 수만 맞춤 (history 없이 이어쓰기)
        with open(config.output, 'r', encoding='utf-8') as f:
            done_lines = sum(1 for _ in f)
        if done_lines >= total:
            print(f'[번역] 이미 완료된 파일 ({done_lines}줄) — 건너뜀')
            return
        if done_lines > 0:
            print(f'[이어쓰기] 출력 {done_lines}줄 감지 (state 없음, history 미복원) → {done_lines + 1}번째 줄부터 재개')
            log.info('[이어쓰기] 출력 %d줄 감지 (state 없음) → %d번째 줄', done_lines, done_lines + 1)
            lines = lines[done_lines:]

    log.info('[번역] 프롬프트 템플릿 (시스템 프롬프트):\n%s', system_prompt)
    log.info('[번역] INFO 로그 주기: %d줄마다 (오류는 항상 기록)', step)

    success_count = 0
    error_count = 0
    write_mode = 'a' if done_lines > 0 else 'w'

    with open(config.output, write_mode, encoding='utf-8') as out:
        for i, line in enumerate(lines, done_lines + 1):
            if not line.strip():
                out.write('\n')
                out.flush()
                _save_state(sp, i, prior_summary, system_prompt, history)
                continue

            messages = [{'role': 'system', 'content': system_prompt}]
            messages.extend(history.to_messages())
            messages.append({'role': 'user', 'content': line})

            try:
                translated = llm_client.chat(messages)
                if i % step == 0 or i == total:
                    log.info('[번역] %d/%d: OK (번역 성공 %d, 빈줄 제외)', i, total, success_count + 1)
                log.debug('[번역] %d/%d 입력 (앞 100자): %s', i, total, line[:100])
                log.debug('[번역] %d/%d 응답: %s', i, total, translated)
                success_count += 1
            except Exception as e:
                log.error(
                    '[번역] %d/%d 실패: %s\n  원문: %s\n  메시지:\n%s',
                    i, total, e, line,
                    json.dumps(messages, ensure_ascii=False, indent=2),
                )
                log.info('[번역] %d/%d: FAIL — %s', i, total, e)
                print(f'[경고] {i}번 줄 번역 실패: {e} — 원문 유지')
                translated = line
                error_count += 1

            history.add_turn(line, translated)
            out.write(translated + '\n')
            out.flush()
            _save_state(sp, i, prior_summary, system_prompt, history)

            src_preview = line[:30] + ('...' if len(line) > 30 else '')
            tgt_preview = translated[:30] + ('...' if len(translated) > 30 else '')
            print(f'[번역] {i}/{total}: {src_preview} → {tgt_preview}')

            if history.should_summarize():
                new_summary = summarize(llm_client, history, prior_summary)
                prior_summary = new_summary
                system_prompt = update_summary(system_prompt, new_summary)
                log.info('[번역] 시스템 프롬프트 업데이트 (요약 반영):\n%s', system_prompt)
                history.trim_to_overlap()

    # 완료 시 state 파일 삭제
    if os.path.exists(sp):
        os.remove(sp)

    blank_count = len(lines) - success_count - error_count
    log.info('[Phase 3] 번역 완료: 번역 %d / 빈줄 %d / 오류 %d / 전체 %d', success_count, blank_count, error_count, total)
    print(f'\n[번역 완료] 번역: {success_count}, 빈줄: {blank_count}, 오류: {error_count}, 전체: {total}')
