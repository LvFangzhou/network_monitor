"""
Celery Beat 定时任务调度配置
"""
from celery.schedules import crontab
from app.config import settings


SNMP_SCHEDULER_INTERVAL_SECONDS = max(1.0, float(settings.SNMP_SCHEDULER_INTERVAL_SECONDS))
SNMP_TASK_EXPIRES_SECONDS = max(1.0, SNMP_SCHEDULER_INTERVAL_SECONDS - 1.0)
ASTERNOS_SCHEDULER_INTERVAL_SECONDS = max(1.0, float(settings.ASTERNOS_SCHEDULER_INTERVAL_SECONDS))
ASTERNOS_TASK_EXPIRES_SECONDS = max(1.0, ASTERNOS_SCHEDULER_INTERVAL_SECONDS - 1.0)
CIRCUIT_INTERFACE_REALTIME_INTERVAL_SECONDS = max(
    5.0,
    float(settings.CIRCUIT_INTERFACE_REALTIME_INTERVAL_SECONDS),
)

# 定时任务配置
beat_schedule = {
    'cleanup-monitoring-history-daily-03-20': {
        'task': 'app.tasks.system_tasks.cleanup_monitoring_history',
        'schedule': crontab(minute=20, hour=3),
        'options': {
            'expires': 6 * 60 * 60,
        },
    },
    'collect-server-resources-every-30s': {
        'task': 'app.tasks.system_tasks.collect_server_resources',
        'schedule': 30.0,
        'options': {
            'expires': 25.0,
        },
    },
    # SNMP全量采集调度 - 按配置分桶调度，避免 Beat 间隔和分桶间隔不一致导致部分桶永远扫不到。
    'collect-snmp-every-30s': {
        'task': 'app.tasks.snmp_tasks.collect_all_snmp',
        'schedule': SNMP_SCHEDULER_INTERVAL_SECONDS,
        'options': {
            'expires': SNMP_TASK_EXPIRES_SECONDS,
        }
    },
    # SNMP接口高频采集 - 端口出入流量独立高频轮询，不受15分钟全量资源采集影响
    'collect-snmp-interface-realtime-every-30s': {
        'task': 'app.tasks.snmp_tasks.collect_all_snmp_interface_realtime',
        'schedule': SNMP_SCHEDULER_INTERVAL_SECONDS,
        'options': {
            'expires': SNMP_TASK_EXPIRES_SECONDS,
        }
    },
    'collect-h3c-s9867-roce-interface-health-every-30s': {
        'task': 'app.tasks.snmp_tasks.collect_h3c_s9867_roce_interface_health',
        'schedule': SNMP_SCHEDULER_INTERVAL_SECONDS,
        'options': {
            'expires': SNMP_TASK_EXPIRES_SECONDS,
        },
    },
    # 线路绑定端口轻量采集 - 重点公网/专线端口保持10秒级曲线
    'collect-circuit-interface-realtime': {
        'task': 'app.tasks.snmp_tasks.collect_circuit_interface_realtime',
        'schedule': CIRCUIT_INTERFACE_REALTIME_INTERVAL_SECONDS,
        'options': {
            'expires': max(4.0, CIRCUIT_INTERFACE_REALTIME_INTERVAL_SECONDS - 1.0),
        }
    },
    'collect-incident-interface-realtime-every-5s': {
        'task': 'app.tasks.snmp_tasks.collect_incident_interface_realtime',
        'schedule': 5.0,
        'options': {
            'expires': 4.0,
        },
    },
    'collect-asternos-interface-realtime-every-30s': {
        'task': 'app.tasks.snmp_tasks.collect_all_asternos_interface_realtime',
        'schedule': ASTERNOS_SCHEDULER_INTERVAL_SECONDS,
        'options': {
            'expires': ASTERNOS_TASK_EXPIRES_SECONDS,
        }
    },
    'collect-asternos-exporter-every-30s': {
        'task': 'app.tasks.snmp_tasks.collect_all_asternos_exporter',
        'schedule': ASTERNOS_SCHEDULER_INTERVAL_SECONDS,
        'options': {
            'expires': ASTERNOS_TASK_EXPIRES_SECONDS,
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
    
    # 关键接口告警检查 - 每30秒执行一次，避免采集/告警检查重叠导致页面卡顿
    'check-fast-state-alerts-every-30s': {
        'task': 'app.tasks.alert_tasks.check_fast_state_alerts',
        'schedule': 30.0,
        'options': {
            'expires': 28.0,
        }
    },
    'check-fast-crc-alerts-every-30s': {
        'task': 'app.tasks.alert_tasks.check_fast_crc_alerts',
        'schedule': 30.0,
        'options': {'expires': 28.0},
    },
    'check-fast-error-alerts-every-30s': {
        'task': 'app.tasks.alert_tasks.check_fast_error_alerts',
        'schedule': 30.0,
        'options': {'expires': 28.0},
    },
    'check-fast-discard-alerts-every-30s': {
        'task': 'app.tasks.alert_tasks.check_fast_discard_alerts',
        'schedule': 30.0,
        'options': {'expires': 28.0},
    },

    # 接口告警快速恢复 - 只扫描当前活动接口告警，端口排除/AdminDown 后不等待全量规则慢扫
    'resolve-interface-alerts-quick-every-30s': {
        'task': 'app.tasks.alert_tasks.resolve_interface_alerts_quick',
        'schedule': 30.0,
        'options': {
            'expires': 25.0,
        }
    },

    # 协议邻居告警检查 - 每60秒执行一次，不被慢速Exporter规则拖住
    'check-protocol-alerts-every-60s': {
        'task': 'app.tasks.alert_tasks.check_protocol_alerts',
        'schedule': 60.0,
        'options': {
            'expires': 50.0,
        }
    },

    # 山石仅上报 BFD Down Trap，没有配对的恢复 Trap；回查当前会话状态后再真实恢复。
    'reconcile-hillstone-bfd-traps-every-60s': {
        'task': 'app.tasks.alert_tasks.reconcile_hillstone_bfd_trap_alerts',
        'schedule': 60.0,
        'options': {
            'expires': 50.0,
        }
    },

    # 山石 BGP Down Trap 可能没有配对恢复 Trap；回查当前邻居状态后再真实恢复。
    'reconcile-hillstone-bgp-traps-every-60s': {
        'task': 'app.tasks.alert_tasks.reconcile_hillstone_bgp_trap_alerts',
        'schedule': 60.0,
        'options': {
            'expires': 50.0,
        }
    },

    # 设备可达性告警检查 - 每60秒执行一次，不被全量慢规则拖住
    'check-reachability-alerts-every-60s': {
        'task': 'app.tasks.alert_tasks.check_reachability_alerts',
        'schedule': 60.0,
        'options': {
            'expires': 50.0,
        }
    },

    # 设备基础健康告警检查 - 每60秒执行一次，避免CPU/内存/温度恢复被全量慢规则拖住
    'check-device-health-alerts-every-60s': {
        'task': 'app.tasks.alert_tasks.check_device_health_alerts',
        'schedule': 60.0,
        'options': {
            'expires': 50.0,
        }
    },

    # 光模块质量独立检查 - 5分钟采样足以覆盖 DDM/FEC 周期，且不受常规慢规则影响
    'check-optical-alerts-every-5m': {
        'task': 'app.tasks.alert_tasks.check_optical_alerts',
        'schedule': 300.0,
        'options': {
            'expires': 240.0,
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
    'prewarm-alert-rule-status-cache-every-3m': {
        'task': 'app.tasks.alert_tasks.prewarm_alert_rule_status_cache',
        'schedule': 180.0,
        'kwargs': {
            'limit': 100,
            'batch_size': 2,
            'max_runtime_seconds': 4,
        },
        'options': {
            'expires': 120.0,
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
    # 网络设备配置备份 - 每天凌晨 00:00 对所有上线设备执行一次
    'run-config-backup-daily-midnight': {
        'task': 'app.tasks.config_backup_tasks.run_scheduled_config_backup',
        'schedule': crontab(minute=0, hour=0),
        'options': {
            'expires': 6 * 3600,
        },
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

    # 菜单首屏缓存预热 - 系统 24 小时运行时后台保持常用页面缓存，用户登录/切菜单优先读缓存
    'prewarm-fast-menu-caches-every-60s': {
        'task': 'app.tasks.menu_cache_tasks.prewarm_fast_menu_caches',
        'schedule': 60.0,
        'options': {
            'expires': 50.0,
        },
    },
    'prewarm-device-overview-cache-every-5m': {
        'task': 'app.tasks.menu_cache_tasks.prewarm_device_overview_cache',
        'schedule': 300.0,
        'options': {
            'expires': 240.0,
        },
    },
    'prewarm-traffic-query-cache-every-60s': {
        'task': 'app.tasks.menu_cache_tasks.prewarm_traffic_query_cache',
        'schedule': 60.0,
        'options': {
            'expires': 50.0,
        },
    },
    'prewarm-device-detail-caches-daily-00-and-12': {
        'task': 'app.tasks.snmp_tasks.prewarm_device_detail_caches',
        'schedule': crontab(minute=0, hour='0,12'),
        'options': {
            'expires': 6 * 60 * 60,
        },
    },
    # ARP/FIB 明细体量较大，每12小时低并发采集并缓存；错开配置备份和连接信息预热。
    'prewarm-forwarding-caches-daily-00-30-and-12-30': {
        'task': 'app.tasks.snmp_tasks.prewarm_forwarding_caches',
        'schedule': crontab(minute=30, hour='0,12'),
        'options': {
            'expires': 3 * 60 * 60,
        },
    },
    'ensure-qos-discard-rules-every-10m': {
        'task': 'app.tasks.snmp_tasks.ensure_qos_discard_rules',
        'schedule': 600.0,
        'options': {
            'expires': 300.0,
        },
    },
    'ensure-h3c-s9867-roce-rules-every-10m': {
        'task': 'app.tasks.snmp_tasks.ensure_h3c_s9867_roce_rules',
        'schedule': 600.0,
        'options': {
            'expires': 300.0,
        },
    },
    'ensure-up-interface-crc-rules-every-10m': {
        'task': 'app.tasks.snmp_tasks.ensure_up_interface_crc_rules',
        'schedule': 600.0,
        'options': {
            'expires': 300.0,
        },
    },
    'ensure-asternos-up-interface-quality-rules-every-10m': {
        'task': 'app.tasks.snmp_tasks.ensure_asternos_up_interface_quality_rules',
        'schedule': 600.0,
        'options': {
            'expires': 300.0,
        },
    },
    # 公网质量探测 - 每1秒调度一次，到期目标按各自采样间隔执行，历史保存到 InfluxDB
    'collect-quality-probes-every-1s': {
        'task': 'app.tasks.quality_tasks.collect_quality_probes',
        'schedule': 1.0,
        'options': {
            'expires': 5.0,
        },
    },
    # 公网路径观察 - 只处理开启了MTR观察的公网质量目标，目标自身控制执行间隔
    'collect-quality-mtr-paths-every-30s': {
        'task': 'app.tasks.quality_tasks.collect_quality_mtr_paths',
        'schedule': 30.0,
        'options': {
            'expires': 25.0,
        },
    },
}
