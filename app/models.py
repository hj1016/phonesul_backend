"""휘발성 in-memory 데이터 모델 (context §3, platform §3).

영속 DB 지양. 방·참여자·상태는 서버 메모리에만 두고 방 종료 시 폐기한다.
실명·연락처·토스 식별값(getAnonymousKey 등)은 저장하지 않는다.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

# REVIEW(개인정보 저장 범위): Member는 서버 발급 식별자(memberId/socketId)만 보유.
# 실명·연락처·토스 식별값 저장 금지(platform §3). 익명·휘발 원칙. 사람 확인 지점.

# ASSUMPTION: 코드 6자, 영문 대문자+숫자, 혼동문자(0,O,1,I) 제외 — 임의값(context §3·§8).
CODE_LENGTH = 6
CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 0,O,1,I 제외

# ASSUMPTION: 유휴 만료 N분 — 임의값(context §3·§8). 만료 폐기 로직은 후속 STEP.
IDLE_EXPIRY_MINUTES = 30

# ASSUMPTION: 짠 연타 쿨다운(ms) — 임의값(context §4.1·§8). 백프레셔 로직은 후속 STEP.
CHEERS_COOLDOWN_MS = 1000

# ASSUMPTION: 방 정원 상한 — 임의값. 회식 규모 고려한 임의 상한. 초과 입장은 거절.
MAX_MEMBERS_PER_ROOM = 50

# ASSUMPTION: memberId 바이트 길이 — 임의값(추측 방지 수준, 익명 식별자).
MEMBER_ID_BYTES = 8


def _now() -> datetime:
    return datetime.now(timezone.utc)


def generate_code() -> str:
    """혼동문자 제외 랜덤 코드 발급(F-RT-01). 충돌 회피는 Room 생성 측 책임."""
    return "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def generate_member_id() -> str:
    """익명 멤버 식별자 발급(서버 발급). 토스 식별값과 무관(platform §3)."""
    return secrets.token_urlsafe(MEMBER_ID_BYTES)


@dataclass
class Member:
    """방 참여자. 서버 발급 식별자만 — 익명·휘발(platform §3)."""
    member_id: str
    socket_id: str
    joined_at: datetime = field(default_factory=_now)
    # 마지막 짠 시각 — 멤버별 연타 쿨다운 판정용. 짠 "횟수"는 저장하지 않는다
    # (비게임: 누가 몇 번 짠 집계 금지, context §6). 시각만 보관해 디바운스에 사용.
    last_cheers_at: datetime | None = None


def cheers_allowed(member: Member, now: datetime) -> bool:
    """멤버별 짠 쿨다운 판정(F-RT-05 백프레셔, context §4.1).

    첫 짠이거나 직전 짠으로부터 쿨다운이 지났으면 허용. now 주입 가능(테스트 결정론).
    ASSUMPTION: 쿨다운 값 CHEERS_COOLDOWN_MS — 임의값(context §4.1·§8).
    """
    if member.last_cheers_at is None:
        return True
    return (now - member.last_cheers_at) >= timedelta(milliseconds=CHEERS_COOLDOWN_MS)


@dataclass
class Room:
    """건배방. wss 채널 단위(context §2). in-memory 휘발."""
    code: str
    host_token: str
    created_at: datetime = field(default_factory=_now)
    last_active_at: datetime = field(default_factory=_now)
    members: list[Member] = field(default_factory=list)

    def touch(self) -> None:
        """활동 발생 시 유휴 타이머 갱신(만료 판정 기준)."""
        self.last_active_at = _now()
