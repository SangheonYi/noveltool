# noveltool

LLM 기반 웹소설 처리 도구. 번역과 소실 데이터 복구 두 가지 기능을 제공한다.

## 기능

| 명령 | 설명 |
|------|------|
| `translate` | 중국어 / 일본어 / 영어 원문을 한국어로 line-by-line 번역 |
| `recover` | 소실된 웹소설 구간을 앞뒤 문맥 기반으로 line-by-line 복구 |

**공통 특징:**
- 나무위키 자동 크롤링으로 등장인물 프로필을 system prompt에 주입
- 롤링 요약으로 장편 소설에서도 맥락 유지
- 전처리 결과 JSON 캐시로 재실행 비용 절감

## 설치

Python 3.14+, [uv](https://github.com/astral-sh/uv) 필요.

```bash
git clone git@github.com:SangheonYi/noveltool.git
cd noveltool
uv venv --python 3.14
source .venv/bin/activate
uv pip install openai pyyaml tenacity requests beautifulsoup4 playwright tiktoken
playwright install chromium
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
  model: "gpt-4o"
```

전체 옵션은 [config.yaml.example](config.yaml.example) 참고.

## 사용법

### 번역

```bash
python translate.py --config config.yaml
```

```bash
# 파일 직접 지정
python translate.py --input input/novel.txt --output output/novel_ko.txt --config config.yaml

# 전처리 결과만 확인 (번역 없이 캐릭터/세계관 식별)
python translate.py --config config.yaml --preprocess-only

# 전처리 캐시 무시하고 재실행
python translate.py --config config.yaml --no-cache

# 설정 확인 (API 호출 없음)
python translate.py --config config.yaml --dry-run
```

**번역 플로우:**
1. 원문 전체를 청크로 분할해 등장인물 이름 병렬 추출
2. LLM으로 원작 세계관 추론 → Playwright 구글 검색으로 검증
3. 검증된 작품의 나무위키 등장인물 프로필 크롤링
4. line-by-line 번역, `history_window` 초과 시 롤링 요약으로 system prompt 갱신

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

## 프로젝트 구조

```
noveltool/
├── translate.py               # 번역 CLI
├── recover.py                 # 복구 CLI
├── config.yaml.example        # 설정 예시
├── SPEC.md                    # 상세 설계 문서
└── noveltool/
    ├── config.py
    ├── llm_client.py
    ├── history.py
    ├── summarizer.py
    ├── prompt.py
    ├── pipeline.py
    ├── recover_pipeline.py
    └── preprocessor/
        ├── extractor.py       # 캐릭터 추출
        ├── identifier.py      # 세계관 추론
        ├── verifier.py        # 구글 검색 검증
        └── namuwiki.py        # 나무위키 크롤링
```

## 환경변수

| 변수 | 설명 |
|------|------|
| `OPENAI_API_KEY` | OpenAI API 키 (또는 호환 서버 키) |
