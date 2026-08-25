import pytest

from app import ratelimit


@pytest.fixture(autouse=True)
def clear_ratelimit_state():
    ratelimit.reset()
    yield
    ratelimit.reset()


def test_allows_up_to_per_ip_max():
    for _ in range(ratelimit._REDEEM_MAX_PER_IP):
        assert ratelimit.redeem_allowed("1.2.3.4") is True
    assert ratelimit.redeem_allowed("1.2.3.4") is False


def test_per_ip_limit_is_independent_per_key():
    for _ in range(ratelimit._REDEEM_MAX_PER_IP):
        ratelimit.redeem_allowed("1.2.3.4")
    assert ratelimit.redeem_allowed("1.2.3.4") is False
    # A different IP still has its own budget.
    assert ratelimit.redeem_allowed("5.6.7.8") is True


def test_global_limit_caps_total_regardless_of_ip_spread():
    """The backstop: even if every attempt claims a distinct IP (e.g. a spoofed
    or misattributed header), the total is still capped."""
    allowed_count = 0
    for i in range(ratelimit._REDEEM_MAX_GLOBAL + 50):
        if ratelimit.redeem_allowed(f"10.0.{i // 256}.{i % 256}"):
            allowed_count += 1
    assert allowed_count == ratelimit._REDEEM_MAX_GLOBAL


def test_prunes_stale_ip_entries_once_over_cap(monkeypatch):
    monkeypatch.setattr(ratelimit, "_MAX_TRACKED_IPS", 5)
    monkeypatch.setattr(ratelimit, "_REDEEM_WINDOW", 0.01)
    for i in range(5):
        ratelimit.redeem_allowed(f"1.1.1.{i}")
    assert len(ratelimit._redeem_attempts) == 5

    import time

    time.sleep(0.02)  # let all existing entries go stale

    ratelimit.redeem_allowed("2.2.2.2")  # pushes tracked-IP count over the cap
    # Stale entries (including the new one's now-expired siblings) get pruned;
    # the dict shouldn't be left unbounded.
    assert len(ratelimit._redeem_attempts) <= 5
