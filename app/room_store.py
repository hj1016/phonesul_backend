"""RoomStore — 방 레지스트리 (F-RT-01, context §3). **Redis 백엔드.**

운영 내구성 STEP에서 내부 구현을 in-memory dict → Redis(redis.asyncio)로 교체했다.
프로세스가 죽거나 재배포돼도 방 메타데이터가 Redis에 남아, 클라이언트가 코드로
재접속(F-RT-07)하면 방이 복구된다. 공개 인터페이스(메서드명·인자)는 보존하되,
Redis I/O이므로 메서드는 async로 바뀐다(호출부는 await로 전환).

설계 원칙(platform §3 익명·휘발 / context §6 비게임):
- Redis에는 **방 메타데이터(code, host_token, created_at)만** 저장한다. 멤버 목록은
  저장하지 않는다 — 소켓이 죽으면 무의미하므로 재접속으로 재구성한다.
- **유휴 만료 = Redis 키 TTL.** last_active_at를 따로 추적하지 않는다. TTL이 곧 유휴
  타이머이며, 만료 자동화와 retired 코드 무한 증식 방지를 TTL이 동시에 해소한다.

ASSUMPTION: 단일 인스턴스(단일 박스) 가정 — 다중 인스턴스 + pub/sub는 범위 밖(context §4·§8).
"""

from __future__ import annotations

import json
import os
import secrets
from datetime import datetime

import redis.asyncio as aioredis

from app.models import IDLE_EXPIRY_MINUTES, Room, generate_code

# ASSUMPTION: 기본 접속 URL — 로컬 단일 Redis. 운영 주소는 REDIS_URL 환경변수로 주입(하드코딩 금지).
DEFAULT_REDIS_URL = "redis://localhost:6379/0"

# ASSUMPTION: 코드 충돌 시 재발급 시도 상한 — 임의값. 32^6 ≈ 10^9 공간이라 사실상
# 발생 불가하나 무한루프 방지용 안전장치.
MAX_CODE_GEN_ATTEMPTS = 10

# ASSUMPTION: 방장 토큰 바이트 길이 — 임의값(추측 불가 수준).
HOST_TOKEN_BYTES = 32

# 유휴 만료 TTL = context의 유휴 만료 N분 환산(만료 자동화). 활동 시 touch로 슬라이딩.
IDLE_EXPIRY_SECONDS = IDLE_EXPIRY_MINUTES * 60

# ASSUMPTION: retired(폐기) 코드 보존 TTL — 임의값(24h). 폐기 직후 같은 코드 재발급·재입장을
# 막을 만큼만 유지하고 자동 소멸시켜 무한 증식 방지(기존 in-memory set의 누적 문제 해소).
RETIRED_TTL_SECONDS = 24 * 60 * 60

# Redis 키 네임스페이스.
ROOM_KEY_PREFIX = "room:"
RETIRED_KEY_PREFIX = "retired:"


def _room_key(code: str) -> str:
    return ROOM_KEY_PREFIX + code


def _retired_key(code: str) -> str:
    return RETIRED_KEY_PREFIX + code


class RoomStore:
    def __init__(self, client: "aioredis.Redis | None" = None) -> None:
        # client 주입 가능(테스트는 fakeredis 주입). 미주입 시 REDIS_URL 기반 실 클라이언트.
        # redis.asyncio는 첫 명령 전까지 연결하지 않으므로 import·생성 시 Redis가 꺼져 있어도 안전.
        if client is not None:
            self._r = client
        else:
            url = os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)
            self._r = aioredis.from_url(url, decode_responses=True)

    async def create_room(self) -> Room:
        """방 생성 + 고유 코드 발급 + 방장 토큰 부여(F-RT-01).

        메타데이터를 JSON 1건으로 SET(ex=TTL) — 값 저장과 TTL 설정이 원자적이라
        TTL 없는 영구 키가 남는 경합이 없다. TTL = 유휴 만료(자동 폐기).
        """
        code = await self._unique_code()
        # REVIEW(로그인 강제 여부): 방장 권한은 서버 발급 토큰으로만 구분. 토스 로그인
        # 강제 아님 — 토큰 보유자가 방장(platform §3). UX 결정 확인 지점.
        room = Room(code=code, host_token=secrets.token_urlsafe(HOST_TOKEN_BYTES))
        payload = json.dumps(
            {
                "code": room.code,
                "host_token": room.host_token,
                "created_at": room.created_at.isoformat(),
            }
        )
        await self._r.set(_room_key(code), payload, ex=IDLE_EXPIRY_SECONDS)
        return room

    async def _unique_code(self) -> str:
        """충돌하지 않고 폐기 이력(retired)도 없는 코드를 발급. 상한 초과 시 예외.

        기존 충돌 회피 로직 유지 — room:{code}·retired:{code} 존재 여부로 판정.
        """
        for _ in range(MAX_CODE_GEN_ATTEMPTS):
            code = generate_code()
            if not await self._r.exists(_room_key(code)) and not await self._r.exists(
                _retired_key(code)
            ):
                return code
        raise RuntimeError("코드 발급 실패: 충돌 재시도 상한 초과")

    async def get(self, code: str) -> Room | None:
        """코드로 방 조회. 없거나 TTL 만료면 None(키가 사라져 동일하게 None)."""
        raw = await self._r.get(_room_key(code))
        if raw is None:
            return None
        data = json.loads(raw)
        return Room(
            code=data["code"],
            host_token=data["host_token"],
            created_at=datetime.fromisoformat(data["created_at"]),
        )

    async def remove(self, code: str) -> None:
        """방 키 폐기(만료/정리). 휘발 원칙(platform §3)."""
        await self._r.delete(_room_key(code))

    async def close(self, code: str) -> None:
        """방을 폐기하고 코드를 retired로 등록(F-RT-06).

        retired는 TTL로 자동 소멸 — 재사용 금지를 충분히 보장하면서 무한 증식을 막는다.
        """
        await self._r.delete(_room_key(code))
        await self._r.set(_retired_key(code), "1", ex=RETIRED_TTL_SECONDS)

    async def is_retired(self, code: str) -> bool:
        return bool(await self._r.exists(_retired_key(code)))

    async def touch(self, code: str) -> None:
        """유휴 TTL을 슬라이딩(활동 시 호출). 값 재기록 없이 PEXPIRE로 TTL만 연장 —

        짠/입퇴장 등 활동 핫패스 비용을 단일 PEXPIRE로 최소화한다. 키가 없으면 no-op.
        models.Room.touch(타임스탬프 갱신)를 대체 — 활동 시각 대신 키 TTL을 민다.
        """
        await self._r.pexpire(_room_key(code), IDLE_EXPIRY_SECONDS * 1000)

    async def is_expired(self, room: Room, now: datetime | None = None) -> bool:
        """유휴 만료 판정. Redis TTL 자동화로 '만료 = 키 부재'로 단순화한다.

        now 인자는 기존 시간주입 테스트와의 시그니처 호환을 위해 유지하되 무시한다
        (실제 만료 시점은 Redis TTL이 결정).
        """
        return not await self.exists(room.code)

    async def exists(self, code: str) -> bool:
        """방 키 존재 여부(TTL 미만료)."""
        return bool(await self._r.exists(_room_key(code)))

    async def expired_rooms(self, now: datetime | None = None) -> list[Room]:
        """만료된 방 목록(비파괴). Redis TTL이 만료 키를 자동 제거하므로 '존재하면서

        만료된' 방은 존재하지 않는다 → 항상 빈 목록. 시그니처는 호출부 호환을 위해 유지.
        REVIEW(만료 알림 경로): 유휴 만료 시 연결된 소켓에 room_closed(expired)를 능동
        통지하던 sweep 경로는 이제 TTL 수동 만료로 대체된다 — 통지 방식 재설계는 별도 작업.
        """
        return []

    async def sweep_expired(self, now: datetime | None = None) -> list[str]:
        """만료된 방을 폐기하고 코드 목록 반환. TTL 자동 만료로 스캔 대상이 없어 빈 목록.

        REVIEW(만료 알림 경로): expired_rooms와 동일 — 능동 통지 재설계는 별도 작업.
        """
        return []

    async def count(self) -> int:
        """현재 살아있는 방 수(room:* 키 스캔). 주로 검수·테스트용."""
        total = 0
        async for _ in self._r.scan_iter(match=ROOM_KEY_PREFIX + "*"):
            total += 1
        return total

    async def aclose(self) -> None:
        """Redis 연결 풀 정리(앱 종료 시 lifespan에서 호출). 이후 재사용하지 않는다."""
        await self._r.aclose()
