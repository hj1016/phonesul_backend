"""엔드포인트 테스트 (F-RT-01): POST /rooms 방 생성."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health():
    res = client.get("/health")
    assert res.status_code == 200
    assert res.json() == {"status": "ok"}


def test_create_room_returns_code_and_host_token():
    res = client.post("/rooms")
    assert res.status_code == 201
    body = res.json()
    assert len(body["code"]) == 6
    assert body["hostToken"]
    # 실명·연락처 등 식별정보가 응답에 섞이지 않음(platform §3 익명).
    assert set(body.keys()) == {"code", "hostToken"}


def test_create_room_codes_differ():
    a = client.post("/rooms").json()["code"]
    b = client.post("/rooms").json()["code"]
    assert a != b
