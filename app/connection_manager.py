"""ConnectionManager — 방별 active 소켓 집합 관리 (context §4).

방(room) 단위로 연결된 WebSocket을 묶어두고, 같은 방 전원에게 broadcast 한다.
소켓↔멤버 매핑을 함께 보유해 끊김 시 어떤 멤버를 제거할지 안다.
roster의 진실 원천은 Room.members(model)이며, 여기서는 소켓 라우팅만 담당한다.

1차는 단일 인스턴스 in-memory.
ASSUMPTION: 단일 인스턴스 가정 — 다중 인스턴스 확장(Redis pub/sub)은 후순위(context §4·§8).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger("phonesul.connection")


class ConnectionManager:
    def __init__(self) -> None:
        # room_code -> 해당 방에 연결된 소켓 집합
        self._rooms: dict[str, set[WebSocket]] = {}
        # websocket -> (room_code, member_id) — 끊김 시 멤버 식별용
        self._sockets: dict[WebSocket, tuple[str, str]] = {}

    async def register(
        self, room_code: str, websocket: WebSocket, member_id: str
    ) -> None:
        """이미 accept된 소켓을 방 집합에 등록하고 멤버 매핑을 기록."""
        self._rooms.setdefault(room_code, set()).add(websocket)
        self._sockets[websocket] = (room_code, member_id)
        logger.info(
            "register room=%s member=%s size=%d",
            room_code,
            member_id,
            len(self._rooms[room_code]),
        )

    def unregister(self, websocket: WebSocket) -> tuple[str, str] | None:
        """소켓을 제거하고 (room_code, member_id)를 반환. 미등록이면 None.

        빈 방 소켓 집합은 정리(휘발 원칙, platform §3). Room 모델 폐기는 호출부 책임.
        """
        entry = self._sockets.pop(websocket, None)
        if entry is None:
            return None
        room_code, member_id = entry
        sockets = self._rooms.get(room_code)
        if sockets:
            sockets.discard(websocket)
            if not sockets:
                del self._rooms[room_code]
        logger.info(
            "unregister room=%s member=%s size=%d",
            room_code,
            member_id,
            self.room_size(room_code),
        )
        return entry

    async def broadcast(self, room_code: str, payload: dict[str, Any]) -> None:
        """같은 방 전원에게 1회씩 **동시** 전송(context §4.1 원자성).

        asyncio.gather로 동시에 보내, 수신 느린 한 클라이언트가 같은 방 전체 전송을
        지연시키지 않게 한다(단일 박스 피크 부하 튜닝). 개별 소켓 전송 실패는 각자
        흡수해 한 명의 실패가 전체 broadcast를 막지 않게 한다 — 실패 소켓 정리는
        호출부의 disconnect 흐름에 맡긴다.
        """
        sockets = list(self._rooms.get(room_code, ()))
        if not sockets:
            return
        await asyncio.gather(*(self._send_one(ws, payload, room_code) for ws in sockets))

    async def _send_one(
        self, websocket: WebSocket, payload: dict[str, Any], room_code: str
    ) -> None:
        try:
            await websocket.send_json(payload)
        except Exception:  # noqa: BLE001 - 개별 소켓 전송 실패 흡수
            logger.warning("broadcast send failed room=%s", room_code)

    def sockets_in(self, room_code: str) -> list[WebSocket]:
        """방의 현재 소켓 목록 사본(방 종료 시 일괄 close용)."""
        return list(self._rooms.get(room_code, ()))

    def members_in(self, room_code: str) -> list[str]:
        """방의 현재 멤버 식별자 목록 — roster 진실 원천(살아있는 소켓 기준).

        멤버 목록은 영속 저장하지 않는다(platform §3). 연결된 소켓 매핑에서 직접
        재구성하므로 프로세스 재시작·재접속(F-RT-07) 후에도 실제 연결 상태와 일치한다.
        """
        members: list[str] = []
        for ws in self._rooms.get(room_code, ()):
            entry = self._sockets.get(ws)
            if entry is not None:
                members.append(entry[1])
        return members

    def room_size(self, room_code: str) -> int:
        return len(self._rooms.get(room_code, ()))

    def total_connections(self) -> int:
        """현재 등록된 전체 소켓 수(운영 모니터링용)."""
        return len(self._sockets)

    def room_count(self) -> int:
        """현재 로컬 소켓을 가진 활성 방 수(운영 모니터링용)."""
        return len(self._rooms)
