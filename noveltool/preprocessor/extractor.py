import json
from concurrent.futures import ThreadPoolExecutor, as_completed

import tiktoken


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


def _extract_from_chunk(llm_client, chunk: list[str]) -> set[str]:
    text = '\n'.join(chunk)
    messages = [
        {
            'role': 'user',
            'content': (
                '다음 텍스트에 등장하는 인물 이름을 모두 추출하세요.\n'
                '별명, 호칭, 직함을 포함한 고유 인물 이름만 추출하고 JSON 배열로만 응답하세요.\n'
                '예: ["张伟", "李娜", "천도류"]\n\n'
                f'[텍스트]\n{text}'
            ),
        }
    ]
    try:
        response = llm_client.chat(messages, temperature=0)
        start = response.find('[')
        end = response.rfind(']') + 1
        if start == -1 or end == 0:
            return set()
        names = json.loads(response[start:end])
        return set(names) if isinstance(names, list) else set()
    except Exception as e:
        print(f'[경고] 캐릭터 추출 실패 (청크): {e}')
        return set()


def extract_characters(llm_client, lines: list[str], chunk_tokens: int, model: str) -> set[str]:
    enc = _get_encoding(model)
    chunks = _split_into_chunks(lines, chunk_tokens, enc)
    print(f'[전처리] 캐릭터 추출: {len(chunks)}개 청크 병렬 처리 중...')

    all_characters: set[str] = set()
    with ThreadPoolExecutor(max_workers=min(len(chunks), 8)) as executor:
        futures = {executor.submit(_extract_from_chunk, llm_client, chunk): i for i, chunk in enumerate(chunks)}
        for future in as_completed(futures):
            all_characters.update(future.result())

    print(f'[전처리] 추출된 캐릭터: {len(all_characters)}명')
    return all_characters
