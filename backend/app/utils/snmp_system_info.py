"""
SNMP system 信息解析工具。
"""
from __future__ import annotations

import re
from typing import Optional


_MODEL_PATTERNS = [
    # Yillion / DensiveloOS:
    #   Densivelo S9867-128DH
    re.compile(r"^\s*Densivelo\s+([A-Za-z0-9._/-]+)", re.IGNORECASE | re.MULTILINE),
    # H3C Comware:
    #   H3C S9820-64H
    re.compile(r"^\s*H3C\s+([A-Za-z]*\d[A-Za-z0-9._/-]*)", re.IGNORECASE | re.MULTILINE),
    # Huawei / HUAWEI:
    #   HUAWEI S6730-H48X6C
    re.compile(r"^\s*HUAWEI\s+([A-Za-z]*\d[A-Za-z0-9._/-]*)", re.IGNORECASE | re.MULTILINE),
    # Ruijie:
    #   Ruijie RG-S6120-20XS4VS2QXS
    re.compile(r"^\s*Ruijie\s+([A-Za-z]*\d[A-Za-z0-9._/-]*)", re.IGNORECASE | re.MULTILINE),
]


_MODEL_SKIP_KEYWORDS = (
    "software",
    "version",
    "release",
    "copyright",
    "platform",
    "operating",
    "system",
    "all rights reserved",
)


def extract_snmp_model(sys_descr: Optional[str]) -> Optional[str]:
    """从 sysDescr 文本中尽量提取设备型号。

    不同厂商 sysDescr 的格式差异很大，先匹配已知厂商格式；
    如果没有命中，再从非版权/软件说明行里提取最像型号的 token。
    """
    text = str(sys_descr or "").strip()
    if not text:
        return None

    for pattern in _MODEL_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1).strip()

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        lowered = line.lower()
        if any(keyword in lowered for keyword in _MODEL_SKIP_KEYWORDS):
            continue
        candidates = re.findall(r"\b[A-Za-z][A-Za-z0-9]*(?:[-_/][A-Za-z0-9]+)+\b", line)
        for candidate in candidates:
            if re.search(r"\d", candidate):
                return candidate.strip()

    return None
