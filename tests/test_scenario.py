"""통합 시나리오 테스트 (S3): 생성→참여→짠 broadcast→퇴장→방장 종료 전 과정."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app


def _drain_rosters(ws, n: int) -> None:
    for _ in range(n):
        assert ws.receive_json()["type"] == "roster"


def test_full_lifecycle_create_join_cheers_leave_close():
    client = TestClient(app)
    body = client.post("/rooms").json()
    code, token = body["code"], body["hostToken"]

    with client.websocket_connect(f"/ws/{code}") as ws1:
        _drain_rosters(ws1, 1)  # 입장: 인원 1

        with client.websocket_connect(f"/ws/{code}") as ws2:
            _drain_rosters(ws2, 1)  # ws2 자신의 roster(2)
            _drain_rosters(ws1, 1)  # ws1이 본 ws2 입장 roster(2)

            # 짠: 전원 1회씩 수신.
            ws1.send_json({"type": "cheers"})
            assert ws1.receive_json()["type"] == "cheers"
            assert ws2.receive_json()["type"] == "cheers"

        # ws2 퇴장 → ws1이 인원 1로 갱신된 roster 수신.
        leave_roster = ws1.receive_json()
        assert leave_roster["type"] == "roster"
        assert leave_roster["count"] == 1

        # 방장 종료 → ws1이 room_closed 수신 후 소켓 종료.
        res = client.post(f"/rooms/{code}/close", headers={"X-Host-Token": token})
        assert res.status_code == 200
        closed = ws1.receive_json()
        assert closed["type"] == "room_closed"
        assert closed["reason"] == "host_ended"
        with pytest.raises(WebSocketDisconnect):
            ws1.receive_json()

    # 종료된 코드는 재입장 불가(재사용 금지).
    with client.websocket_connect(f"/ws/{code}") as ws3:
        assert ws3.receive_json()["code"] == "invalid_code"
