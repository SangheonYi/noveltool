#!/usr/bin/env python3
import sys, os
# 백그라운드 실행 시에도 항상 noveltool 디렉토리 기준으로 동작
_ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(_ROOT)
sys.path.insert(0, _ROOT)
from noveltool.config import load_config
from noveltool.llm_client import LLMClient
from noveltool import logger
from noveltool.preprocessor.namuwiki import fetch_characters

cfg = load_config('config.yaml')
logger.setup(log_dir=cfg.log_dir, level=cfg.log_level)
llm = LLMClient(cfg)
cache = os.path.join(_ROOT, 'parody_cache')  # 번역 파이프라인 .cache와 분리된 전용 디렉토리
os.makedirs(cache, exist_ok=True)

targets = [
    # 누락된 원작 복구
    "명일방주/오퍼레이터",
    "명탐정 코난/등장인물",
    "데이트 어 라이브/등장인물",
    "나의 히어로 아카데미아/등장인물",
    "짱구",
    "장송의 프리렌/등장인물",
    "공의 경계/등장인물",
    "학사신공/등장인물",
    # 5명·3명으로 떨어진 것 재시도
    "DC 코믹스/등장인물",
    "워해머 40,000/등장인물",
    # 추가
    "드래곤볼/등장인물",
    "붕괴: 스타레일/등장인물",
]

total_before = 0
total_after = 0
for article in targets:
    print(f"\n▶ {article}", flush=True)
    p = fetch_characters(article, cache, llm, force=True)
    print(f"  → {len(p)}명", flush=True)
    total_after += len(p)

print(f"\n재추출 완료: 총 {total_after}명")
