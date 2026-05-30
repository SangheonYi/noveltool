import json
import os
from concurrent.futures import Future, ThreadPoolExecutor

from . import logger
from .config import Config
from .history import HistoryManager
from .llm_client import LLMClient
from .preprocessor.extractor import extract_characters
from .preprocessor.identifier import identify_works
from .preprocessor.namuwiki import CharacterProfile, fetch_characters, filter_by_raw_chars
from .preprocessor.verifier import verify_works
from .prompt import build_system_prompt, update_summary
from .reviewer import ReviewResult, build_retranslate_prompt, review_batch
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
        all_lines = [line.rstrip('\n') for line in f]

    # 전처리(원작 인식)는 항상 전문 사용 — max_lines는 번역 범위만 제한
    translate_lines = all_lines[:config.translation.max_lines] if config.translation.max_lines else all_lines

    if config.translation.max_lines:
        print(f'[파이프라인] 입력: {config.input} ({len(all_lines)}줄 전문 전처리, 번역 {len(translate_lines)}줄)')
    else:
        print(f'[파이프라인] 입력: {config.input} ({len(all_lines)}줄)')
    log.info('[파이프라인] 입력: %s (%d줄)', config.input, len(all_lines))

    print('\n[Phase 1] 전처리 시작')
    log.info('=== Phase 1: 전처리 ===')
    characters = _preprocess(config, all_lines, no_cache)

    if preprocess_only:
        print(f'\n[--preprocess-only] 검증된 캐릭터 수: {len(characters)}')
        for c in characters:
            print(f'  {c.original} ({c.korean}) [{c.work}]: {c.desc[:50]}')
        return

    print('\n[Phase 2] System Prompt 빌드')
    log.info('=== Phase 2: System Prompt 빌드 ===')
    system_prompt = build_system_prompt(config, characters)
    log.info('[Phase 2] System Prompt (%d자):\n%s', len(system_prompt), system_prompt)

    # 캐시 재구축(--no-cache) 시 새 프로필과 state의 프로필 비교
    # 동일하면 이어쓰기 유지, 달라졌으면 처음부터 재번역
    if no_cache:
        sp_path = _state_path(config.output)
        old_state = _load_state(sp_path)
        if old_state and old_state.get('system_prompt') == system_prompt:
            print('[이어쓰기] 캐시 재구축 후 프로필 동일 — 이어쓰기 유지')
            log.info('[이어쓰기] 캐시 재구축 후 프로필 동일 — 이어쓰기 유지')
        else:
            reason = '프로필 변경' if old_state else '기존 번역 없음'
            for path in (config.output, sp_path):
                if os.path.exists(path):
                    os.remove(path)
                    print(f'[리셋] {reason} → 번역 초기화: {path}')
            log.info('[리셋] %s → 번역 초기화', reason)

    print('\n[Phase 3] 번역 시작')
    log.info('=== Phase 3: 번역 시작 (%d줄) ===', len(translate_lines))
    _translate(config, translate_lines, system_prompt, len(translate_lines))


def _input_preprocess_cache_path(cache_dir: str, input_path: str, total_lines: int) -> str:
    """입력 파일 기준 전처리 결과 캐시 경로.
    input 파일 경로 + 줄 수를 키로 사용 — 파일이 바뀌면 자동 무효화."""
    import hashlib
    key = hashlib.md5(f'{os.path.abspath(input_path)}:{total_lines}'.encode()).hexdigest()[:12]
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, f'_preprocess_{key}.json')


def _identify_from_cache(raw_chars: dict[str, str], cache_dir: str) -> str | None:
    """기존 캐릭터 캐시와 raw_chars를 대조해 원작을 추론.
    캐시 파일 중 raw_chars와 가장 많이 겹치는 작품을 반환.
    매칭 수가 MIN_HITS 미만이면 None 반환 → 웹 검색 폴백."""
    import glob
    MIN_HITS = 3

    best_work: str | None = None
    best_count = 0

    for fpath in glob.glob(os.path.join(cache_dir, '*_characters.json')):
        fname = os.path.basename(fpath)
        if fname.startswith('_'):
            continue
        try:
            with open(fpath, encoding='utf-8') as f:
                profiles = json.load(f)
        except Exception:
            continue
        if not profiles:
            continue

        count = 0
        for p in profiles:
            orig = p.get('original', '')
            kor = p.get('korean', '')
            for name in raw_chars.keys():
                n = name.strip()
                if not n:
                    continue
                if (n == orig or n == kor
                        or (n in orig and len(n) > 1)
                        or (orig in n and len(orig) > 1)
                        or (n in kor and len(n) > 1)
                        or (kor in n and len(kor) > 1)):
                    count += 1
                    break

        if count > best_count:
            best_count = count
            best_work = profiles[0].get('work') or fname.replace('_characters.json', '')

    if best_count >= MIN_HITS:
        return best_work
    return None


def _original_lang_seeds(raw_chars: dict[str, str]) -> list[dict[str, str]]:
    """raw_chars 중 한국어(한글)가 아닌 이름만 {name, reading} 형태로 반환.
    나무위키 seed로 한국어 표기를 넘기면 동음이자 캐릭터에 잘못 매핑되는 문제 방지."""
    return [
        {'name': n, 'reading': r}
        for n, r in raw_chars.items()
        if not any('가' <= c <= '힣' for c in n)
    ]


def _preprocess(config: Config, lines: list[str], no_cache: bool) -> list[CharacterProfile]:
    log = logger.get()
    cache_dir = config.preprocessing.cache_dir

    if no_cache:
        _clear_cache(cache_dir)

    llm_client = LLMClient(config)

    # 입력 파일 기준 전처리 결과 캐시 (extract → identify → verify 전체 결과)
    pp_cache = _input_preprocess_cache_path(cache_dir, config.input, len(lines))
    if not no_cache and os.path.exists(pp_cache):
        with open(pp_cache, encoding='utf-8') as f:
            pp_data = json.load(f)
        # backward compat: 이전 포맷은 article 목록만 저장
        if isinstance(pp_data, list):
            articles: list[str] = pp_data
            raw_chars: dict[str, str] = {}
        else:
            articles = pp_data['articles']
            # raw_chars: 신규 포맷 [{name, reading}] 또는 구형 [str]
            raw_chars_raw = pp_data.get('raw_chars', [])
            if raw_chars_raw and isinstance(raw_chars_raw[0], dict):
                raw_chars = {item['name']: item.get('reading', '') for item in raw_chars_raw}
            else:
                raw_chars = {n: '' for n in raw_chars_raw}
        if config.work:
            articles = [config.work]
        print(f'[전처리] 전처리 캐시 hit → 원작 {len(articles)}개: {articles}')
        log.info('[전처리] 전처리 캐시 hit: %s', articles)
        all_characters: list[CharacterProfile] = []
        for article in articles:
            chars = fetch_characters(article, cache_dir, llm_client, seed_names=_original_lang_seeds(raw_chars))
            all_characters.extend(chars)
        filtered = filter_by_raw_chars(all_characters, set(raw_chars.keys()))
        log.info('[전처리] 캐시 hit 완료: 전체 %d명 → 현재 텍스트 등장 %d명', len(all_characters), len(filtered))
        if len(all_characters) != len(filtered):
            print(f'[전처리] 캐릭터 필터링: 전체 {len(all_characters)}명 → 현재 텍스트 등장 {len(filtered)}명')
        return filtered

    raw_chars: dict[str, str] = extract_characters(llm_client, lines, config.preprocessing.chunk_tokens, config.llm.model)
    if not raw_chars:
        log.warning('[전처리] 추출된 캐릭터 없음')
        print('[전처리] 추출된 캐릭터 없음 — 캐릭터 없이 진행')
        return []

    if config.work:
        # 원작 직접 지정: identify/verify 건너뜀
        article_names = [config.work]
        print(f'[전처리] 원작 직접 지정: {config.work}')
        log.info('[전처리] 원작 직접 지정: %s', config.work)
    else:
        # 1순위: 기존 캐릭터 캐시와 직접 대조
        cached_work = _identify_from_cache(raw_chars, cache_dir)  # type: ignore[arg-type]
        if cached_work:
            article_names = [cached_work]
            print(f'[전처리] 캐시 대조로 원작 식별: {cached_work}')
            log.info('[전처리] 캐시 대조 원작 식별: %s', cached_work)
        else:
            # 2순위: 나무위키 웹 검색 + LLM 검증
            candidates = identify_works(llm_client, set(raw_chars.keys()))
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

            article_names = [w.namuwiki_article for w in verified]
    raw_chars_serializable = [{'name': n, 'reading': r} for n, r in raw_chars.items()]
    with open(pp_cache, 'w', encoding='utf-8') as f:
        json.dump({'articles': article_names, 'raw_chars': raw_chars_serializable}, f, ensure_ascii=False)
    log.info('[전처리] 전처리 결과 캐시 저장: %s', article_names)

    all_characters = []
    for article in article_names:
        chars = fetch_characters(article, cache_dir, llm_client, seed_names=_original_lang_seeds(raw_chars))
        all_characters.extend(chars)

    filtered = filter_by_raw_chars(all_characters, set(raw_chars.keys()))
    log.info('[전처리] 완료: 전체 %d명 → 현재 텍스트 등장 %d명', len(all_characters), len(filtered))
    print(f'[전처리] 완료: 전체 {len(all_characters)}명 → 현재 텍스트 등장 {len(filtered)}명')
    return filtered


def _clear_cache(cache_dir: str) -> None:
    if not os.path.isdir(cache_dir):
        return
    for fname in os.listdir(cache_dir):
        if fname.endswith('_characters.json') or fname.startswith('_preprocess_'):
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

    write_mode = 'a' if done_lines > 0 else 'w'
    out = open(config.output, write_mode, encoding='utf-8')
    success_count = error_count = 0

    if config.review.enabled:
        _translate_loop_with_review(
            config, lines, system_prompt, prior_summary, history,
            llm_client, total, done_lines, step, sp, out,
        )
    else:
        _translate_loop(
            config, lines, system_prompt, prior_summary, history,
            llm_client, total, done_lines, step, sp, out,
        )

    out.close()

    # 완료 시 state 파일 삭제
    if os.path.exists(sp):
        os.remove(sp)

    log.info('[Phase 3] 번역 완료')
    print(f'\n[번역 완료] 전체: {total}')


# ── 기본 번역 루프 (검수 없음) ────────────────────────────────────────────
def _translate_loop(
    config: Config,
    lines: list[str],
    system_prompt: str,
    prior_summary: str | None,
    history: HistoryManager,
    llm_client: LLMClient,
    total: int,
    done_lines: int,
    step: int,
    sp: str,
    out,
) -> None:
    log = logger.get()
    success_count = error_count = 0

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
                log.info('[번역] %d/%d: OK', i, total)
            log.debug('[번역] %d/%d 입력: %s', i, total, line[:100])
            log.debug('[번역] %d/%d 응답: %s', i, total, translated)
            success_count += 1
        except Exception as e:
            log.error('[번역] %d/%d 실패: %s', i, total, e)
            print(f'[경고] {i}번 줄 번역 실패: {e} — 원문 유지')
            translated = line
            error_count += 1

        history.add_turn(line, translated)
        out.write(translated + '\n')
        out.flush()
        _save_state(sp, i, prior_summary, system_prompt, history)

        src_p = line[:30] + ('...' if len(line) > 30 else '')
        tgt_p = translated[:30] + ('...' if len(translated) > 30 else '')
        print(f'[번역] {i}/{total}: {src_p} → {tgt_p}')

        if history.should_summarize():
            new_summary = summarize(llm_client, history, prior_summary)
            prior_summary = new_summary
            system_prompt = update_summary(system_prompt, new_summary)
            history.trim_to_overlap()


# ── 검수 병렬 번역 루프 ───────────────────────────────────────────────────
def _translate_loop_with_review(
    config: Config,
    lines: list[str],
    system_prompt: str,
    prior_summary: str | None,
    history: HistoryManager,
    llm_client: LLMClient,
    total: int,
    done_lines: int,
    step: int,
    sp: str,
    out,
) -> None:
    """
    검수 병렬 번역 루프.

    슬라이딩 윈도우 1개: 번역(N줄) → 검수 비동기 제출 → 다음 N줄 번역(tentative)
    검수 OK → tentative 확정, flush
    검수 FAIL → tentative 버림, 롤백 후 재번역, 이후 재개
    """
    log = logger.get()
    BATCH = config.review.batch_size
    MAX_RETRY = config.review.max_retranslate

    def translate_batch(
        batch_lines: list[str],
        start_idx: int,
        cur_history: HistoryManager,
        cur_prompt: str,
        cur_summary: str | None,
    ) -> tuple[list[tuple[str, str | None]], HistoryManager, str, str | None]:
        """
        batch_lines를 번역. 반환: (결과 목록, 업데이트된 history, prompt, summary)
        결과 항목: (원문, 번역문) — 빈 줄이면 번역문이 None
        """
        results: list[tuple[str, str | None]] = []
        for j, line in enumerate(batch_lines):
            abs_i = start_idx + j
            if not line.strip():
                results.append((line, None))
                continue
            messages = [{'role': 'system', 'content': cur_prompt}]
            messages.extend(cur_history.to_messages())
            messages.append({'role': 'user', 'content': line})
            try:
                translated = llm_client.chat(messages)
                if abs_i % step == 0 or abs_i == total:
                    log.info('[번역] %d/%d', abs_i, total)
                log.debug('[번역] %d/%d → %s', abs_i, total, translated[:80])
            except Exception as e:
                log.error('[번역] %d/%d 실패: %s', abs_i, total, e)
                print(f'[경고] {abs_i}번 줄 번역 실패: {e} — 원문 유지')
                translated = line
            cur_history.add_turn(line, translated)
            results.append((line, translated))

            src_p = line[:25] + ('...' if len(line) > 25 else '')
            tgt_p = translated[:25] + ('...' if len(translated) > 25 else '')
            print(f'[번역] {abs_i}/{total}: {src_p} → {tgt_p}')

            if cur_history.should_summarize():
                new_sum = summarize(llm_client, cur_history, cur_summary)
                cur_summary = new_sum
                cur_prompt = update_summary(cur_prompt, new_sum)
                cur_history.trim_to_overlap()

        return results, cur_history, cur_prompt, cur_summary

    def flush_batch(results: list[tuple[str, str | None]], confirmed_idx: int) -> int:
        for src, tgt in results:
            out.write((tgt if tgt is not None else '') + '\n')
        out.flush()
        return confirmed_idx + len(results)

    executor = ThreadPoolExecutor(max_workers=1)
    confirmed_done = done_lines  # 출력에 flush된 줄 수

    i = 0  # lines[] 내 현재 위치
    pending_future: Future | None = None
    pending_results: list[tuple[str, str | None]] = []
    pending_start: int = done_lines
    pending_history_snap: list = []
    pending_prompt: str = system_prompt
    pending_summary: str | None = prior_summary

    # 검수 결과 대기 & 처리
    def await_review(
        future: Future,
        results: list[tuple[str, str | None]],
        batch_start: int,
        h_snap_before: list,   # 이 배치 번역 시작 전 history 스냅샷
        prompt_before: str,
        summary_before: str | None,
    ) -> tuple[bool, list[tuple[str, str | None]], str, str | None, list | None]:
        """
        검수 결과 처리.
        반환: (ok, 확정결과, prompt, summary, corrected_h_snap)
          - ok=True  → corrected_h_snap=None (history는 그대로 사용)
          - ok=False → corrected_h_snap=재번역 후 history 스냅샷
        """
        review: ReviewResult = future.result()
        if review.ok:
            log.info('[검수] OK — %d줄 확정', len(results))
            print('[검수] OK')
            return True, results, prompt_before, summary_before, None

        # 실패 → 재번역
        log.warning('[검수] 문제 발견 → 재번역\n%s', review.feedback)
        print(f'[검수] 문제 발견 → 재번역\n  {review.feedback[:120]}')

        retrans_prompt = build_retranslate_prompt(prompt_before, review.feedback)
        batch_lines = [src for src, _ in results]
        retrans = results  # 초기값 (재번역 전)

        for attempt in range(1, MAX_RETRY + 1):
            rh = HistoryManager(config.translation.history_window, config.translation.summary_overlap)
            rh.restore(h_snap_before)
            retrans, rh, retrans_prompt, summary_before = translate_batch(
                batch_lines, batch_start, rh, retrans_prompt, summary_before,
            )
            pairs = [(s, t) for s, t in retrans if t is not None]
            re_review = review_batch(llm_client, pairs, prompt_before)  # 원래 프롬프트로 검수
            if re_review.ok:
                log.info('[검수] 재번역 OK (시도 %d)', attempt)
                print(f'[검수] 재번역 OK (시도 {attempt})')
                return True, retrans, prompt_before, summary_before, rh.snapshot()
            log.warning('[검수] 재번역 시도 %d 실패: %s', attempt, re_review.feedback)
            retrans_prompt = build_retranslate_prompt(prompt_before, re_review.feedback)

        # 최대 재번역 초과 → 마지막 재번역 결과로 진행
        log.warning('[검수] 재번역 %d회 후에도 문제 → 그냥 진행', MAX_RETRY)
        print(f'[검수] 재번역 {MAX_RETRY}회 초과 — 그냥 진행')
        return False, retrans, prompt_before, summary_before, rh.snapshot()

    try:
        while i < len(lines):
            batch_end = min(i + BATCH, len(lines))
            batch_lines = lines[i:batch_end]
            batch_start_abs = done_lines + i

            # history 스냅샷 (이 배치 전 상태)
            h_snap = history.snapshot()
            prompt_snap = system_prompt
            summary_snap = prior_summary

            # 현재 배치 번역
            results, history, system_prompt, prior_summary = translate_batch(
                batch_lines, batch_start_abs + 1, history, system_prompt, prior_summary,
            )

            # 검수 비동기 제출
            pairs = [(s, t) for s, t in results if t is not None]
            future = executor.submit(review_batch, llm_client, pairs, system_prompt)

            # 이전 배치 검수 결과 처리 (있으면)
            if pending_future is not None:
                ok, confirmed_results, pending_prompt, pending_summary, corrected_snap = await_review(
                    pending_future, pending_results, pending_start,
                    pending_history_snap, pending_prompt, pending_summary,
                )
                # 이전 배치 flush (OK든 재번역이든 확정된 결과)
                confirmed_done = flush_batch(confirmed_results, confirmed_done)

                if not ok or corrected_snap is not None:
                    # 재번역 발생 → history를 재번역 후 상태로 교체
                    # (corrected_snap = 재번역 후 history, 현재 tentative 기반 h_snap은 무효)
                    history.restore(corrected_snap)
                    system_prompt = pending_prompt
                    prior_summary = pending_summary
                    _save_state(sp, confirmed_done, prior_summary, system_prompt, history)
                    # 현재 tentative 배치 버리고 future 취소 후 재번역
                    future.cancel()
                    pending_future = None
                    continue  # i 그대로 — 현재 배치부터 다시 번역
                else:
                    _save_state(sp, confirmed_done, pending_summary, pending_prompt, history)

            # tentative를 pending으로 이동
            pending_future = future
            pending_results = results
            pending_start = batch_start_abs + 1
            pending_history_snap = h_snap
            pending_prompt = prompt_snap
            pending_summary = summary_snap

            i += len(batch_lines)

        # 마지막 pending 처리
        if pending_future is not None:
            ok, confirmed_results, pending_prompt, pending_summary, corrected_snap = await_review(
                pending_future, pending_results, pending_start,
                pending_history_snap, pending_prompt, pending_summary,
            )
            if corrected_snap is not None:
                history.restore(corrected_snap)
            confirmed_done = flush_batch(confirmed_results, confirmed_done)
            _save_state(sp, confirmed_done, pending_summary, pending_prompt, history)

    finally:
        executor.shutdown(wait=False)
