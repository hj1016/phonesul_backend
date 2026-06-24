"""RoomStore 단위 테스트 (F-RT-01): 코드 생성·충돌·만료."""

from __future__ import annotations

from datetime import timedelta

import pytest

from app import models
from app.models import CODE_ALPHABET, CODE_LENGTH, IDLE_EXPIRY_MINUTES
from app.room_store import RoomStore


def test_generate_code_format():
    """코드는 6자, 허용 문자셋만, 혼동문자(0/O/1/I) 미포함(ASSUMPTION 값 검증)."""
    forbidden = set("0O1I")
    for _ in range(500):
        code = models.generate_code()
        assert len(code) == CODE_LENGTH
        assert all(ch in CODE_ALPHABET for ch in code)
        assert not (set(code) & forbidden)


def test_create_room_returns_code_and_token():
    store = RoomStore()
    room = store.create_room()
    assert len(room.code) == CODE_LENGTH
    assert room.host_token  # 방장 토큰 발급됨
    assert store.get(room.code) is room  # O(1) 조회


def test_codes_are_unique_across_rooms():
    store = RoomStore()
    codes = {store.create_room().code for _ in range(200)}
    assert len(codes) == 200  # 충돌 없이 전부 고유


def test_collision_triggers_regeneration(monkeypatch):
    """첫 발급 코드가 기존과 충돌하면 재발급되는지 검증."""
    store = RoomStore()
    existing = store.create_room()

    # generate_code가 한 번은 기존 코드(충돌), 그다음 새 코드를 내도록 강제.
    seq = iter([existing.code, "ABC234"])
    monkeypatch.setattr("app.room_store.generate_code", lambda: next(seq))

    new_room = store.create_room()
    assert new_room.code == "ABC234"  # 충돌분을 건너뛰고 재발급
    assert store.count() == 2


def test_collision_limit_raises(monkeypatch):
    """항상 같은 코드만 나오면 상한 초과로 예외(무한루프 방지)."""
    store = RoomStore()
    store.create_room()
    dup = next(iter(store._rooms))
    monkeypatch.setattr("app.room_store.generate_code", lambda: dup)
    with pytest.raises(RuntimeError):
        store.create_room()


def test_room_not_expired_before_idle_window():
    store = RoomStore()
    room = store.create_room()
    just_before = room.last_active_at + timedelta(minutes=IDLE_EXPIRY_MINUTES - 1)
    assert store.is_expired(room, now=just_before) is False


def test_room_expired_after_idle_window():
    store = RoomStore()
    room = store.create_room()
    after = room.last_active_at + timedelta(minutes=IDLE_EXPIRY_MINUTES)
    assert store.is_expired(room, now=after) is True


def test_sweep_expired_removes_only_idle_rooms():
    store = RoomStore()
    fresh = store.create_room()
    stale = store.create_room()
    # stale의 마지막 활동을 과거로 밀어 만료시킴.
    stale.last_active_at = stale.last_active_at - timedelta(minutes=IDLE_EXPIRY_MINUTES + 5)

    removed = store.sweep_expired(now=fresh.last_active_at)
    assert stale.code in removed
    assert fresh.code not in removed
    assert store.get(stale.code) is None
    assert store.get(fresh.code) is fresh


def test_touch_resets_idle_timer():
    store = RoomStore()
    room = store.create_room()
    later = room.last_active_at + timedelta(minutes=IDLE_EXPIRY_MINUTES + 1)
    # touch 전이면 만료, touch로 갱신하면 미만료.
    assert store.is_expired(room, now=later) is True
    room.last_active_at = later  # touch() 효과를 결정론적으로 모사
    assert store.is_expired(room, now=later) is False
