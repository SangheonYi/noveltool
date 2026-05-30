"""
dedup.py — 중복 파일 탐지 및 삭제

4단계 캐스케이드 검증:
  1. 파일 크기     (I/O 없음)
  2. MD5          (빠른 1차 필터)
  3. SHA-256      (강한 2차 필터)
  4. 앞뒤 N바이트  (실질적 100% 확인, 최소 I/O)
"""
from __future__ import annotations

import collections
import hashlib
import os
from dataclasses import dataclass, field
from typing import Callable


PARTIAL_BYTES = 1000  # 앞뒤 각 비교 바이트 수


@dataclass
class DupGroup:
    keep: str                    # 유지할 파일 경로
    delete: list[str]            # 삭제 대상 경로 목록
    size: int = 0                # 파일 크기 (bytes)

    @property
    def wasted_bytes(self) -> int:
        return self.size * len(self.delete)


@dataclass
class DupResult:
    groups: list[DupGroup] = field(default_factory=list)
    total_scanned: int = 0
    inaccessible: int = 0

    @property
    def delete_count(self) -> int:
        return sum(len(g.delete) for g in self.groups)

    @property
    def wasted_bytes(self) -> int:
        return sum(g.wasted_bytes for g in self.groups)


def _hash_file(path: str, algo: str) -> str:
    h = hashlib.new(algo)
    with open(path, 'rb') as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def _partial_bytes(path: str, n: int = PARTIAL_BYTES) -> bytes:
    """앞 n바이트 + 뒤 n바이트 (파일이 작으면 전체)."""
    size = os.path.getsize(path)
    with open(path, 'rb') as f:
        head = f.read(n)
        tail = b''
        if size > n * 2:
            f.seek(-n, 2)
            tail = f.read(n)
    return head + tail


def _best_to_keep(paths: list[str]) -> str:
    """경로 깊이 얕은 것 우선, 동률이면 짧은 경로."""
    return min(paths, key=lambda p: (p.count(os.sep), len(p)))


def _group_by(paths: list[str], key_fn: Callable[[str], str]) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = collections.defaultdict(list)
    for p in paths:
        try:
            buckets[key_fn(p)].append(p)
        except OSError:
            pass
    return {k: v for k, v in buckets.items() if len(v) >= 2}


def find_duplicates(
    root: str,
    progress: Callable[[str], None] | None = None,
    partial_n: int = PARTIAL_BYTES,
) -> DupResult:
    """
    root 아래의 모든 파일에서 완전 중복을 탐지한다.

    Parameters
    ----------
    root      : 탐색 루트 디렉터리
    progress  : 진행 메시지를 받는 콜백 (선택)
    partial_n : 4단계에서 비교할 앞뒤 바이트 수

    Returns
    -------
    DupResult
    """
    def log(msg: str) -> None:
        if progress:
            progress(msg)

    result = DupResult()

    # ── 1단계: 크기 ───────────────────────────────────────────────────
    log('1단계: 파일 크기 그룹화...')
    size_map: dict[int, list[str]] = collections.defaultdict(list)
    for dirpath, _, filenames in os.walk(root):
        for fname in filenames:
            fp = os.path.join(dirpath, fname)
            try:
                sz = os.path.getsize(fp)
                if sz > 0:
                    size_map[sz].append(fp)
                    result.total_scanned += 1
            except OSError:
                result.inaccessible += 1

    candidates = [p for paths in size_map.values() if len(paths) >= 2 for p in paths]
    log(f'  전체 {result.total_scanned:,}개 → 크기 중복 후보 {len(candidates):,}개')

    # ── 2단계: MD5 ────────────────────────────────────────────────────
    log('2단계: MD5 해시...')
    md5_groups = _group_by(candidates, lambda p: _hash_file(p, 'md5'))
    after_md5 = [p for paths in md5_groups.values() for p in paths]
    log(f'  MD5 일치 {len(after_md5):,}개 ({len(md5_groups):,}그룹) / 탈락 {len(candidates)-len(after_md5):,}개')

    # ── 3단계: SHA-256 ────────────────────────────────────────────────
    log('3단계: SHA-256 해시 (MD5 일치한 것만)...')
    sha_groups = _group_by(after_md5, lambda p: _hash_file(p, 'sha256'))
    after_sha = [p for paths in sha_groups.values() for p in paths]
    log(f'  SHA-256 일치 {len(after_sha):,}개 ({len(sha_groups):,}그룹)')

    # ── 4단계: 앞뒤 N바이트 비교 ─────────────────────────────────────
    log(f'4단계: 앞뒤 {partial_n}바이트 최종 확인 (SHA-256 일치한 것만)...')
    partial_groups = _group_by(after_sha, lambda p: _partial_bytes(p, partial_n))
    warned = len(after_sha) - sum(len(v) for v in partial_groups.values())
    if warned:
        log(f'  [경고] SHA-256 일치했으나 앞뒤 바이트 다름: {warned}개 제외')
    log(f'  최종 확인 {sum(len(v) for v in partial_groups.values()):,}개 ({len(partial_groups):,}그룹)')

    # ── 결과 정리 ─────────────────────────────────────────────────────
    for paths in partial_groups.values():
        keep = _best_to_keep(paths)
        result.groups.append(DupGroup(
            keep=keep,
            delete=[p for p in paths if p != keep],
            size=os.path.getsize(keep),
        ))

    result.groups.sort(key=lambda g: g.wasted_bytes, reverse=True)
    return result


def delete_duplicates(
    result: DupResult,
    dry_run: bool = False,
    progress: Callable[[str], None] | None = None,
) -> tuple[int, list[dict]]:
    """
    DupResult에 따라 중복 파일을 삭제한다.

    Parameters
    ----------
    result  : find_duplicates() 반환값
    dry_run : True 시 실제 삭제 없이 목록만 반환
    progress: 진행 메시지 콜백

    Returns
    -------
    (deleted_count, failed_list)
    failed_list 각 항목: {'path': str, 'reason': str}
    """
    def log(msg: str) -> None:
        if progress:
            progress(msg)

    deleted = 0
    failed: list[dict] = []

    for group in result.groups:
        for path in group.delete:
            if dry_run:
                log(f'[DRY] {path}')
                deleted += 1
                continue
            try:
                os.remove(path)
                log(f'[삭제] {path}')
                deleted += 1
            except OSError as e:
                log(f'[실패] {path}: {e}')
                failed.append({'path': path, 'reason': str(e)})

    return deleted, failed
