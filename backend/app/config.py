"""
系统配置管理
"""
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """应用配置类"""
    
    # 应用配置
    APP_NAME: str = "并行网络运营平台"
    APP_VERSION: str = "1.0.0"
    DEBUG: bool = False
    SECRET_KEY: str = "your-secret-key-change-in-production"
    
    # API配置
    API_PREFIX: str = "/api/v1"
    DOCS_URL: str = "/docs"
    REDOC_URL: str = "/redoc"
    FRONTEND_PUBLIC_URL: str = "http://172.18.17.250:8080"
    TACACS_WEBHOOK_URL: str = ""
    
    # PostgreSQL配置
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432
    POSTGRES_USER: str = "network_monitor"
    POSTGRES_PASSWORD: str = "network_monitor"
    POSTGRES_DB: str = "network_monitor"
    DB_POOL_SIZE: int = 30
    DB_MAX_OVERFLOW: int = 60
    DB_POOL_TIMEOUT: int = 30
    DB_POOL_RECYCLE: int = 1800
    
    @property
    def DATABASE_URL(self) -> str:
        return f"postgresql+psycopg2://{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/{self.POSTGRES_DB}"
    
    # InfluxDB2配置
    INFLUXDB_URL: str = "http://localhost:8086"
    INFLUXDB_TOKEN: str = "network-monitor-token"
    INFLUXDB_ORG: str = "network-monitor"
    INFLUXDB_BUCKET: str = "network_metrics"
    INFLUXDB_RETENTION_DAYS: int = 365
    INFLUXDB_DATA_PATH: str = "/host-data/influxdb"
    
    # Redis配置
    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_PASSWORD: Optional[str] = None
    
    @property
    def REDIS_URL(self) -> str:
        if self.REDIS_PASSWORD:
            return f"redis://:{self.REDIS_PASSWORD}@{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}"
    
    # RabbitMQ配置
    RABBITMQ_HOST: str = "localhost"
    RABBITMQ_PORT: int = 5672
    RABBITMQ_USER: str = "guest"
    RABBITMQ_PASSWORD: str = "guest"
    RABBITMQ_VHOST: str = "/"
    
    @property
    def CELERY_BROKER_URL(self) -> str:
        return f"amqp://{self.RABBITMQ_USER}:{self.RABBITMQ_PASSWORD}@{self.RABBITMQ_HOST}:{self.RABBITMQ_PORT}/{self.RABBITMQ_VHOST}"
    
    @property
    def CELERY_RESULT_BACKEND(self) -> str:
        return self.REDIS_URL
    
    # SNMP配置
    SNMP_DEFAULT_TIMEOUT: int = 1
    SNMP_DEFAULT_RETRIES: int = 3
    SNMP_DEFAULT_PORT: int = 161
    # 全量 SNMP 端口采集按批次分摊：Beat 每 30 秒调度一批，默认约 5 分钟完整采集一轮。
    # 300-500 台 128 口交换机时，避免所有设备在同一个窗口内集中 walk。
    SNMP_SCHEDULER_INTERVAL_SECONDS: int = 30
    SNMP_FULL_COLLECTION_INTERVAL_SECONDS: int = 300
    # 端口流量历史要持续可见，因此接口高频采集与全量资源采集拆分。
    # 这里保持约 60 秒内完整轮一遍所有 SNMP 设备端口基础流量数据。
    SNMP_INTERFACE_REALTIME_INTERVAL_SECONDS: int = 60
    SNMP_MAX_DEVICES_PER_TICK: int = 40
    SNMP_INTERFACE_REALTIME_MAX_WORKERS: int = 4
    # Asteros Exporter 全量资源/协议/队列指标也按批次分摊，避免和端口流量采集抢队列。
    ASTERNOS_SCHEDULER_INTERVAL_SECONDS: int = 30
    ASTERNOS_FULL_COLLECTION_INTERVAL_SECONDS: int = 120
    ASTERNOS_MAX_DEVICES_PER_TICK: int = 20
    
    # gNMI配置
    GNMI_DEFAULT_PORT: int = 57400
    GNMI_BATCH_SIZE: int = 1000
    GNMI_BATCH_TIMEOUT: float = 1.0
    GNMI_RECONNECT_DELAY: float = 5.0
    GNMI_MAX_RECONNECT_DELAY: float = 300.0
    
    # 告警配置
    ALERT_CHECK_INTERVAL: int = 30
    ALERT_RESOLVE_INTERVAL: int = 60
    
    # 通知配置
    WECHAT_WEBHOOK_URL: Optional[str] = None
    DINGTALK_WEBHOOK_URL: Optional[str] = None
    SYSTEM_ALERT_WEBHOOK_URL: Optional[str] = None
    SMTP_HOST: Optional[str] = None
    SMTP_PORT: int = 587
    SMTP_USER: Optional[str] = None
    SMTP_PASSWORD: Optional[str] = None
    SMTP_FROM: Optional[str] = None
    
    # 日志配置
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "json"
    LOG_FILE: Optional[str] = None
    
    # 自监控配置
    SELF_MONITORING_ENABLED: bool = True
    SELF_MONITORING_INTERVAL: int = 60

    # Syslog 配置
    SYSLOG_ENABLED: bool = True
    SYSLOG_LISTEN_HOST: str = "0.0.0.0"
    SYSLOG_LISTEN_PORT: int = 514

    # SNMP Trap 配置。容器内监听 1162，compose 默认映射宿主机 UDP/162。
    SNMP_TRAP_ENABLED: bool = True
    SNMP_TRAP_LISTEN_HOST: str = "0.0.0.0"
    SNMP_TRAP_LISTEN_PORT: int = 1162

    # Flow 配置（用于按客户公网 IP 统计流量）
    FLOW_ENABLED: bool = True
    FLOW_LISTEN_HOST: str = "0.0.0.0"
    FLOW_NETFLOW_PORT: int = 2055
    FLOW_SFLOW_PORT: int = 6343
    FLOW_FLUSH_INTERVAL_SECONDS: int = 10
    FLOW_CUSTOMER_CACHE_SECONDS: int = 60

    # 请求治理
    REQUEST_ID_HEADER: str = "X-Request-ID"
    RATE_LIMIT_LOGIN_PER_MINUTE: int = 15
    RATE_LIMIT_WRITE_PER_MINUTE: int = 180
    RATE_LIMIT_READ_PER_MINUTE: int = 600
    IDEMPOTENCY_TTL_SECONDS: int = 600
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


# 全局配置实例
settings = Settings()
