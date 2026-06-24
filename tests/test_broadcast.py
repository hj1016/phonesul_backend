"""ConnectionManager.broadcast 동시 전송(gather) 경로 검증.

수신 느린/끊긴 한 소켓이 같은 방 전체 전송을 막지 않는다 — 개별 실패는 흡수되고
나머지 소켓에는 정상 전달된다(단일 박스 피크 부하 튜닝).
"""

from __future__ import annotations

from app.connection_manager import ConnectionManager


class _FakeWS:
    """send_json만 흉내내는 가짜 소켓. fail=True면 전송 시 예외."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.received: list[dict] = []

    async def send_json(self, payload: dict) -> None:
        if self.fail:
            raise RuntimeError("broken socket")
        self.received.append(payload)


async def test_broadcast_delivers_despite_one_failure():
    mgr = ConnectionManager()
    good1, bad, good2 = _FakeWS(), _FakeWS(fail=True), _FakeWS()
    await mgr.register("R", good1, "m1")
    await mgr.register("R", bad, "m2")
    await mgr.register("R", good2, "m3")

    payload = {"type": "cheers", "room": "R"}
    # 한 소켓이 예외를 던져도 broadcast 자체는 예외 없이 완료되어야 한다.
    await mgr.broadcast("R", payload)

    # 실패 소켓을 제외한 나머지에는 1회씩 정상 전달.
    assert good1.received == [payload]
    assert good2.received == [payload]
    assert bad.received == []


async def test_broadcast_empty_room_is_noop():
    mgr = ConnectionManager()
    # 소켓이 없는 방에 broadcast해도 예외 없이 통과.
    await mgr.broadcast("EMPTY", {"type": "roster", "room": "EMPTY"})
