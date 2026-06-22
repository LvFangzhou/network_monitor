from celery import Celery
from app.config import settings
from app.tasks.celerybeat_schedule import beat_schedule

# 创建Celery应用
celery_app = Celery(
    "network_monitor",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.tasks.snmp_tasks",
        "app.tasks.alert_tasks",
        "app.tasks.notification_tasks",
        "app.tasks.resource_tasks",
        "app.tasks.tacacs_tasks",
        "app.tasks.system_tasks",
    ]
)

# Celery配置
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=300,
    task_default_queue="celery",
    task_routes={
        "app.tasks.snmp_tasks.collect_all_snmp": {"queue": "snmp_realtime"},
        "app.tasks.snmp_tasks.collect_snmp_for_device": {"queue": "snmp_realtime"},
        "app.tasks.snmp_tasks.collect_all_snmp_interface_realtime": {"queue": "snmp_interface_realtime"},
        "app.tasks.snmp_tasks.collect_circuit_interface_realtime": {"queue": "snmp_circuit_realtime"},
        "app.tasks.alert_tasks.check_fast_alerts": {"queue": "snmp_realtime"},
        "app.tasks.snmp_tasks.collect_all_asternos_interface_realtime": {"queue": "asternos_realtime"},
        "app.tasks.snmp_tasks.collect_all_asternos_exporter": {"queue": "asternos"},
        "app.tasks.snmp_tasks.collect_asternos_for_device": {"queue": "asternos"},
        "app.tasks.alert_tasks.check_protocol_alerts": {"queue": "alerts"},
        "app.tasks.alert_tasks.check_device_health_alerts": {"queue": "alerts"},
        "app.tasks.alert_tasks.check_alerts": {"queue": "alerts"},
        "app.tasks.alert_tasks.prewarm_alert_rule_status_cache": {"queue": "alerts"},
        "app.tasks.alert_tasks._send_alert_notification": {"queue": "notification"},
        "app.tasks.alert_tasks._send_alert_event_notification": {"queue": "notification"},
        "app.tasks.notification_tasks.*": {"queue": "notification"},
        "app.tasks.tacacs_tasks.process_tacacs_command_logs": {"queue": "tacacs"},
        "app.tasks.resource_tasks.collect_circuit_usage_hourly": {"queue": "resource"},
        "app.tasks.system_tasks.check_influxdb_storage_health": {"queue": "system"},
    },
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
    beat_schedule=beat_schedule,  # 定时任务调度配置
)

__all__ = ["celery_app"]
