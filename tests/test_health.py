"""Tests for the provider health manager."""

from __future__ import annotations

from multitempmail.health import HealthManager


def test_unknown_provider_is_healthy() -> None:
    health = HealthManager()
    assert health.is_healthy("mailtm")
    assert health.effective_priority("mailtm", 100) == 100


def test_failure_streak_demotes_priority() -> None:
    health = HealthManager(unhealthy_after=2, penalty=20)
    health.record_failure("mailtm")
    assert health.is_healthy("mailtm")
    assert health.effective_priority("mailtm", 100) == 80
    health.record_failure("mailtm", "boom")
    assert not health.is_healthy("mailtm")
    assert health.effective_priority("mailtm", 100) == 60


def test_penalty_is_capped() -> None:
    health = HealthManager(unhealthy_after=2, penalty=20)
    for _ in range(10):
        health.record_failure("mailtm")
    assert health.effective_priority("mailtm", 100) == 20
    assert health.effective_priority("mailtm", 10) == 0


def test_success_recovers_priority() -> None:
    health = HealthManager(unhealthy_after=2, penalty=20)
    for _ in range(4):
        health.record_failure("mailtm")
    assert health.effective_priority("mailtm", 100) == 20
    health.record_success("mailtm", 0.1)
    assert health.is_healthy("mailtm")
    assert health.effective_priority("mailtm", 100) == 100


def test_snapshot_zeros_for_unknown_provider() -> None:
    snapshot = HealthManager().snapshot("mailcx", 95)
    assert snapshot.name == "mailcx"
    assert snapshot.healthy
    assert snapshot.consecutive_failures == 0
    assert snapshot.total_requests == 0
    assert snapshot.avg_response_time is None
    assert snapshot.last_success_at is None
    assert snapshot.last_failure_at is None
    assert snapshot.priority == 95
    assert snapshot.effective_priority == 95


def test_snapshot_reflects_state_and_error() -> None:
    health = HealthManager()
    health.record_failure("mailtd", "rate limit")
    health.record_failure("mailtd", "rate limit again")
    snapshot = health.snapshot("mailtd", 60)
    assert not snapshot.healthy
    assert snapshot.consecutive_failures == 2
    assert snapshot.total_requests == 2
    assert snapshot.last_error == "rate limit again"
    assert snapshot.last_failure_at is not None
    assert snapshot.effective_priority == 20


def test_avg_response_time() -> None:
    health = HealthManager()
    health.record_success("mailtm", 0.1)
    health.record_success("mailtm", 0.3)
    snapshot = health.snapshot("mailtm", 100)
    assert snapshot.avg_response_time == 0.2
    assert snapshot.consecutive_successes == 2
    assert snapshot.total_requests == 2


def test_reset_and_clear() -> None:
    health = HealthManager()
    health.record_failure("mailtm")
    health.record_failure("mailcx")
    assert health.providers == ["mailcx", "mailtm"]
    health.reset("mailtm")
    assert "mailtm" not in health
    health.clear()
    assert health.providers == []


def test_snapshots_with_priorities() -> None:
    health = HealthManager()
    health.record_success("mailtm")
    views = health.snapshots({"mailtm": 100, "mailcx": 95})
    names = [v.name for v in views]
    assert names == ["mailcx", "mailtm"]
    by_name = {v.name: v for v in views}
    assert by_name["mailcx"].priority == 95
    assert by_name["mailtm"].priority == 100
