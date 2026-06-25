"""폰술 건배방 서버 — FastAPI 진입점.

범위: 헬스체크 + 방 생성(F-RT-01) + 방 참여·인원 동기화(F-RT-03/04)
      + 짠 broadcast(F-RT-05) + 방 종료·만료(F-RT-06) + 재연결(F-RT-07 기본).

통신 분리(platform §1 vs §2):
  - 건배방 실시간(클라이언트 ↔ 이 서버)은 내부 wss. 앱인토스 API 규약(mTLS·QPM) 무관.
  - 앱인토스 API 호출 없음(핵심 경로에서 분리, platform §2).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import datetime, timezone

from fastapi import FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app import messages
from app.abuse import AttemptLimiter
from app.connection_manager import ConnectionManager
from app.models import (
    MAX_MEMBERS_PER_ROOM,
    Member,
    Room,
    cheers_allowed,
    generate_member_id,
)
from app.room_store import RoomStore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("phonesul.main")

manager = ConnectionManager()
rooms = RoomStore()
limiter = AttemptLimiter()

# WebSocket close 코드(RFC 6455): 1008 정책 위반, 1001 going away(방 종료).
WS_POLICY_VIOLATION = 1008
WS_GOING_AWAY = 1001

# ASSUMPTION: 모니터 샘플링 주기(초) — 임의값. 가벼운 수준의 주기 로깅·지표 갱신.
MONITOR_INTERVAL_SECONDS = 30

# 최근 모니터 샘플(가벼운 지표). 단일 박스의 진짜 한계는 메모리가 아니라 피크 시 이벤트
# 루프 밀림이므로, "B(다중 인스턴스)로 넘어갈 시점"을 데이터로 판단할 근거를 남긴다.
_last_monitor: dict[str, float | int] = {"loop_lag_ms": 0.0, "connections": 0, "rooms": 0}


async def _monitor_loop() -> None:
    """이벤트 루프 밀림(lag)과 동시 연결 수를 주기적으로 샘플링·로깅(가벼운 형태).

    sleep(interval)이 예상보다 늦게 깬 만큼이 루프 밀림이다 — 핸들러가 루프를 오래
    점유할수록 커진다. 과설계 금지: 외부 의존성·고해상도 히스토그램 없이 단일 샘플만.
    """
    loop = asyncio.get_running_loop()
    while True:
        start = loop.time()
        await asyncio.sleep(MONITOR_INTERVAL_SECONDS)
        lag_ms = max(0.0, (loop.time() - start - MONITOR_INTERVAL_SECONDS) * 1000)
        conns = manager.total_connections()
        rooms_n = manager.room_count()
        _last_monitor.update(loop_lag_ms=round(lag_ms, 1), connections=conns, rooms=rooms_n)
        logger.info(
            "monitor loop_lag_ms=%.1f connections=%d rooms=%d", lag_ms, conns, rooms_n
        )


async def _terminate_room(room: Room, reason: str) -> None:
    """방·멤버·소켓 정리·폐기 후 코드 재사용 금지(F-RT-06).

    reason: host_ended | expired. 전원에 room_closed 안내 → 소켓 종료 → 방 폐기.
    """
    await manager.broadcast(room.code, messages.room_closed(room.code, reason))
    for ws in manager.sockets_in(room.code):
        with contextlib.suppress(Exception):
            await ws.close(code=WS_GOING_AWAY)
        manager.unregister(ws)
    await rooms.close(room.code)  # remove + 코드 retire(재사용 금지)
    logger.info("room terminated code=%s reason=%s", room.code, reason)


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    """앱 수명주기. 유휴 만료는 Redis 키 TTL이 처리하므로 만료 sweep은 없다.

    시작 시 가벼운 모니터 루프(루프 밀림·연결 수)를 띄우고, 종료 시 이를 정리한 뒤
    Redis 연결 풀을 닫는다(생성은 RoomStore가 첫 명령 때 지연 수행). 단일 박스 —
    소켓은 프로세스 로컬(ConnectionManager).
    """
    monitor = asyncio.create_task(_monitor_loop())
    try:
        yield
    finally:
        monitor.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await monitor
        await rooms.aclose()


app = FastAPI(title="phonesul-server", version="0.1.0", lifespan=lifespan)

# UNVERIFIED(토스 WebView Origin): 토스 WebView가 보내는 Origin 값 미확인(context §4·§8).
# 실기기/토스 WebView 없이 검증 불가. "*"는 전부 허용. 출시 전 실제 Origin으로 좁혀야 함.
ALLOWED_ORIGINS = ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict[str, str]:
    """헬스체크. 로컬 uvicorn 기동 확인용."""
    return {"status": "ok"}


@app.get("/metrics")
async def metrics() -> dict[str, float | int]:
    """가벼운 운영 지표 — 단일 박스 한계 판단 근거(과설계 금지).

    동시 연결 수·활성 방 수·최근 이벤트 루프 밀림(ms)만 노출한다. 단일 박스의 진짜
    한계는 메모리가 아니라 피크 시 루프 밀림이므로, "B(다중 인스턴스)로 넘어갈 시점"을
    데이터로 판단하기 위함(context §4·§8). 개인정보·짠 집계는 노출하지 않는다(platform
    §3·context §6 — 운영 카운터일 뿐 비게임 지표 아님).
    """
    return {
        "connections": manager.total_connections(),
        "rooms": manager.room_count(),
        "loop_lag_ms": _last_monitor["loop_lag_ms"],
    }


@app.post("/rooms", status_code=201)
async def create_room() -> dict[str, str]:
    """방 생성 (F-RT-01).

    Room 생성 + 랜덤 코드 발급 + 방장 토큰 반환.
    REVIEW(로그인 강제 여부): 방장 토큰만으로 권한 구분, 토스 로그인 비강제(platform §3).
    hostToken은 생성자에게만 1회 반환. 서버는 영속 저장 안 함(휘발, platform §3).
    """
    room = await rooms.create_room()
    return {"code": room.code, "hostToken": room.host_token}


@app.post("/rooms/{room_code}/close")
async def close_room(
    room_code: str, x_host_token: str = Header(...)
) -> dict[str, str]:
    """방장 종료 (F-RT-06).

    방장 토큰(X-Host-Token) 검증 후 방·멤버·소켓 폐기. 코드는 재사용 금지.
    토큰 불일치는 404로 응답(방 존재 여부 노출 최소화). 폐기 후 같은 코드 입장 불가.
    """
    room = await rooms.get(room_code)
    # 방 없음 / 토큰 불일치를 동일 404로 처리 — 방 존재·토큰 추측 단서 최소화.
    if room is None or x_host_token != room.host_token:
        raise HTTPException(status_code=404, detail="방을 찾을 수 없어요.")
    await _terminate_room(room, "host_ended")
    return {"status": "closed", "code": room_code}


# REVIEW(wss: 운영자 채널톡 확인 2026-06-22): 미니앱 WebView ↔ 우리 서버 wss 사용 가능
# 근거 기록. 정책 개정 가능성 있어 출시 전 재확인(platform §1·§5).
@app.websocket("/ws/{room_code}")
async def ws_endpoint(websocket: WebSocket, room_code: str) -> None:
    """방 참여 + 인원 동기화 (F-RT-03/04) + 재연결 (F-RT-07 기본).

    연결 = 참여. 코드로 방 식별 → 익명 멤버 등록 → roster 브로드캐스트.
    잘못된/만료/정원초과 코드는 error + 연결 종료. 끊김 시 멤버 제거 후 roster 갱신.

    재연결(F-RT-07): 끊긴 클라이언트가 같은 코드로 다시 연결하면 재입장으로 처리되고
    아래 5)에서 roster가 재전송되어 인원 상태가 복구된다. 멤버 식별자(member_id)를
    유지하는 identity-resume는 후순위(범위 외) — 1차는 익명 재입장으로 충분.

    REVIEW(백그라운드 소켓 처리): 미니앱 백그라운드 전환 시 소켓·사운드 처리(프론트
    F-SY-04 연계). 서버는 '백그라운드'와 '끊김'을 구분할 수 없어 유휴 만료에 위임한다.
    클라이언트가 백그라운드에서 소켓 유지/정리 중 무엇을 할지는 프론트와 합의 필요.
    """
    await websocket.accept()

    # 1) Origin 검사 — WebSocket은 CORSMiddleware 미적용이라 수동 확인.
    if not _origin_allowed(websocket):
        await _reject(websocket, "forbidden_origin", "허용되지 않은 접속이에요.")
        return

    client_key = _client_key(websocket)

    # 2) 코드 추측 시도 제한(platform §3 남용 방지). 임계 초과 시 차단.
    if limiter.is_blocked(client_key):
        await _reject(websocket, "too_many_attempts", "잠시 후 다시 시도해 주세요.")
        return

    # 3) 코드 검증 — 없음/만료는 실패 기록 후 거절.
    room = await rooms.get(room_code)
    if room is None or await rooms.is_expired(room):
        limiter.record_failure(client_key)
        await _reject(websocket, "invalid_code", "방을 찾을 수 없어요.")
        return

    # 4) 정원 검사 — 현재 연결된 소켓 수 기준(roster 진실 원천 = ConnectionManager).
    if manager.room_size(room_code) >= MAX_MEMBERS_PER_ROOM:
        await _reject(websocket, "room_full", "방 인원이 가득 찼어요.")
        return

    # 5) 멤버 등록(익명 식별자만, platform §3). 성공 시 시도 기록 해제.
    limiter.reset(client_key)
    # 방장 식별: 연결 시 host_token 쿼리 파라미터가 방 토큰과 일치하면 방장 소켓.
    # REVIEW(host_token URL 노출): 쿼리 파라미터는 wss(TLS)로 전송 중엔 암호화되나
    # 서버/프록시 접근 로그·브라우저 히스토리에 남을 수 있음. 운영 로그 토큰 마스킹
    # 또는 연결 후 첫 메시지 인증으로의 전환을 운영자와 확인(platform §3 인증/식별).
    host_token = websocket.query_params.get("host_token")
    is_host = bool(host_token) and host_token == room.host_token
    member = Member(
        member_id=generate_member_id(),
        socket_id=generate_member_id(),
        is_host=is_host,
    )
    # 멤버십은 ConnectionManager(살아있는 소켓)가 보유 — Room에는 저장하지 않는다
    # (platform §3 휘발, 재접속으로 재구성). roster·정원도 여기서 파생.
    await rooms.touch(room.code)  # 활동 → Redis 키 TTL 슬라이딩(models.Room.touch 대체)
    await manager.register(room_code, websocket, member.member_id)
    logger.info("ws join room=%s member=%s", room_code, member.member_id)
    await _broadcast_roster(room.code)

    # 6) 메시지 수신 루프. 끊김/종료 시 멤버 제거 후 roster 갱신.
    #    _dispatch가 False를 반환하면(방장 leave로 방이 종료되어 이 소켓도 닫힘) 루프 종료.
    closed = False
    try:
        while True:
            raw = await websocket.receive_text()
            if not await _dispatch(room, member, websocket, raw):
                closed = True
                break
    except WebSocketDisconnect:
        logger.info("ws disconnect room=%s member=%s", room_code, member.member_id)
    finally:
        manager.unregister(websocket)
        # 방장 leave로 이미 방 전체가 종료된 경우 roster 정리는 _terminate_room이
        # 수행했으므로 중복 처리하지 않는다(빈 방에 roster 재전송 방지).
        if not closed:
            await rooms.touch(room.code)  # 활동 → Redis 키 TTL 슬라이딩
            await _broadcast_roster(room.code)


async def _dispatch(
    room: Room, member: Member, websocket: WebSocket, raw: str
) -> bool:
    """수신 JSON을 타입별로 분기(context §4).

    반환값은 연결 유지 여부 — False면 이 소켓이 닫혔으니(방장 leave로 방 종료) 수신
    루프를 끝내야 함을 호출부에 알린다. 그 외에는 True(연결 유지).
    """
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        await websocket.send_json(messages.error("bad_json", "메시지 형식이 올바르지 않아요."))
        return True

    msg_type = msg.get("type")
    if msg_type not in messages.CLIENT_TYPES:
        await websocket.send_json(messages.error("unknown_type", "지원하지 않는 요청이에요."))
        return True

    if msg_type == messages.TYPE_CHEERS:
        await _handle_cheers(room, member)
        return True

    if msg_type == messages.TYPE_LEAVE:
        return await _handle_leave(room, member)

    # join은 연결 수립 시 이미 처리됨(연결=참여) — 별도 동작 없음.
    logger.info("ws msg room=%s member=%s type=%s (no-op)", room.code, member.member_id, msg_type)
    return True


async def _handle_leave(room: Room, member: Member) -> bool:
    """명시적 '나가기'(F-RT-04/06). 반환값은 연결 유지 여부.

    방장이 나가기를 누르면(의도적 행동) 방을 종료해 전원 폐기한다(host_ended). 이때
    방장 소켓도 함께 닫히므로 False를 반환해 수신 루프를 끝낸다. 이는 우발적 소켓
    끊김(백그라운드)과 구분된다 — 끊김은 현행대로 방을 유지하고 유휴 만료에 위임하되
    (현행 유지 결정), 명시적 leave만 종료로 처리한다.

    일반 멤버의 leave는 본인만 퇴장 — 클라이언트의 소켓 종료가 수신 루프 finally에서
    멤버 제거·roster 갱신을 일으키므로 서버 측 추가 동작은 없다(현행 유지, True 반환).
    """
    if member.is_host:
        logger.info("ws leave (host) room=%s member=%s", room.code, member.member_id)
        await _terminate_room(room, "host_ended")
        return False
    logger.info("ws leave (member) room=%s member=%s", room.code, member.member_id)
    return True


async def _handle_cheers(room: Room, member: Member) -> None:
    """짠 broadcast (F-RT-05).

    방 전원에게 1회씩 짠 신호를 보낸다(원자성, context §4.1). 누가·몇 번 짠은
    집계·전송하지 않는다(비게임, context §6). 연타는 멤버별 쿨다운으로 디바운스 —
    쿨다운 내 후속 짠은 조용히 드롭(정상 연타이므로 에러 응답 안 함).
    """
    now = datetime.now(timezone.utc)
    if not cheers_allowed(member, now):
        return
    member.last_cheers_at = now
    await rooms.touch(room.code)  # 활동 → Redis 키 TTL 슬라이딩(값 재기록 없는 PEXPIRE)
    # broadcast 봉투에 sender/count 없음 — "누가 먼저/많이"가 구조적으로 불가능.
    await manager.broadcast(room.code, messages.cheers(room.code))


async def _broadcast_roster(room_code: str) -> None:
    """현재 인원 목록을 방 전원에게 동기화(F-RT-04).

    진실 원천은 ConnectionManager(살아있는 소켓) — 재접속 후에도 실제 연결과 일치.
    """
    member_ids = manager.members_in(room_code)
    await manager.broadcast(
        room_code, messages.roster(room_code, len(member_ids), member_ids)
    )


async def _reject(websocket: WebSocket, code: str, message: str) -> None:
    """error 안내 후 연결 종료. 문구는 음주 권장 톤 금지(platform §4)."""
    try:
        await websocket.send_json(messages.error(code, message))
    except Exception:  # noqa: BLE001 - 이미 끊긴 소켓이면 무시
        pass
    await websocket.close(code=WS_POLICY_VIOLATION)


def _origin_allowed(websocket: WebSocket) -> bool:
    # UNVERIFIED(토스 WebView Origin): 실제 Origin 값 확인 전까지 "*"로 전부 허용.
    if "*" in ALLOWED_ORIGINS:
        return True
    origin = websocket.headers.get("origin")
    return origin in ALLOWED_ORIGINS


def _client_key(websocket: WebSocket) -> str:
    # REVIEW(남용 방지): 시도 제한 식별 키 = client IP. WebView/프록시 환경에서
    # 부정확할 수 있음(공유 IP). 운영 환경에서 키 선택 재확인(platform §3).
    return websocket.client.host if websocket.client else "unknown"
