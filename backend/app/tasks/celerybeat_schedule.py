"""
Celery Beat 定时任务调度配置
"""
from celery.schedules import crontab

# 定时任务配置
beat_schedule = {
    # SNMP全量采集调度 - 每10秒调度一批设备，每台设备约60秒完整采集一轮
    'collect-snmp-every-10s': {
        'task': 'app.tasks.snmp_tasks.collect_all_snmp',
        'schedule': 10.0,
        'options': {
            'expires': 8.0,
        }
    },
    # 线路绑定端口轻量采集 - 重点公网/专线端口保持10秒级曲线
    'collect-circuit-interface-realtime-every-10s': {
        'task': 'app.tasks.snmp_tasks.collect_circuit_interface_realtime',
        'schedule': 10.0,
        'options': {
            'expires': 8.0,
        }
    },
    'collect-asternos-interface-realtime-every-10s': {
        'task': 'app.tasks.snmp_tasks.collect_all_asternos_interface_realtime',
        'schedule': 10.0,
        'options': {
            'expires': 8.0,
        }
    },
    'collect-asternos-exporter-every-60s': {
        'task': 'app.tasks.snmp_tasks.collect_all_asternos_exporter',
        'schedule': 60.0,
        'options': {
            'expires': 50.0,
        }
    },
    'verify-unreachable-snmp-every-1m': {
        'task': 'app.tasks.snmp_tasks.verify_unreachable_snmp_devices',
        'schedule': 60.0,
        'options': {
            'expires': 50.0,
        }
    },
    'collect-device-reachability-every-30s': {
        'task': 'app.tasks.snmp_tasks.collect_device_reachability',
        'schedule': 30.0,
        'options': {
            'expires': 25.0,
        }
    },
    
    # 同步gNMI设备 - 每5分钟执行一次
    'sync-gnmi-devices-every-5m': {
        'task': 'app.tasks.snmp_tasks.sync_gnmi_devices',
        'schedule': 300.0,  # 5分钟
        'options': {
            'expires': 240.0,
        }
    },
    
    # 更新Ping监控设备列表 - 每2分钟执行一次
    'update-ping-monitor-every-2m': {
        'task': 'app.tasks.snmp_tasks.update_ping_monitor',
        'schedule': 120.0,  # 2分钟
        'options': {
            'expires': 100.0,
        }
    },
    
    # 关键接口告警检查 - 每10秒执行一次，保障端口类告警10-30秒内触达
    'check-fast-alerts-every-10s': {
        'task': 'app.tasks.alert_tasks.check_fast_alerts',
        'schedule': 10.0,
        'options': {
            'expires': 8.0,
        }
    },

    # 协议邻居告警检查 - 每30秒执行一次，不被慢速Exporter规则拖住
    'check-protocol-alerts-every-30s': {
        'task': 'app.tasks.alert_tasks.check_protocol_alerts',
        'schedule': 30.0,
        'options': {
            'expires': 25.0,
        }
    },

    # 设备基础健康告警检查 - 每30秒执行一次，避免CPU/内存/温度恢复被全量慢规则拖住
    'check-device-health-alerts-every-30s': {
        'task': 'app.tasks.alert_tasks.check_device_health_alerts',
        'schedule': 30.0,
        'options': {
            'expires': 25.0,
        }
    },

    # 常规告警检查 - 每60秒执行一次，避免较慢规则拖住关键接口告警
    'check-alerts-every-60s': {
        'task': 'app.tasks.alert_tasks.check_alerts',
        'schedule': 60.0,
        'options': {
            'expires': 50.0,
        }
    },

    # 告警规则详情缓存预热 - 用户首次点击“查看状态”时优先命中Redis缓存
    'prewarm-alert-rule-status-cache-every-60s': {
        'task': 'app.tasks.alert_tasks.prewarm_alert_rule_status_cache',
        'schedule': 60.0,
        'options': {
            'expires': 50.0,
        }
    },
    
    # 恢复过期告警 - 每小时执行一次
    'resolve-stale-alerts-every-hour': {
        'task': 'app.tasks.alert_tasks.resolve_stale_alerts',
        'schedule': crontab(minute=0, hour='*'),  # 每小时整点
    },

    # 公网/专线使用率统计 - 每小时执行一次，统计过去一小时平均Mbps
    'collect-circuit-usage-hourly': {
        'task': 'app.tasks.resource_tasks.collect_circuit_usage_hourly',
        'schedule': crontab(minute=5, hour='*'),
    },
    'process-tacacs-command-logs-every-10s': {
        'task': 'app.tasks.tacacs_tasks.process_tacacs_command_logs',
        'schedule': 10.0,
        'options': {
            'expires': 60.0,
        },
    },

    # InfluxDB 自检 - 每分钟确认挂载目录、写入和查询都正常
    'check-influxdb-storage-health-every-60s': {
        'task': 'app.tasks.system_tasks.check_influxdb_storage_health',
        'schedule': 60.0,
        'options': {
            'expires': 50.0,
        },
    },
}
