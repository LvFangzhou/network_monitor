"""服务器物理连接可信度与端口变更安全检查。"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple


EVIDENCE_WEIGHTS = {
    "manual": 100,
    "lldp": 55,
    "agent": 45,
    "mac_table": 35,
    "redfish": 25,
    "arp": 15,
}


def normalize_mac(value: str) -> str:
    raw = re.sub(r"[^0-9a-fA-F]", "", str(value or "")).lower()
    if len(raw) != 12:
        raise ValueError("MAC 地址格式不正确")
    return ":".join(raw[index:index + 2] for index in range(0, 12, 2))


def score_connection_evidence(evidence: Iterable[Dict[str, Any]]) -> Tuple[float, str, List[str]]:
    rows = list(evidence or [])
    sources = {str(row.get("source") or "").lower() for row in rows}
    score = max((EVIDENCE_WEIGHTS.get(source, 0) for source in sources), default=0)
    # 多个独立数据面互相印证，比重复的同源事件更可信。
    if len(sources) >= 2:
        score += 20
    if len(sources) >= 3:
        score += 10

    conflicts: List[str] = []
    details = [row.get("details") or {} for row in rows]
    if any(item.get("mac_move") or item.get("multiple_switch_ports") for item in details):
        score -= 35
        conflicts.append("检测到 MAC 漂移或同一 MAC 出现在多个物理端口")
    if any(item.get("virtualization_host") or item.get("bridge") for item in details):
        score -= 20
        conflicts.append("存在虚拟化宿主机/网桥线索，不能仅凭 MAC 表确认物理连接")
    if any(item.get("lag_member") and not item.get("lag_consistent") for item in details):
        score -= 20
        conflicts.append("聚合成员信息不完整或不一致")
    score = float(max(0, min(100, score)))
    level = "high" if score >= 80 and not conflicts else "medium" if score >= 55 else "low"
    return score, level, conflicts


ALLOWED_PORT_CONFIG_KEYS = {
    "description", "mode", "access_vlan", "allowed_vlans", "native_vlan", "mtu",
    "aggregation_group", "pfc_enabled", "pfc_priorities", "ecn_enabled",
}

MIN_CONNECTION_CONFIDENCE = 55


def build_config_diff(existing: Dict[str, Any], requested: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [
        {"field": key, "before": existing.get(key), "after": value}
        for key, value in requested.items()
        if key in ALLOWED_PORT_CONFIG_KEYS and existing.get(key) != value
    ]


def precheck_port_change(connection: Any, requested: Dict[str, Any]) -> Dict[str, Any]:
    errors: List[str] = []
    warnings: List[str] = []
    if connection.state != "confirmed":
        errors.append("连接关系尚未人工确认")
    if float(connection.confidence or 0) < MIN_CONNECTION_CONFIDENCE:
        errors.append(f"连接可信度低于 {MIN_CONNECTION_CONFIDENCE} 分")
    if not requested:
        errors.append("没有需要变更的端口配置")
    unknown = sorted(set(requested) - ALLOWED_PORT_CONFIG_KEYS)
    if unknown:
        errors.append(f"包含不支持的配置字段：{', '.join(unknown)}")
    mode = requested.get("mode")
    if mode and mode not in {"access", "trunk", "hybrid"}:
        errors.append("端口模式只允许 access/trunk/hybrid")
    if mode == "access" and not requested.get("access_vlan"):
        errors.append("Access 模式必须指定 access_vlan")
    if mode == "trunk" and not requested.get("allowed_vlans"):
        warnings.append("Trunk 模式未指定允许 VLAN，请确认是否保留现状")
    mtu = requested.get("mtu")
    if mtu is not None:
        try:
            mtu_value = int(mtu)
        except (TypeError, ValueError):
            errors.append("MTU 必须是整数")
        else:
            if not 576 <= mtu_value <= 9216:
                errors.append("MTU 必须在 576～9216 之间")
    if requested.get("pfc_enabled") and not requested.get("pfc_priorities"):
        warnings.append("已启用 PFC，但没有指定优先级")
    if requested.get("ecn_enabled") and not requested.get("pfc_enabled"):
        warnings.append("启用 ECN 前请确认交换机队列和服务器 RoCE 参数一致")
    if connection.conflict_reasons:
        errors.append("连接仍存在冲突证据，需重新确认")
    return {"passed": not errors, "errors": errors, "warnings": warnings}
