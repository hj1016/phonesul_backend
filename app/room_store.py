"""RoomStore — 방 레지스트리 (F-RT-01, context §3).

코드→방 O(1) 조회, 코드 충돌 회피, 유휴 만료 폐기를 담당한다.
1차 단일 인스턴스 in-memory(휘발). 영속 저장 없음(platform §3).

ASSUMPTION: 단일 인스턴스 가정 — 다중 인스턴스 확장(Redis)은 후순위(context §4·§8).
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from app.models import IDLE_EXPIRY_MINUTES, Room, generate_code

# ASSUMPTION: 코드 충돌 시 재발급 시도 상한 — 임의값. 상한 초과는 사실상 발생 불가
# (문자셋 32^6 ≈ 10^9 공간)하나 무한루프 방지용 안전장치.
MAX_CODE_GEN_ATTEMPTS = 10

# ASSUMPTION: 방장 토큰 바이트 길이 — 임의값(추측 불가 수준).
HOST_TOKEN_BYTES = 32


def _now() -> datetime:
    return datetime.now(timezone.utc)


class RoomStore:
    def __init__(self) -> None:
        self._rooms: dict[str, Room] = {}
        # 폐기된 코드 — 재사용 금지(F-RT-06). 새 코드 발급 시 회피.
        # ASSUMPTION: 프로세스 생애 동안 누적(휘발 — 재시작 시 초기화). 단일 인스턴스
        # 가정상 충분. 장기 운영 시 TTL/정리 도입 검토(context §4·§8).
        self._retired: set[str] = set()

    def create_room(self) -> Room:
        """방 생성 + 고유 코드 발급 + 방장 토큰 부여(F-RT-01)."""
        code = self._unique_code()
        # REVIEW(로그인 강제 여부): 방장 권한은 서버 발급 토큰으로만 구분.
        # 토스 로그인 강제 아님 — 토큰 보유자가 방장(platform §3). UX 결정 확인 지점.
        room = Room(code=code, host_token=secrets.token_urlsafe(HOST_TOKEN_BYTES))
        self._rooms[code] = room
        return room

    def _unique_code(self) -> str:
        """충돌하지 않고 폐기 이력도 없는 코드를 발급. 상한 초과 시 예외(안전장치)."""
        for _ in range(MAX_CODE_GEN_ATTEMPTS):
            code = generate_code()
            if code not in self._rooms and code not in self._retired:
                return code
        raise RuntimeError("코드 발급 실패: 충돌 재시도 상한 초과")

    def get(self, code: str) -> Room | None:
        """코드로 방 조회 — O(1). 없으면 None."""
        return self._rooms.get(code)

    def remove(self, code: str) -> None:
        """방 폐기(방장 종료 또는 만료). 휘발 원칙(platform §3)."""
        self._rooms.pop(code, None)

    def close(self, code: str) -> None:
        """방을 폐기하고 코드를 재사용 금지 목록에 등록(F-RT-06)."""
        self._rooms.pop(code, None)
        self._retired.add(code)

    def is_retired(self, code: str) -> bool:
        return code in self._retired

    def expired_rooms(self, now: datetime | None = None) -> list[Room]:
        """만료된 방 목록을 반환(비파괴). 실제 폐기는 호출부(_terminate_room)가 수행."""
        moment = now or _now()
        return [r for r in self._rooms.values() if self.is_expired(r, moment)]

    def is_expired(self, room: Room, now: datetime | None = None) -> bool:
        """유휴 만료 판정. now 주입 가능(테스트 결정론)."""
        # ASSUMPTION: 유휴 만료 N=IDLE_EXPIRY_MINUTES분 — 임의값(context §3·§8).
        deadline = room.last_active_at + timedelta(minutes=IDLE_EXPIRY_MINUTES)
        return (now or _now()) >= deadline

    def sweep_expired(self, now: datetime | None = None) -> list[str]:
        """만료된 방을 모두 폐기하고 폐기된 코드 목록 반환(F-RT-06).

        주기 호출은 후속 STEP에서 백그라운드 태스크로 연결.
        """
        moment = now or _now()
        expired = [c for c, r in self._rooms.items() if self.is_expired(r, moment)]
        for code in expired:
            del self._rooms[code]
        return expired

    def count(self) -> int:
        return len(self._rooms)
