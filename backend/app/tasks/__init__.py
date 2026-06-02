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
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=1000,
    beat_schedule=beat_schedule,  # 定时任务调度配置
)

__all__ = ["celery_app"]
