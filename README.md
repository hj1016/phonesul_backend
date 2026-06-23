# phonesul_backend

폰술 실시간 건배방 백엔드 (FastAPI + WebSocket). 별도 레포·별도 배포.
규칙은 `CLAUDE.md`(+ pipeline/platform/context)와 `docs/spec` 참조.

## 실행 (로컬, VSCode)

```bash
# 1) 가상환경 (Windows PowerShell)
python -m venv .venv
.venv\Scripts\Activate.ps1

# 2) 의존성
pip install -r requirements.txt

# 3) 기동
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- 헬스체크: `GET http://localhost:8000/health` → `{"status":"ok"}`
- wss 엔드포인트(골격): `ws://localhost:8000/ws/{room_code}`
  - 연결 시 서버 로그에 `ws connect room=...`, 해제 시 `ws disconnect room=...` 출력.
  - 현재 메시지 처리는 미구현(타입 분기 골격만) — 모든 요청에 `not_implemented` error 응답.

## 구조 (S2 골격)

| 파일 | 역할 |
|---|---|
| `app/main.py` | FastAPI 진입점, 헬스체크, wss 엔드포인트, 타입 분기 골격 |
| `app/connection_manager.py` | 방별 active 소켓 집합 관리·broadcast (context §4) |
| `app/models.py` | 휘발성 in-memory 모델 Room/Member (context §3) |
| `app/messages.py` | JSON 메시지 타입 상수·봉투 빌더 (context §4) |

> 1차 단일 인스턴스 in-memory. 다중 인스턴스(Redis pub/sub)는 후순위(context §4).
