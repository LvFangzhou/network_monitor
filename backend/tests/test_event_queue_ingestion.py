import base64
from unittest.mock import Mock, patch

from app.services.snmp_trap_listener import _enqueue_trap_event
from app.services.syslog_listener import _enqueue_syslog_event
from app.tasks.event_tasks import process_snmp_trap_event, process_syslog_event


@patch("app.tasks.event_tasks.record_event_queue_metric")
@patch("app.tasks.event_tasks.process_syslog_event.apply_async")
def test_syslog_listener_enqueues_without_sync_persist(apply_async: Mock, _metric: Mock) -> None:
    with patch("app.services.syslog_listener._persist_syslog_event") as persist:
        _enqueue_syslog_event("10.0.0.1", "<189>test")

    apply_async.assert_called_once()
    assert apply_async.call_args.kwargs["queue"] == "events_syslog"
    persist.assert_not_called()


@patch("app.tasks.event_tasks.record_event_queue_metric")
@patch("app.tasks.event_tasks.process_snmp_trap_event.apply_async")
def test_trap_listener_base64_encodes_payload(apply_async: Mock, _metric: Mock) -> None:
    payload = b"\x30\x03\x02\x01\x01"
    _enqueue_trap_event("10.0.0.2", payload)

    args = apply_async.call_args.kwargs["args"]
    assert args[0] == "10.0.0.2"
    assert base64.b64decode(args[1]) == payload
    assert apply_async.call_args.kwargs["queue"] == "events_trap"


@patch("app.tasks.event_tasks.redis_client")
@patch("app.services.syslog_listener._persist_syslog_event")
def test_syslog_task_processes_event_once(persist: Mock, redis: Mock) -> None:
    redis.exists.return_value = False
    redis.set.return_value = True

    process_syslog_event.run("10.0.0.3", "message", "event-1", 1.0)

    persist.assert_called_once_with("10.0.0.3", "message")


@patch("app.tasks.event_tasks.redis_client")
@patch("app.services.snmp_trap_listener._handle_trap_datagram")
def test_trap_task_decodes_payload(handle: Mock, redis: Mock) -> None:
    redis.exists.return_value = False
    redis.set.return_value = True
    payload = b"trap-payload"

    process_snmp_trap_event.run("10.0.0.4", base64.b64encode(payload).decode("ascii"), "event-2", 1.0)

    handle.assert_called_once_with("10.0.0.4", payload)
