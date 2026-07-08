"""
数据库连接管理
"""
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from typing import Generator
from app.config import settings
from app.core import get_logger

logger = get_logger(__name__)

# 创建数据库引擎
engine = create_engine(
    settings.DATABASE_URL,
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
    pool_recycle=settings.DB_POOL_RECYCLE,
    echo=settings.DEBUG
)

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 声明基类
Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """获取数据库会话的依赖函数"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """初始化数据库表"""
    try:
        Base.metadata.create_all(bind=engine)
        ensure_compatible_schema()
        logger.info("数据库表初始化完成")
    except Exception as e:
        logger.error("数据库表初始化失败", error=str(e))
        raise


def ensure_compatible_schema() -> None:
    """为已有数据库补齐兼容字段。"""
    inspector = inspect(engine)
    table_names = set(inspector.get_table_names())
    with engine.begin() as connection:
        if "datacenters" in table_names:
            datacenter_columns = {column["name"] for column in inspector.get_columns("datacenters")}
            if "code" not in datacenter_columns:
                connection.execute(text("ALTER TABLE datacenters ADD COLUMN code VARCHAR(50)"))
            if "contact_email" not in datacenter_columns:
                connection.execute(text("ALTER TABLE datacenters ADD COLUMN contact_email VARCHAR(100)"))
            if "network_owner" not in datacenter_columns:
                connection.execute(text("ALTER TABLE datacenters ADD COLUMN network_owner VARCHAR(100)"))
            if "network_owner_email" not in datacenter_columns:
                connection.execute(text("ALTER TABLE datacenters ADD COLUMN network_owner_email VARCHAR(255)"))
            if "robot_mention" not in datacenter_columns:
                connection.execute(text("ALTER TABLE datacenters ADD COLUMN robot_mention VARCHAR(255)"))
            if "build_date" not in datacenter_columns:
                connection.execute(text("ALTER TABLE datacenters ADD COLUMN build_date TIMESTAMP WITH TIME ZONE"))

        if "users" in table_names:
            user_columns = {column["name"] for column in inspector.get_columns("users")}
            if "read_only" not in user_columns:
                connection.execute(text("ALTER TABLE users ADD COLUMN read_only BOOLEAN DEFAULT FALSE"))
            if "allowed_menus" not in user_columns:
                connection.execute(text("ALTER TABLE users ADD COLUMN allowed_menus JSON DEFAULT '[]'::json"))

        if "devices" in table_names:
            device_columns = {column["name"] for column in inspector.get_columns("devices")}
            if "device_role" not in device_columns:
                connection.execute(text("ALTER TABLE devices ADD COLUMN device_role VARCHAR(50)"))

        if "circuits" in table_names:
            circuit_columns = {column["name"] for column in inspector.get_columns("circuits")}
            if "access_mode" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN access_mode VARCHAR(20) DEFAULT 'single'"))
            if "physical_port_rate_gbps" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN physical_port_rate_gbps INTEGER DEFAULT 0"))
            if "primary_port_rate" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN primary_port_rate VARCHAR(20)"))
            if "secondary_port_rate" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN secondary_port_rate VARCHAR(20)"))
            if "dual_link_mode" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN dual_link_mode VARCHAR(20)"))
            if "primary_device_id" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN primary_device_id INTEGER"))
            if "primary_port_name" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN primary_port_name VARCHAR(100)"))
            if "secondary_device_id" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN secondary_device_id INTEGER"))
            if "secondary_port_name" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN secondary_port_name VARCHAR(100)"))
            if "primary_local_interconnect_ip" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN primary_local_interconnect_ip VARCHAR(100)"))
            if "primary_remote_interconnect_ip" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN primary_remote_interconnect_ip VARCHAR(100)"))
            if "secondary_local_interconnect_ip" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN secondary_local_interconnect_ip VARCHAR(100)"))
            if "secondary_remote_interconnect_ip" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN secondary_remote_interconnect_ip VARCHAR(100)"))
            if "primary_interconnect_type" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN primary_interconnect_type VARCHAR(20)"))
            if "secondary_interconnect_type" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN secondary_interconnect_type VARCHAR(20)"))
            if "primary_routing_mode" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN primary_routing_mode VARCHAR(50)"))
            if "primary_bfd_mode" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN primary_bfd_mode VARCHAR(20) DEFAULT 'none'"))
            if "secondary_routing_mode" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN secondary_routing_mode VARCHAR(50)"))
            if "secondary_bfd_mode" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN secondary_bfd_mode VARCHAR(20) DEFAULT 'none'"))
            if "primary_interconnect_ip" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN primary_interconnect_ip VARCHAR(100)"))
            if "secondary_interconnect_ip" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN secondary_interconnect_ip VARCHAR(100)"))
            if "primary_vlan_id" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN primary_vlan_id INTEGER"))
            if "secondary_vlan_id" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN secondary_vlan_id INTEGER"))
            if "address_segments" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN address_segments JSON DEFAULT '[]'::json"))
            if "customer_id" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN customer_id INTEGER"))
            if "interconnect_address" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN interconnect_address VARCHAR(100)"))
            if "local_interconnect_address" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN local_interconnect_address VARCHAR(100)"))
            if "remote_interconnect_address" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN remote_interconnect_address VARCHAR(100)"))
            if "interconnect_type" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN interconnect_type VARCHAR(20)"))
            if "routing_mode" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN routing_mode VARCHAR(50)"))
            if "bfd_mode" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN bfd_mode VARCHAR(20) DEFAULT 'none'"))
            if "bfd_enabled" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN bfd_enabled BOOLEAN DEFAULT FALSE"))
            if "routed_cidrs" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN routed_cidrs TEXT"))
            if "routed_networks" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN routed_networks JSON DEFAULT '[]'::json"))
            if "local_routed_cidrs" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN local_routed_cidrs TEXT"))
            if "local_routed_networks" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN local_routed_networks JSON DEFAULT '[]'::json"))
            if "remote_routed_cidrs" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN remote_routed_cidrs TEXT"))
            if "remote_routed_networks" not in circuit_columns:
                connection.execute(text("ALTER TABLE circuits ADD COLUMN remote_routed_networks JSON DEFAULT '[]'::json"))

        if "customers" in table_names:
            customer_columns = {column["name"] for column in inspector.get_columns("customers")}
            if "customer_sites" not in customer_columns:
                connection.execute(text("ALTER TABLE customers ADD COLUMN customer_sites JSON DEFAULT '[]'::json"))
            if "service_manager_name" not in customer_columns:
                connection.execute(text("ALTER TABLE customers ADD COLUMN service_manager_name VARCHAR(100)"))
            if "service_manager_contact" not in customer_columns:
                connection.execute(text("ALTER TABLE customers ADD COLUMN service_manager_contact VARCHAR(255)"))
            if "sales_name" not in customer_columns:
                connection.execute(text("ALTER TABLE customers ADD COLUMN sales_name VARCHAR(100)"))
            if "sales_contact" not in customer_columns:
                connection.execute(text("ALTER TABLE customers ADD COLUMN sales_contact VARCHAR(255)"))

        if "circuit_audits" in table_names:
            audit_columns = {column["name"] for column in inspector.get_columns("circuit_audits")}
            if "circuit_name" not in audit_columns:
                connection.execute(text("ALTER TABLE circuit_audits ADD COLUMN circuit_name VARCHAR(100)"))

        if "customer_audits" in table_names:
            customer_audit_columns = {column["name"] for column in inspector.get_columns("customer_audits")}
            if "customer_name" not in customer_audit_columns:
                connection.execute(text("ALTER TABLE customer_audits ADD COLUMN customer_name VARCHAR(100)"))

        if "alert_rules" in table_names:
            alert_rule_columns = {column["name"] for column in inspector.get_columns("alert_rules")}
            if "extra_config" not in alert_rule_columns:
                connection.execute(text("ALTER TABLE alert_rules ADD COLUMN extra_config JSON DEFAULT '{}'::json"))

        if "audit_logs" in table_names:
            audit_log_columns = {column["name"] for column in inspector.get_columns("audit_logs")}
            if "resource_type" not in audit_log_columns:
                connection.execute(text("ALTER TABLE audit_logs ADD COLUMN resource_type VARCHAR(100)"))
            if "resource_id" not in audit_log_columns:
                connection.execute(text("ALTER TABLE audit_logs ADD COLUMN resource_id VARCHAR(100)"))
            if "query_params" not in audit_log_columns:
                connection.execute(text("ALTER TABLE audit_logs ADD COLUMN query_params JSON DEFAULT '{}'::json"))

        if "alert_histories" in table_names:
            alert_history_columns = {column["name"] for column in inspector.get_columns("alert_histories")}
            if "alarm_id" not in alert_history_columns:
                connection.execute(text("ALTER TABLE alert_histories ADD COLUMN alarm_id VARCHAR(64)"))
            if "alert_target_type" not in alert_history_columns:
                connection.execute(text("ALTER TABLE alert_histories ADD COLUMN alert_target_type VARCHAR(50)"))
            if "alert_target_key" not in alert_history_columns:
                connection.execute(text("ALTER TABLE alert_histories ADD COLUMN alert_target_key VARCHAR(255)"))
            if "alert_target_name" not in alert_history_columns:
                connection.execute(text("ALTER TABLE alert_histories ADD COLUMN alert_target_name VARCHAR(255)"))
            if "ignored_by" not in alert_history_columns:
                connection.execute(text("ALTER TABLE alert_histories ADD COLUMN ignored_by VARCHAR(100)"))
            if "ignored_at" not in alert_history_columns:
                connection.execute(text("ALTER TABLE alert_histories ADD COLUMN ignored_at TIMESTAMP WITH TIME ZONE"))
            if "resolved_by" not in alert_history_columns:
                connection.execute(text("ALTER TABLE alert_histories ADD COLUMN resolved_by VARCHAR(100)"))

        if "alert_silences" in table_names:
            alert_silence_columns = {column["name"] for column in inspector.get_columns("alert_silences")}
            if "created_by" not in alert_silence_columns:
                connection.execute(text("ALTER TABLE alert_silences ADD COLUMN created_by VARCHAR(100)"))
            if "include_device_ip" not in alert_silence_columns:
                connection.execute(text("ALTER TABLE alert_silences ADD COLUMN include_device_ip VARCHAR(255)"))
            if "include_interface" not in alert_silence_columns:
                connection.execute(text("ALTER TABLE alert_silences ADD COLUMN include_interface VARCHAR(255)"))
            if "include_message" not in alert_silence_columns:
                connection.execute(text("ALTER TABLE alert_silences ADD COLUMN include_message VARCHAR(255)"))
            if "exclude_device_ip" not in alert_silence_columns:
                connection.execute(text("ALTER TABLE alert_silences ADD COLUMN exclude_device_ip VARCHAR(255)"))
            if "exclude_interface" not in alert_silence_columns:
                connection.execute(text("ALTER TABLE alert_silences ADD COLUMN exclude_interface VARCHAR(255)"))
            if "exclude_message" not in alert_silence_columns:
                connection.execute(text("ALTER TABLE alert_silences ADD COLUMN exclude_message VARCHAR(255)"))
            if "starts_at" not in alert_silence_columns:
                connection.execute(text("ALTER TABLE alert_silences ADD COLUMN starts_at TIMESTAMP WITH TIME ZONE"))
            if "conditions" not in alert_silence_columns:
                connection.execute(text("ALTER TABLE alert_silences ADD COLUMN conditions JSON DEFAULT '[]'::json"))

        if "devices" in table_names:
            device_columns = {column["name"] for column in inspector.get_columns("devices")}
            if "is_monitored" not in device_columns:
                connection.execute(text("ALTER TABLE devices ADD COLUMN is_monitored BOOLEAN DEFAULT FALSE"))
            if "monitor_source" not in device_columns:
                connection.execute(text("ALTER TABLE devices ADD COLUMN monitor_source VARCHAR(50) DEFAULT 'snmp'"))
            if "prometheus_url" not in device_columns:
                connection.execute(text("ALTER TABLE devices ADD COLUMN prometheus_url VARCHAR(255)"))
            if "prometheus_job" not in device_columns:
                connection.execute(text("ALTER TABLE devices ADD COLUMN prometheus_job VARCHAR(100)"))
            if "prometheus_instance" not in device_columns:
                connection.execute(text("ALTER TABLE devices ADD COLUMN prometheus_instance VARCHAR(255)"))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_devices_ip_inet_valid "
                "ON devices ((ip_address::inet)) "
                "WHERE ip_address ~ '^((25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})\\.){3}(25[0-5]|2[0-4][0-9]|1?[0-9]{1,2})$'"
            ))

        if "alert_histories" in table_names:
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_alert_histories_status_started "
                "ON alert_histories (status, started_at DESC)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_alert_histories_status_rule "
                "ON alert_histories (status, rule_id)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_alert_histories_status_device "
                "ON alert_histories (status, device_id)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_alert_histories_rule_device_status "
                "ON alert_histories (rule_id, device_id, status)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_alert_histories_device_started "
                "ON alert_histories (device_id, started_at DESC)"
            ))
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_alert_histories_started_desc "
                "ON alert_histories (started_at DESC)"
            ))

        if "alert_silences" in table_names:
            connection.execute(text(
                "CREATE INDEX IF NOT EXISTS idx_alert_silences_created_at "
                "ON alert_silences (created_at DESC)"
            ))
