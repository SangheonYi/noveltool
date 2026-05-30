# noveltool 스펙

LLM 기반 웹소설 처리 도구. **번역(translate)**, **epub 변환(epub_to_txt)**, **데이터 복구(recover)**, **시리즈 다운로드(series_download)** 네 가지 태스크를 제공한다.

---

## 1. 프로젝트 구조

```
noveltool/
├── translate.py               # 번역 CLI 진입점
├── recover.py                 # 복구 CLI 진입점
├── epub_to_txt.py             # epub → txt 변환 CLI
├── series_download.py         # 시리즈 다운로드 CLI
├── translate_parallel.sh      # 병렬 배치 번역 (최대 N개 동시, state 기반 이어쓰기)
├── translate_batch.sh         # 순차 배치 번역
├── watch_progress.sh          # 진행 상황 실시간 모니터링
├── config.yaml.example        # 전체 설정 옵션 + 주석
├── noveltool/
│   ├── __init__.py
│   ├── config.py              # 설정 로드, 환경변수 치환, 필수값 검증
│   ├── logger.py              # 파일 로거 초기화 + stdout TeeStream (콘솔→로그 미러링)
│   ├── llm_client.py          # OpenAI ChatCompletion 래퍼 (exponential backoff retry)
│   ├── history.py             # HistoryManager — history 윈도우, 요약 트리거
│   ├── summarizer.py          # 롤링 요약 생성
│   ├── prompt.py              # system prompt 빌더, 캐릭터 섹션/요약 삽입
│   ├── pipeline.py            # 번역 파이프라인 (state 저장/이어쓰기 포함)
│   ├── recover_pipeline.py    # 복구 파이프라인
│   ├── series_downloader.py   # 시리즈 다운로드 모듈
│   └── preprocessor/
│       ├── __init__.py
│       ├── extractor.py       # tiktoken 청크 분할 + 병렬 캐릭터 이름 추출
│       ├── identifier.py      # 나무위키 검색 기반 원작 식별 (빈도 집계)
│       ├── verifier.py        # 나무위키 검색 + LLM 검증
│       └── namuwiki.py        # 나무위키 캐릭터 프로필 추출 (2단계 이름 매핑 + 병렬 프로필)
└── output/
```

---

## 2. 공유 컴포넌트

| 모듈 | 역할 |
|------|------|
| `config.py` | 설정 로드, 환경변수 치환, 필수값 검증, 미설정 모델 자동 감지 |
| `logger.py` | 파일 핸들러 초기화 + `_TeeStream`으로 sys.stdout 래핑 |
| `llm_client.py` | OpenAI ChatCompletion 래퍼, exponential backoff retry, fallback LLM 자동 전환 |
| `history.py` | HistoryManager — history 윈도우, 요약 트리거 |
| `summarizer.py` | 롤링 요약 생성 |
| `prompt.py` | system prompt 빌더, 캐릭터 섹션/요약 삽입, 무의미 desc 필터링 |
| `preprocessor/` | 캐릭터 추출 → 원작 식별 → 검증 → 나무위키 프로필 |

---

## 3. 설정 파일 (`config.yaml`)

```yaml
llm:
  base_url: "https://api.openai.com/v1"
  api_key: "${OPENAI_API_KEY}"        # 환경변수 또는 직접 입력
  model: "gpt-4o"                     # 미설정 시 /models API 첫 번째 모델 자동 사용
  temperature: 0.3
  max_completion_tokens: 4096
  # fallback:                         # primary 실패 시 자동 전환할 fallback LLM (선택)
  #   base_url: "https://generativelanguage.googleapis.com/v1beta/openai/"
  #   api_key: "${GEMINI_API_KEY}"
  #   model: "gemini-2.5-flash"
  #   rpm_limit: 10                   # 분당 요청 제한 (rate limiter 간격 계산에 사용)

translation:
  history_window: 20                  # 번역 history 최대 턴 수. 초과 시 롤링 요약
  summary_overlap: 0.5                # 요약 후 재사용할 history 비율
  source_language: auto               # auto | zh | ja | en
  target_language: ko
  # max_lines: 200                    # 테스트용: N줄만 번역 (전처리는 항상 전문 사용)

recovery:
  before_lines: 20                    # 소실 전 context로 사용할 최대 라인 수
  after_lines: 10                     # 소실 후 forward reference로 사용할 최대 라인 수

preprocessing:
  chunk_tokens: 6000                  # 캐릭터 추출 청크 최대 토큰 수 (tiktoken 기준)
  cache_dir: ".cache"                 # 전처리 캐시 저장 디렉터리

search:
  engine: namuwiki                    # namuwiki | duckduckgo | playwright
  headless: true                      # playwright 전용
  result_count: 5                     # 수집할 검색 결과 수

system_prompt:
  base: |
    ...
  extra_rules:
    - "..."

input: "input/novel.txt"
output: "output/novel_ko.txt"

# work: "스트라이크 더 블러드"           # 나무위키 작품 문서명 직접 지정
                                      # 설정 시 identify/verify 단계 생략
                                      # CLI: --work "작품명"

# log_dir: "output/logs"
# log_level: "INFO"                   # DEBUG | INFO | WARNING | ERROR
# log_translation_step: 100
```

---

## 4. 로거 (`logger.py`)

`logger.setup(log_dir, level)` 호출 시:
1. `{log_dir}/{YYYYMMDD_HHMMSS}.log` 파일 핸들러 등록
2. `sys.stdout`을 `_TeeStream`으로 교체 — `print()` 출력이 콘솔과 로그 파일에 동시 기록됨
3. 로그 포맷: `HH:MM:SS [LEVEL] 메시지` (파일), `HH:MM:SS [출력 ] 라인` (콘솔 미러)

기본 레벨 INFO에서 DEBUG 항목(각 줄 원문·번역 전문, 요약 전문)은 기록되지 않는다.

---

## 5. Task A — 번역 (translate)

### 5-1. 전체 흐름

```
[입력 파일 (원문 txt)]
        │  전처리는 항상 전문(全文) 사용
        ▼  max_lines는 번역 범위만 제한
[Phase 1] 전처리 — 캐릭터/세계관 식별
  1-1. tiktoken 청크 분할 + LLM 병렬 캐릭터 이름 추출
       (저자·역자명, 조직·단체명, 한글 이름 제외)
  1-2. 원작 식별 (우선순위):
       ① --work 직접 지정 → identify/verify 생략
       ② 기존 캐릭터 캐시 대조로 자동 선택 (3개 이상 매칭)
       ③ 나무위키 이름 검색 빈도 집계 + LLM 검증 (폴백)
  1-3. 나무위키 등장인물 페이지에서 캐릭터 프로필 추출 → 작품별 캐시 저장
       (캐시 있으면 나무위키 fetch 생략)
  1-4. 현재 텍스트 등장 인물만 필터링 → 중복 제거 → 무의미 desc 제거
        │
        ▼
[Phase 2] System Prompt 빌드
  --no-cache일 때: 새 프로필 vs state의 프로필 비교
    → 동일: 이어쓰기 유지
    → 변경: 출력·state 삭제 후 재번역
        │
        ▼
[Phase 3] 번역 루프 (line-by-line)
  이어쓰기 체크: {output}.state.json 존재 시 history·요약문 복원 후 재개
  번역 → history 유지 → n 초과 시 롤링 요약 → 반복
  매 줄마다 state 파일 원자적 저장
  완료 시 state 파일 자동 삭제
        │
        ▼
[출력 파일 (번역 txt)]
```

### 5-2. Messages 구조

```
System: [base rules] + [캐릭터 프로필?] + [extra rules?] + [이야기 요약?]
User:   {원문 한 줄}
Asst:   {번역된 한국어 한 줄}
...
```

### 5-3. 롤링 요약

- history 턴 수 > `history_window` 시 트리거
- 첫 번째: 전체 n쌍으로 요약
- 이후: 이전 요약 + 후반 n/2쌍으로 갱신
- 요약 후 messages = `[system]` + 후반 n/2쌍

### 5-4. 전처리 캐시 구조

```
.cache/
├── {article}_characters.json      # 나무위키 캐릭터 프로필 (작품별)
│   [{original, korean, desc, work}, ...]
└── _preprocess_{hash}.json        # 원작 식별 결과 캐시 (입력 파일별)
    {"articles": ["작품명"], "raw_chars": ["캐릭터명", ...]}
```

`_preprocess_{hash}.json`:
- 키: `md5(abs_input_path + ":" + total_lines)[:12]`
- 입력 파일이 바뀌면 자동 무효화
- `articles`: 검증된 나무위키 문서명 목록
- `raw_chars`: 원문에서 추출한 캐릭터 이름 목록 (필터링에 재사용)

`--no-cache` 시 두 종류 캐시 모두 삭제.

### 5-5. 이어쓰기 (state 파일)

**state 파일 경로:** `{output}.state.json`

```json
{
  "done_lines": 3456,
  "prior_summary": "...",
  "system_prompt": "...",
  "history_pairs": [["원문1", "번역1"], ...]
}
```

**재개 우선순위:**
1. state 있음 → history·요약문 완전 복원 후 `done_lines+1`번째 줄부터 재개
2. state 없고 출력 파일 있음 → 줄 수만 맞춰 이어쓰기 (history 미복원)
3. 둘 다 없음 → 처음부터 시작

**`--no-cache` 시 이어쓰기 판단:**
캐시 재구축 완료 후 새 system_prompt를 state의 system_prompt와 비교한다.
- 동일 → 이어쓰기 유지 (캐릭터 프로필 변화 없음)
- 불일치 → 출력·state 삭제, 1줄부터 재번역 (프로필 갱신됨)

state 파일은 매 줄 번역 직후 `{state}.tmp` → `os.replace()` 원자적 갱신.
번역 완료 시 자동 삭제.

### 5-6. 나무위키 캐릭터 프로필 추출 (`namuwiki.py`)

```
fetch_characters(article, cache_dir, llm, seed_names)
  ↓ 캐시 hit → 로드 반환
  ↓ 캐시 miss
  fetch {article}/등장인물 페이지 (requests → Playwright 폴백)
  ↓
  _llm_list_names() — 2단계 이름 목록 추출:
    1단계: seed_names(원어) → 나무위키 한국어 이름 매핑
           LLM에게 "이 원어 이름의 한국어 표기를 찾아라" (원어 추측 없음)
           응답 검증: original이 seed_names에 없으면 재매핑 시도
    2단계: 나머지 인물 전체 추출 (이미 매핑된 인물 제외)
  ↓
  각 캐릭터에 대해 (병렬):
    _fetch_character_page():
      1순위: {article}/등장인물/{korean} (작품 하위 경로)
      2순위: {korean} 직접 페이지 (본문에 작품명 포함 시만 사용)
              → 타 작품 동명 페이지 오매칭 방지
      실패 시: 메인 문서에서 해당 인물 구간 추출
               (_section_for_char: 이름 등장~다음 인물 이름 직전까지)
    _llm_profile_single(): 페이지 텍스트에서 desc 추출
  ↓
  캐시 저장 ({article}_characters.json)
```

`seed_names`는 원어(일본어/중국어/영어) 이름만 전달. 한글 이름은 제외 (`_original_lang_seeds()`).

### 5-7. 캐릭터 필터링 및 중복 제거 (`filter_by_raw_chars`)

1. **매칭**: raw_chars의 각 이름과 캐시의 `original`·`korean` 양방향 substring 비교
2. **1차 dedup**: 같은 `korean` 이름 → 공백 제거 기준 `original`이 가장 긴 항목(full name) 유지
3. **2차 dedup**: `original`이 다른 항목의 `original` substring이고 `korean`도 substring 관계면 fragment로 제거

### 5-8. System Prompt 캐릭터 섹션 (`prompt.py`)

desc에 아래 패턴이 포함되면 무의미 desc로 판단, 해당 항목은 이름만 출력:
- "이름만 언급", "프로필을 추출할 수 없", "상세 정보가 부족", "캐릭터 정보가 아닌" 등

무의미 desc 항목 예시:
```
- リディアーヌ・ディディエ (리디안느 디디에)        ← desc 없이 이름만
- 暁古城 (아카츠키 코죠): 16세 남성으로, ...       ← 유의미한 desc 포함
```

### 5-9. CLI

```bash
python translate.py --config config.yaml
python translate.py --config config.yaml --work "스트라이크 더 블러드"
python translate.py --config config.yaml --preprocess-only
python translate.py --config config.yaml --no-cache
python translate.py --config config.yaml --max-lines 200
python translate.py --config config.yaml --dry-run
python translate.py --input novel.txt --output novel_ko.txt --config config.yaml
```

---

## 6. Task B — epub 변환 (epub_to_txt)

### 6-1. 변환 과정

1. epub(zip) 내부 `META-INF/container.xml` → OPF 파일 경로 파악
2. OPF spine 순서대로 HTML/XHTML 파일 목록 추출
3. 각 챕터 HTML 파싱:
   - `<script>`, `<style>`, `<nav>`, `<head>` 제거
   - `<ruby>` 태그: `<rt>`/`<rp>`(후리가나) 제거, 기본 한자만 유지
   - 블록 태그(`<p>`, `<div>`, `<h1~6>` 등)에서 줄바꿈 생성
4. 빈 줄 2개 이상 압축, 챕터 간 빈 줄 2개 (또는 지정 구분자)

### 6-2. CLI

```bash
python epub_to_txt.py novel.epub
python epub_to_txt.py novel.epub output/novel.txt
python epub_to_txt.py novel.epub --chapter-sep "==="
```

---

## 7. Task C — 데이터 복구 (recover)

### 7-1. 개요

소실된 웹소설 데이터를 앞뒤 문맥과 LLM을 이용해 line-by-line으로 복구한다.
번역 태스크와 동일한 캐릭터 전처리 파이프라인을 재사용해 일관된 system prompt를 구성한다.

### 7-2. 입력

| 항목 | 설명 |
|------|------|
| `--before` | 소실 전 컨텍스트 파일. `recovery.before_lines`만큼 뒤에서 slice |
| `--after` | 소실 후 컨텍스트 파일. `recovery.after_lines`만큼 앞에서 slice |
| `--lines` | 복구할 라인 수 |
| `--summary` | (선택) 지난 이야기 요약 문자열 또는 파일 경로 |
| `--output` | 복구된 줄을 저장할 출력 파일 |

### 7-3. System Prompt 구성

```
[base rules]
[복구 태스크 안내] — 소실 분량, 한 줄씩 복구 지시
[후속 문맥 참고] — after context (messages에는 포함 안 함)
[등장인물 프로필] — 검증된 캐릭터가 있을 때만
[이야기 요약] — --summary 제공 시
```

### 7-4. 복구 플로우

```
messages = [system_prompt]
before context n줄 → (User: "계속", Asst: {line}) × n쌍으로 주입
for i in range(missing_lines):
  messages.append(User: "계속")
  recovered = llm.chat(messages)
  messages.append(Asst: recovered)
  output.write(recovered)
```

### 7-5. CLI

```bash
python recover.py \
  --config config.yaml \
  --before before_context.txt \
  --after  after_context.txt \
  --lines  50 \
  --output recovered.txt
```

---

## 8. Task D — 시리즈 다운로드 (series_download)

시리즈 페이지에서 다운로드 링크를 수집하고 aria2로 병렬 다운로드한다.

### 8-1. 플로우

```
URL → cloudscraper fetch → BeautifulSoup 파싱
  → 제목별 링크 수집 (호스트·확장자 필터)
  → aiohttp 병렬 HEAD 요청으로 dead link 제거
  → downloads.json 저장
  → aria2c -x16 -s16으로 제목별 폴더에 다운로드
```

### 8-2. CLI

```bash
python series_download.py --url URL
python series_download.py --url URL --hosts mega pixeldrain --ext .epub .pdf
python series_download.py --url URL --no-download --output links.json
python series_download.py --url URL --no-validate
python series_download.py --url URL --out-dir ./downloads
```

---

## 9. 오류 처리

| 상황 | 처리 방식 |
|------|-----------|
| API rate limit | exponential backoff 후 재시도 (최대 3회) |
| primary LLM 실패 (재시도 소진) | fallback LLM으로 자동 전환 (설정 시); fallback 없으면 예외 |
| 번역 API 오류 | 해당 줄 skip + 오류 로그, 원문 그대로 출력 |
| 복구 API 오류 | 해당 줄 skip + 오류 로그, 빈 줄 출력 |
| 설정 파일 누락 | 즉시 종료 + 사용법 안내 |
| 입력 파일 없음 | 즉시 종료 |
| 출력 경로 없음 | 자동 디렉터리 생성 |
| 나무위키 캐릭터 페이지 없음 | 메인 문서 구간 추출로 폴백 |
| 나무위키 타 작품 동명 페이지 | 작품명 포함 여부 검증 후 거부, 메인 문서 폴백 |
| 나무위키 접근 실패 | requests 실패 시 Playwright 폴백, 둘 다 실패 시 경고 후 진행 |
| 원작 자동 식별 실패 | 캐릭터 없이 번역 진행 (--work 직접 지정 권장) |
| state 파일 손상 | 파싱 실패 시 무시하고 처음부터 시작 |
| epub spine 파일 누락 | 해당 챕터 경고 후 건너뜀 |
| --no-cache 후 프로필 변경 | 출력·state 삭제, 1줄부터 재번역 |
| --no-cache 후 프로필 동일 | 이어쓰기 유지 |

---

## 10. 비기능 요구사항

- Python 3.14+
- 의존성: `openai`, `pyyaml`, `tenacity`, `requests`, `beautifulsoup4`, `playwright`, `tiktoken`
- series_download 추가 의존성: `cloudscraper`, `aiohttp`, `tqdm`
- 출력 파일은 줄 단위 즉시 flush (crash-safe)
- state 파일 원자적 갱신 (tmp → replace) — 중단 시 손상 없음
- 전처리 캐시로 재실행 비용 절감 (번역/복구 공유)
- 진행 상황 콘솔 출력: `[번역] 42/300`, `[복구] 12/50`
- 로그 파일 자동 생성: `{log_dir}/{YYYYMMDD_HHMMSS}.log` (콘솔 출력 동시 기록)
