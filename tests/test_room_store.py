"""RoomStore 단위 테스트 (F-RT-01) — Redis 백엔드(async + fakeredis).

코드 생성·충돌·재시작 복구·TTL 만료·retired TTL·touch 슬라이딩을 검증한다.
시간 주입 결정론(now=...) 대신, 만료는 Redis 키 TTL을 직접 점검·조작(redis_sync)해
결정론적으로 확인한다.
"""

from __future__ import annotations

import pytest

from app import models
from app.models import CODE_ALPHABET, CODE_LENGTH
from app.room_store import (
    IDLE_EXPIRY_SECONDS,
    RETIRED_TTL_SECONDS,
    ROOM_KEY_PREFIX,
    RoomStore,
)


def test_generate_code_format():
    """코드는 6자, 허용 문자셋만, 혼동문자(0/O/1/I) 미포함(ASSUMPTION 값 검증)."""
    forbidden = set("0O1I")
    for _ in range(500):
        code = models.generate_code()
        assert len(code) == CODE_LENGTH
        assert all(ch in CODE_ALPHABET for ch in code)
        assert not (set(code) & forbidden)


async def test_create_room_returns_code_and_token():
    store = RoomStore()
    room = await store.create_room()
    assert len(room.code) == CODE_LENGTH
    assert room.host_token  # 방장 토큰 발급됨
    got = await store.get(room.code)  # 조회는 새 Room 인스턴스(값 동등)
    assert got is not None
    assert got.code == room.code
    assert got.host_token == room.host_token


async def test_codes_are_unique_across_rooms():
    store = RoomStore()
    codes = {(await store.create_room()).code for _ in range(200)}
    assert len(codes) == 200  # 충돌 없이 전부 고유


async def test_collision_triggers_regeneration(monkeypatch):
    """첫 발급 코드가 기존과 충돌하면 재발급되는지 검증."""
    store = RoomStore()
    existing = await store.create_room()
    # generate_code가 한 번은 기존 코드(충돌), 그다음 새 코드를 내도록 강제.
    seq = iter([existing.code, "ABC234"])
    monkeypatch.setattr("app.room_store.generate_code", lambda: next(seq))
    new_room = await store.create_room()
    assert new_room.code == "ABC234"  # 충돌분을 건너뛰고 재발급
    assert await store.count() == 2


async def test_collision_limit_raises(monkeypatch):
    """항상 같은 코드만 나오면 상한 초과로 예외(무한루프 방지)."""
    store = RoomStore()
    first = await store.create_room()
    monkeypatch.setattr("app.room_store.generate_code", lambda: first.code)
    with pytest.raises(RuntimeError):
        await store.create_room()


async def test_create_sets_idle_ttl(redis_sync):
    """방 키에 유휴 만료 TTL이 설정된다(만료 자동화)."""
    store = RoomStore()
    room = await store.create_room()
    ttl = redis_sync.ttl(ROOM_KEY_PREFIX + room.code)
    assert 0 < ttl <= IDLE_EXPIRY_SECONDS


async def test_touch_slides_idle_ttl(redis_sync):
    """touch가 값 재기록 없이 TTL만 다시 민다(핫패스 비용 최소)."""
    store = RoomStore()
    room = await store.create_room()
    redis_sync.expire(ROOM_KEY_PREFIX + room.code, 5)  # TTL을 짧게
    assert redis_sync.ttl(ROOM_KEY_PREFIX + room.code) <= 5
    await store.touch(room.code)
    assert redis_sync.ttl(ROOM_KEY_PREFIX + room.code) > 5  # 다시 풀 TTL로 슬라이딩


async def test_is_expired_reflects_key_absence(redis_sync):
    """만료 = 키 부재(TTL 소멸)로 단순화. get도 None."""
    store = RoomStore()
    room = await store.create_room()
    assert await store.is_expired(room) is False
    redis_sync.delete(ROOM_KEY_PREFIX + room.code)  # TTL 만료 모사
    assert await store.is_expired(room) is True
    assert await store.get(room.code) is None


async def test_close_retires_code_with_ttl(redis_sync):
    """방장 종료: 방 키 삭제 + retired 코드에 TTL 부여(재사용 금지 + 무한 증식 방지)."""
    store = RoomStore()
    room = await store.create_room()
    await store.close(room.code)
    assert await store.get(room.code) is None
    assert await store.is_retired(room.code) is True
    rttl = redis_sync.ttl("retired:" + room.code)
    assert 0 < rttl <= RETIRED_TTL_SECONDS


async def test_retired_code_not_reused(monkeypatch):
    """폐기 코드가 먼저 발급돼도 건너뛰고 새 코드를 낸다(재사용 거절)."""
    store = RoomStore()
    room = await store.create_room()
    await store.close(room.code)
    seq = iter([room.code, "ABC234"])
    monkeypatch.setattr("app.room_store.generate_code", lambda: next(seq))
    new_room = await store.create_room()
    assert new_room.code == "ABC234"


async def test_restart_recovers_room_metadata(fake_server):
    """재시작 복구: 새 RoomStore 인스턴스(프로세스 재시작 모사)도 같은 Redis에서

    방 메타데이터·host_token을 복구해 재접속(F-RT-07)이 가능하다.
    """
    store1 = RoomStore()
    room = await store1.create_room()
    store2 = RoomStore()  # 재시작 모사 — conftest 패치로 같은 FakeServer 사용
    got = await store2.get(room.code)
    assert got is not None
    assert got.code == room.code
    assert got.host_token == room.host_token  # 토큰 생존 → 방장 재인증·재접속 가능
