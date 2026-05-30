#!/usr/bin/env python3
"""
패러디 소설 원작들의 등장인물 프로필을 나무위키에서 캐싱.

흐름:
  /mnt/d/nov 파일 분석 → 원작별 소설 수 집계
  → verify_works (나무위키 문서명 확인)
  → fetch_characters (프로필 JSON 캐싱)

사용법:
  python cache_parody_profiles.py --config config.yaml
  python cache_parody_profiles.py --config config.yaml --works 나루토 원피스
  python cache_parody_profiles.py --config config.yaml --dry-run
"""

import sys
import os
import argparse
from collections import defaultdict
from pathlib import Path

# 같은 패키지 루트에서 실행
sys.path.insert(0, str(Path(__file__).parent))

from noveltool.config import load_config
from noveltool.llm_client import LLMClient
from noveltool import logger
from noveltool.preprocessor.identifier import WorkCandidate
from noveltool.preprocessor.verifier import verify_works
from noveltool.preprocessor.namuwiki import fetch_characters

# analyze_novels.py에서 분석 기능 재사용
_NOV_SCRIPT = Path("/mnt/d/nov")
sys.path.insert(0, str(_NOV_SCRIPT))
from analyze_novels import analyze, NOV_DIR


def _collect_sources() -> dict[str, int]:
    """파일명 키워드 기반으로 원작별 파일 수 집계."""
    entries = analyze(NOV_DIR, llm_client=None)
    counts: dict[str, int] = defaultdict(int)
    for e in entries:
        if not e.is_parody or not e.parody_source:
            continue
        for src in e.parody_source.split(" x "):
            counts[src.strip()] += 1
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def main() -> None:
    parser = argparse.ArgumentParser(description="패러디 원작 캐릭터 프로필 캐싱")
    parser.add_argument("--config", default="config.yaml",
                        help="noveltool config.yaml 경로 (기본: config.yaml)")
    parser.add_argument("--works", nargs="*", metavar="WORK",
                        help="캐싱할 원작명 직접 지정 (기본: 탐지된 전체 원작)")
    parser.add_argument("--engine", default="duckduckgo",
                        choices=["duckduckgo", "namuwiki", "playwright"],
                        help="나무위키 문서 검증 엔진 (기본: duckduckgo)")
    parser.add_argument("--dry-run", action="store_true",
                        help="실제 API 호출 없이 캐싱 대상 목록만 출력")
    args = parser.parse_args()

    cfg = load_config(args.config)
    logger.setup(log_dir=cfg.log_dir, level=cfg.log_level)
    llm_client = LLMClient(cfg)
    cache_dir = os.path.join(os.path.dirname(os.path.abspath(args.config)), 'parody_cache')
    os.makedirs(cache_dir, exist_ok=True)

    # ── 1단계: 원작 목록 수집 ────────────────────────────────────────────────
    print("분석 중: 파일명에서 원작 목록 추출...")
    source_counts = _collect_sources()

    if not source_counts:
        print("감지된 패러디 원작 없음.")
        return

    if args.works:
        # 지정된 원작만 (없으면 count=0으로 표시)
        target = {w: source_counts.get(w, 0) for w in args.works}
    else:
        target = source_counts

    print(f"\n캐싱 대상 원작 {len(target)}개 (소설 수 순):")
    for work, cnt in target.items():
        cached = Path(cache_dir) / f"{work.replace(' ', '_')}_characters.json"
        status = "[캐시 있음]" if cached.exists() else "[신규]     "
        print(f"  {status} {work:<20} ({cnt}개 파일)")

    if args.dry_run:
        print("\n[dry-run] 실제 캐싱 없이 종료.")
        return

    # 이미 캐시된 원작은 건너뜀 (fetch_characters 내부에서도 처리하지만 명시적으로 표시)
    to_verify = list(target.keys())

    # ── 2단계: verify_works → 나무위키 문서명 확정 ──────────────────────────
    print(f"\n[Step 1] 나무위키 문서명 검증 중... (엔진: {args.engine})")
    candidates = [
        WorkCandidate(
            work=work,
            characters=[],
            confidence=1.0,
            reason="파일명 키워드 직접 매칭",
        )
        for work in to_verify
    ]

    verified = verify_works(
        llm_client,
        candidates,
        engine=args.engine,
        headless=cfg.search.headless,
        result_count=cfg.search.result_count,
        debug_dir=cache_dir,
    )

    if not verified:
        print("검증된 원작 없음 — 종료.")
        return

    print(f"\n검증 결과: {len(verified)}/{len(candidates)}개 확인됨")
    for v in verified:
        print(f"  {v.work:<20} → 나무위키: {v.namuwiki_article}")

    failed = [c.work for c in candidates if not any(v.work == c.work for v in verified)]
    if failed:
        print(f"\n검증 실패 (나무위키 문서 없음): {failed}")

    # ── 3단계: fetch_characters → 캐릭터 프로필 캐싱 ───────────────────────
    print(f"\n[Step 2] 캐릭터 프로필 캐싱 중... (저장: {cache_dir})")
    total_profiles = 0
    for work in verified:
        print(f"\n  ▶ [{work.work}] '{work.namuwiki_article}' ...")
        profiles = fetch_characters(work.namuwiki_article, cache_dir, llm_client)
        total_profiles += len(profiles)
        print(f"    → {len(profiles)}명 완료")

    print(f"\n{'='*50}")
    print(f"  캐싱 완료: {total_profiles}명 / {len(verified)}개 원작")
    print(f"  캐시 위치: {cache_dir}")
    print(f"{'='*50}")


if __name__ == "__main__":
    main()
