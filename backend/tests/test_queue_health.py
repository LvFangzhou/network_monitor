from app.utils.queue_health import _queue_status


def test_empty_queue_with_consumer_is_healthy() -> None:
    assert _queue_status(0, 1) == "healthy"


def test_queue_without_consumer_is_warning_before_backlog() -> None:
    assert _queue_status(0, 0) == "warning"


def test_queue_backlog_without_consumer_is_critical() -> None:
    assert _queue_status(1, 0) == "critical"


def test_large_backlog_is_critical() -> None:
    assert _queue_status(1000, 1) == "critical"
