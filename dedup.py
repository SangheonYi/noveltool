#!/usr/bin/env python3
"""중복 파일 탐지 및 삭제 CLI

Usage:
  python dedup.py --root /mnt/d/nov
  python dedup.py --root /mnt/d/nov --dry-run
  python dedup.py --root /mnt/d/nov --partial 2000
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from noveltool.dedup import DupResult, find_duplicates, delete_duplicates


def fmt(b: int) -> str:
    for u, n in [('GB', 1e9), ('MB', 1e6), ('KB', 1e3)]:
        if b >= n:
            return f'{b/n:.2f} {u}'
    return f'{b} B'


def main() -> None:
    parser = argparse.ArgumentParser(description='4단계 캐스케이드 중복 파일 탐지/삭제')
    parser.add_argument('--root', required=True, help='탐색 루트 디렉터리')
    parser.add_argument('--dry-run', action='store_true', help='삭제 없이 목록만 출력')
    parser.add_argument('--partial', type=int, default=1000, metavar='N',
                        help='앞뒤 비교 바이트 수 (기본: 1000)')
    parser.add_argument('--top', type=int, default=20, help='상세 출력할 그룹 수 (기본: 20)')
    args = parser.parse_args()

    print(f'루트: {args.root}')
    print(f'모드: {"DRY-RUN" if args.dry_run else "실제 삭제"}')
    print()

    result = find_duplicates(
        root=args.root,
        progress=lambda msg: print(f'  {msg}', flush=True),
        partial_n=args.partial,
    )

    print(f'\n{"="*50}')
    print(f'완전 중복 그룹 : {len(result.groups):,}개')
    print(f'삭제 대상 파일 : {result.delete_count:,}개')
    print(f'회수 가능 용량 : {fmt(result.wasted_bytes)}')
    print(f'{"="*50}')

    if not result.groups:
        print('중복 파일 없음.')
        return

    print(f'\n상위 {args.top}개 그룹:')
    for g in result.groups[:args.top]:
        print(f'\n  [{fmt(g.size)}] 유지: {g.keep}')
        for d in g.delete:
            print(f'          삭제: {d}')

    if args.dry_run:
        print('\n[DRY-RUN] 실제 삭제 없이 종료합니다.')
        return

    print(f'\n삭제 대상 {result.delete_count:,}개 파일 / {fmt(result.wasted_bytes)} 회수')
    ans = input('삭제를 진행하시겠습니까? [y/N] ').strip().lower()
    if ans != 'y':
        print('취소.')
        return

    deleted, failed = delete_duplicates(
        result,
        dry_run=False,
        progress=lambda msg: print(f'  {msg}', flush=True),
    )
    print(f'\n완료: {deleted:,}개 삭제', end='')
    if failed:
        print(f', {len(failed)}개 실패')
        for f in failed:
            print(f'  [실패] {f["path"]}: {f["reason"]}')
    else:
        print()
    print(f'회수 용량: {fmt(result.wasted_bytes)}')


if __name__ == '__main__':
    main()
