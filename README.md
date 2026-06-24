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

# 3) 상태 저장소(Redis) — 재시작 내구성·유휴/retired TTL용. 로컬은 Docker로:
#    docker run -d --name phonesul-redis -p 6379:6379 redis:7
#    (Windows는 Docker Desktop + WSL2 전제)
#    접속 주소는 환경변수 REDIS_URL로 주입(미설정 시 기본 redis://localhost:6379/0):
#    PowerShell: $env:REDIS_URL = "redis://localhost:6379/0"

# 4) 기동 (로컬 개발 — Windows는 uvloop 미지원이라 기본 asyncio 루프)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

- **자동 테스트는 Redis 불필요**: `pytest`는 `fakeredis`(인메모리)로 돌아 Redis 컨테이너 없이 통과.
  실기동(`uvicorn`)·수동 검증에만 위 Redis 컨테이너가 필요.
- 수천 동시 연결 운영 시 OS 파일 디스크립터 한도 상향 필요 → 아래 *운영 튜닝* 참고(`ulimit -n`).
- 헬스체크: `GET /health` → `{"status":"ok"}`
- 운영 지표(가벼움): `GET /metrics` → `{"connections":N,"rooms":N,"loop_lag_ms":N}`.
  서버 로그에도 주기적으로 `monitor loop_lag_ms=... connections=... rooms=...` 출력.
- 방 생성: `POST /rooms` → `{"code","hostToken"}` · 방장 종료: `POST /rooms/{code}/close`(헤더 `X-Host-Token`).
- wss: `ws://localhost:8000/ws/{room_code}` (방장은 `?host_token=` 첨부). 메시지 `type`: `cheers`/`leave` 등(JSON).

## 운영 튜닝 (단일 박스)

- **단일 프로세스 필수.** 소켓·방 멤버십은 프로세스 로컬(`ConnectionManager`)이라 워커를
  늘리면 broadcast가 깨진다 → `--workers 1`(기본) 유지. 다중 인스턴스 확장은 범위 밖.
- **uvloop(운영, Linux).** `uvicorn[standard]`에 포함 — 추가 의존성 없음. 운영 기동 시 명시:
  ```bash
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --loop uvloop --workers 1
  ```
  Windows 로컬 개발은 uvloop 미지원 → 기본 asyncio 루프로 자동 폴백.
- **파일 디스크립터 상한(FD).** 동시 수천 연결을 받으려면 OS의 FD 한도를 올린다(배포 환경
  설정, 코드 아님):
  ```bash
  ulimit -n 65536       # 또는 systemd 유닛에 LimitNOFILE=65536
  ```
- **한계 판단 근거.** 단일 박스의 진짜 한계는 메모리가 아니라 피크 시 **이벤트 루프 밀림**이다.
  `/metrics`의 `loop_lag_ms`·`connections`가 지속적으로 커지면 그때가 다중 인스턴스(B)로
  넘어갈 신호 — 데이터로 판단한다.

## 구조 (S2 골격)

| 파일 | 역할 |
|---|---|
| `app/main.py` | FastAPI 진입점, 헬스체크, wss 엔드포인트, 타입 분기 골격 |
| `app/connection_manager.py` | 방별 active 소켓 집합 관리·broadcast (context §4) |
| `app/models.py` | 휘발성 in-memory 모델 Room/Member (context §3) |
| `app/messages.py` | JSON 메시지 타입 상수·봉투 빌더 (context §4) |

> 상태 저장소는 Redis(재시작 내구성·TTL 만료). 단일 인스턴스(단일 프로세스) 기준.
> 다중 인스턴스(Redis pub/sub broadcast 중계)는 후순위(context §4).
