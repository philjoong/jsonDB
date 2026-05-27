# open-chat

카카오톡 PC 오픈채팅 **클립보드 수집** 파이프라인 (설계: `design.md`, 구현 계획: `development-plan.md`).

## 설치

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

`config/rooms.yaml`의 `title`을 PC 카카오톡 창 제목과 **완전 일치**하도록 수정하세요 (끝 `(N)` 안 읽음 수는 자동 제거 후 비교).

## 수집 (1a–2c)

한 번 수집 (클립보드 → 파싱 → SQLite `messages`):

```powershell
python -m openchat collect
```

10분 주기 watch:

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

- 성공한 방: `data/openchat.db`에 `content_hash` 기준 중복 무시 insert, `data/state/{room_id}_last.txt` 스냅샷 갱신
- 선택: 신규 줄 `captures/{room_id}/capture_*_diff.txt`
- 창 없음: 해당 방 스킵 + `collect_runs` 기록
- 클립보드: 수집 후 복원 (`.env`에서 `NO_RESTORE_CLIPBOARD=true`로 비활성 가능)

레거시 단일 방 PoC:

```powershell
python kakao_clipboard_crawler.py --room "방 제목"
```
