# noveltool 스펙

LLM 기반 웹소설 처리 도구. **번역(translate)**과 **데이터 복구(recover)** 두 가지 태스크를 제공한다.

---

## 1. 프로젝트 구조

```
noveltool/
├── translate.py               # 번역 CLI 진입점
├── recover.py                 # 복구 CLI 진입점
├── config.yaml.example
├── noveltool/
│   ├── __init__.py
│   ├── config.py              # 설정 로드 및 환경변수 치환
│   ├── llm_client.py          # OpenAI API 래퍼 (retry 포함)
│   ├── history.py             # history 윈도우 관리
│   ├── summarizer.py          # 롤링 요약 생성
│   ├── prompt.py              # system prompt 빌더
│   ├── pipeline.py            # 번역 파이프라인
│   ├── recover_pipeline.py    # 복구 파이프라인
│   └── preprocessor/
│       ├── __init__.py
│       ├── extractor.py       # tiktoken 청크 분할 + 병렬 캐릭터 추출
│       ├── identifier.py      # LLM 원작 세계관 추론
│       ├── verifier.py        # Playwright 구글 검색 + LLM 검증
│       └── namuwiki.py        # 나무위키 크롤링 + JSON 캐시
└── output/
```

---

## 2. 공유 컴포넌트

두 태스크가 동일하게 사용하는 모듈:

| 모듈 | 역할 |
|------|------|
| `config.py` | 설정 로드, 환경변수 치환, 필수값 검증 |
| `llm_client.py` | OpenAI ChatCompletion 래퍼, exponential backoff retry |
| `history.py` | HistoryManager — history 윈도우, 요약 트리거 |
| `summarizer.py` | 롤링 요약 생성 |
| `prompt.py` | system prompt 빌더, 캐릭터 섹션/요약 삽입 |
| `preprocessor/` | 캐릭터 추출 → 세계관 추론 → 검증 → 나무위키 크롤링 |

---

## 3. 설정 파일 (`config.yaml`)

```yaml
llm:
  base_url: "https://api.openai.com/v1"
  api_key: "${OPENAI_API_KEY}"
  model: "gpt-4o"
  temperature: 0.3
  max_completion_tokens: 4096

translation:
  history_window: 20          # n: 번역 history 최대 턴 수
  summary_overlap: 0.5        # 요약 후 재사용할 history 비율

recovery:
  before_lines: 20            # n: 소실 전 context로 사용할 최대 라인 수
  after_lines: 10             # m: 소실 후 forward reference로 사용할 최대 라인 수

preprocessing:
  chunk_tokens: 6000
  cache_dir: ".cache"

search:
  engine: playwright
  headless: true
  result_count: 5

system_prompt:
  base: |
    당신은 전문 웹소설 번역가입니다. 아래 규칙을 반드시 따르세요.

    [출력 형식]
    - 번역 결과 한 줄만 출력하세요. 설명, 주석, 따옴표, 원문 인용을 절대 추가하지 마세요.

    [충실도 — 최우선 원칙]
    - 원문의 내용을 임의로 생략하거나 추가하지 마세요.
    - 원문에 없는 묘사, 감정, 설명을 번역문에 덧붙이지 마세요.
    - 원문에 있는 내용을 번역문에서 빠뜨리지 마세요.

    [문체 및 어조]
    - 한국 웹소설의 자연스러운 어조와 뉘앙스를 사용하세요.
    - 중국어 사자성어, 일본어 관용어구 등은 동등한 의미의 한국어 표현이나 쉬운 서술어로 번역하세요.

    [등장인물 말투]
    - 각 인물의 성격과 말투를 임의로 바꾸지 마세요. 원문의 느낌을 그대로 살려야 합니다.
    - 이전 번역 history를 참조하여 각 인물의 말투를 일관되게 유지하세요.

    [이름 표기]
    - 등장인물 이름은 아래 지정된 표기를 일관되게 사용하세요.
  extra_rules: []

input: "input/novel.txt"      # 번역 태스크 전용
output: "output/novel_ko.txt" # 번역 태스크 전용
```

---

## 4. Task A — 번역 (translate)

### 4-1. 전체 흐름

```
[입력 파일 (원문 txt)]
        │
        ▼
[Phase 1] 전처리 — 캐릭터/세계관 식별
  1-1. tiktoken 청크 분할
  1-2. LLM 병렬 캐릭터 추출
  1-3. LLM 원작 세계관 추론
  1-4. Playwright 구글 검색 + LLM 검증
  1-5. 나무위키 캐릭터 프로필 크롤링 (검증된 작품만)
        │
        ▼
[Phase 2] System Prompt 빌드
  검증된 캐릭터 프로필 주입 (없으면 캐릭터 섹션 생략)
        │
        ▼
[Phase 3] 번역 루프 (line-by-line)
  번역 → history 유지 → n 초과 시 롤링 요약 → 반복
        │
        ▼
[출력 파일 (번역 txt)]
```

### 4-2. Messages 구조

```
System: [base rules] + [캐릭터 프로필?] + [extra rules?] + [이야기 요약?]
User:   {원문 한 줄}
Asst:   {번역된 한국어 한 줄}
User:   {원문 한 줄}
Asst:   {번역된 한국어 한 줄}
...
```

### 4-3. 롤링 요약

- history 턴 수 > `history_window` 시 트리거
- 첫 번째: 전체 n쌍으로 요약
- 이후: 이전 요약 + 후반 n/2쌍으로 갱신
- 요약 후 messages = `[system]` + 후반 n/2쌍

### 4-4. CLI

```bash
python translate.py --config config.yaml
python translate.py --config config.yaml --preprocess-only
python translate.py --config config.yaml --no-cache
python translate.py --config config.yaml --dry-run
python translate.py --input novel.txt --output novel_ko.txt --config config.yaml
```

---

## 5. Task B — 데이터 복구 (recover)

### 5-1. 개요

소실된 웹소설 데이터를 앞뒤 문맥과 LLM을 이용해 line-by-line으로 복구한다.  
번역 태스크와 동일한 캐릭터 전처리 파이프라인을 재사용해 일관된 system prompt를 구성한다.

### 5-2. 입력

| 항목 | 설명 |
|------|------|
| `--before` | 소실 전 컨텍스트 파일 (Korean txt). `recovery.before_lines`만큼 뒤에서 slice |
| `--after` | 소실 후 컨텍스트 파일 (Korean txt). `recovery.after_lines`만큼 앞에서 slice |
| `--lines` | 복구할 라인 수 `l` |
| `--summary` | (선택) 지난 이야기 요약 문자열 또는 파일 경로 |
| `--output` | 복구된 `l`줄을 저장할 출력 파일 |

### 5-3. 전처리

번역 태스크의 전처리 파이프라인(캐릭터 추출 → 세계관 추론 → 검증 → 나무위키)을  
`--before` 파일을 입력으로 동일하게 실행한다. 캐시가 있으면 크롤링 생략.

### 5-4. System Prompt 구성

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

### 5-5. 복구 플로우 (line-by-line)

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

### 5-6. Messages 구조 예시

```json
[
  {"role": "system",    "content": "...[base rules]\n\n[복구 안내]\n약 50줄 소실...\n\n[후속 문맥 참고]\n장웨이는 칼을 뽑았다..."},
  {"role": "user",      "content": "계속"},
  {"role": "assistant", "content": "그날 밤, 적막한 골목에는 바람만이 불었다."},
  {"role": "user",      "content": "계속"},
  {"role": "assistant", "content": "장웨이는 숨을 죽이며 그림자 속에 몸을 숨겼다."},
  {"role": "user",      "content": "계속"},
  {"role": "assistant", "content": "{recovered_line_1}"},
  {"role": "user",      "content": "계속"},
  {"role": "assistant", "content": "{recovered_line_2}"}
]
```

### 5-7. CLI

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

# 드라이런
python recover.py --config config.yaml \
  --before before.txt --after after.txt --lines 30 --dry-run
```

---

## 6. 오류 처리

| 상황 | 처리 방식 |
|------|-----------|
| API rate limit | exponential backoff 후 재시도 (최대 3회) |
| 번역 API 오류 | 해당 줄 skip + 경고 로그, 원문 그대로 출력 |
| 복구 API 오류 | 해당 줄 skip + 경고 로그, 빈 줄 출력 |
| 설정 파일 누락 | 즉시 종료 + 사용법 안내 |
| 입력 파일 없음 | 즉시 종료 |
| 출력 경로 없음 | 자동 디렉터리 생성 |
| 구글 검색 실패 (봇 차단 등) | 해당 후보 검증 실패 처리 + 경고 로그 |
| 나무위키 접근 실패 | 해당 작품 캐릭터 없이 진행 + 경고 로그 |
| LLM 세계관 추론 결과 없음 | 캐릭터 섹션 생략하고 진행 |

---

## 7. 비기능 요구사항

- Python 3.14+
- 의존성: `openai`, `pyyaml`, `tenacity`, `requests`, `beautifulsoup4`, `playwright`, `tiktoken`
- 출력 파일은 줄 단위 즉시 flush (crash-safe)
- 전처리 캐시로 재실행 비용 절감 (번역/복구 공유)
- 진행 상황 콘솔 출력: `[번역] 42/300`, `[복구] 12/50`
