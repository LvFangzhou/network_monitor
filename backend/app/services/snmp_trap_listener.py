"""
SNMP Trap UDP 监听器。

目前主要解析山石 Hillstone 私有 Trap，并转换为系统现有告警历史。
"""
from __future__ import annotations

import asyncio
import base64
import re
import socket
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.core import get_logger
from app.database import SessionLocal
from app.models import AlertHistory, AlertRule, AlertSilence, Device
from app.tasks.system_tasks import _detect_webhook_provider
from app.tasks.alert_tasks import (
    _ensure_alarm_id,
    _is_silenced,
    enqueue_alert_notification,
)

logger = get_logger(__name__)

SNMP_TRAP_OID = "1.3.6.1.6.3.1.1.4.1.0"
SYS_NAME_OID = "1.3.6.1.2.1.1.5.0"
HILLSTONE_TRAP_BASE = "1.3.6.1.4.1.28557.3"
HILLSTONE_TRAP_SEVERITY_OID = "1.3.6.1.4.1.28557.4"
HILLSTONE_TRAP_TIME_OID = "1.3.6.1.4.1.28557.5"
IGNORED_IPV6_TRAP_OIDS = {
    f"{HILLSTONE_TRAP_BASE}.62",
    f"{HILLSTONE_TRAP_BASE}.74",
    f"{HILLSTONE_TRAP_BASE}.75",
}
CONFIG_TRAP_AGGREGATION_SECONDS = 60


@dataclass(frozen=True)
class TrapDefinition:
    name: str
    severity: str
    clear_oid: Optional[str] = None
    firing_oid: Optional[str] = None
    category: Optional[str] = None


HILLSTONE_TRAPS: Dict[str, TrapDefinition] = {
    f"{HILLSTONE_TRAP_BASE}.1": TrapDefinition("山石CPU阈值Trap", "P0", clear_oid=f"{HILLSTONE_TRAP_BASE}.51", category="cpu"),
    f"{HILLSTONE_TRAP_BASE}.51": TrapDefinition("山石CPU阈值恢复Trap", "P2", firing_oid=f"{HILLSTONE_TRAP_BASE}.1", category="cpu"),
    f"{HILLSTONE_TRAP_BASE}.2": TrapDefinition("山石风扇异常Trap", "P0", clear_oid=f"{HILLSTONE_TRAP_BASE}.48", category="fan"),
    f"{HILLSTONE_TRAP_BASE}.38": TrapDefinition("山石风扇异常Trap", "P0", clear_oid=f"{HILLSTONE_TRAP_BASE}.48", category="fan"),
    f"{HILLSTONE_TRAP_BASE}.48": TrapDefinition("山石风扇恢复Trap", "P2", firing_oid=f"{HILLSTONE_TRAP_BASE}.38", category="fan"),
    f"{HILLSTONE_TRAP_BASE}.3": TrapDefinition("山石温度过高Trap", "P0", clear_oid=f"{HILLSTONE_TRAP_BASE}.50", category="temperature"),
    f"{HILLSTONE_TRAP_BASE}.50": TrapDefinition("山石温度恢复Trap", "P2", firing_oid=f"{HILLSTONE_TRAP_BASE}.3", category="temperature"),
    f"{HILLSTONE_TRAP_BASE}.4": TrapDefinition("山石内存阈值Trap", "P0", clear_oid=f"{HILLSTONE_TRAP_BASE}.52", category="memory"),
    f"{HILLSTONE_TRAP_BASE}.52": TrapDefinition("山石内存阈值恢复Trap", "P2", firing_oid=f"{HILLSTONE_TRAP_BASE}.4", category="memory"),
    f"{HILLSTONE_TRAP_BASE}.5": TrapDefinition("山石HA状态变化Trap", "P0", category="ha"),
    f"{HILLSTONE_TRAP_BASE}.6": TrapDefinition("山石攻击检测Trap", "P0", category="attack"),
    f"{HILLSTONE_TRAP_BASE}.7": TrapDefinition("山石IPSec隧道Down Trap", "P1", clear_oid=f"{HILLSTONE_TRAP_BASE}.8", category="ipsec"),
    f"{HILLSTONE_TRAP_BASE}.8": TrapDefinition("山石IPSec隧道恢复Trap", "P2", firing_oid=f"{HILLSTONE_TRAP_BASE}.7", category="ipsec"),
    f"{HILLSTONE_TRAP_BASE}.9": TrapDefinition("山石接口地址变化Trap", "P1", category="interface_ip"),
    f"{HILLSTONE_TRAP_BASE}.10": TrapDefinition("山石设备名称变化Trap", "P1", category="device_name"),
    f"{HILLSTONE_TRAP_BASE}.11": TrapDefinition("山石IPS状态变化Trap", "P0", category="ips"),
    f"{HILLSTONE_TRAP_BASE}.12": TrapDefinition("山石AV状态变化Trap", "P0", category="av"),
    f"{HILLSTONE_TRAP_BASE}.13": TrapDefinition("山石系统重启Trap", "P0", category="reboot"),
    f"{HILLSTONE_TRAP_BASE}.14": TrapDefinition("山石磁盘空间不足Trap", "P0", clear_oid=f"{HILLSTONE_TRAP_BASE}.53", category="disk"),
    f"{HILLSTONE_TRAP_BASE}.53": TrapDefinition("山石磁盘空间恢复Trap", "P2", firing_oid=f"{HILLSTONE_TRAP_BASE}.14", category="disk"),
    f"{HILLSTONE_TRAP_BASE}.15": TrapDefinition("山石会话数超阈值Trap", "P1", category="session"),
    f"{HILLSTONE_TRAP_BASE}.16": TrapDefinition("山石日志缓存超阈值Trap", "P0", category="log_buffer"),
    f"{HILLSTONE_TRAP_BASE}.17": TrapDefinition("山石接口带宽超阈值Trap", "P0", clear_oid=f"{HILLSTONE_TRAP_BASE}.54", category="bandwidth"),
    f"{HILLSTONE_TRAP_BASE}.54": TrapDefinition("山石接口带宽恢复Trap", "P2", firing_oid=f"{HILLSTONE_TRAP_BASE}.17", category="bandwidth"),
    f"{HILLSTONE_TRAP_BASE}.18": TrapDefinition("山石策略数量超阈值Trap", "P0", category="policy"),
    f"{HILLSTONE_TRAP_BASE}.19": TrapDefinition("【Hillstone】山石配置变更Trap", "P3", category="config"),
    f"{HILLSTONE_TRAP_BASE}.20": TrapDefinition("山石板卡上线Trap", "P1", category="slot"),
    f"{HILLSTONE_TRAP_BASE}.21": TrapDefinition("山石板卡下线Trap", "P0", category="slot"),
    f"{HILLSTONE_TRAP_BASE}.22": TrapDefinition("山石SNAT资源超阈值Trap", "P0", category="snat"),
    f"{HILLSTONE_TRAP_BASE}.23": TrapDefinition("山石登录失败Trap", "P1", category="login"),
    f"{HILLSTONE_TRAP_BASE}.24": TrapDefinition("山石Bypass模式Trap", "P0", category="bypass"),
    f"{HILLSTONE_TRAP_BASE}.25": TrapDefinition("山石Inline模式Trap", "P1", category="bypass"),
    f"{HILLSTONE_TRAP_BASE}.37": TrapDefinition("山石电源异常Trap", "P0", clear_oid=f"{HILLSTONE_TRAP_BASE}.49", category="power"),
    f"{HILLSTONE_TRAP_BASE}.49": TrapDefinition("山石电源恢复Trap", "P2", firing_oid=f"{HILLSTONE_TRAP_BASE}.37", category="power"),
    f"{HILLSTONE_TRAP_BASE}.55": TrapDefinition("山石硬盘空间超阈值Trap", "P0", clear_oid=f"{HILLSTONE_TRAP_BASE}.56", category="hard_disk"),
    f"{HILLSTONE_TRAP_BASE}.56": TrapDefinition("山石硬盘空间恢复Trap", "P2", firing_oid=f"{HILLSTONE_TRAP_BASE}.55", category="hard_disk"),
    f"{HILLSTONE_TRAP_BASE}.59": TrapDefinition("山石用户批量注销Trap", "P1", category="user_logout"),
    f"{HILLSTONE_TRAP_BASE}.60": TrapDefinition("山石会话限制Trap", "P0", category="session_limit"),
    f"{HILLSTONE_TRAP_BASE}.61": TrapDefinition("山石OSPF邻居Down Trap", "P0", category="ospf"),
    f"{HILLSTONE_TRAP_BASE}.63": TrapDefinition("山石BGP邻居Down Trap", "P0", category="bgp"),
    f"{HILLSTONE_TRAP_BASE}.66": TrapDefinition("山石接口物理Up Trap", "P2", firing_oid=f"{HILLSTONE_TRAP_BASE}.67", category="if_physical"),
    f"{HILLSTONE_TRAP_BASE}.67": TrapDefinition("山石接口物理Down Trap", "P1", clear_oid=f"{HILLSTONE_TRAP_BASE}.66", category="if_physical"),
    f"{HILLSTONE_TRAP_BASE}.68": TrapDefinition("山石接口管理Up Trap", "P2", firing_oid=f"{HILLSTONE_TRAP_BASE}.69", category="if_admin"),
    f"{HILLSTONE_TRAP_BASE}.69": TrapDefinition("山石接口管理Down Trap", "P1", clear_oid=f"{HILLSTONE_TRAP_BASE}.68", category="if_admin"),
    f"{HILLSTONE_TRAP_BASE}.70": TrapDefinition("山石接口链路Up Trap", "P2", firing_oid=f"{HILLSTONE_TRAP_BASE}.71", category="if_link"),
    f"{HILLSTONE_TRAP_BASE}.71": TrapDefinition("山石接口链路Down Trap", "P1", clear_oid=f"{HILLSTONE_TRAP_BASE}.70", category="if_link"),
    f"{HILLSTONE_TRAP_BASE}.72": TrapDefinition("山石接口协议Up Trap", "P2", firing_oid=f"{HILLSTONE_TRAP_BASE}.73", category="if_protocol"),
    f"{HILLSTONE_TRAP_BASE}.73": TrapDefinition("山石接口协议Down Trap", "P1", clear_oid=f"{HILLSTONE_TRAP_BASE}.72", category="if_protocol"),
    f"{HILLSTONE_TRAP_BASE}.76": TrapDefinition("山石进程Crash Trap", "P0", category="daemon_crash"),
    f"{HILLSTONE_TRAP_BASE}.77": TrapDefinition("山石进程心跳丢失Trap", "P0", category="daemon_heartbeat"),
    f"{HILLSTONE_TRAP_BASE}.78": TrapDefinition("山石进程死锁Trap", "P0", category="daemon_deadlock"),
    f"{HILLSTONE_TRAP_BASE}.79": TrapDefinition("山石BFD会话Down Trap", "P0", category="bfd"),
}


class _SnmpTrapProtocol(asyncio.DatagramProtocol):
    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        source_ip, _ = addr
        try:
            _enqueue_trap_event(source_ip, data)
        except Exception as exc:
            logger.error("处理SNMP Trap失败", source_ip=source_ip, error=str(exc))


def _enqueue_trap_event(source_ip: str, data: bytes) -> None:
    from app.tasks.event_tasks import process_snmp_trap_event, record_event_queue_metric

    event_id = uuid.uuid4().hex
    received_at = time.time()
    payload_b64 = base64.b64encode(data).decode("ascii")
    try:
        process_snmp_trap_event.apply_async(
            args=[source_ip, payload_b64, event_id, received_at],
            queue="events_trap",
            expires=600,
            retry=False,
        )
        record_event_queue_metric("events_trap", "submitted")
        record_event_queue_metric("events_trap", "last_submitted_at", int(received_at))
    except Exception as exc:
        record_event_queue_metric("events_trap", "enqueue_failed")
        record_event_queue_metric("events_trap", "last_error", str(exc)[:500])
        logger.error("SNMP Trap事件入队失败", source_ip=source_ip, error=str(exc))
        if settings.EVENT_QUEUE_SYNC_FALLBACK:
            record_event_queue_metric("events_trap", "sync_fallback")
            _handle_trap_datagram(source_ip, data)


def _parse_snmp_trap(data: bytes) -> Dict[str, Any]:
    from pyasn1.codec.ber import decoder
    from pysnmp.proto import api

    version = api.decodeMessageVersion(data)
    proto = api.protoModules[version]
    message, _ = decoder.decode(data, asn1Spec=proto.Message())
    pdu = proto.apiMessage.getPDU(message)

    trap_oid: Optional[str] = None
    community = ""
    varbinds: List[Tuple[str, str]] = []

    try:
        community = proto.apiMessage.getCommunity(message).prettyPrint()
    except Exception:
        community = ""

    if version == api.protoVersion1 and pdu.isSameTypeWith(proto.TrapPDU()):
        enterprise_oid = proto.apiTrapPDU.getEnterprise(pdu).prettyPrint()
        specific_trap = int(proto.apiTrapPDU.getSpecificTrap(pdu))
        trap_oid = f"{enterprise_oid}.0.{specific_trap}"
        varbinds = [
            (oid.prettyPrint(), value.prettyPrint())
            for oid, value in proto.apiTrapPDU.getVarBinds(pdu)
        ]
    else:
        varbinds = [
            (oid.prettyPrint(), value.prettyPrint())
            for oid, value in proto.apiPDU.getVarBinds(pdu)
        ]
        for oid, value in varbinds:
            if oid == SNMP_TRAP_OID:
                trap_oid = value
                break

    return {
        "version": int(version),
        "community": community,
        "trap_oid": trap_oid,
        "varbinds": varbinds,
    }


def _webhook_channels() -> List[Dict[str, Any]]:
    channels: List[Dict[str, Any]] = []
    for webhook_url in [
        settings.SYSTEM_ALERT_WEBHOOK_URL,
        settings.WECHAT_WEBHOOK_URL,
        settings.DINGTALK_WEBHOOK_URL,
    ]:
        value = (webhook_url or "").strip()
        if not value:
            continue
        channel_type = _detect_webhook_provider(value)
        config_key = "url" if channel_type == "webhook" else "webhook"
        if any(item.get("type") == channel_type and item.get("config", {}).get(config_key) == value for item in channels):
            continue
        channels.append({"type": channel_type, "config": {config_key: value}})
    return channels


def _ensure_trap_rule(db, definition: TrapDefinition, trap_oid: str) -> AlertRule:
    rule_name = definition.name
    rule = db.query(AlertRule).filter(
        AlertRule.metric_type == "snmp_trap",
        AlertRule.name == rule_name,
    ).first()
    if rule:
        return rule

    rule = AlertRule(
        name=rule_name,
        description="由山石防火墙 SNMP Trap 自动生成的告警规则",
        rule_type="event",
        metric_type="snmp_trap",
        condition="==",
        threshold=1.0,
        duration=0,
        severity=definition.severity,
        suppress_duration=300,
        enabled=1,
        device_ids=[],
        notification_channels=_webhook_channels(),
        extra_config={
            "trap_oid": trap_oid,
            "trap_category": definition.category,
            "vendor": "Hillstone",
        },
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def _resolve_device(db, source_ip: str, sys_name: Optional[str]) -> Optional[Device]:
    device = db.query(Device).filter(Device.ip_address == source_ip).first()
    if device or not sys_name:
        return device
    return db.query(Device).filter(
        (Device.name == sys_name) | (Device.hostname == sys_name)
    ).first()


def _trap_detail_text(varbinds: List[Tuple[str, str]], *, preserve_full_text: bool = False) -> str:
    ignored_oids = {SNMP_TRAP_OID, SYS_NAME_OID, HILLSTONE_TRAP_SEVERITY_OID, HILLSTONE_TRAP_TIME_OID}
    details = []
    for oid, value in varbinds:
        if oid in ignored_oids:
            continue
        if oid.startswith("1.3.6.1.2.1.1.3."):
            continue
        if not value or value == "Null":
            continue
        normalized = _normalize_trap_value(str(value).strip())
        if normalized:
            details.append(normalized)
    detail_text = " / ".join(dict.fromkeys(details))
    return detail_text if preserve_full_text else detail_text[:500]


def _normalize_trap_value(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    decoded = _decode_hex_text(text)
    return decoded or text


def _decode_hex_text(value: str) -> str:
    text = (value or "").strip()
    if not re.fullmatch(r"0x[0-9a-fA-F]+", text) or len(text) % 2:
        return ""
    try:
        decoded = bytes.fromhex(text[2:]).decode("utf-8", errors="replace").strip()
    except ValueError:
        return ""
    if not decoded or decoded == text:
        return ""
    return decoded


def _clean_config_trap_detail(detail_text: str, *device_identifiers: Optional[str]) -> str:
    """Remove device identity fields without discarding the actual config payload."""
    detail = (detail_text or "").strip()
    identifiers = {
        str(value).strip().casefold()
        for value in device_identifiers
        if value is not None and str(value).strip()
    }
    if not detail:
        return ""

    # Hillstone commonly sends ``hostname / operation detail``.  The hostname
    # VarBind is not guaranteed to be present, so compare against both sysName
    # and the device identity resolved from the source address.
    parts = [part.strip() for part in re.split(r"\s+/\s+", detail) if part.strip()]
    while len(parts) > 1 and parts[0].casefold() in identifiers:
        parts.pop(0)
    parts = [part for part in parts if part.casefold() not in identifiers]
    return " / ".join(parts).strip(" /\r\n\t")


def _summarize_trap_target(definition: TrapDefinition, sys_name: Optional[str], detail_text: str) -> str:
    detail = (detail_text or "").strip()
    if definition.category == "config":
        detail = _clean_config_trap_detail(detail, sys_name)
        return (detail or "配置变更")[:180]
    return (detail or definition.category or definition.name)[:180]


def _canonical_target_key(definition: TrapDefinition, trap_oid: str, detail_text: str) -> str:
    pair_oid = definition.firing_oid or trap_oid
    category = definition.category or pair_oid
    detail = detail_text or "device"
    if definition.category == "config":
        # A single configuration commit can emit many Trap packets with slightly
        # different details.  Use one device-level key and bound reuse by the
        # aggregation window in _handle_trap_datagram.
        return f"hillstone_trap:{category}:device"
    if definition.category == "bfd":
        local_match = re.search(r"\blocal\s*:\s*([0-9a-fA-F:.]+)", detail, re.IGNORECASE)
        neighbor_match = re.search(r"\bneighbor\s*:\s*([0-9a-fA-F:.]+)", detail, re.IGNORECASE)
        if local_match or neighbor_match:
            local = local_match.group(1).lower() if local_match else "unknown"
            neighbor = neighbor_match.group(1).lower() if neighbor_match else "unknown"
            # BFD Down Trap 的原文包含设备名和 "UP -> DOWN"。只用会话两端地址
            # 作为对象键，避免同一会话因文案变化生成多条活动告警。
            return f"hillstone_trap:bfd:local={local}|neighbor={neighbor}"
    if definition.category == "bgp":
        peer_match = re.search(r"\bBGP\s+peer\s+((?:\d{1,3}\.){3}\d{1,3})", detail, re.IGNORECASE)
        vr_match = re.search(r"\bvirtual\s+router\s+([^\s]+)", detail, re.IGNORECASE)
        if peer_match:
            peer = peer_match.group(1).lower()
            vr = (vr_match.group(1).lower() if vr_match else "default")
            # BGP 状态变化 Trap 的原文包含状态迁移方向，只用 VR + peer 作为对象键，
            # 避免 Established -> Idle 与 Idle -> Established 被当成两个不同对象。
            return f"hillstone_trap:bgp:vr={vr}|peer={peer}"
    return f"hillstone_trap:{category}:{detail}"


def _build_trap_message(
    device: Optional[Device],
    source_ip: str,
    definition: TrapDefinition,
    trap_oid: str,
    severity: Optional[str],
    trap_time: Optional[str],
    detail_text: str,
) -> str:
    device_name = device.name if device else source_ip
    device_ip = device.ip_address if device else source_ip
    lines = [
        f"设备 {device_name} ({device_ip}) 收到山石Trap",
        f"规则: {definition.name}",
        f"Trap OID: {trap_oid}",
    ]
    if severity:
        lines.append(f"Trap级别: {severity}")
    if trap_time:
        lines.append(f"设备时间: {trap_time}")
    if detail_text:
        lines.append(f"Trap内容: {detail_text}")
    return "\n".join(lines)


def _config_batch_message(
    device: Device,
    source_ip: str,
    definition: TrapDefinition,
    trap_oid: str,
    trap_time: Optional[str],
    details: List[str],
) -> str:
    device_name = device.name or source_ip
    device_ip = device.ip_address or source_ip
    unique_details = list(dict.fromkeys(item.strip() for item in details if item and item.strip()))
    lines = [
        f"设备 {device_name} ({device_ip}) 收到山石Trap",
        f"规则: {definition.name}",
        f"Trap OID: {trap_oid}",
    ]
    if trap_time:
        lines.append(f"设备时间: {trap_time}")
    lines.append("Trap内容:")
    lines.extend(f"- {item}" for item in unique_details)
    return "\n".join(lines)


def _config_batch_details(message: Optional[str]) -> List[str]:
    text = (message or "").strip()
    if "Trap内容:" not in text:
        return []
    detail_text = text.split("Trap内容:", 1)[1].strip()
    return [
        line.removeprefix("- ").strip()
        for line in detail_text.splitlines()
        if line.strip() and not line.strip().startswith("- 其余 ")
    ]


def _is_clear_trap(definition: TrapDefinition) -> bool:
    return bool(definition.firing_oid)


def _handle_trap_datagram(source_ip: str, data: bytes) -> None:
    parsed = _parse_snmp_trap(data)
    trap_oid = str(parsed.get("trap_oid") or "").strip()
    if not trap_oid:
        logger.warning("收到SNMP Trap但未解析到Trap OID", source_ip=source_ip)
        return
    if trap_oid in IGNORED_IPV6_TRAP_OIDS:
        logger.info("忽略IPv6相关山石Trap", source_ip=source_ip, trap_oid=trap_oid)
        return
    if not trap_oid.startswith(HILLSTONE_TRAP_BASE + "."):
        logger.info("收到非山石私有Trap，暂不转换为告警", source_ip=source_ip, trap_oid=trap_oid)
        return

    definition = HILLSTONE_TRAPS.get(trap_oid) or TrapDefinition("山石未知Trap", "P1", category="unknown")
    varbinds = parsed.get("varbinds") or []
    varbind_map = {oid: value for oid, value in varbinds}
    sys_name = varbind_map.get(SYS_NAME_OID)
    severity = varbind_map.get(HILLSTONE_TRAP_SEVERITY_OID)
    trap_time = varbind_map.get(HILLSTONE_TRAP_TIME_OID)
    detail_text = _trap_detail_text(varbinds, preserve_full_text=definition.category == "config")
    target_key = _canonical_target_key(definition, trap_oid, detail_text)
    target_name = _summarize_trap_target(definition, sys_name, detail_text)

    db = SessionLocal()
    try:
        device = _resolve_device(db, source_ip, sys_name)
        if not device:
            logger.warning("收到山石Trap但未匹配到设备", source_ip=source_ip, sys_name=sys_name, trap_oid=trap_oid)
            return

        rule = _ensure_trap_rule(db, definition, trap_oid)
        target = {
            "target_type": "snmp_trap",
            "target_key": target_key,
            "target_name": target_name,
            "value": 1.0,
        }

        if _is_clear_trap(definition):
            existing = db.query(AlertHistory).filter(
                AlertHistory.device_id == device.id,
                AlertHistory.alert_target_key == target_key,
                AlertHistory.status.in_(["firing", "acknowledged", "ignored", "snoozed"]),
            ).order_by(AlertHistory.started_at.desc()).first()
            if existing:
                existing.status = "resolved"
                existing.resolved_by = "snmp_trap"
                existing.resolved_at = datetime.now(timezone.utc)
                existing.resolution_note = definition.name
                existing.updated_at = datetime.now(timezone.utc)
                db.commit()
                enqueue_alert_notification(existing.id, "auto_resolved", "snmp_trap")
            logger.info("山石恢复Trap已处理", source_ip=source_ip, trap_oid=trap_oid, matched=bool(existing))
            return

        message = _build_trap_message(device, source_ip, definition, trap_oid, severity, trap_time, detail_text)
        existing_query = db.query(AlertHistory).filter(
            AlertHistory.rule_id == rule.id,
            AlertHistory.device_id == device.id,
            AlertHistory.alert_target_key == target_key,
            AlertHistory.status.in_(["firing", "acknowledged", "ignored", "snoozed"]),
        )
        if definition.category == "config":
            aggregation_started_at = datetime.now(timezone.utc) - timedelta(seconds=CONFIG_TRAP_AGGREGATION_SECONDS)
            existing_query = existing_query.filter(AlertHistory.started_at >= aggregation_started_at)
        existing = existing_query.order_by(AlertHistory.started_at.desc()).first()

        if definition.category == "config":
            detail_summary = _clean_config_trap_detail(
                detail_text,
                sys_name,
                device.name,
                device.ip_address,
                source_ip,
            ) or "配置变更（Trap未携带详细内容）"
            device_identifiers = {
                str(value).strip().casefold()
                for value in (sys_name, device.name, device.ip_address, source_ip)
                if value is not None and str(value).strip()
            }
            previous_details = [
                item for item in (_config_batch_details(existing.message) if existing else [])
                if item.casefold() not in device_identifiers
            ]
            batch_details = list(dict.fromkeys([*previous_details, detail_summary]))
            message = _config_batch_message(
                device,
                source_ip,
                definition,
                trap_oid,
                trap_time,
                batch_details,
            )
            target_name = f"配置变更（{len(batch_details)}条）"

        if _is_silenced(db, rule, device, target):
            if existing:
                existing.status = "ignored"
                existing.ignored_by = "alert_silence"
                existing.ignored_at = datetime.now(timezone.utc)
                existing.message = message
                existing.updated_at = datetime.now(timezone.utc)
            else:
                existing = AlertHistory(
                    rule_id=rule.id,
                    device_id=device.id,
                    alert_value=1.0,
                    threshold=1.0,
                    message=message,
                    alert_target_type="snmp_trap",
                    alert_target_key=target_key,
                    alert_target_name=target_name,
                    status="ignored",
                    ignored_by="alert_silence",
                    ignored_at=datetime.now(timezone.utc),
                    started_at=datetime.now(timezone.utc),
                )
                db.add(existing)
            db.commit()
            db.refresh(existing)
            _ensure_alarm_id(db, existing)
            logger.info("山石Trap命中屏蔽规则，已记录为忽略", source_ip=source_ip, trap_oid=trap_oid, alert_id=existing.id)
            return

        if existing:
            existing.alert_value = 1.0
            existing.message = message
            existing.alert_target_type = "snmp_trap"
            existing.alert_target_name = target_name
            existing.updated_at = datetime.now(timezone.utc)
            db.commit()
            logger.info("山石Trap持续告警已更新", source_ip=source_ip, trap_oid=trap_oid, alert_id=existing.id)
            return

        alert = AlertHistory(
            rule_id=rule.id,
            device_id=device.id,
            alert_value=1.0,
            threshold=1.0,
            message=message,
            alert_target_type="snmp_trap",
            alert_target_key=target_key,
            alert_target_name=target_name,
            status="firing",
            started_at=datetime.now(timezone.utc),
        )
        db.add(alert)
        db.commit()
        db.refresh(alert)
        _ensure_alarm_id(db, alert)
        enqueue_alert_notification(
            alert.id,
            countdown_seconds=CONFIG_TRAP_AGGREGATION_SECONDS if definition.category == "config" else 0,
        )
        logger.info("山石Trap已转换为告警", source_ip=source_ip, trap_oid=trap_oid, alert_id=alert.id)
    except Exception as exc:
        db.rollback()
        logger.error("写入山石Trap告警失败", source_ip=source_ip, trap_oid=trap_oid, error=str(exc))
    finally:
        db.close()


class SnmpTrapListener:
    def __init__(self) -> None:
        self.transport = None

    async def start(self) -> None:
        if not settings.SNMP_TRAP_ENABLED:
            logger.info("SNMP Trap监听未启用")
            return
        if self.transport is not None:
            return
        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(
            lambda: _SnmpTrapProtocol(),
            local_addr=(settings.SNMP_TRAP_LISTEN_HOST, settings.SNMP_TRAP_LISTEN_PORT),
            family=socket.AF_INET,
        )
        logger.info(
            "SNMP Trap监听已启动",
            host=settings.SNMP_TRAP_LISTEN_HOST,
            port=settings.SNMP_TRAP_LISTEN_PORT,
        )

    async def stop(self) -> None:
        if self.transport is not None:
            self.transport.close()
            self.transport = None
            logger.info("SNMP Trap监听已停止")


snmp_trap_listener = SnmpTrapListener()
