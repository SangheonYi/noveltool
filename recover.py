#!/usr/bin/env python3
import argparse
import os
import sys


def _load_summary(value: str | None) -> str | None:
    if not value:
        return None
    if os.path.isfile(value):
        with open(value, encoding='utf-8') as f:
            return f.read().strip()
    return value.strip()


def main() -> None:
    parser = argparse.ArgumentParser(
        description='noveltool — 웹소설 데이터 복구',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            '예시:\n'
            '  python recover.py --config config.yaml \\\n'
            '    --before before.txt --after after.txt --lines 50 --output recovered.txt\n\n'
            '  python recover.py --config config.yaml \\\n'
            '    --before before.txt --after after.txt --lines 30 \\\n'
            '    --summary "주인공은 적진에 잠입해 있으며..." --output recovered.txt\n\n'
            '  python recover.py --config config.yaml \\\n'
            '    --before before.txt --after after.txt --lines 30 \\\n'
            '    --summary summary.txt --output recovered.txt'
        ),
    )
    parser.add_argument('--config', default='config.yaml', help='설정 파일 경로 (기본값: config.yaml)')
    parser.add_argument('--before', required=True, help='소실 전 컨텍스트 파일 경로')
    parser.add_argument('--after', required=True, help='소실 후 컨텍스트 파일 경로')
    parser.add_argument('--lines', required=True, type=int, help='복구할 라인 수')
    parser.add_argument('--summary', default=None, help='지난 이야기 요약 (문자열 또는 파일 경로)')
    parser.add_argument('--output', default='recovered.txt', help='복구 결과 출력 파일 (기본값: recovered.txt)')
    parser.add_argument('--no-cache', action='store_true', help='전처리 캐시를 무시하고 재실행')
    parser.add_argument('--dry-run', action='store_true', help='API 호출 없이 설정 확인만 수행')
    args = parser.parse_args()

    for path, label in ((args.before, '--before'), (args.after, '--after')):
        if not os.path.isfile(path):
            print(f'오류: {label} 파일을 찾을 수 없습니다: {path}', file=sys.stderr)
            sys.exit(1)

    if args.lines <= 0:
        print('오류: --lines 는 1 이상이어야 합니다.', file=sys.stderr)
        sys.exit(1)

    from noveltool.config import load_config
    config = load_config(args.config)

    summary = _load_summary(args.summary)

    if args.dry_run:
        print('[드라이런] 설정 로드 완료')
        print(f'  모델          : {config.llm.model}')
        print(f'  before 파일   : {args.before}')
        print(f'  after 파일    : {args.after}')
        print(f'  복구 라인 수  : {args.lines}')
        print(f'  before_lines  : {config.recovery.before_lines}')
        print(f'  after_lines   : {config.recovery.after_lines}')
        print(f'  출력 파일     : {args.output}')
        print(f'  요약 제공     : {"있음" if summary else "없음"}')
        return

    from noveltool import logger
    log_path = logger.setup(config.log_dir, config.log_level)
    print(f'[로그] {log_path}')

    from noveltool.recover_pipeline import run
    run(
        config=config,
        before_file=args.before,
        after_file=args.after,
        missing_lines=args.lines,
        summary=summary,
        output=args.output,
        no_cache=args.no_cache,
    )


if __name__ == '__main__':
    main()
