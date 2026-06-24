"""테스트 (F-RT-05): 짠 broadcast 원자성·쿨다운·비게임."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.models import CHEERS_COOLDOWN_MS, Member, cheers_allowed
from app.main import app


# ---- 단위: 쿨다운 판정(결정론, now 주입) ----

def _member() -> Member:
    return Member(member_id="m1", socket_id="s1")


def test_first_cheers_allowed():
    base = datetime(2026, 6, 23, 12, 0, 0, tzinfo=timezone.utc)
    assert cheers_allowed(_member(), now=base) is True


def test_rapid_repeat_blocked_within_cooldown():
    base = datetime(2026, 6, 23, 12, 0, 0, tzinfo=timezone.utc)
    m = _member()
    m.last_cheers_at = base
    within = base + timedelta(milliseconds=CHEERS_COOLDOWN_MS - 1)
    assert cheers_allowed(m, now=within) is False


def test_repeat_allowed_after_cooldown():
    base = datetime(2026, 6, 23, 12, 0, 0, tzinfo=timezone.utc)
    m = _member()
    m.last_cheers_at = base
    after = base + timedelta(milliseconds=CHEERS_COOLDOWN_MS)
    assert cheers_allowed(m, now=after) is True


# ---- 통합: 다중 클라이언트 broadcast ----

def _new_room(client: TestClient) -> str:
    return client.post("/rooms").json()["code"]


def _drain_rosters(ws, n: int) -> None:
    """입장 시 누적된 roster 메시지 n개를 비운다."""
    for _ in range(n):
        msg = ws.receive_json()
        assert msg["type"] == "roster"


def test_cheers_broadcasts_to_all_members_once():
    """1회 짠 = 방 전원 1회씩(원자성). 3 클라이언트 모킹."""
    client = TestClient(app)
    code = _new_room(client)

    with client.websocket_connect(f"/ws/{code}") as ws1, \
         client.websocket_connect(f"/ws/{code}") as ws2, \
         client.websocket_connect(f"/ws/{code}") as ws3:
        # 입장 누적 roster 비우기: ws1=3, ws2=2, ws3=1.
        _drain_rosters(ws1, 3)
        _drain_rosters(ws2, 2)
        _drain_rosters(ws3, 1)

        ws1.send_json({"type": "cheers"})

        for ws in (ws1, ws2, ws3):
            msg = ws.receive_json()
            assert msg["type"] == "cheers"
            assert msg["room"] == code
            # 비게임: sender/count 등 경쟁 유발 필드 없음(context §6).
            assert set(msg.keys()) == {"type", "room"}


def test_cooldown_debounces_rapid_repeat():
    """연타: 쿨다운 내 두 번째 짠은 드롭(broadcast 안 됨)."""
    client = TestClient(app)
    code = _new_room(client)

    with client.websocket_connect(f"/ws/{code}") as ws1, \
         client.websocket_connect(f"/ws/{code}") as ws2:
        _drain_rosters(ws1, 2)
        _drain_rosters(ws2, 1)

        ws1.send_json({"type": "cheers"})       # 첫 짠 → broadcast
        assert ws2.receive_json()["type"] == "cheers"
        assert ws1.receive_json()["type"] == "cheers"

        ws1.send_json({"type": "cheers"})       # 즉시 두 번째 → 쿨다운 드롭
        # 드롭 확인: 다음으로 보낸 미지원 타입의 error가 곧장 오면 두 번째 짠은 없었던 것.
        ws1.send_json({"type": "definitely-not-a-type"})
        nxt = ws1.receive_json()
        assert nxt["type"] == "error"
        assert nxt["code"] == "unknown_type"


def test_different_members_cheer_simultaneously():
    """서로 다른 멤버의 짠은 각각 통과(동시 축하). 쿨다운은 멤버별."""
    client = TestClient(app)
    code = _new_room(client)

    with client.websocket_connect(f"/ws/{code}") as ws1, \
         client.websocket_connect(f"/ws/{code}") as ws2:
        _drain_rosters(ws1, 2)
        _drain_rosters(ws2, 1)

        ws1.send_json({"type": "cheers"})
        assert ws1.receive_json()["type"] == "cheers"
        assert ws2.receive_json()["type"] == "cheers"

        ws2.send_json({"type": "cheers"})       # 다른 멤버 → 통과
        assert ws1.receive_json()["type"] == "cheers"
        assert ws2.receive_json()["type"] == "cheers"


# ---- 핫패스 점검: 짠 경로에 Redis 상태 I/O가 없음(구조적 확인) ----

def test_cheers_hot_path_has_no_redis_state_io(monkeypatch):
    """짠 처리 동안 Redis 상태 읽기/쓰기(get/set/delete/exists/hgetall/scan)가 일어나지

    않음을 구조적으로 확인한다. 유일하게 허용되는 Redis 접촉은 가벼운 TTL 슬라이딩
    (touch=pexpire)뿐 — 방/멤버 상태는 핫패스에서 건드리지 않는다(context §6, 비게임·
    저장 안 함). pexpire는 일부러 감시하지 않는다(허용된 단일 경량 연산).
    """
    import app.main as main

    client = TestClient(app)
    code = _new_room(client)

    state_ops: list[str] = []
    redis = main.rooms._r
    for name in ("get", "set", "delete", "exists", "hgetall", "hset", "mget"):
        original = getattr(redis, name, None)
        if original is None:
            continue

        async def spy(*args, _n=name, _o=original, **kwargs):
            state_ops.append(_n)
            return await _o(*args, **kwargs)

        monkeypatch.setattr(redis, name, spy)

    with client.websocket_connect(f"/ws/{code}") as ws:
        ws.receive_json()       # 입장 roster (connect 단계의 get/set은 여기까지)
        state_ops.clear()       # 짠 처리만 따로 관측
        ws.send_json({"type": "cheers"})
        assert ws.receive_json()["type"] == "cheers"

    # 짠 처리 동안 상태 I/O 0건 — TTL 슬라이딩(pexpire)만 발생.
    assert state_ops == []
