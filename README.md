# noveltool

LLM 기반 웹소설 처리 도구. 번역, epub 변환, 소실 데이터 복구 기능을 제공한다.

## 기능

| 명령 | 설명 |
|------|------|
| `translate` | 중국어 / 일본어 / 영어 원문을 한국어로 line-by-line 번역 |
| `epub_to_txt` | epub 파일을 plain text로 변환 (후리가나 자동 제거) |
| `recover` | 소실된 웹소설 구간을 앞뒤 문맥 기반으로 line-by-line 복구 |

**공통 특징:**
- 나무위키 자동 크롤링으로 등장인물 프로필을 system prompt에 주입
- 롤링 요약으로 장편 소설에서도 맥락 유지
- 번역 중단 시 state 파일로 context(history·요약) 보존 → 이어쓰기
- 전처리 결과 JSON 캐시로 재실행 비용 절감
- 실행 로그를 파일에 자동 저장 (콘솔 출력 동시 기록)

## 설치

Python 3.14+, [uv](https://github.com/astral-sh/uv) 필요.

```bash
git clone git@github.com:SangheonYi/noveltool.git
cd noveltool
uv venv --python 3.14
source .venv/bin/activate
uv pip install openai pyyaml tenacity requests beautifulsoup4 playwright tiktoken
playwright install chromium   # playwright 엔진 사용 시에만 필요
```

## 설정

```bash
cp config.yaml.example config.yaml
```

`config.yaml`을 열어 아래 항목을 수정한다.

```yaml
llm:
  base_url: "https://api.openai.com/v1"
  api_key: "${OPENAI_API_KEY}"   # 환경변수 또는 직접 입력
  model: "gpt-4o"                # 미설정 시 /models API 첫 번째 모델 자동 사용
```

전체 옵션은 [config.yaml.example](config.yaml.example) 참고.

## 사용법

### epub → txt 변환

```bash
# 같은 폴더에 .txt 생성
python epub_to_txt.py novel.epub

# 출력 경로 직접 지정
python epub_to_txt.py novel.epub output/novel.txt

# 챕터 구분자 삽입
python epub_to_txt.py novel.epub --chapter-sep "==="
```

- epub spine 순서대로 챕터를 추출하고 HTML 태그 제거
- `<ruby>` 태그의 후리가나(`<rt>`) 자동 제거 → 한자/한어 기본 글자만 유지
- 추가 의존성 없음 (stdlib `zipfile` + 기설치 `beautifulsoup4`)

### 번역

```bash
python translate.py --config config.yaml
```

```bash
# 파일 직접 지정 (config의 input/output 무시)
python translate.py --input input/novel.txt --output output/novel_ko.txt --config config.yaml

# 전처리 결과만 확인 (번역 없이 캐릭터/세계관 식별)
python translate.py --config config.yaml --preprocess-only

# 전처리 캐시 무시하고 재실행
python translate.py --config config.yaml --no-cache

# 테스트용: 앞 N줄만 번역 (config의 max_lines 무시)
python translate.py --config config.yaml --max-lines 200

# 설정 확인 (API 호출 없음)
python translate.py --config config.yaml --dry-run
```

**번역 플로우:**
1. 원문 전체를 청크로 분할해 등장인물 이름 병렬 추출
2. LLM으로 원작 세계관 추론 → 나무위키 직접 검색으로 문서 후보 수집 + LLM 검증
3. 검증된 작품의 나무위키 캐릭터 페이지에서 LLM으로 프로필 추출 (3단계 병렬)
   - 캐릭터 프로필은 작품별 JSON으로 캐시 → 재실행 시 나무위키 fetch 생략
4. line-by-line 번역, `history_window` 초과 시 롤링 요약으로 system prompt 갱신
5. 매 줄마다 state(`{output}.state.json`) 저장 → 중단 후 재실행 시 history·요약문 완전 복원

**병렬 배치 실행:**

```bash
# 최대 10개 동시 번역 (완료 파일 자동 스킵, 부분 완료 파일 이어쓰기)
bash translate_parallel.sh

# 진행 상황 실시간 모니터링
watch -n 1 bash watch_progress.sh
```

### 복구

소실된 구간의 앞 파일과 뒤 파일, 소실 라인 수를 지정한다.

```bash
python recover.py \
  --config config.yaml \
  --before before_context.txt \
  --after  after_context.txt \
  --lines  50 \
  --output recovered.txt
```

```bash
# 이야기 요약 직접 전달
python recover.py --config config.yaml \
  --before before.txt --after after.txt --lines 30 \
  --summary "주인공은 적진에 잠입해 있으며..." \
  --output recovered.txt

# 이야기 요약 파일로 전달
python recover.py --config config.yaml \
  --before before.txt --after after.txt --lines 30 \
  --summary summary.txt --output recovered.txt

# 설정 확인 (API 호출 없음)
python recover.py --config config.yaml \
  --before before.txt --after after.txt --lines 30 --dry-run
```

**복구 플로우:**
1. `--before` 파일에서 `recovery.before_lines`만큼, `--after` 파일에서 `recovery.after_lines`만큼 슬라이스
2. before 파일 기준으로 번역과 동일한 전처리 파이프라인 실행 (캐시 재사용 가능)
3. after context를 system prompt에 참조 블록으로 삽입
4. before context를 history로 주입 후 `l`줄 line-by-line 생성

## 로그

실행 시 `log_dir`(기본값: 출력 파일 폴더의 `logs/`) 아래에 `YYYYMMDD_HHMMSS.log` 파일이 생성된다.  
콘솔 출력도 로그 파일에 함께 기록되며, `log_level: DEBUG` 설정 시 각 줄 원문·번역문 전문을 포함한다.

```yaml
# config.yaml 로그 관련 옵션
log_dir: "output/logs"       # 기본값: {output_dir}/logs/
log_level: "INFO"            # DEBUG | INFO | WARNING | ERROR
log_translation_step: 100    # INFO 로그 주기 (N줄마다 진행률 기록, 오류는 항상 기록)
```

## 프로젝트 구조

```
noveltool/
├── translate.py               # 번역 CLI
├── recover.py                 # 복구 CLI
├── epub_to_txt.py             # epub → txt 변환 CLI
├── translate_parallel.sh      # 병렬 배치 번역 (최대 10개 동시)
├── translate_batch.sh         # 순차 배치 번역
├── watch_progress.sh          # 진행 상황 모니터링 (watch -n 1 bash watch_progress.sh)
├── config.yaml.example        # 설정 예시 (전체 옵션 및 주석)
├── SPEC.md                    # 상세 설계 문서
└── noveltool/
    ├── config.py              # 설정 로드, 환경변수 치환
    ├── logger.py              # 파일 로거 + stdout TeeStream
    ├── llm_client.py          # OpenAI API 래퍼 (retry 포함)
    ├── history.py             # history 윈도우 관리
    ├── summarizer.py          # 롤링 요약 생성
    ├── prompt.py              # system prompt 빌더
    ├── pipeline.py            # 번역 파이프라인 (state 저장/이어쓰기 포함)
    ├── recover_pipeline.py    # 복구 파이프라인
    └── preprocessor/
        ├── extractor.py       # tiktoken 청크 분할 + 병렬 캐릭터 추출
        ├── identifier.py      # LLM 원작 세계관 추론
        ├── verifier.py        # 나무위키 직접 검색 + LLM 검증
        └── namuwiki.py        # 나무위키 LLM 프로필 추출 (3단계 병렬)
```

## 환경변수

| 변수 | 설명 |
|------|------|
| `OPENAI_API_KEY` | OpenAI API 키 (또는 호환 서버 키) |
