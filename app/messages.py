"""메시지 포맷 정의 (context §4).

방 통신은 JSON. 최상위 `type` 필드로 분기한다.
이 골격 단계에서는 타입 상수와 봉투(envelope) 빌더만 둔다.
실제 join/cheers/roster 처리 로직은 후속 STEP에서 채운다.
"""

from __future__ import annotations

from typing import Any

# --- 클라이언트 → 서버 메시지 타입 ---
TYPE_JOIN = "join"      # 코드로 방 참여 (F-RT-03)
TYPE_LEAVE = "leave"    # 방 퇴장 (F-RT-04)
TYPE_CHEERS = "cheers"  # "짠" 전송 (F-RT-05)

# --- 서버 → 클라이언트 메시지 타입 ---
TYPE_ROSTER = "roster"            # 인원 목록·상태 동기화 (F-RT-04)
TYPE_ERROR = "error"              # 에러 안내 (platform §4: 음주 조장 톤 금지)
TYPE_ROOM_CLOSED = "room_closed"  # 방 종료·만료 안내 (F-RT-06)

CLIENT_TYPES = {TYPE_JOIN, TYPE_LEAVE, TYPE_CHEERS}
SERVER_TYPES = {TYPE_ROSTER, TYPE_ERROR, TYPE_CHEERS, TYPE_ROOM_CLOSED}


def error(code: str, message: str) -> dict[str, Any]:
    """에러 봉투. 문구는 음주 권장 톤 금지(platform §4·context §5)."""
    return {"type": TYPE_ERROR, "code": code, "message": message}


def roster(room_code: str, count: int, member_ids: list[str]) -> dict[str, Any]:
    """인원 동기화 봉투. 실명·연락처 없이 서버 발급 memberId만 노출(platform §3)."""
    return {
        "type": TYPE_ROSTER,
        "room": room_code,
        "count": count,
        "members": member_ids,
    }


def cheers(room_code: str) -> dict[str, Any]:
    """짠 브로드캐스트 봉투. 누가·몇 번 짠 집계 없음(비게임, context §6)."""
    return {"type": TYPE_CHEERS, "room": room_code}


def room_closed(room_code: str, reason: str) -> dict[str, Any]:
    """방 종료·만료 안내 봉투(F-RT-06). reason: host_ended | expired."""
    return {"type": TYPE_ROOM_CLOSED, "room": room_code, "reason": reason}
