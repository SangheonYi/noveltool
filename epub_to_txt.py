#!/usr/bin/env python3
"""epub → plain text converter

Usage:
  python epub_to_txt.py novel.epub
  python epub_to_txt.py novel.epub output.txt
  python epub_to_txt.py novel.epub --chapter-sep "==="
"""

import argparse
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from bs4 import BeautifulSoup, NavigableString, Tag


BLOCK_TAGS = frozenset({
    'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6',
    'li', 'tr', 'br', 'hr', 'blockquote', 'section', 'article',
})
SKIP_TAGS = frozenset({'script', 'style', 'nav', 'head', 'rt', 'rp', 'ruby'})


def _opf_path(epub: zipfile.ZipFile) -> str:
    data = epub.read('META-INF/container.xml')
    root = ET.fromstring(data)
    for elem in root.iter():
        if elem.tag.endswith('rootfile'):
            return elem.get('full-path', '')
    raise ValueError('META-INF/container.xml에서 rootfile을 찾을 수 없음')


def _spine_hrefs(epub: zipfile.ZipFile, opf_path: str) -> list[str]:
    data = epub.read(opf_path)
    root = ET.fromstring(data)

    # namespace 추출
    m = re.match(r'\{(.+?)\}', root.tag)
    ns = m.group(1) if m else ''
    p = f'{{{ns}}}' if ns else ''

    manifest: dict[str, str] = {}
    for item in root.iter(f'{p}item'):
        item_id = item.get('id', '')
        href = item.get('href', '')
        media_type = item.get('media-type', '')
        if item_id and href and ('html' in media_type or href.endswith(('.xhtml', '.html', '.htm'))):
            manifest[item_id] = href

    base = posixpath.dirname(opf_path)
    hrefs: list[str] = []
    for itemref in root.iter(f'{p}itemref'):
        idref = itemref.get('idref', '')
        if idref in manifest:
            href = manifest[idref]
            # 상대 경로 → epub 내 절대 경로
            full = posixpath.normpath(posixpath.join(base, href)) if base else href
            hrefs.append(full)

    return hrefs


def _extract_text(tag: Tag | NavigableString) -> str:
    if isinstance(tag, NavigableString):
        return str(tag)

    name = tag.name or ''

    # ruby 태그: rt/rp 제거, 나머지(기본 글자)만 유지
    if name == 'ruby':
        parts = []
        for child in tag.children:
            if isinstance(child, NavigableString):
                parts.append(str(child))
            elif child.name not in ('rt', 'rp'):
                parts.append(_extract_text(child))
        return ''.join(parts)

    if name in SKIP_TAGS:
        return ''

    parts = []
    if name in BLOCK_TAGS:
        parts.append('\n')

    for child in tag.children:
        parts.append(_extract_text(child))

    if name in BLOCK_TAGS:
        parts.append('\n')

    return ''.join(parts)


def _html_to_text(html_bytes: bytes) -> str:
    soup = BeautifulSoup(html_bytes, 'html.parser')

    for tag in soup(['script', 'style', 'nav', 'head']):
        tag.decompose()

    body = soup.find('body') or soup
    text = _extract_text(body)

    # 연속 공백 정리 (줄 내부)
    lines = [re.sub(r'[ \t]+', ' ', line).strip() for line in text.splitlines()]

    # 빈 줄 3개 이상 → 2개로 압축
    result: list[str] = []
    blank_run = 0
    for line in lines:
        if line == '':
            blank_run += 1
            if blank_run <= 2:
                result.append('')
        else:
            blank_run = 0
            result.append(line)

    return '\n'.join(result).strip()


def convert(epub_path: str, output_path: str, chapter_sep: str = '') -> int:
    """epub → txt 변환. 반환값: 총 줄 수"""
    with zipfile.ZipFile(epub_path, 'r') as epub:
        opf = _opf_path(epub)
        hrefs = _spine_hrefs(epub, opf)

        if not hrefs:
            raise ValueError('spine에서 읽을 수 있는 콘텐츠 파일을 찾지 못했음')

        chapters: list[str] = []
        for href in hrefs:
            try:
                html_bytes = epub.read(href)
            except KeyError:
                # 일부 epub은 경로 표기가 다를 수 있음 — 이름 부분만으로 재탐색
                name = posixpath.basename(href)
                candidates = [n for n in epub.namelist() if n.endswith(name)]
                if not candidates:
                    print(f'[경고] 파일을 찾지 못해 건너뜀: {href}')
                    continue
                html_bytes = epub.read(candidates[0])

            text = _html_to_text(html_bytes)
            if text:
                chapters.append(text)

    sep = f'\n\n{chapter_sep}\n\n' if chapter_sep else '\n\n'
    full_text = sep.join(chapters)

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(full_text)
        if not full_text.endswith('\n'):
            f.write('\n')

    return full_text.count('\n') + 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description='epub → plain text 변환기',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            '예시:\n'
            '  python epub_to_txt.py novel.epub\n'
            '  python epub_to_txt.py novel.epub output.txt\n'
            '  python epub_to_txt.py novel.epub --chapter-sep "==="\n'
        ),
    )
    parser.add_argument('input', help='입력 epub 파일 경로')
    parser.add_argument('output', nargs='?', help='출력 txt 파일 경로 (기본값: 입력 파일과 같은 위치에 .txt)')
    parser.add_argument('--chapter-sep', default='', metavar='SEP',
                        help='챕터 구분자 문자열 (기본값: 빈 줄 2개)')
    args = parser.parse_args()

    input_path = args.input
    if args.output:
        output_path = args.output
    else:
        output_path = str(Path(input_path).with_suffix('.txt'))

    print(f'입력: {input_path}')
    print(f'출력: {output_path}')

    try:
        total_lines = convert(input_path, output_path, args.chapter_sep)
        print(f'변환 완료: {total_lines:,}줄 → {output_path}')
    except Exception as e:
        print(f'오류: {e}')
        raise SystemExit(1)


if __name__ == '__main__':
    main()
