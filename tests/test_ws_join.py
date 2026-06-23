"""통합 테스트 (F-RT-03/04): 입장 → roster → 퇴장."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app


def _new_room(client: TestClient) -> str:
    return client.post("/rooms").json()["code"]


def test_join_broadcasts_roster_to_all_members():
    client = TestClient(app)
    code = _new_room(client)

    with client.websocket_connect(f"/ws/{code}") as ws1:
        r1 = ws1.receive_json()
        assert r1["type"] == "roster"
        assert r1["count"] == 1
        assert len(r1["members"]) == 1
        # 익명 식별자만 — 토스 식별값 아님(platform §3).
        assert isinstance(r1["members"][0], str) and r1["members"][0]

        with client.websocket_connect(f"/ws/{code}") as ws2:
            # 새로 들어온 ws2와 기존 ws1 모두 인원 2로 갱신된 roster 수신.
            r2 = ws2.receive_json()
            r1b = ws1.receive_json()
            assert r2["count"] == 2
            assert r1b["count"] == 2
            assert len(set(r2["members"])) == 2  # 멤버 식별자 고유

        # ws2 퇴장 → 남은 ws1이 인원 1로 갱신된 roster 수신.
        r1c = ws1.receive_json()
        assert r1c["count"] == 1


def test_invalid_code_rejected_with_error_then_close():
    client = TestClient(app)
    # 존재하지 않는 코드(방 생성 안 함).
    with client.websocket_connect("/ws/ZZZZZZ") as ws:
        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["code"] == "invalid_code"
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()


def test_unknown_message_type_returns_error_but_keeps_connection():
    client = TestClient(app)
    code = _new_room(client)
    with client.websocket_connect(f"/ws/{code}") as ws:
        ws.receive_json()  # 최초 roster
        ws.send_json({"type": "definitely-not-a-type"})
        err = ws.receive_json()
        assert err["type"] == "error"
        assert err["code"] == "unknown_type"


def test_member_removed_on_disconnect():
    client = TestClient(app)
    code = _new_room(client)
    with client.websocket_connect(f"/ws/{code}") as ws1:
        ws1.receive_json()  # count 1
        with client.websocket_connect(f"/ws/{code}") as ws2:
            ws2.receive_json()
            ws1.receive_json()  # count 2
        roster_after_leave = ws1.receive_json()
        assert roster_after_leave["count"] == 1
        assert len(roster_after_leave["members"]) == 1
