"""테스트 (F-RT-06/07): 방장 종료·유휴 만료·코드 재사용 금지·재연결."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app, rooms


def _create(client: TestClient) -> tuple[str, str]:
    body = client.post("/rooms").json()
    return body["code"], body["hostToken"]


# ---- F-RT-06: 방장 종료 ----

def test_host_close_notifies_members_and_closes_socket():
    client = TestClient(app)
    code, token = _create(client)
    with client.websocket_connect(f"/ws/{code}") as ws:
        ws.receive_json()  # 최초 roster
        res = client.post(f"/rooms/{code}/close", headers={"X-Host-Token": token})
        assert res.status_code == 200
        assert res.json()["status"] == "closed"

        msg = ws.receive_json()
        assert msg["type"] == "room_closed"
        assert msg["reason"] == "host_ended"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_host_close_wrong_token_rejected():
    client = TestClient(app)
    code, _token = _create(client)
    res = client.post(f"/rooms/{code}/close", headers={"X-Host-Token": "wrong"})
    assert res.status_code == 404  # 방 존재·토큰 추측 단서 최소화


def test_close_unknown_room_404():
    client = TestClient(app)
    res = client.post("/rooms/ZZZZZZ/close", headers={"X-Host-Token": "x"})
    assert res.status_code == 404


def test_closed_room_code_cannot_be_rejoined():
    client = TestClient(app)
    code, token = _create(client)
    client.post(f"/rooms/{code}/close", headers={"X-Host-Token": token})
    # 폐기된 코드로 재입장 시도 → 거절.
    with client.websocket_connect(f"/ws/{code}") as ws:
        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["code"] == "invalid_code"


def test_closed_code_is_retired_and_not_reused(monkeypatch):
    code, token = _create(TestClient(app))
    rooms.close(code)
    assert rooms.is_retired(code) is True
    # generate_code가 폐기 코드를 먼저 내도 재사용하지 않고 다음 코드를 발급.
    seq = iter([code, "ABC234"])
    monkeypatch.setattr("app.room_store.generate_code", lambda: next(seq))
    new_room = rooms.create_room()
    assert new_room.code == "ABC234"


# ---- F-RT-06: 유휴 만료(백그라운드 sweep) ----

def test_idle_expiry_terminates_room(monkeypatch):
    monkeypatch.setattr("app.main.SWEEP_INTERVAL_SECONDS", 0.1)
    client = TestClient(app)
    with client:  # lifespan 시작 → 만료 sweep 백그라운드 태스크 기동
        code, _ = _create(client)
        with client.websocket_connect(f"/ws/{code}") as ws:
            ws.receive_json()  # 최초 roster
            # 방을 유휴 만료 상태로 강제(연결 시 touch된 시각을 과거로 되돌림).
            room = rooms.get(code)
            room.last_active_at = room.last_active_at - timedelta(minutes=999)
            msg = ws.receive_json()  # sweeper가 만료 처리 → room_closed
            assert msg["type"] == "room_closed"
            assert msg["reason"] == "expired"


# ---- F-RT-07: 재연결(기본) ----

def test_reconnect_restores_roster():
    client = TestClient(app)
    code, _ = _create(client)
    # 첫 연결 후 종료(끊김 모사).
    with client.websocket_connect(f"/ws/{code}") as ws1:
        assert ws1.receive_json()["count"] == 1
    # 같은 코드로 재연결 → 재입장, roster 재전송으로 상태 복구.
    with client.websocket_connect(f"/ws/{code}") as ws2:
        roster = ws2.receive_json()
        assert roster["type"] == "roster"
        assert roster["count"] == 1
