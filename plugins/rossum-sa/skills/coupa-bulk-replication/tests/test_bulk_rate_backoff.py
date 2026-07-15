"""Rate limiting + blind 429/503 backoff for every Coupa request.

Coupa sends no rate headers and no Retry-After (live-verified, spec §4.5/4.6),
so the backoff is blind exponential. 401/400 must NOT be retried here —
callers own token refresh / hard failures.
"""
import pytest
import requests

import coupa_bulk_import as cbi
from bulk_helpers import StubResponse


# ── RateLimiter ──────────────────────────────────────────────────────────────

def test_rate_limiter_enforces_min_interval(monkeypatch):
    clock = {"now": 0.0}
    sleeps = []
    monkeypatch.setattr(cbi.time, "monotonic", lambda: clock["now"])

    def fake_sleep(s):
        sleeps.append(s)
        clock["now"] += s

    monkeypatch.setattr(cbi.time, "sleep", fake_sleep)
    limiter = cbi.RateLimiter(2.0)          # 2 req/s -> min interval 0.5s
    for _ in range(4):
        limiter.wait()
    # 4 requests at 2/s take >= 1.5s of enforced spacing
    assert sum(sleeps) >= 1.5


def test_rate_limiter_none_is_noop(monkeypatch):
    monkeypatch.setattr(cbi.time, "sleep",
                        lambda s: pytest.fail("no-op limiter must never sleep"))
    limiter = cbi.RateLimiter(None)
    for _ in range(10):
        limiter.wait()


# ── coupa_call backoff ───────────────────────────────────────────────────────

def _no_sleep(monkeypatch):
    monkeypatch.setattr(cbi.time, "sleep", lambda s: None)


def test_coupa_call_retries_429_then_succeeds(monkeypatch):
    _no_sleep(monkeypatch)
    responses = [StubResponse({}, status=429), StubResponse({"ok": True})]
    resp = cbi.coupa_call(lambda: responses.pop(0))
    assert resp.json() == {"ok": True}


def test_coupa_call_retries_503_and_connection_errors(monkeypatch):
    _no_sleep(monkeypatch)
    attempts = {"n": 0}

    def fn():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise requests.exceptions.ConnectionError("blip")
        if attempts["n"] == 2:
            return StubResponse({}, status=503)
        return StubResponse({"ok": True})

    assert cbi.coupa_call(fn).json() == {"ok": True}
    assert attempts["n"] == 3


def test_coupa_call_exhausted_raises(monkeypatch):
    _no_sleep(monkeypatch)
    with pytest.raises(requests.HTTPError):
        cbi.coupa_call(lambda: StubResponse({}, status=429), _attempts=3)


def test_coupa_call_does_not_retry_401(monkeypatch):
    # token refresh is the CALLER's job — a retried 401 would just 401 again
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return StubResponse({}, status=401)

    with pytest.raises(requests.HTTPError):
        cbi.coupa_call(fn)
    assert calls["n"] == 1


def test_coupa_call_does_not_retry_400(monkeypatch):
    # a typo'd fields list must fail loudly, not burn 8 backoff rounds
    _no_sleep(monkeypatch)
    calls = {"n": 0}

    def fn():
        calls["n"] += 1
        return StubResponse({}, status=400)

    with pytest.raises(requests.HTTPError):
        cbi.coupa_call(fn)
    assert calls["n"] == 1


def test_coupa_call_waits_on_limiter(monkeypatch):
    waits = []
    monkeypatch.setattr(cbi, "LIMITER",
                        type("L", (), {"wait": lambda self: waits.append(1)})())
    cbi.coupa_call(lambda: StubResponse({}))
    assert waits == [1]
