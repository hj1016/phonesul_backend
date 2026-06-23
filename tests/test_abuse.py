"""AttemptLimiter 단위 테스트 (platform §3 남용 방지): 시도 제한 윈도우."""

from __future__ import annotations

from datetime import timedelta

from app.abuse import MAX_ATTEMPTS, WINDOW_SECONDS, AttemptLimiter


def test_not_blocked_below_threshold():
    lim = AttemptLimiter()
    base = _base()
    for _ in range(MAX_ATTEMPTS - 1):
        lim.record_failure("ip-1", now=base)
    assert lim.is_blocked("ip-1", now=base) is False


def test_blocked_at_threshold():
    lim = AttemptLimiter()
    base = _base()
    for _ in range(MAX_ATTEMPTS):
        lim.record_failure("ip-1", now=base)
    assert lim.is_blocked("ip-1", now=base) is True


def test_window_slides_old_failures_out():
    lim = AttemptLimiter()
    base = _base()
    for _ in range(MAX_ATTEMPTS):
        lim.record_failure("ip-1", now=base)
    # 윈도우를 벗어난 시점에는 과거 실패가 만료되어 차단 해제.
    later = base + timedelta(seconds=WINDOW_SECONDS + 1)
    assert lim.is_blocked("ip-1", now=later) is False


def test_reset_clears_failures():
    lim = AttemptLimiter()
    base = _base()
    for _ in range(MAX_ATTEMPTS):
        lim.record_failure("ip-1", now=base)
    lim.reset("ip-1")
    assert lim.is_blocked("ip-1", now=base) is False


def test_keys_are_isolated():
    lim = AttemptLimiter()
    base = _base()
    for _ in range(MAX_ATTEMPTS):
        lim.record_failure("ip-1", now=base)
    assert lim.is_blocked("ip-1", now=base) is True
    assert lim.is_blocked("ip-2", now=base) is False


def _base():
    from datetime import datetime, timezone
    return datetime(2026, 6, 23, 12, 0, 0, tzinfo=timezone.utc)
