"""AttemptLimiter — 코드 추측 무차별 시도 제한 (platform §3 남용 방지).

REVIEW(남용 방지): 코드 추측 brute force 대비. 임계·윈도우 값과 식별 키(IP/연결)
선택은 운영 정책 결정 사항 — 사람 확인 지점.

이 모듈은 실패한 방 조회(잘못된 코드) 횟수를 키 단위로 슬라이딩 윈도우 집계한다.
실제 enforce 지점은 방 참여(F-RT-03, 후속 STEP)의 코드 검증 흐름에 연결한다.
시간 주입 가능(테스트 결정론).
"""

from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

# ASSUMPTION: 시도 제한 임계·윈도우 — 임의값. 검수에서 운영 정책으로 조정.
MAX_ATTEMPTS = 10           # 윈도우 내 허용 실패 횟수
WINDOW_SECONDS = 60         # 슬라이딩 윈도우 길이(초)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class AttemptLimiter:
    def __init__(self) -> None:
        # key -> 최근 실패 시각 큐
        self._fails: dict[str, deque[datetime]] = defaultdict(deque)

    def _prune(self, key: str, now: datetime) -> None:
        cutoff = now - timedelta(seconds=WINDOW_SECONDS)
        q = self._fails[key]
        while q and q[0] < cutoff:
            q.popleft()

    def is_blocked(self, key: str, now: datetime | None = None) -> bool:
        """현재 키가 시도 제한에 걸려 있는지(윈도우 내 실패 ≥ 임계)."""
        moment = now or _now()
        self._prune(key, moment)
        return len(self._fails[key]) >= MAX_ATTEMPTS

    def record_failure(self, key: str, now: datetime | None = None) -> None:
        """잘못된 코드 시도 1건 기록."""
        moment = now or _now()
        self._prune(key, moment)
        self._fails[key].append(moment)

    def reset(self, key: str) -> None:
        """성공 시 해당 키의 실패 기록 해제."""
        self._fails.pop(key, None)
