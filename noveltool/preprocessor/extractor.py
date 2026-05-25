import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import tiktoken

from .. import logger

_PROMPT_TEMPLATE = (
    '다음 텍스트에 등장하는 인물 이름을 모두 추출하세요.\n'
    '별명, 호칭, 직함을 포함한 고유 인물 이름만 추출하고 JSON 배열로만 응답하세요.\n'
    '예: ["张伟", "李娜", "천도류"]\n\n'
    '[텍스트]\n{text}'
)


def _get_encoding(model: str) -> tiktoken.Encoding:
    try:
        return tiktoken.encoding_for_model(model)
    except KeyError:
        return tiktoken.get_encoding('cl100k_base')


def _count_tokens(text: str, enc: tiktoken.Encoding) -> int:
    return len(enc.encode(text))


def _split_into_chunks(lines: list[str], max_tokens: int, enc: tiktoken.Encoding) -> list[list[str]]:
    chunks: list[list[str]] = []
    current: list[str] = []
    current_tokens = 0

    for line in lines:
        line_tokens = _count_tokens(line, enc)
        if current_tokens + line_tokens > max_tokens and current:
            chunks.append(current)
            current = [line]
            current_tokens = line_tokens
        else:
            current.append(line)
            current_tokens += line_tokens

    if current:
        chunks.append(current)

    return chunks


def _extract_from_chunk(llm_client, chunk: list[str], chunk_idx: int) -> set[str]:
    log = logger.get()
    text = '\n'.join(chunk)
    messages = [{'role': 'user', 'content': _PROMPT_TEMPLATE.format(text=text)}]
    log.debug('[추출] 청크 %d 입력 텍스트 (%d줄, 앞 100자):\n%s', chunk_idx, len(chunk), text[:100])
    try:
        response = llm_client.chat(messages, temperature=0)
        start = response.find('[')
        end = response.rfind(']') + 1
        if start == -1 or end == 0:
            log.warning('[추출] 청크 %d: 응답에 JSON 배열 없음 (응답: %s)', chunk_idx, response[:100])
            return set()
        names = json.loads(response[start:end])
        result = set(names) if isinstance(names, list) else set()
        log.info('[추출] 청크 %d → %d명: %s', chunk_idx, len(result), sorted(result))
        return result
    except Exception as e:
        log.error('[추출] 청크 %d 실패: %s', chunk_idx, e)
        print(f'[경고] 캐릭터 추출 실패 (청크): {e}')
        return set()


def extract_characters(llm_client, lines: list[str], chunk_tokens: int, model: str) -> set[str]:
    log = logger.get()
    enc = _get_encoding(model)
    chunks = _split_into_chunks(lines, chunk_tokens, enc)

    log.info('[추출] 시작: %d줄 → %d개 청크 (최대 %d토큰/청크)', len(lines), len(chunks), chunk_tokens)
    log.info('[추출] 프롬프트 템플릿:\n%s', _PROMPT_TEMPLATE.replace('{text}', '... (청크 텍스트)'))
    print(f'[전처리] 캐릭터 추출: {len(chunks)}개 청크 병렬 처리 중...')

    all_characters: set[str] = set()
    with ThreadPoolExecutor(max_workers=min(len(chunks), 8)) as executor:
        futures = {executor.submit(_extract_from_chunk, llm_client, chunk, i): i for i, chunk in enumerate(chunks)}
        for future in as_completed(futures):
            all_characters.update(future.result())

    log.info('[추출] 완료: 총 %d명 → %s', len(all_characters), sorted(all_characters))
    print(f'[전처리] 추출된 캐릭터: {len(all_characters)}명')
    return all_characters
