"""단위 테스트 (S3): roster 계산·메시지 봉투 불변식.

특히 비게임 보장(짠 봉투에 sender/count 부재)을 구조적으로 고정한다(context §6).
"""

from __future__ import annotations

from app import messages
from app.models import Member, Room


def test_roster_envelope_counts_members():
    room = Room(code="ABC234", host_token="t")
    room.members = [
        Member(member_id="m1", socket_id="s1"),
        Member(member_id="m2", socket_id="s2"),
    ]
    member_ids = [m.member_id for m in room.members]
    payload = messages.roster(room.code, len(member_ids), member_ids)
    assert payload["type"] == "roster"
    assert payload["room"] == "ABC234"
    assert payload["count"] == 2
    assert payload["members"] == ["m1", "m2"]


def test_cheers_envelope_has_no_sender_or_count():
    """비게임: 누가/몇 번 짠 정보가 봉투에 구조적으로 없음(context §6)."""
    payload = messages.cheers("ABC234")
    assert set(payload.keys()) == {"type", "room"}
    assert "sender" not in payload
    assert "count" not in payload


def test_error_envelope_shape():
    payload = messages.error("invalid_code", "방을 찾을 수 없어요.")
    assert payload == {"type": "error", "code": "invalid_code", "message": "방을 찾을 수 없어요."}


def test_room_closed_envelope_carries_reason():
    assert messages.room_closed("ABC234", "expired") == {
        "type": "room_closed",
        "room": "ABC234",
        "reason": "expired",
    }
