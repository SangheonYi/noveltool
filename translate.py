#!/usr/bin/env python3
import argparse
import sys


def main() -> None:
    parser = argparse.ArgumentParser(
        description='LLM 웹소설 번역기',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            '예시:\n'
            '  python translate.py --config config.yaml\n'
            '  python translate.py --config config.yaml --preprocess-only\n'
            '  python translate.py --config config.yaml --no-cache\n'
            '  python translate.py --config config.yaml --dry-run\n'
            '  python translate.py --input novel.txt --output novel_ko.txt --config config.yaml'
        ),
    )
    parser.add_argument('--config', default='config.yaml', help='설정 파일 경로 (기본값: config.yaml)')
    parser.add_argument('--input', help='입력 파일 경로 (설정 파일의 input 항목 무시)')
    parser.add_argument('--output', help='출력 파일 경로 (설정 파일의 output 항목 무시)')
    parser.add_argument('--preprocess-only', action='store_true', help='전처리만 실행하고 종료')
    parser.add_argument('--no-cache', action='store_true', help='전처리 캐시를 무시하고 재실행')
    parser.add_argument('--dry-run', action='store_true', help='API 호출 없이 설정 확인만 수행')
    parser.add_argument('--max-lines', type=int, default=None, help='번역할 최대 라인 수 (config 값 override, 미설정 시 전체)')
    args = parser.parse_args()

    from noveltool.config import load_config
    config = load_config(args.config)

    if args.input:
        config.input = args.input
    if args.output:
        config.output = args.output
    if args.max_lines is not None:
        config.translation.max_lines = args.max_lines

    if not config.input:
        print('오류: 입력 파일이 지정되지 않았습니다. --input 또는 config.yaml의 input 항목을 설정하세요.', file=sys.stderr)
        sys.exit(1)
    if not config.output:
        print('오류: 출력 파일이 지정되지 않았습니다. --output 또는 config.yaml의 output 항목을 설정하세요.', file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        print('[드라이런] 설정 로드 완료')
        print(f'  모델         : {config.llm.model}')
        print(f'  입력 파일    : {config.input}')
        print(f'  출력 파일    : {config.output}')
        print(f'  history_window: {config.translation.history_window}')
        print(f'  summary_overlap: {config.translation.summary_overlap}')
        print(f'  chunk_tokens : {config.preprocessing.chunk_tokens}')
        print(f'  search engine: {config.search.engine}')
        print(f'  최대 라인 수 : {config.translation.max_lines if config.translation.max_lines else "전체"}')
        return

    from noveltool.pipeline import run
    run(config, preprocess_only=args.preprocess_only, no_cache=args.no_cache)



if __name__ == '__main__':
    main()
