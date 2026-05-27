# open-chat

카카오톡 PC 오픈채팅 **클립보드 수집** → 분석 → 통계 → HTML 리포트 파이프라인.

- 전체 설계: [`development-plan.md`](development-plan.md)
- **웹 UI (내부망)**: [`docs/web-service-guide.md`](docs/web-service-guide.md) · 계획: [`simple-service-development-plan.md`](simple-service-development-plan.md)

## 설치

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

프로젝트 설정은 `config/projects.yaml`(권장) 또는 `config/rooms.yaml`을 사용합니다.  
창 제목(`titles`)은 PC 카카오톡과 **완전 일치**해야 합니다 (끝 `(N)` 안 읽음 수는 자동 제거 후 비교).

예시: [`config/projects.yaml`](config/projects.yaml)

## 웹 UI (Simple Service)

카카오톡이 실행된 **같은 Windows PC**에서:

```powershell
python -m openchat serve
```

기본 주소: `http://0.0.0.0:8000` (사내망에서 `http://<PC IP>:8000`)

| 기능 | 웹 경로 |
|------|---------|
| 프로젝트 CRUD | `/projects` |
| 수집·분석·리포트 실행 | `/projects/{id}` |
| 전역 주기·데이터 스코프 (최근 N일) | `/settings` |
| 리포트 목록·미리보기 | `/reports` |
| 프로젝트 통계 | `/stats/projects/{id}` |

운영 주기·리포트/통계/이메일에 쓰이는 **최근 N일**은 `config/ui_settings.yaml`에 저장합니다.  
API 키·메일 API URL은 `.env`에 둡니다.

자세한 내용: **[docs/web-service-guide.md](docs/web-service-guide.md)**

## CLI — 수집 (1a–2c)

한 번 수집 (클립보드 → 파싱 → SQLite `messages`):

```powershell
python -m openchat collect
```

10분 주기 watch (`COLLECT_INTERVAL_MINUTES` 또는 `ui_settings.yaml`):

```powershell
python -m openchat collect --watch
```

한 사이클만 실행 후 종료:

```powershell
python -m openchat collect --watch --once
```

7일 초과 원시 메시지 삭제:

```powershell
python -m openchat purge
```

- 성공한 방: `data/openchat.db`에 `content_hash` 기준 중복 무시 insert, `data/state/` 스냅샷 갱신
- 선택: `captures/{room_id}/capture_*`
- 창 없음: 해당 방 스킵 + `collect_runs` 기록

## CLI — 분석·리포트

```powershell
python -m openchat analyze
python -m openchat aggregate
python -m openchat report
python -m openchat pipeline    # collect → analyze → aggregate → report
```

## 레거시 단일 방 PoC

```powershell
python kakao_clipboard_crawler.py --room "방 제목"
```

## 테스트

```powershell
python -m pytest
```
