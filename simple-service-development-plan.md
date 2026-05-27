# Simple Service 개발 계획 (웹 UI + 프로젝트/방 묶음 + 리포트/이메일)

> **구현 상태 (MVP)**: 0~6단계 웹 기능 구현 완료.  
> **운영 문서**: [`docs/web-service-guide.md`](docs/web-service-guide.md) · **빠른 시작**: [`README.md`](README.md)

## 목적

현재 CLI 중심 파이프라인(수집 → 분석 → 통계 → HTML 리포트)을 **Windows PC(카카오톡 실행 동일 호스트)**에서 서비스 형태로 운영하기 위해, 아래를 제공하는 **내부망용(인증 없음) 웹 UI**를 추가한다.

- **0.0.0.0 서버 바인딩**으로 사내망에서 접속 가능
- **프로젝트 단위 운영**
  - 프로젝트 1개가 **여러 개의 채팅방 제목(title)** 을 묶어 관리
  - 프로젝트별로 **리포트 생성/이메일 발송** 가능
  - 프로젝트별로 **sender/receivers** 설정 가능
- 웹에서 **프로젝트 CRUD**, **수집/분석/리포트 수동 실행**, **수집/분석 주기 설정**, `update_notes_url` 등 기존 설정 변경
- 웹에서 **통계(집계 결과) 조회** 가능

## 비목표(초기)

- 외부 인터넷 공개/로그인/권한 관리(내부망 전제)
- 카카오톡 미실행/원격 수집(수집기는 로컬 UI에 의존)
- 실시간 스트리밍 대시보드(초기에는 “최근 N일/최근 N버킷” 중심)

---

## 현행 구조 요약(구현 반영)

| 영역 | 경로·모듈 |
|------|-----------|
| 설정 | `load_settings()`: `.env` + `config/projects.yaml`(또는 `rooms.yaml`) + `config/ui_settings.yaml` 병합 |
| 프로젝트 YAML CRUD | `openchat/projects_store.py` |
| 웹 UI | `openchat/webapp.py` (`python -m openchat serve`) |
| 수집/분석/리포트 실행 | `openchat/pipeline.py`, `openchat/job_service.py` |
| 통계 (프로젝트·스코프) | `stats/project_stats.py` |
| 이메일 | `report/openchat_email.py`, `openchat/email_delivery.py`, `email_sender.send_html_email` |
| CLI | `openchat/cli.py` (기존 collect / analyze / report / pipeline 유지) |

- DB: `data/openchat.db` — `background_jobs`, `report_runs` (웹 job·리포트 이력)
- 리포트 출력: `reports/{project_id}/report_*.html`

---

## 핵심 변경 요구사항(설계)

### 1) “프로젝트 1개 = 여러 채팅방(title)”로 확장

현재 `config/rooms.yaml`은 `rooms[]`에서 “채팅방(창) 1개” 단위로 설정한다. 이를 아래 중 하나로 전환한다.

#### 옵션 A (권장): `rooms.yaml` 스키마 확장(프로젝트 단위)

- 프로젝트는 `id`로 식별
- `titles: [ ... ]`로 여러 개 채팅방 제목을 매핑

예시(개념):

```yaml
projects:
  - id: lineage-m
    label: "리니지M"
    enabled: true
    titles:
      - "리니지m방"
      - "리니지m방 2"
    update_notes_url: "https://lineagem.plaync.com/board/update/list"
    email:
      sender: "a@company.com"
      receivers:
        - "b@company.com"
```

- 장점: 설정 파일만으로도 완전한 설정(백업/이동 용이)
- 단점: 웹 CRUD 시 YAML read/write 필요

#### 옵션 B: DB에 프로젝트 설정을 저장하고, YAML은 “초기 seed”로만 사용

- 장점: 웹 CRUD 구현이 단순(원자적 업데이트/검증 쉬움)
- 단점: 운영자가 파일로 직접 관리하기 어려움

초기에는 **옵션 A**로 진행하되, 아래를 같이 제공한다.

- 서버가 뜰 때 YAML을 읽어 메모리에 반영
- 웹에서 수정 시 YAML을 다시 저장(atomic write)
- “설정 변경 후 재로딩” 버튼 제공(또는 자동 reload)

### 2) 프로젝트별 리포트 생성/발송

- 리포트 생성은 기존 `report.render_html` 파이프라인을 **프로젝트(=room_id 집합)** 기준으로 스코프를 제한해 실행
- 프로젝트별로 `update_notes_url`, `PATCHNOTES_PATH`, `ROADMAP_PATH`, `REPORTER_WINDOW` 등을 사용할 수 있어야 함
  - 전역 기본값(.env)을 두고, 프로젝트별 override 허용

### 3) Outlook 호환 HTML 이메일 본문

- `email_sender.py`의 HTML 생성 스타일(테이블 기반, VML 버튼, ASCII 엔티티 변환)을 **오픈채팅 리포트 이메일 템플릿에도 재사용**한다.
- 이메일 본문 구성(권장):
  - 상단: 프로젝트명/기간/주요 요약(LLM synthesis 결과 핵심 bullet)
  - 중단: TOP N 주제(카드 형태, 인용 1~2개)
  - 하단: “전체 리포트 보기” 링크(웹에서 리포트 HTML 제공 시) 또는 리포트 경로 안내

### 4) 통계 웹 조회

가능하다. 구현 방향은 아래 2종을 제공한다.

- **프로젝트 통계 요약 페이지**
  - 최근 N일/최근 N버킷 기준: 주제/태그 상위, 패치 반응 상위, 참여자 수(distinct_nicks) 추이
- **리포트 생성 시점 통계 스냅샷**
  - 리포트 생성 작업이 만든 payload/집계 결과를 함께 저장해, 나중에 동일 결과를 웹에서 그대로 확인

### 4.1) (추가) 웹에서 “포함 메시지/통계 범위(DB 스코프)” 통제

리포트 이메일 발송 시 포함되는 메시지/인사이트 범위와, 웹에서 통계를 보여줄 때 포함되는 범위가 어긋나면 운영이 어려우므로, 웹 UI에서 **하나의 스코프 설정을 공유**하도록 한다.

#### 요구사항

- 웹에서 “최근 N일” 스코프를 설정하면,
  - 리포트 생성(HTML) 시 포함되는 데이터 범위
  - 리포트 이메일 발송 시 포함되는 데이터 범위
  - 웹 통계 페이지(`/stats/...`)에 표시되는 데이터 범위
  가 **동일한 스코프**를 사용해야 한다.
- 스코프는 **`message_at`(발화 시각)** 기준으로 제한하는 것을 기본으로 한다.
  - 이유: 운영자가 “최근 N일 여론”을 보고 싶을 때, 수집 지연/누락(`collected_at`)보다 발화 기준이 직관적이다.
- 스코프 설정은 서버 재시작에도 유지되어야 하므로 **YAML로 저장**한다.

#### 제안 스키마 (예: `config/ui_settings.yaml`)

```yaml
data_scope:
  mode: "last_days"        # 초기: last_days만 지원
  last_days: 7             # 웹에서 변경 가능
  time_field: "message_at" # 고정(초기)
  tz: "Asia/Seoul"         # 기본은 .env TZ를 따르되, 표시용으로 유지
```

#### 구현 개념(스코프 적용 위치)

- **원시 메시지 조회(`messages`)**: `message_at >= now - N days`
- **주기별 분석 결과(`periodic_insights`)**:
  - 원칙: `period_end > window_start` AND `period_start < window_end` 인 버킷만 포함
  - 리포트/통계는 이 버킷 집합을 입력으로 사용
- **집계 캐시(`topic_stats`, `patch_reaction_stats`)**:
  - 1차: 스코프 쿼리로 `periodic_insights`를 집계해 즉석 계산(단순/정확)
  - 2차: 성능이 필요하면 “스코프 기반 stats 스냅샷”을 저장(후속)

---

## 데이터 모델(권장)

### 1) 프로젝트 설정

옵션 A(YAML) 기준으로도, 런타임에서 아래 구조로 다루는 것을 목표로 한다.

- `ProjectConfig`
  - `id: str` (기존 room id 개념을 프로젝트 id로 승격)
  - `label: str`
  - `enabled: bool`
  - `titles: list[str]` (여러 채팅방명)
  - `update_notes_url: str`
  - `email_sender: str`
  - `email_receivers: list[str]`
  - 선택: 프로젝트별 `reporter_window`, `exclude_nicks`, `exclude_body_patterns` override

### 2) DB 확장(웹 서비스 운영 편의)

아래 테이블 추가를 권장한다.

- `project_settings` (선택: YAML을 쓰더라도, 웹에서 설정한 email만 DB로 저장하고 YAML과 merge)
  - `project_id` PK
  - `email_sender`
  - `email_receivers_json`
  - `updated_at`
- `report_runs` / `report_outputs`
  - `id`, `project_id`, `created_at`, `window`, `output_path`, `period_keys_json`
  - `stats_snapshot_json` (웹 통계 페이지에서 그대로 렌더링용)
  - (추가) `scope_json` (리포트 생성 당시 사용한 data_scope 설정을 저장)

---

## 서버/웹 아키텍처(권장: FastAPI)

### 실행 방식

- 서버 프로세스: `python -m openchat serve` (신규 CLI 커맨드)
- 바인딩: `0.0.0.0:{PORT}` (기본 8000)
- 정적 파일:
  - UI 번들을 만들지 않는 단순 버전: Jinja2 템플릿 + HTMX(또는 순수 JS)
  - 리포트 HTML 제공: `reports/`를 read-only로 서빙(경로 traversal 방지)

### 작업 실행(수집/분석/리포트)

웹에서 버튼 클릭 시 시간이 오래 걸릴 수 있으므로, 아래 중 하나를 택한다.

- 1차: **동기 실행 + 진행 로그 스트리밍**
  - 내부망/소규모 운영에 단순
- 2차(권장): **백그라운드 잡 큐(내장)**
  - Python `threading` + DB에 job 상태 저장
  - 웹은 job 상태를 폴링/스트리밍

초기 구현은 “동기 실행(짧은 작업)” + “리포트/분석은 백그라운드 job”의 혼합을 권장한다.

---

## API/페이지 설계(초기 MVP)

### 페이지(웹 UI)

- `/` 대시보드
  - 프로젝트 목록, 최근 수집/분석/리포트 상태 요약, 최근 에러
- `/projects`
  - 프로젝트 목록 + 생성 버튼
- `/projects/{project_id}`
  - 프로젝트 상세(라벨, titles, update_notes_url, 제외 규칙, 이메일 sender/receivers)
  - 실행 버튼:
    - 수집 1회 실행(해당 프로젝트 titles 대상)
    - 분석 실행(해당 프로젝트 스코프)
    - 리포트 생성
    - 리포트 생성 + 이메일 발송
- `/settings`
  - 전역 설정(.env 기반 값 중 UI에서 다룰 항목)
    - 수집 주기(`COLLECT_INTERVAL_MINUTES`)
    - 분석 주기(`ANALYZER_PERIOD`)
    - 리포트 기본 윈도우(`REPORTER_WINDOW`)
    - 출력 디렉토리 등
  - (추가) **데이터 스코프(공유)**
    - 최근 N일(`data_scope.last_days`)
    - 적용 기준: `message_at`(초기 고정)
- `/stats/projects/{project_id}`
  - 프로젝트 통계(최근 N일/최근 N버킷)
- `/reports`
  - 생성된 리포트 목록(프로젝트별 필터)
- `/reports/{report_run_id}`
  - 리포트 미리보기(HTML iframe 또는 링크)
  - 해당 리포트의 통계 스냅샷 표시

### API(내부용)

- `GET /api/projects`
- `POST /api/projects`
- `GET /api/projects/{id}`
- `PUT /api/projects/{id}`
- `DELETE /api/projects/{id}`
- `POST /api/projects/{id}:collect`
- `POST /api/projects/{id}:analyze`
- `POST /api/projects/{id}:report`
- `POST /api/projects/{id}:report-email`
- `GET /api/projects/{id}/stats`
- `GET /api/reports`
- `GET /api/reports/{id}`
  - (추가) 스코프
    - `GET /api/settings/scope`
    - `PUT /api/settings/scope`  (YAML 저장 + 즉시 반영)

---

## 구현 단계(권장 순서)

### 0단계: 준비/정리 — 완료

- `.gitignore` 운영 산출물 포함
- CLI·웹 공통 설정 로드 경로 정리

### 1단계: 설정 스키마 확장(프로젝트 + titles) — 완료

- `config/projects.yaml` + `ProjectConfig` / `titles[]`
- `collector/runner.py`: 프로젝트별 복수 title 수집
- 구 `rooms.yaml` 호환

### 2단계: 웹 서버 골격 + 프로젝트 CRUD — 완료

- `openchat/webapp.py`, Jinja2 템플릿
- `projects_store.atomic_write_yaml`, `/api/projects`, 설정 reload

### 3단계: 전역/프로젝트 설정 UI — 완료

- `/settings`, `config/ui_settings.yaml`
- `data_scope.last_days` → 리포트·이메일·통계 공유 (`effective_scope_days`)

### 4단계: 실행 버튼(수집/분석/리포트) — 완료

- 프로젝트 상세 실행 버튼, `background_jobs`, `/reports`, `/jobs/{id}`

### 5단계: 이메일 — 완료

- 프로젝트 `email.sender` / `receivers`, `report/openchat_email.py`
- 리포트+이메일, 리포트별 발송, `email_snapshot_json`

### 6단계: 통계 웹 조회 — 완료 (1차: 표)

- `/stats/projects/{id}`, `GET /api/projects/{id}/stats`
- `periodic_insights` 즉석 집계 + `message_at` 일별 표
- **후속**: Chart.js 웹 재사용 (2차)

---

## 후속 개선(선택)

- 통계 페이지 차트 (`report/charts.py` 연동)
- Windows 서비스·작업 스케줄러 등록 가이드
- 리포트/DB 보관 자동 purge
- 웹 인증(필요 시)

---

## 통계/리포트 스코프(프로젝트 단위) 정의

프로젝트는 복수 titles를 가지므로, DB에는 아래 중 하나로 스코프를 잡는다.

- 메시지 저장 시점에 `room_id`를 “프로젝트 id”로 저장(권장)
  - title은 `source_room_title` 같은 필드로 보관(추적용)
- 또는 메시지를 (project_id, title)로 같이 저장하고, 조회 시 project_id로 묶음

리포트/통계/분석 모두 동일 스코프(프로젝트)로 동작하도록 통일한다.

---

## 운영/배포(내부망)

- 실행: Windows 서비스로 등록하거나, 작업 스케줄러/백그라운드 실행
- 포트: 사내 정책에 맞춰 방화벽/예외 처리
- 데이터:
  - `data/openchat.db` 백업 정책
  - `reports/` 보관 정책(기본 N일 또는 N개 유지)
- 보안:
  - 인증 없음이므로, 접근 가능한 네트워크를 내부로 제한(방화벽)
  - 리포트/로그에서 개인정보/민감정보 취급 기준은 별도 운영 정책으로 합의

---

## 테스트 계획(최소)

자동화 (`pytest`):

- `tests/openchat/` — 설정, projects_store, webapp, pipeline, email
- `tests/stats/test_project_stats.py` — 스코프·집계
- `tests/report/test_scope.py` — 리포트 버킷 필터

수동 스모크 (웹):

1. 프로젝트 생성 → 수집 → 분석(job) → 리포트 → 통계 → (선택) 이메일

회귀: CLI `collect` / `analyze` / `report` / `pipeline` 동작 확인

