# 웹 서비스 운영 가이드 (Simple Service)

내부망용 웹 UI로 오픈채팅 인사이트 파이프라인을 운영하는 방법입니다.  
설계·단계별 계획은 루트의 [`simple-service-development-plan.md`](../simple-service-development-plan.md)를 참고하세요.

## 개요

| 항목 | 설명 |
|------|------|
| 실행 환경 | Windows PC (카카오톡 PC 클라이언트와 **동일 호스트**) |
| 접속 | `0.0.0.0` 바인딩 — 사내망 브라우저에서 접속 |
| 인증 | 없음 (방화벽·내부망으로 접근 제한 전제) |
| 프로젝트 | 1개 프로젝트 = 여러 카카오톡 **창 제목** + 공통 `room_id`(DB) |

파이프라인: **수집 → 분석 → 통계 집계 → HTML 리포트 → (선택) 이메일**

## 사전 요구 사항

1. Python 3.11+ 및 `requirements.txt` 설치
2. 카카오톡 PC 실행, 수집 대상 채팅방 창이 열려 있거나 제목으로 찾을 수 있음
3. (리포트 LLM) `REPORTER_API_KEY` 등 OpenAI 호환 API
4. (분석 LLM) 기본은 Ollama 등 `OPENAI_API_BASE` 로컬 EXAONE
5. (이메일) 사내 Email API (`EMAIL_API_BASE_URL`)

## 설정 파일

### `.env` — 비밀값·경로·LLM

민감 정보와 인프라 고정값만 둡니다. 운영 주기·데이터 스코프는 `ui_settings.yaml`을 권장합니다.

| 변수 | 용도 |
|------|------|
| `DATABASE_PATH` | SQLite DB (기본 `data/openchat.db`) |
| `PROJECTS_CONFIG` | 프로젝트 YAML (미설정 시 `config/projects.yaml` 우선) |
| `OUTPUT_DIR` | HTML 리포트 출력 (기본 `reports/`) |
| `EMAIL_API_BASE_URL` | 메일 발송 API 베이스 URL |
| `SERVE_PUBLIC_BASE_URL` | 이메일 본문 «전체 리포트» 링크용 (예: `http://10.x.x.x:8000`) |
| `REPORTER_API_KEY` | Reporter LLM |
| `OPENAI_API_BASE` / `ANALYZER_*` | Periodic Analyzer |

전체 목록: [`.env.example`](../.env.example)

### `config/projects.yaml` — 프로젝트·채팅방·이메일

```yaml
projects:
  - id: lineage-m
    label: "리니지M"
    enabled: true
    titles:
      - "리니지m방"
      - "리니지m방 2"
    update_notes_url: "https://example.com/board/update/list"
    email:
      sender: "from@company.com"
      receivers:
        - "to1@company.com"
        - "to2@company.com"

exclude_nicks:
  - "오픈채팅봇"
exclude_body_patterns:
  - "사진"
```

- `id` → DB `room_id` (프로젝트 단위로 통일)
- `titles` → 카카오톡 창 제목과 **완전 일치** (끝 `(N)` 안 읽음 수는 자동 제거 후 비교)
- 구 스키마 `config/rooms.yaml` (`rooms[].title`)도 읽을 수 있음

웹에서 프로젝트 CRUD 시 이 파일에 **atomic write**로 저장됩니다.

### `config/ui_settings.yaml` — 운영 주기·데이터 스코프

```yaml
collect_interval_minutes: 10
analyzer_period: 1d
reporter_window: 7d
data_scope:
  mode: last_days
  last_days: 7
  time_field: message_at
  tz: Asia/Seoul
```

**데이터 스코프** (`last_days`)는 아래가 **동일한 N일**을 사용합니다.

- 리포트 HTML 생성
- 리포트 이메일 본문 요약
- 웹 통계 (`/stats/projects/{id}`)

메시지는 `message_at` 기준, 분석 버킷은 `period_start` / `period_end`와 윈도우 교차 조건으로 필터합니다.

## 서버 실행

```powershell
cd open-chat-main
.venv\Scripts\activate
python -m openchat serve
```

| 환경 변수 | 기본값 | 설명 |
|-----------|--------|------|
| `SERVE_HOST` | `0.0.0.0` | 바인드 주소 |
| `SERVE_PORT` | `8000` | 포트 |
| `SERVE_RELOAD` | off | 개발 시 `true` |

브라우저: `http://<서버IP>:8000/`  
API 문서: `http://<서버IP>:8000/api/docs`

## 웹 UI 페이지

| 경로 | 기능 |
|------|------|
| `/` | 대시보드, 프로젝트 요약 |
| `/projects` | 프로젝트 목록 |
| `/projects/new`, `/projects/{id}/edit` | 생성·수정 (titles, URL, 이메일) |
| `/projects/{id}` | **수집 / 분석 / 리포트 / 리포트+이메일** 실행 |
| `/settings` | 수집·분석 주기, 데이터 스코프 |
| `/reports` | 생성된 리포트 목록 |
| `/reports/{run_id}` | 리포트 미리보기·이메일 발송 |
| `/stats/projects/{id}` | 프로젝트 통계 (표) |
| `/jobs/{job_id}` | 백그라운드 작업 상태·로그 |

헤더 **설정 다시 불러오기**: `projects.yaml` / `ui_settings.yaml`을 디스크에서 재로드합니다.

## 권장 운영 흐름 (웹)

1. **설정** → 최근 N일·수집/분석 주기 확인
2. **프로젝트** → titles·`update_notes_url`·이메일 sender/receivers 설정
3. 프로젝트 상세 → **수집 1회** (카카오톡 창 필요)
4. **분석 실행** (백그라운드 job, `/jobs/...`에서 완료 확인)
5. **리포트 생성** 또는 **리포트 + 이메일**
6. **통계 보기**로 스코프 내 메시지·주제 확인
7. `/reports`에서 HTML 확인

장시간 작업(분석·리포트·이메일)은 백그라운드 스레드 + DB `background_jobs` 테이블에 상태가 저장됩니다.

## CLI (기존 방식)

웹과 동일 설정(`.env` + YAML)을 읽습니다.

```powershell
python -m openchat collect
python -m openchat analyze
python -m openchat aggregate
python -m openchat report
python -m openchat pipeline   # collect → analyze → aggregate → report
```

주기 수집:

```powershell
python -m openchat collect --watch
```

## REST API 요약

| 메서드 | 경로 | 설명 |
|--------|------|------|
| GET/POST/PUT/DELETE | `/api/projects` | 프로젝트 CRUD |
| POST | `/api/projects/{id}/collect` | 수집 1회 |
| POST | `/api/projects/{id}/analyze` | 분석 (202, job) |
| POST | `/api/projects/{id}/report` | 리포트 (202, job) |
| POST | `/api/projects/{id}/report-email` | 리포트 메일 (202, body: `run_id` 선택) |
| POST | `/api/projects/{id}/report-and-email` | 리포트 생성 후 메일 |
| GET | `/api/projects/{id}/stats` | 통계 JSON |
| GET/PUT | `/api/settings`, `/api/settings/scope` | 전역·스코프 설정 |
| GET | `/api/jobs/{job_id}` | 작업 상태 |
| GET | `/api/reports`, `/api/reports/{id}` | 리포트 메타 |

## 데이터·산출물

| 경로 | 내용 |
|------|------|
| `data/openchat.db` | 메시지, `periodic_insights`, job, `report_runs` |
| `data/state/{project_id}__{title_slug}_last.txt` | 방별 스냅샷 |
| `captures/{room_id}/` | 선택적 캡처 파일 |
| `reports/{project_id}/report_*.html` | 프로젝트별 리포트 |

정기 백업: DB + `reports/` + `config/*.yaml`

## 보안·운영 주의

- 인증이 없으므로 **내부망·방화벽**으로 접근을 제한하세요.
- 리포트·이메일·DB에 채팅 내용이 포함됩니다. 사내 개인정보/보안 정책을 따르세요.
- `SERVE_PUBLIC_BASE_URL`은 이메일 수신자가 리포트 HTML에 접근 가능한 URL이어야 합니다.

## 문제 해결

| 증상 | 확인 |
|------|------|
| 수집 스킵 | 카카오톡 실행 여부, `titles`와 창 제목 일치 |
| 분석 0건 | 메시지 DB 존재, `--include-current` / 웹은 기본 포함, 이미 분석된 버킷은 `--force` |
| 리포트 비어 있음 | analyze 후 aggregate(리포트 job이 자동 실행), 스코프 N일 내 버킷 존재 |
| 이메일 실패 | `EMAIL_API_BASE_URL`, 프로젝트 `email.sender`/`receivers`, API 로그 |
| 통계 0 | 스코프 일수·`message_at` 범위, 프로젝트 id와 DB `room_id` 일치 |
| 설정 반영 안 됨 | 웹 **설정 다시 불러오기** 또는 서버 재시작 |

## 관련 문서

- [README.md](../README.md) — 설치·CLI 요약
- [simple-service-development-plan.md](../simple-service-development-plan.md) — 설계·구현 단계
- [development-plan.md](../development-plan.md) — 전체 파이프라인 설계
