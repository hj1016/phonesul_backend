"""안정성 테스트 (S3, context §7): 정원·연타·재연결 반복 시 누수·크래시 없음."""

from __future__ import annotations

from contextlib import ExitStack

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app, manager


def _create(client: TestClient) -> tuple[str, str]:
    body = client.post("/rooms").json()
    return body["code"], body["hostToken"]


def test_capacity_rejects_over_limit(monkeypatch):
    """정원 초과 입장 거절. MAX를 작게 줄여 가볍게 검증."""
    monkeypatch.setattr("app.main.MAX_MEMBERS_PER_ROOM", 3)
    client = TestClient(app)
    code, _ = _create(client)
    with ExitStack() as stack:
        for _ in range(3):
            stack.enter_context(client.websocket_connect(f"/ws/{code}"))
        # 4번째는 정원 초과로 거절.
        with client.websocket_connect(f"/ws/{code}") as ws_over:
            err = ws_over.receive_json()
            assert err["type"] == "error"
            assert err["code"] == "room_full"
            with pytest.raises(WebSocketDisconnect):
                ws_over.receive_json()


def test_no_socket_leak_after_host_close(redis_sync):
    """방장 종료 후 ConnectionManager 내부에 방·소켓 잔여 없음(누수 방지)."""
    client = TestClient(app)
    code, token = _create(client)
    with client.websocket_connect(f"/ws/{code}"), \
         client.websocket_connect(f"/ws/{code}"):
        res = client.post(f"/rooms/{code}/close", headers={"X-Host-Token": token})
        assert res.status_code == 200
        # 종료 응답 시점에 terminate가 동기 완료 → 즉시 단언 가능.
        assert manager.room_size(code) == 0
        assert code not in manager._rooms
        assert all(rc != code for rc, _mid in manager._sockets.values())
        assert redis_sync.exists("room:" + code) == 0  # Redis 방 키도 폐기


def test_repeated_reconnect_no_leak():
    """같은 코드로 반복 재연결/끊김 시 인원이 정확히 복구되고 잔여 없음."""
    client = TestClient(app)
    code, token = _create(client)
    for _ in range(20):
        with client.websocket_connect(f"/ws/{code}") as ws:
            roster = ws.receive_json()
            assert roster["type"] == "roster"
            assert roster["count"] == 1  # 직전 연결이 깔끔히 정리됨
    # 마지막 끊김 후 방엔 소켓이 남지 않음.
    assert manager.room_size(code) == 0
    # 방장 정리.
    client.post(f"/rooms/{code}/close", headers={"X-Host-Token": token})


def test_rapid_cheers_no_crash():
    """짠 연타 다발에도 크래시 없이 수신 루프 유지(쿨다운으로 흡수)."""
    client = TestClient(app)
    code, _ = _create(client)
    with client.websocket_connect(f"/ws/{code}") as ws1, \
         client.websocket_connect(f"/ws/{code}") as ws2:
        # 입장 roster 비우기: ws1=2, ws2=1.
        for _ in range(2):
            ws1.receive_json()
        ws2.receive_json()

        for _ in range(30):
            ws1.send_json({"type": "cheers"})
        # 첫 짠은 broadcast, 나머지는 쿨다운 드롭. 양쪽 모두 1회 수신.
        assert ws1.receive_json()["type"] == "cheers"
        assert ws2.receive_json()["type"] == "cheers"

        # 루프 생존 확인: 정상 메시지에 여전히 응답.
        ws1.send_json({"type": "nope"})
        assert ws1.receive_json()["code"] == "unknown_type"
