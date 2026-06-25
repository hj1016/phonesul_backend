"""테스트 공용 픽스처 — fakeredis로 RoomStore를 대체(자동 테스트는 Redis 서버 불요).

설계:
- 테스트마다 새 FakeServer를 만들어 격리한다.
- 앱 전역 RoomStore(main.rooms)의 클라이언트를 이 서버 기반 async fakeredis로 교체하고,
  직접 RoomStore()를 만드는 단위 테스트도 같은 서버를 쓰도록 aioredis.from_url을 패치한다.
- redis_sync는 같은 FakeServer 데이터를 공유하는 동기 클라이언트 — TestClient(동기) 테스트가
  Redis 상태를 동기적으로 점검/조작할 수 있게 한다(TTL 만료·키 소멸 모사 등).
"""

from __future__ import annotations

import fakeredis
import fakeredis.aioredis
import pytest

import app.main as main
import app.room_store as room_store


@pytest.fixture
def fake_server() -> fakeredis.FakeServer:
    return fakeredis.FakeServer()


@pytest.fixture(autouse=True)
def patch_redis(fake_server, monkeypatch):
    """앱·단위 테스트의 Redis를 공유 FakeServer 기반 fakeredis로 대체."""

    def _make_async():
        return fakeredis.aioredis.FakeRedis(server=fake_server, decode_responses=True)

    monkeypatch.setattr(main.rooms, "_r", _make_async())
    monkeypatch.setattr(room_store.aioredis, "from_url", lambda *a, **k: _make_async())
    yield


@pytest.fixture
def redis_sync(fake_server) -> fakeredis.FakeStrictRedis:
    """같은 FakeServer 데이터를 공유하는 동기 클라이언트(테스트 상태 점검·조작용)."""
    return fakeredis.FakeStrictRedis(server=fake_server, decode_responses=True)
