# 게임 오픈채팅 인사이트 리포트 시스템 — 설계서 (design.md)

## 1. 배경·목표·비목표

### 1.1 배경

카카오톡 오픈채팅에는 자사 게임과 관련된 여론·버그 제보·밸런스 논의가 실시간으로 흘러간다. 운영자가 개발한 게임의 건강한 의사결정을 위해, **다수 방의 대화를 구조화해 주기적으로 요약·분석**할 필요가 있다. 동시에 **매주 발행하는 업데이트 노트**와 **다음 주 예정** 같은 공식 자료를 넣으면, "무엇이 나갔는데 어떻게 반응했는지", "아직 말이 많은데 다음 패치에 안 넣은 것" 같은 **대조 분석**이 가능해진다.

카카오는 오픈채팅 메시지를 위한 **공개 실시간 API**를 제공하지 않으므로, 본 설계는 **Windows PC 카카오톡 클라이언트에서 사용자가 연 채팅 창에 화면으로 표시되는 텍스트**를 전제로 한다.

본 시스템은 단일 PC 규모를 전제로 하며, 벡터 검색 기반 RAG는 도입하지 않는다. 대신 **방·주기 단위로 LLM이 직접 분석해 구조화 요약을 누적**하고, 누적된 요약 위에서 통계·리포트를 합성한다. 분석 주기는 시간·일·주 등 운영자가 설정에 따라 바꿀 수 있다.

### 1.2 목표

- **수집**: **20개 이상** 오픈채팅방을 PC에서 **항상 열어둔 상태**에서, **10분** 주기로 표시 영역 텍스트를 수집·SQLite에 저장한다. 방 제목은 설정값과 **완전 일치**(안 읽음 수 `(N)` 접미사는 매칭 전 제거).
- **주기별 분석**: 설정된 분석 주기마다(시간·일·주 등) 방·기간 단위로 LLM(`.env`에서 지정한 모델)이 채팅을 읽고 **구조화된 주기별 요약(JSON)** 을 생성한다.
- **누적·통계**: 주기별 요약을 누적하여 **주제·태그 빈도 추이**와 **패치/업데이트 반응**만 집계한다. 소수 닉네임이 과대 대표되지 않도록 주제별 **고유 참여 닉 수(`distinct_nicks`)** 를 함께 산출한다.
- **컨텍스트 주입**: 업데이트 노트·예정 일정 등 **운영자 제공 문서**를 분석·리포트 파이프라인에 넣어 품질을 높인다.
- **생성**: OpenAI API 또는 Claude / Gemini / Codex 등 **CLI·API**를 통해 구조화된 분석 결과를 만든 뒤, **HTML 리포트**로 출력한다.
- **운영**: **UI에서 수집·분석·리포트 주기**를 설정하고, **결과를 지정 폴더**에 저장한다.

### 1.3 비목표 (초기 버전에서 다루지 않거나 제한)

- 카카오 서버에 직접 접속하는 메시지 동기화, 비공식 프로토콜 역공학을 통한 **전체 이력 무제한 수집**.
- 채팅방 **비참여자**에 대한 모니터링, 동의 없는 제3자 데이터 수집.
- 실시간 스트리밍 대시보드(필요 시 후속 단계).
- **벡터 검색 기반 RAG**: 단일 PC·주기별 분석 규모에서는 직접 분석 + 누적 통계로 충분하다고 판단. 검색형 드릴다운 요구가 분명해지기 전까지는 도입하지 않는다.

---

## 2. 용어·범위

### 2.1 용어

| 용어 | 설명 |
|------|------|
| **수집 텍스트** | 카카오톡 PC 채팅 리스트(`EVA_VH_ListControl_Dblclk`)에 **Ctrl+A/C로 복사한** 문자열. 스크롤 밖 과거 메시지는 기본 범위에 포함되지 않을 수 있음. |
| **분석 주기 (analysis period)** | 한 번의 LLM 분석이 다루는 시간 길이. 설정 가능(예: 1시간, 6시간, 1일, 1주). |
| **기간 버킷 (period bucket)** | 분석 주기 단위로 잘린 시간 구간 하나. `period_start`, `period_end`, 사람이 읽기 좋은 `period_key`(예: `2026-05-12`, `2026-05-12T14`, `2026-W19`)로 식별. |
| **주기별 요약 (periodic insight)** | 한 방·한 기간 버킷의 채팅을 LLM이 읽어 만든 구조화 JSON. 주제·태그·언급 수·`distinct_nicks`·패치 반응·인용 참조(`message_id` 등)를 포함. |
| **인사이트 스토어** | 주기별 요약을 누적 저장하는 SQLite 테이블. 통계·리포트의 1차 입력원. |
| **누적 통계** | 인사이트 스토어를 집계한 **주제·태그 빈도 추이**와 **패치 반응 누적**만 해당. |
| **Quote Resolver** | 리포트 생성 시 `messages` 테이블에서 **원문 검색**으로 예시 인용문을 가져오는 단계. HTML에는 DB 원문을 그대로 넣는다. |

### 2.2 수집 범위

- **대상**: 사용자 PC에서 **직접 연 상태인** 오픈채팅방 **20개 이상**. 닫히거나 찾을 수 없는 방은 해당 수집 사이클만 스킵.
- **내용**: 해당 창 리스트 컨트롤에 **현재 로드된** 텍스트. 이미지·이모티콘·삭제된 메시지는 파서에서 **제외**.
- **제외 데이터**: UIAutomation·OCR 경로는 사용하지 않는다.

---

## 3. 이해관계자·사용 시나리오

### 3.1 이해관계자

- **운영/기획**: 주간 리포트로 패치 우선순위·커뮤니케이션 방향 결정.
- **개발자(본 시스템 사용자)**: 수집 품질·스케줄·모델 선택 유지보수.

### 3.2 시나리오

1. 사용자가 카카오톡 PC에서 게임 관련 오픈채팅 **20개 이상**을 연다.
2. 수집기가 **10분**마다 각 방 창에서 텍스트를 읽어 SQLite `messages`에 쌓는다(방별 격리·중복 제거).
3. 설정된 분석 주기마다 **주기별 분석 잡**이 돌아 각 방·기간 버킷별 구조화 요약을 인사이트 스토어에 저장한다.
4. 사용자가 **업데이트 노트 파일**(및 예정 일정) 경로를 지정한다.
5. 설정된 주기(또는 수동 실행)로 리포트 잡이 돌아간다: **누적된 주기별 요약 + 통계 + 업데이트 노트 → LLM 합성 → HTML 저장**.
6. 사용자가 결과 폴더에서 `YYYY-MM-DD_HHmm/index.html` 또는 `latest.html`을 연다.

---

### 4. 데이터 흐름 요약

1. **Collector** (클립보드) → **Parser/Normalizer** → 원시 메시지(SQLite `messages`).
2. **Bucketizer → Periodic Analyzer** → 주기별 요약(JSON)을 **인사이트 스토어**에 누적.
3. **Aggregator** → **주제·태그 추이**, **패치 반응**만 산출.
4. **리포트 잡**: 누적 요약 + 통계 + Context loader → **Model router** (`.env`) → **Quote Resolver**(원문 검색) → **HTML 렌더러**.

구현 단계·파서·통계 상세는 `development-plan.md`를 따른다.

---

## 5. 상세 설계

### 5.1 Collector (Windows, 클립보드)

1차 구현은 `kakao_clipboard_crawler.py`와 동일한 방식이다.

- **창 식별**: `EnumWindows` + 제목 **완전 일치**. `normalize_title(title)`로 끝의 `(숫자)`(안 읽음 수) 제거 후 `rooms` 설정과 비교.
- **텍스트 획득**: 채팅 리스트 자식 창 클래스 `EVA_VH_ListControl_Dblclk`에 **Ctrl+A → Ctrl+C**, 클립보드 UTF-16 텍스트 수신. 수집 후 클립보드 복원(옵션).
- **다중 방**: 설정된 방 목록을 **순차** 캡처(클립보드 전역). 방마다 `room_id`로 DB·상태 분리.
- **중복·스크롤**: `content_hash`(방·닉·시각·본문) 및 스냅샷 overlap diff로 신규 메시지만 insert.
- **실패 시**: 해당 방만 경고 로그; `collect_runs`에 기록. 리포트 메타에 **커버리지 낮음** 가능.

### 5.2 저장소

#### 5.2.1 원시 이벤트

- **SQLite** 단일 DB (`DATABASE_PATH`, 기본 `data/openchat.db`). 컬럼 예: `message_id`, `room_id`, `collected_at`, `message_at`, `nick`, `body`, `content_hash`.
- 파싱된 **논리 메시지 1건 = 1행**. 원시 텍스트 TTL **`retention.raw_days = 7`**.
- 캡처 샘플 형식·파서 규칙은 `development-plan.md` §2 참고.

#### 5.2.2 인사이트 스토어

- 동일 SQLite 안에 별도 테이블로 주기별 분석 결과를 저장.
- 스키마 예: `room_id`, `period_key`, `period_start`, `period_end`, `period_type`(`hourly` / `daily` / `weekly` / `custom`), `message_count`, `coverage`, `topics_json`, `patch_reactions_json`, `analyzer_model`, `analyzer_version`, `prompt_hash`, `created_at`.
- 집계 캐시 테이블: `topic_stats`, `patch_reaction_stats` (상세는 `development-plan.md` §4).
- 분석은 **idempotent**: 같은 `(room_id, period_key, analyzer_version)`은 덮어쓰되, 버전이 다르면 이력으로 남길 수 있음.
- `period_type`이 다른 분석 결과는 같은 테이블 안에 공존 가능(예: 평소 일간, 이슈 발생 시 임시로 시간 단위 재분석).

### 5.3 주기별 분석기 (Periodic Analyzer)

설정된 분석 주기마다 각 방·기간 버킷별로 다음을 수행한다.

#### 5.3.1 기간 버킷 분할

- `analyzer.period` 설정(예: `1h`, `6h`, `1d`, `1w`)에 따라 원시 이벤트를 버킷으로 자른다.
- 각 버킷은 `period_start`(포함) / `period_end`(제외) 와 사람이 읽기 좋은 `period_key`를 갖는다.
  - 예: `period=1d` → `period_key="2026-05-12"`
  - 예: `period=6h` → `period_key="2026-05-12T12"` (12:00–18:00 버킷)
  - 예: `period=1w` → `period_key="2026-W19"`
- 버킷 경계는 운영자가 지정한 타임존(`tz`) 기준으로 정렬.

#### 5.3.2 입력 준비

- 해당 방·기간 버킷의 `messages`를 시간순 정렬·중복 제거(이미지/이모티콘/삭제·봇 등은 파서 단계에서 제외).
- 주제별 **`distinct_nicks`**(고유 닉 수)를 LLM 출력에 포함. `mentions`만으로 상위 주제를 정하지 않고, `min_distinct_nicks`(기본 3) 미만은 **표본 부족** 처리.
- 토큰 추정치가 `analyzer.two_stage_threshold_tokens`(예: 100K)을 넘으면 **서브 버킷 분할(2-stage)**. 결과는 원래 `period_key`로 저장.

#### 5.3.3 LLM 분석 호출

- 프롬프트는 채팅 원문을 `message_id`와 함께 넣고, **고정된 JSON 스키마**로만 출력하도록 강제(Structured Outputs / 함수 호출 / 후처리 파서).
- 분석·리포트 모델은 **`.env`** (`ANALYZER_*`, `REPORTER_*`)로 지정. 비용·품질에 따라 분리 가능.
- **인용 참조**: Analyzer는 `message_id` 또는 `search_phrase`만 JSON에 남긴다. HTML에 넣을 **원문 문자열은 Quote Resolver**가 `messages`에서 조회·검증한 뒤 **그대로** 삽입한다.

#### 5.3.4 출력 스키마 (예시)

```json
{
  "room_id": "...",
  "period_key": "2026-05-12",
  "period_start": "2026-05-12T00:00:00+09:00",
  "period_end": "2026-05-13T00:00:00+09:00",
  "period_type": "daily",
  "message_count": 842,
  "coverage": "high | partial | low",
  "topics": [
    {
      "tag": "bug | balance | event | ops | meta",
      "title": "보스 B 패턴 무한 루프",
      "topic_key": "boss_b_infinite_loop",
      "mentions": 23,
      "distinct_nicks": 12,
      "underrepresented": false,
      "first_seen": "2026-05-12T14:22:00+09:00",
      "quote_refs": [
        { "message_id": 12345 },
        { "search_phrase": "무한 루프", "room_id": "..." }
      ]
    }
  ],
  "patch_reactions": [
    {
      "patch_item": "캐릭터 X 너프",
      "stance": "negative | neutral | positive | mixed",
      "mentions": 41,
      "distinct_nicks": 18,
      "summary": "...",
      "quote_refs": [{ "message_id": 12345 }]
    }
  ]
}
```

`underrepresented`: `distinct_nicks < min_distinct_nicks`일 때 true. 리포트 상위 주제·통계 집계에서 제외 또는 각주.

#### 5.3.5 재현성·버전 관리

- `analyzer_version`, `prompt_hash`, `model_id`를 분석 결과와 함께 저장.
- 프롬프트나 모델이 바뀌어도 과거 분석은 그대로 두고, 새 버전으로 **재분석 도구**를 통해 다시 채울 수 있음.
- 분석 주기를 바꿔도(예: `daily` → `6h`) 과거 데이터는 원래 `period_type`으로 남고, 신규 버킷부터 새 주기로 채워짐. 필요 시 과거 구간을 새 주기로 **재분석**할 수 있음.

### 5.4 통계·시계열 (Aggregator)

인사이트 스토어를 집계해 리포트에 쓸 시계열을 만든다. **본 설계에서 다루는 통계는 아래 두 가지뿐**이다.

#### 5.4.1 주제·태그 빈도 추이

- `period_key` × `tag`(또는 `topic_key`)별 `mentions` 합산.
- 동일 주제에 `distinct_nicks`·`underrepresented`를 함께 저장·표기.
- 상위 주제 선정: `mentions` 단독 정렬 금지. `distinct_nicks >= min_distinct_nicks` 미만은 집계·리포트 본문에서 제외 또는 각주.
- 시각화: **기간(period_key) 축**의 태그·주제 추이 차트만 제공.

#### 5.4.2 패치/업데이트 반응

- Context loader의 패치 항목과 `patch_reactions` 매칭.
- `patch_item` × `period_key`별 `mentions`, `stance` 분포, `distinct_nicks`.
- **갭 분석**: 업데이트 노트에 없는데 `mentions` 상위인 `topics` 목록.

#### 5.4.3 의도적으로 제외하는 통계

시간대별 건수, 방 간 비교, 이상치(z-score), 신조어·반복 키워드 전용 집계, 감성 추이는 **구현·리포트 모두에서 제외**한다.

집계는 SQL로 표현하고, `topic_stats`·`patch_reaction_stats`에 캐시한 뒤 HTML 렌더러에 전달한다. 수식·후처리 규칙은 `development-plan.md` §5를 따른다.

### 5.5 Context loader

- **입력**: 지난 업데이트 노트, 다음 주 예정, 선택 FAQ/공지.
- **형식**: 로컬 파일 경로(마크다운, 텍스트). 버전 태그·날짜 메타를 리포트에 표기.
- **LLM 주입**: 전문 전체를 길이 제한 내 요약본으로 넣거나, 패치 항목명을 키로 인사이트 스토어의 `patch_reactions`와 매칭해 관련 항목만 전달.

### 5.6 리포트 합성기 (Reporter)

- **입력**: 리포트 기간(예: 최근 7일)에 해당하는 주기별 요약 목록 + 통계 집계 + 업데이트 노트 요약/발췌.
- **출력**: **구조화된 JSON 스키마**(섹션별 제목·요약·태그·근거 메시지·인용 ID 등) → HTML로 렌더.
- **할루시네이션 완화**: HTML 인용은 **Quote Resolver**가 `messages`에서 조회한 원문만 사용. LLM이 생성한 인용문을 HTML에 직접 넣지 않는다.
- **예시 인용**: `quote_refs` → DB 검색 → **원문·닉·시간 그대로** HTML 블록 삽입(마스킹 없음).
- **토큰 관리**: 주기별 요약 JSON은 일반적으로 수만~수십만 토큰 내. 큰 경우 주제별로 부분 합성 후 머지(map-reduce). 원시 채팅은 합성 단계에서 직접 넣지 않음.

### 5.7 Model router

- **분석용·리포트 합성용** 프로바이더·모델·API 키는 **`.env`** (`ANALYZER_*`, `REPORTER_*`)에서 로드.
- **OpenAI API**(HTTP)와 **Claude / Gemini / Codex CLI**(서브프로세스, stdin/stdout 또는 파일)를 공통 인터페이스로 추상화.
- 재시도·타임아웃·토큰 한도 처리. 주기별 분석은 방·기간 버킷 단위로 병렬 호출 가능.

### 5.8 HTML 리포트

**섹션 예시**

1. **요약**: 기간, 방 개수, 분석 버킷 수, 상위 주제(표본 충분한 것만).
2. **통계·시계열**: **주제·태그 빈도 추이**, **패치 반응** 차트(period_key 축).
3. **주제별 논의**: 태그별 요약 + Quote Resolver **원문 인용**.
4. **업데이트 노트 대조**: 패치 항목별 stance·언급량·`distinct_nicks`·갭 주제.
5. **다음 주 예정과의 정렬**: 기대·우려·요청(로드맵 Context).
6. **메타**: `.env` 모델·분석 주기·버전·소스 기간·원시 7일 보관·인용 검증 실패 건수.

**파일 배치**

- `reports/YYYY-MM-DD_HHmm/index.html` (권장)
- 동일 디렉터리에 사용된 CSS·작은 자산
- **latest**: 동일 내용을 `latest.html`로 복사하거나 심볼릭 링크(Windows에서는 junction/복사 중 선택)

### 5.9 스케줄·UI

- **수집 주기**: 기본 **10분** (`COLLECT_INTERVAL_MINUTES` 또는 `schedule.collect`).
- **분석 주기 (`analyzer.period`)**: `hourly`, `6h`, `daily`, `weekly` 등에서 선택. 또는 cron 유사 문자열로 임의 주기. 수동 실행·과거 버킷 재분석도 지원.
- **분석 실행 시각 (`schedule.analyze`)**: 위 주기의 버킷이 닫힌 직후 (예: 일간이면 다음 날 새벽) 실행.
- **리포트 주기 (`schedule.report`)**: 매일 특정 시각, 매주 특정 요일·시각, 또는 cron 유사 문자열. 분석 주기와 독립.
- **구현 후보**:
  - 앱 내 타이머 스레드 + 다음 실행 시각 계산(권장 단순 형태).
  - 또는 Windows 작업 스케줄러에 CLI 등록(앱이 외부에서 한 번 실행되게 함).
- **결과 폴더**: 사용자 지정 루트 아래 타임스탬프 하위 폴더 생성.

### 5.10 구현 스택

| 접근 | 장점 | 단점 |
|------|------|------|
| **Python + PyQt/PySide 단일** | 한 프로세스로 단순 |


---

## 6. 설정 항목 목록

환경 변수는 **`.env`** 에 두고, 앱 시작 시 로드한다. 상세 키 목록은 `development-plan.md` §6.

| 키 (`.env` / 설정) | 설명 |
|----|------|
| `DATABASE_PATH` | SQLite 경로 (기본 `data/openchat.db`) |
| `TZ` | 기간 버킷·스케줄 타임존 (예: `Asia/Seoul`) |
| `COLLECT_INTERVAL_MINUTES` | 수집 주기 (기본 **10**) |
| `ROOMS_CONFIG` | 방 목록 YAML (`canonical_title` 완전 일치) |
| `MIN_DISTINCT_NICKS` | 주제·패치 반응 최소 고유 닉 수 (기본 3) |
| `ANALYZER_PROVIDER` / `ANALYZER_MODEL` / `ANALYZER_API_KEY` | 주기별 분석 LLM |
| `ANALYZER_PERIOD` | 분석 주기 (`1d`, `1w` 등) |
| `ANALYZER_PROMPT_VERSION` | 프롬프트 버전 태그 |
| `analyzer.two_stage_threshold_tokens` | 서브 버킷 분할 임계 |
| `REPORTER_PROVIDER` / `REPORTER_MODEL` / `REPORTER_API_KEY` | 리포트 합성 LLM |
| `REPORTER_WINDOW` | 리포트 기간 (예: `7d`) |
| `RETENTION_RAW_DAYS` | 원시 메시지 TTL (기본 **7**) |
| `retention.insight_days` | 인사이트·집계 캐시 TTL |
| `PATCHNOTES_PATH`, `ROADMAP_PATH` | 업데이트 노트·예정 |
| `OUTPUT_DIR` | HTML 리포트 루트 |
| `schedule.analyze` / `schedule.report` | 분석·리포트 cron (후속 UI와 병행 가능) |
| `exclude_nicks`, `exclude_body_patterns` | 파서 제외 (봇, 사진, 삭제 메시지 등) |

---

## 7. 보안·프라이버시·보존 기간

- **비밀**: API 키는 **`.env`** (git 제외). 필요 시 OS 자격 증명 저장소로 이전 가능.
- **데이터 최소화**: 원시 `messages`는 **`RETENTION_RAW_DAYS=7`** 이후 삭제 job. 인사이트·`topic_stats`는 더 길게 보관 가능.
- **외부 전송**: 주기별 분석은 원시 채팅을 LLM에 전송. 리포트 합성은 **인사이트 JSON만** 외부로 보내도록 기본 설정 → 장기적으로 외부 전송량 감소.
- **인용·닉네임**: 리포트 HTML에는 **원문·닉 그대로** 표기(마스킹 없음). 로컬 DB·리포트 파일 접근 통제는 운영자 책임.
- **TLS**: 클라우드 LLM API 호출 구간 HTTPS.

---

## 8. 리스크와 완화

| 리스크 | 완화 |
|--------|------|
| 카카오 PC UI·리스트 클래스 변경 | `LIST_CONTROL_CLASS` 버전 태그; `kakao_clipboard_crawler`로 회귀 확인 |
| 20방 순차 수집 지연 | 방당 타임아웃; 실패 방 스킵·`collect_runs` 로그 |
| 소수 닉의 여론 과대 | `distinct_nicks`, `min_distinct_nicks`, `underrepresented` |
| 분석 LLM 비용 | 방·버킷 단위 호출; `.env`에서 저가·고가 모델 분리 |
| 컨텍스트 초과 | 서브 버킷 분할(2-stage) |
| LLM 할루시네이션 | JSON 스키마 + Quote Resolver는 DB 원문만 HTML 삽입 |
| 분석 누락 | idempotent 재실행; `analyzer_version`별 재분석 |
| 법·약관 | 참여 방·목적·7일 원시 보관·로컬 리포트 접근 통제 |

---

## 9. 로드맵

| 단계 | 내용 |
|------|------|
| **0 PoC** | `kakao_clipboard_crawler.py` 단일 방 캡처 ✓ |
| **1 수집** | 20+ 방, 10분 watch, 제목 정규화, diff, SQLite `messages`, 7일 purge |
| **2 파싱** | captures 샘플 기반 파서, 이미지/이모티콘/삭제/봇 제외 |
| **3 분석** | Periodic Analyzer, `.env` 모델, `distinct_nicks`, 인사이트 스토어 |
| **4 통계** | 주제·태그 추이, 패치 반응, 갭만 (감성·이상치·방 비교 제외) |
| **5 리포트** | Quote Resolver 원문 인용, HTML, 패치노트 대조 |
| **6 스케줄** | collect / analyze / report CLI·작업 스케줄러 |
| **7 운영** | `collect_runs` 관측, 커버리지 경고 |

단계별 상세·통계 수식은 **`development-plan.md`** §7.

## 10. 관련 문서

| 문서 | 역할 |
|------|------|
| `design.md` (본 문서) | 아키텍처·스키마·운영 가정 정본 |
| `development-plan.md` | 구현 단계, 파서, 통계 정의, `.env`, SQLite 개요 |

---

## 문서 이력

| 버전 | 날짜 | 요약 |
|------|------|------|
| 0.1 | 2026-05-12 | 초안 작성(수집·RAG·HTML·스케줄·설정·리스크) |
| 0.2 | 2026-05-12 | RAG 제거. 방·일자 단위 직접 분석(Daily Analyzer) + 인사이트 스토어 + 누적 통계(Aggregator) 구조로 재편. |
| 0.3 | 2026-05-12 | "일일 분석" → "주기별 분석"으로 일반화. 분석 주기를 `hourly`/`daily`/`weekly`/cron 등 설정 가능하게 변경. 스키마에 `period_key`/`period_start`/`period_end`/`period_type` 도입, 통계 공통 그레인(`stats.common_grain`)·재분석 도구·다중 주기 혼용 운영 항목 추가. |
| 0.4 | 2026-05-22 | 클립보드 Collector 확정(UIAutomation/OCR 제거). 20+ 방·10분 수집·SQLite·원시 7일·`.env` 모델. 통계를 주제·태그 추이·패치 반응만으로 축소. `distinct_nicks`·Quote Resolver·원문 HTML 인용. `development-plan.md` 추가. |
