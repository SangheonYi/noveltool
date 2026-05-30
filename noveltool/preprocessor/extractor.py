import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import tiktoken

from .. import logger

_PROMPT_TEMPLATE = (
    '다음 텍스트에 등장하는 인물 이름을 모두 추출하세요.\n\n'
    '[추출 규칙]\n'
    '- 소설 속 등장인물 이름만 추출 (별명·호칭 포함)\n'
    '- 제외: 저자·역자·출판사·감수자 등 책 제작 관련 실존 인물\n'
    '- 제외: 조직·단체·기관·세력 이름 (예: 사자왕기관, 太史局, S.H.I.E.L.D.)\n'
    '- 제외: 한글로만 이루어진 이름 (원문이 외국어이므로 번역 잔재일 가능성이 높음)\n'
    '- 이름 형태는 텍스트에 나온 그대로 사용 (후리가나 제거, 존댓말·호칭 분리)\n'
    '- 한자·한어 이름은 독음(읽는 법)도 함께 추출. 독음을 모르면 빈 문자열로 두세요.\n\n'
    '형식: [{{"name": "이름", "reading": "독음(히라가나·가타카나·로마자·병음)"}}, ...]\n'
    '예: [{{"name": "矢瀬基樹", "reading": "やぜもとき"}}, '
    '{{"name": "絃神冥駕", "reading": "いとがみめいが"}}, '
    '{{"name": "アヴローラ・フロレスティーナ", "reading": ""}}, '
    '{{"name": "Harry Potter", "reading": ""}}]\n\n'
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


def _extract_from_chunk(llm_client, chunk: list[str], chunk_idx: int) -> dict[str, str]:
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
            return {}
        data = json.loads(response[start:end])
        result: dict[str, str] = {}
        for item in data:
            if isinstance(item, dict):
                name = (item.get('name') or '').strip()
                reading = (item.get('reading') or '').strip()
                if name:
                    result[name] = reading
            elif isinstance(item, str) and item.strip():
                # 이전 포맷(문자열 배열) 하위 호환
                result[item.strip()] = ''
        log.info('[추출] 청크 %d → %d명: %s', chunk_idx, len(result), sorted(result.keys()))
        return result
    except Exception as e:
        log.error('[추출] 청크 %d 실패: %s', chunk_idx, e)
        print(f'[경고] 캐릭터 추출 실패 (청크): {e}')
        return {}


def extract_characters(llm_client, lines: list[str], chunk_tokens: int, model: str) -> dict[str, str]:
    """등장인물 이름과 독음 추출. 반환: {이름: 독음(없으면 빈 문자열)}"""
    log = logger.get()
    enc = _get_encoding(model)
    chunks = _split_into_chunks(lines, chunk_tokens, enc)

    log.info('[추출] 시작: %d줄 → %d개 청크 (최대 %d토큰/청크)', len(lines), len(chunks), chunk_tokens)
    print(f'[전처리] 캐릭터 추출: {len(chunks)}개 청크 병렬 처리 중...')

    all_characters: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=min(len(chunks), 8)) as executor:
        futures = {executor.submit(_extract_from_chunk, llm_client, chunk, i): i for i, chunk in enumerate(chunks)}
        for future in as_completed(futures):
            chunk_result = future.result()
            for name, reading in chunk_result.items():
                # 같은 이름이 여러 청크에서 나오면 독음이 있는 쪽 우선
                if name not in all_characters or (not all_characters[name] and reading):
                    all_characters[name] = reading

    log.info('[추출] 완료: 총 %d명', len(all_characters))
    print(f'[전처리] 추출된 캐릭터: {len(all_characters)}명')
    return all_characters
