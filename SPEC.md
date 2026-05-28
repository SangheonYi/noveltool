# noveltool 스펙

LLM 기반 웹소설 처리 도구. **번역(translate)**, **epub 변환(epub_to_txt)**, **데이터 복구(recover)** 세 가지 태스크를 제공한다.

---

## 1. 프로젝트 구조

```
noveltool/
├── translate.py               # 번역 CLI 진입점
├── recover.py                 # 복구 CLI 진입점
├── epub_to_txt.py             # epub → txt 변환 CLI
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
│   └── preprocessor/
│       ├── __init__.py
│       ├── extractor.py       # tiktoken 청크 분할 + 병렬 캐릭터 이름 추출
│       ├── identifier.py      # LLM 원작 세계관 추론 (catch-all 후보 필터링)
│       ├── verifier.py        # 나무위키 직접 검색 + LLM 검증
│       └── namuwiki.py        # 나무위키 LLM 프로필 추출 (3단계 병렬)
└── output/
```

---

## 2. 공유 컴포넌트

두 태스크가 동일하게 사용하는 모듈:

| 모듈 | 역할 |
|------|------|
| `config.py` | 설정 로드, 환경변수 치환, 필수값 검증, 미설정 모델 자동 감지 |
| `logger.py` | 파일 핸들러 초기화 + `_TeeStream`으로 sys.stdout 래핑 |
| `llm_client.py` | OpenAI ChatCompletion 래퍼, exponential backoff retry |
| `history.py` | HistoryManager — history 윈도우, 요약 트리거 |
| `summarizer.py` | 롤링 요약 생성 |
| `prompt.py` | system prompt 빌더, 캐릭터 섹션/요약 삽입 |
| `preprocessor/` | 캐릭터 추출 → 세계관 추론 → 검증 → 나무위키 프로필 |

---

## 3. 설정 파일 (`config.yaml`)

```yaml
llm:
  base_url: "https://api.openai.com/v1"
  api_key: "${OPENAI_API_KEY}"        # 환경변수 또는 직접 입력
  model: "gpt-4o"                     # 미설정 시 /models API 첫 번째 모델 자동 사용
  temperature: 0.3
  max_completion_tokens: 4096

translation:
  history_window: 20                  # 번역 history 최대 턴 수. 초과 시 롤링 요약
  summary_overlap: 0.5                # 요약 후 재사용할 history 비율
  source_language: auto               # auto | zh | ja | en
  target_language: ko
  # max_lines: 200                    # 테스트용: N줄만 번역 (미설정 시 전체)

recovery:
  before_lines: 20                    # 소실 전 context로 사용할 최대 라인 수
  after_lines: 10                     # 소실 후 forward reference로 사용할 최대 라인 수

preprocessing:
  chunk_tokens: 6000                  # 캐릭터 추출 청크 최대 토큰 수 (tiktoken 기준)
  cache_dir: ".cache"                 # 전처리 캐시 디렉터리
                                      # {article}_characters.json: 나무위키 캐릭터 프로필 캐시

search:
  engine: namuwiki                    # namuwiki (나무위키 직접 검색, 기본값, 가장 안정적)
                                      # duckduckgo (requests 기반, 봇 차단 가능)
                                      # playwright (헤드리스 Chrome, Google — 봇 차단 가능)
  headless: true                      # playwright 전용. false 시 브라우저 창 표시
  result_count: 5                     # 수집할 검색 결과 수

system_prompt:
  base: |                             # 번역/복구 공통 system prompt 기본 규칙
    ...
  extra_rules:                        # 소설별 추가 규칙 목록 (선택)
    - "..."

input: "input/novel.txt"              # 번역 태스크 입력 파일
output: "output/novel_ko.txt"         # 번역 태스크 출력 파일

# log_dir: "output/logs"              # 로그 파일 디렉터리 (기본값: {output_dir}/logs/)
# log_level: "INFO"                   # DEBUG | INFO | WARNING | ERROR (기본값: INFO)
# log_translation_step: 100           # 번역 INFO 로그 주기 (기본값: 100, 오류는 항상)
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
        │
        ▼
[Phase 1] 전처리 — 캐릭터/세계관 식별
  1-1. tiktoken 청크 분할 + LLM 병렬 캐릭터 이름 추출
  1-2. LLM 원작 세계관 추론 (catch-all/Unknown 후보 자동 제외)
  1-3. 나무위키 직접 검색으로 문서 후보 수집 + LLM 검증
  나무위키 캐릭터 프로필 추출 (3단계, 병렬):
    Step 1: 등장인물 문서에서 LLM으로 이름 목록 추출
    Step 2: 각 캐릭터 개별 나무위키 페이지 fetch
    Step 3: LLM으로 캐릭터별 상세 프로필 생성
    → 작품별 JSON 캐시 저장 (재실행 시 나무위키 fetch 생략)
        │
        ▼
[Phase 2] System Prompt 빌드
  검증된 캐릭터 프로필 주입 (없으면 캐릭터 섹션 생략)
        │
        ▼
[Phase 3] 번역 루프 (line-by-line)
  이어쓰기 체크: {output}.state.json 존재 시 history·요약문 복원 후 재개
  번역 → history 유지 → n 초과 시 롤링 요약 → 반복
  매 줄마다 state 파일 원자적 저장 (중단 후 재실행 대비)
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
└── {article}_characters.json      # 나무위키 캐릭터 프로필 (작품별)
    [{original, korean, desc, work}, ...]
```

캐릭터 프로필만 캐싱한다. 추출/추론/검색(1~3단계)은 매 실행마다 진행한다.  
`--no-cache` 플래그 지정 시 `_characters.json` 파일 전부 삭제 후 재실행.

### 5-5. 이어쓰기 (state 파일)

번역 중 프로세스가 중단되더라도 context를 유지하며 이어쓰기가 가능하다.

**state 파일 경로:** `{output}.state.json`

**저장 내용:**
```json
{
  "done_lines": 3456,
  "prior_summary": "...",
  "system_prompt": "...",
  "history_pairs": [["원문1", "번역1"], ...]
}
```

**재개 우선순위:**
1. state 파일 있음 → history·요약문 완전 복원 후 `done_lines+1`번째 줄부터 재개
2. state 없고 출력 파일 있음 → 줄 수만 맞춰 이어쓰기 (history 미복원)
3. 둘 다 없음 → 처음부터 시작

state 파일은 매 줄 번역 직후 `{state}.tmp` → `os.replace()` 원자적 갱신.  
번역 완료 시 자동 삭제.

### 5-6. 나무위키 검색 (`verifier.py`)

`search.engine: namuwiki` (기본값):
- `https://namu.wiki/Search?q={작품명} 등장인물` 직접 요청
- 결과에서 `/w/` 링크만 추출 (틀·분류·채널·갤러리 필터링)
- LLM이 후보 목록에서 원작에 해당하는 문서를 선택

`duckduckgo` / `playwright` 엔진도 지원하나 봇 차단 가능성 있음.

### 5-7. CLI

```bash
python translate.py --config config.yaml
python translate.py --config config.yaml --preprocess-only   # 전처리만 실행
python translate.py --config config.yaml --no-cache          # 캐시 무시 재실행
python translate.py --config config.yaml --max-lines 200     # 앞 N줄만 번역
python translate.py --config config.yaml --dry-run           # API 호출 없이 설정 확인
python translate.py --input novel.txt --output novel_ko.txt --config config.yaml
```

---

## 6. Task B — epub 변환 (epub_to_txt)

### 6-1. 개요

epub 파일을 plain text로 변환한다. 번역 파이프라인의 입력 파일로 바로 사용할 수 있도록 후리가나를 제거하고 HTML을 정제한다.

### 6-2. 변환 과정

1. epub(zip) 내부 `META-INF/container.xml` → OPF 파일 경로 파악
2. OPF spine 순서대로 HTML/XHTML 파일 목록 추출
3. 각 챕터 HTML 파싱:
   - `<script>`, `<style>`, `<nav>`, `<head>` 제거
   - `<ruby>` 태그: `<rt>`/`<rp>`(후리가나) 제거, 기본 한자만 유지
   - 블록 태그(`<p>`, `<div>`, `<h1~6>` 등)에서 줄바꿈 생성
4. 빈 줄 2개 이상 압축, 챕터 간 빈 줄 2개 (또는 지정 구분자)

### 6-3. CLI

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
| `--before` | 소실 전 컨텍스트 파일 (Korean txt). `recovery.before_lines`만큼 뒤에서 slice |
| `--after` | 소실 후 컨텍스트 파일 (Korean txt). `recovery.after_lines`만큼 앞에서 slice |
| `--lines` | 복구할 라인 수 `l` |
| `--summary` | (선택) 지난 이야기 요약 문자열 또는 파일 경로 |
| `--output` | 복구된 `l`줄을 저장할 출력 파일 |

### 7-3. 전처리

번역 태스크의 전처리 파이프라인(캐릭터 추출 → 세계관 추론 → 검증 → 나무위키)을  
`--before` 파일을 입력으로 동일하게 실행한다. 캐시가 있으면 건너뜀.

### 7-4. System Prompt 구성

```
[base rules — 번역과 동일]

[복구 태스크 안내]
이 소설의 일부 데이터가 소실되었습니다.
소실된 분량은 약 {l}줄입니다.
앞뒤 문맥을 참고하여 소실된 내용을 한 줄씩 자연스럽게 복구해 주세요.
한 번에 반드시 한 줄만 출력하세요.

[후속 문맥 참고]     ← after context m줄을 블록으로 삽입 (messages에는 포함 안 함)
{after_context}

[등장인물 프로필]    ← 검증된 캐릭터가 있을 때만
...

[이야기 요약]        ← --summary 제공 시
{summary}
```

after context는 messages history에 넣지 않고 system prompt에 참조 블록으로만 삽입한다.  
LLM이 after를 미리 알고 있어 복구 내용이 자연스럽게 수렴하도록 유도한다.

### 7-5. 복구 플로우 (line-by-line)

```
초기화:
  messages = [system_prompt]
  before 컨텍스트 n줄을 history로 주입
    → (User: "계속", Assistant: {before_line}) × n쌍

for i in range(l):
  messages.append(User: "계속")
  recovered_line = llm_client.chat(messages)
  messages.append(Assistant: recovered_line)
  output에 즉시 write + flush
  print(f"[복구] {i+1}/{l}: {recovered_line[:40]}")
```

### 7-6. CLI

```bash
# 기본 실행
python recover.py \
  --config config.yaml \
  --before before_context.txt \
  --after  after_context.txt \
  --lines  50 \
  --output recovered.txt

# 요약 직접 전달
python recover.py --config config.yaml \
  --before before.txt --after after.txt --lines 30 \
  --summary "주인공 장웨이는 적진에 잠입해 있으며..." \
  --output recovered.txt

# 요약 파일로 전달
python recover.py --config config.yaml \
  --before before.txt --after after.txt --lines 30 \
  --summary summary.txt \
  --output recovered.txt

# 드라이런 (API 호출 없이 설정 확인)
python recover.py --config config.yaml \
  --before before.txt --after after.txt --lines 30 --dry-run
```

---

## 8. 오류 처리

| 상황 | 처리 방식 |
|------|-----------|
| API rate limit | exponential backoff 후 재시도 (최대 3회) |
| 번역 API 오류 | 해당 줄 skip + 오류 로그, 원문 그대로 출력 |
| 복구 API 오류 | 해당 줄 skip + 오류 로그, 빈 줄 출력 |
| 설정 파일 누락 | 즉시 종료 + 사용법 안내 |
| 입력 파일 없음 | 즉시 종료 |
| 출력 경로 없음 | 자동 디렉터리 생성 |
| 나무위키 검색 실패 | 해당 후보 검증 실패 처리 + 경고 로그 |
| 나무위키 접근 실패 | 해당 작품 캐릭터 없이 진행 + 경고 로그 |
| LLM 세계관 추론 결과 없음 | 캐릭터 섹션 생략하고 진행 |
| catch-all 세계관 후보 | 검색 없이 필터링 (Unknown/Other/기타 패턴 자동 감지) |
| state 파일 손상 | 파싱 실패 시 무시하고 처음부터 시작 |
| epub spine 파일 누락 | 해당 챕터 경고 후 건너뜀 |

---

## 9. 비기능 요구사항

- Python 3.14+
- 의존성: `openai`, `pyyaml`, `tenacity`, `requests`, `beautifulsoup4`, `playwright`, `tiktoken`
- 출력 파일은 줄 단위 즉시 flush (crash-safe)
- state 파일 원자적 갱신 (tmp → replace) — 중단 시 손상 없음
- 전처리 캐시로 재실행 비용 절감 (번역/복구 공유)
- 진행 상황 콘솔 출력: `[번역] 42/300`, `[복구] 12/50`
- 로그 파일 자동 생성: `{log_dir}/{YYYYMMDD_HHMMSS}.log` (콘솔 출력 동시 기록)
