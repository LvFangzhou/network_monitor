import asyncio
import difflib
import os
import hashlib
import io
import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from collections import defaultdict
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from app.core import get_logger
from app.database import SessionLocal
from app.models import ConfigBackupJob, ConfigBackupResult, Datacenter, Device
from app.tasks import celery_app
from app.tasks.system_tasks import _notification_channels
from app.utils import notification_manager
from app.utils.config_backup_settings import config_backup_notification_channels

logger = get_logger(__name__)
CONFIG_BACKUP_CONCURRENCY = max(1, int(os.getenv("CONFIG_BACKUP_CONCURRENCY", "32")))
CONFIG_BACKUP_WAIT_TIMEOUT_SECONDS = max(1, int(os.getenv("CONFIG_BACKUP_WAIT_TIMEOUT_SECONDS", "2")))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _backup_command(device: Device) -> str:
    vendor = f"{device.vendor or ''} {device.model or ''}".lower()
    if any(marker in vendor for marker in ["h3c", "华三"]):
        return "dis current-configuration"
    if any(marker in vendor for marker in ["asteros", "asternos", "asterfusion", "星融元", "ruijie", "锐捷"]):
        return "show running-config"
    if any(marker in vendor for marker in ["hillstone", "山石"]):
        return "show configuration running"
    if any(marker in vendor for marker in ["cisco", "nexus", "ios", "nx-os"]):
        return "show running-config"
    return "display current-configuration"


def _startup_config_command(device: Device) -> Optional[str]:
    vendor = _vendor_text(device)
    if any(marker in vendor for marker in ["h3c", "华三"]):
        return "display saved-configuration"
    if _is_asteros(device) or _is_ruijie(device):
        return "show startup-config"
    if _is_hillstone(device):
        return "show configuration startup"
    return None


def _save_config_command(device: Device) -> Optional[str]:
    vendor = _vendor_text(device)
    if any(marker in vendor for marker in ["h3c", "华三"]):
        return "save force"
    if _is_asteros(device):
        return "write running-config"
    if _is_ruijie(device):
        return "write"
    if _is_hillstone(device):
        return "save"
    return None


def _vendor_text(device: Device) -> str:
    return f"{getattr(device, 'vendor', '') or ''} {getattr(device, 'model', '') or ''}".lower()


def _is_asteros(device: Device) -> bool:
    return any(marker in _vendor_text(device) for marker in ["asteros", "asternos", "asterfusion", "星融元"])


def _is_h3c(device: Device) -> bool:
    return any(marker in _vendor_text(device) for marker in ["h3c", "华三"])


def _is_hillstone(device: Device) -> bool:
    return any(marker in _vendor_text(device) for marker in ["hillstone", "山石"])


def _is_ruijie(device: Device) -> bool:
    return any(marker in _vendor_text(device) for marker in ["ruijie", "锐捷", "rgos"])


def _prefer_paramiko_shell(device: Device) -> bool:
    """已知用交互 shell 更稳定的设备，避免 Netmiko 先空等超时再回退。"""
    vendor = _vendor_text(device)
    return any(marker in vendor for marker in ["h3c", "华三", "asteros", "asternos", "asterfusion", "星融元", "ruijie", "锐捷", "rgos"])


def _looks_like_interactive_prompt(text: str) -> bool:
    normalized = text.lower()
    prompt_markers = [
        "do you want to change the password",
        "[y/n]",
        "--more--",
        "press any key",
        "are you sure",
        "continue?",
    ]
    return any(marker in normalized for marker in prompt_markers)


def _looks_like_byte_pager(text: str) -> bool:
    """Asteros/CX 系列分页提示可能只显示 `byte 19082`。"""
    return bool(re.search(r"(?:^|\n)\s*byte\s+\d+\s*$", text, re.IGNORECASE))


def _expected_config_end_marker(device: Device, command: str) -> Optional[str]:
    vendor = _vendor_text(device)
    normalized_command = command.strip().lower()
    if normalized_command in {"dis current-configuration", "display current-configuration", "display saved-configuration"} and any(marker in vendor for marker in ["h3c", "华三"]):
        return "return"
    if normalized_command in {"show running-config", "show startup-config"} and any(marker in vendor for marker in ["asteros", "asternos", "asterfusion", "星融元", "ruijie", "锐捷", "cisco", "nexus", "ios", "nx-os"]):
        return "end"
    return None


def _has_config_end(config: str, marker: str) -> bool:
    lines = [line.strip().lower() for line in (config or "").splitlines() if line.strip()]
    if not lines:
        return False
    normalized_marker = marker.strip().lower()
    return normalized_marker in lines[-20:]


def _validate_config_content(device: Device, command: str, config: str) -> None:
    """确认备份结果不是登录提示/命令回显/交互提示，而是真实配置内容。"""
    text = (config or "").strip()
    if not text:
        raise RuntimeError("备份命令未返回配置内容")

    lines = [line for line in text.splitlines() if line.strip()]
    normalized = text.lower()
    command_only = len(lines) <= 1 and command.lower() in normalized
    if command_only:
        raise RuntimeError(f"备份内容异常：仅获取到命令回显 {command}，未获取到配置正文")

    if _looks_like_interactive_prompt(text):
        raise RuntimeError("备份内容异常：设备仍停留在交互提示/分页提示，未完整输出配置")

    # 大多数设备的配置不会只有几行。阈值保守一点，避免把真正的空配置误判成功。
    min_lines = 5
    if _is_asteros(device):
        min_lines = 8
    if len(lines) < min_lines:
        preview = " / ".join(line.strip() for line in lines[:3])[:160]
        raise RuntimeError(f"备份内容过短：仅 {len(lines)} 行，疑似未完整输出配置。预览：{preview}")

    expected_end_marker = _expected_config_end_marker(device, command)
    if expected_end_marker and not _has_config_end(text, expected_end_marker):
        preview = " / ".join(line.strip() for line in lines[-5:])[:220]
        raise RuntimeError(f"备份内容未读取到配置结束标记 {expected_end_marker}，疑似分页或输出截断。尾部预览：{preview}")


def _screen_disable_commands(device: Device) -> List[str]:
    vendor = f"{device.vendor or ''} {device.model or ''}".lower()
    commands = ["screen-length 0 temporary", "terminal length 0"]
    if any(marker in vendor for marker in ["asteros", "asternos", "asterfusion", "星融元"]):
        # 部分 Asteros/CX308 设备不支持 terminal length/screen-length，发送后会产生 Syntax error，
        # 反而污染后续配置采集结果；这里不下发分页命令，依靠读取循环处理分页提示。
        return []
    if any(marker in vendor for marker in ["ruijie", "锐捷", "hillstone", "山石", "cisco", "nexus", "ios", "nx-os"]):
        return ["terminal length 0", "screen-length 0 temporary"]
    return commands


def _netmiko_device_type(device: Device) -> str:
    vendor = f"{device.vendor or ''} {device.model or ''}".lower()
    if any(marker in vendor for marker in ["h3c", "华三"]):
        return "hp_comware"
    if any(marker in vendor for marker in ["ruijie", "锐捷"]):
        return "cisco_ios"
    if any(marker in vendor for marker in ["asteros", "asternos", "asterfusion", "星融元"]):
        return "cisco_ios"
    if any(marker in vendor for marker in ["hillstone", "山石"]):
        return "autodetect"
    if any(marker in vendor for marker in ["huawei", "华为"]):
        return "huawei"
    if any(marker in vendor for marker in ["cisco", "nexus", "ios", "nx-os"]):
        return "cisco_ios"
    return "autodetect"


def _collect_config_netmiko(device: Device) -> Tuple[str, str]:
    try:
        from netmiko import ConnectHandler
        from netmiko.ssh_autodetect import SSHDetect
    except ImportError as exc:
        raise RuntimeError("缺少 netmiko 依赖") from exc

    username = (device.ssh_username or "").strip()
    password = device.ssh_password or None
    key_text = (device.ssh_key or "").strip()
    if not username:
        raise RuntimeError("设备未配置 SSH 用户名")
    if not password and not key_text:
        raise RuntimeError("设备未配置 SSH 密码或密钥")
    if key_text and not password:
        raise RuntimeError("Netmiko 不使用内联 SSH Key，交由 Paramiko 回退处理")

    command = _backup_command(device)
    device_type = _netmiko_device_type(device)
    base_params: Dict[str, Any] = {
        "device_type": device_type,
        "host": device.ip_address,
        "port": int(device.ssh_port or 22),
        "username": username,
        "timeout": 20,
        "conn_timeout": 15,
        "banner_timeout": 15,
        "auth_timeout": 15,
        "fast_cli": False,
    }
    if password:
        base_params["password"] = password

    if device_type == "autodetect":
        try:
            guesser = SSHDetect(**base_params)
            detected = guesser.autodetect()
            if detected:
                base_params["device_type"] = detected
        except Exception as exc:
            logger.info("Netmiko 自动识别失败，使用 cisco_ios 通用方式重试", device_id=device.id, ip=device.ip_address, error=str(exc))
            base_params["device_type"] = "cisco_ios"

    connection = ConnectHandler(**base_params)
    try:
        if _is_asteros(device):
            # Asteros 登录后 CLI 初始化较慢，立即下发命令容易只返回命令回显。
            time.sleep(5)
        output = connection.send_command(
            command,
            expect_string=None,
            read_timeout=45,
            strip_prompt=True,
            strip_command=True,
        )
        config = _clean_config_output(output, command)
        _validate_config_content(device, command, config)
        return command, config
    finally:
        connection.disconnect()


def _read_shell_output(shell: Any, idle_seconds: float = 0.8, timeout_seconds: float = 45.0) -> str:
    started = time.monotonic()
    last_data = time.monotonic()
    chunks: List[str] = []
    while time.monotonic() - started < timeout_seconds:
        if shell.recv_ready():
            data = shell.recv(65535).decode("utf-8", errors="ignore")
            chunks.append(data)
            last_data = time.monotonic()
            lowered = data.lower()
            if "--more--" in lowered or "---- more ----" in lowered:
                shell.send(" ")
            if _looks_like_byte_pager(data):
                shell.send(" ")
            if "press return" in lowered or "press enter" in lowered:
                shell.send("\n")
            continue
        if chunks and time.monotonic() - last_data >= idle_seconds:
            break
        time.sleep(0.1)
    return "".join(chunks)


def _strip_backspace_overstrikes(text: str) -> str:
    """清理 h\\bho\\bow 这类终端退格重绘输出。"""
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r".\x08", "", text)
    return text


def _strip_terminal_control(text: str) -> str:
    text = _strip_backspace_overstrikes(text).replace("\r\n", "\n").replace("\r", "\n")
    # 清理 ANSI/VT 控制序列。Asteros 部分设备会输出 ESC[?1h、ESC=、ESC[m 等终端控制符。
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"\x1b[()][A-Za-z0-9]", "", text)
    text = re.sub(r"\x1b[=>]", "", text)
    text = re.sub(r"\x1b[@-Z\\-_]", "", text)
    text = text.replace("\x1b", "")
    text = text.replace("\x07", "")
    return text


def _looks_like_prompt_line(line: str) -> bool:
    stripped = line.strip()
    return bool(re.fullmatch(r"(?:<[^<>\s]{1,160}>|[A-Za-z0-9_.:/-]{1,160}#)", stripped))


def _output_returned_to_prompt(output: str) -> bool:
    text = _strip_terminal_control(output)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return any(_looks_like_prompt_line(line) for line in lines[-8:])


def _is_config_capture_noise_line(line: str, command: Optional[str] = None) -> bool:
    """过滤采集配置时由终端控制/关分页命令产生的噪音。

    这类内容不是设备配置本身，例如：
    - <H3C>screen-length 0 temporary / terminal length 0
    - % Unrecognized command found at '^' position.
    - Asteros/Ruijie 的 `hostname# show startup-config` 命令回显
    如果不清理，会导致 running/startup 明明一致却被 diff 标记为不一致。
    """
    stripped = (line or "").strip()
    if not stripped:
        return True
    lower = stripped.lower()
    if stripped == "^" or re.fullmatch(r"\^+", stripped):
        return True
    if re.search(r"unrecognized\s+command|unknown\s+command|invalid\s+input|incomplete\s+command", lower):
        return True

    command_patterns = [
        r"screen-length\s+0\s+temporary",
        r"terminal\s+length\s+0",
        r"terminal\s+pager\s+0",
        r"no\s+page",
        r"display\s+current-configuration",
        r"dis\s+current-configuration",
        r"display\s+saved-configuration",
        r"show\s+running-config",
        r"show\s+startup-config",
        r"show\s+configuration\s+running",
        r"show\s+configuration\s+startup",
    ]
    if command:
        command_patterns.append(re.escape(command.strip()))
    command_union = "(?:" + "|".join(command_patterns) + ")"
    prompt_prefix = r"(?:<[^<>]{1,200}>|[A-Za-z0-9_.:/-]{1,200}#|\[[^\]]{1,200}\])\s*"
    return bool(
        re.fullmatch(command_union, lower, re.IGNORECASE)
        or re.fullmatch(prompt_prefix + command_union, stripped, re.IGNORECASE)
    )


def _clean_config_output(output: str, command: str) -> str:
    text = _strip_terminal_control(output)
    text = text.replace("--More--", "").replace("---- More ----", "")
    lines = [line.rstrip() for line in text.splitlines()]
    cleaned: List[str] = []
    command_seen = False
    for line in lines:
        stripped = line.strip()
        if not command_seen:
            if command in stripped:
                command_seen = True
            continue
        if re.fullmatch(r"byte\s+\d+", stripped, re.IGNORECASE):
            continue
        if stripped.lower() in {"return", "end"}:
            cleaned.append(stripped)
            continue
        cleaned.append(line)
    if not cleaned:
        cleaned = lines
    compacted: List[str] = []
    for line in cleaned:
        if not line.strip():
            # 配置文件里空行基本没有语义，删除多余空行，保留 # / ! 这类厂商自己的段落分隔符。
            continue
        if _is_config_capture_noise_line(line, command):
            continue
        if _looks_like_prompt_line(line):
            continue
        compacted.append(line.rstrip())
    while compacted and not compacted[0].strip():
        compacted.pop(0)
    while compacted and not compacted[-1].strip():
        compacted.pop()
    if compacted:
        tail = compacted[-1].strip()
        if _looks_like_prompt_line(tail):
            compacted.pop()
    return "\n".join(compacted).strip()


def _expand_number_range_list(value: str, limit: int = 4096) -> List[int]:
    """展开 2001,2201-2205 这类配置展示。异常片段直接忽略，避免误伤备份。"""
    numbers: List[int] = []
    seen: set[int] = set()
    for part in re.split(r"\s*,\s*", value.strip()):
        if not part:
            continue
        if re.fullmatch(r"\d+", part):
            start = end = int(part)
        else:
            match = re.fullmatch(r"(\d+)\s*-\s*(\d+)", part)
            if not match:
                continue
            start, end = int(match.group(1)), int(match.group(2))
            if start > end:
                start, end = end, start
        if end - start > limit:
            continue
        for number in range(start, end + 1):
            if number not in seen:
                seen.add(number)
                numbers.append(number)
    return numbers


def _normalize_config_for_compare(config: str, device: Optional[Device] = None) -> List[str]:
    """归一化运行配置/启动配置，忽略时间戳、空行、分页提示和自动生成注释。"""
    normalized: List[str] = []
    asteros_like = bool(device and _is_asteros(device))
    seen_vlan_declarations: set[str] = set()
    vlan_declarations: List[str] = []
    volatile_patterns = [
        r"^\s*$",
        r"^!?\s*(last|current)\s+(configuration|change|updated|saved).*",
        r"^!?\s*(generated|created)\s+by\s+.*",
        r"^!?\s*generated\s+at\s+.*",
        r"^!?\s*time\s*[:=].*",
        r"^!?\s*timestamp\s*[:=].*",
        r"^#\s*configuration\s+(last\s+)?(modified|saved|generated).*",
        r"^\s*building\s+configuration.*",
        r"^\s*current\s+configuration.*",
        r"^\s*running\s+configuration\s*:?\s*$",
        r"^\s*startup\s+configuration\s*:?\s*$",
        r"^\s*正在收集配置.*",
        r"^#\s*generated\s+by\s+.*",
        r"^#\s*size\s+is\s+\d+\s+bytes\s*$",
        r"^#\s*software\s+version\s+.*",
        r"^\s*display\s+.*configuration\s*$",
        r"^\s*show\s+.*config\s*$",
        r"^.*[#>]\s*(?:display|dis|show)\s+.*config(?:uration)?\s*$",
        r"^.*[#>]\s*(?:screen-length\s+0\s+temporary|terminal\s+length\s+0|terminal\s+pager\s+0)\s*$",
        r"^\s*(?:screen-length\s+0\s+temporary|terminal\s+length\s+0|terminal\s+pager\s+0)\s*$",
        r"^\s*\^+\s*$",
        r"^\s*%\s*(?:unrecognized|unknown|invalid|incomplete)\s+command.*",
    ]
    compiled = [re.compile(pattern, re.IGNORECASE) for pattern in volatile_patterns]
    for raw_line in (config or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        if stripped in {"#", "!", "return", "end"}:
            # 段落分隔符不影响配置语义，跳过后能减少无意义 diff。
            continue
        if _is_config_capture_noise_line(stripped):
            continue
        lower_stripped = stripped.lower()
        if lower_stripped in {"exit", "exit-vrf"}:
            # Asteros/CX 与启动配置之间经常把同一层级结束符显示成 exit 或 !，
            # 这类段落结束符不影响配置语义。
            continue
        if asteros_like and re.fullmatch(r"(?:lacp\s+fallback|mode\s+dynamic|commit)", stripped, re.IGNORECASE):
            # Asteros startup-config 会在 link-aggregation 段落里展开默认 LACP/commit 行，
            # running-config 经常省略；这不是实际配置不一致。
            continue
        if asteros_like and re.fullmatch(r"description\s+\S+", stripped, re.IGNORECASE):
            # Asteros startup-config 常把 VLAN/接口默认描述展开显示，running-config 省略。
            continue
        if asteros_like and re.fullmatch(r"switchport\s+access\s+vlan\s+\d+", stripped, re.IGNORECASE):
            # Asteros link-aggregation 默认 access vlan 会在 startup 中展开显示。
            continue
        if any(pattern.search(stripped) for pattern in compiled):
            continue
        vlan_range_match = re.fullmatch(r"vlan\s+range\s+(.+)", stripped, re.IGNORECASE)
        if vlan_range_match:
            for vlan_id in _expand_number_range_list(vlan_range_match.group(1)):
                declaration = f"vlan {vlan_id}"
                if declaration not in seen_vlan_declarations:
                    seen_vlan_declarations.add(declaration)
                    vlan_declarations.append(declaration)
            continue
        trunk_range_match = re.fullmatch(r"switchport\s+trunk\s+range\s+vlan\s+(.+)", stripped, re.IGNORECASE)
        if trunk_range_match:
            for vlan_id in _expand_number_range_list(trunk_range_match.group(1)):
                normalized.append(f"switchport trunk vlan {vlan_id}")
            continue
        vlan_declaration_match = re.fullmatch(r"vlan\s+(\d+)", stripped, re.IGNORECASE)
        if vlan_declaration_match:
            declaration = f"vlan {int(vlan_declaration_match.group(1))}"
            if declaration in seen_vlan_declarations:
                continue
            seen_vlan_declarations.add(declaration)
            vlan_declarations.append(declaration)
            continue
        normalized.append(stripped)
    if vlan_declarations:
        normalized.extend(sorted(vlan_declarations, key=lambda item: int(item.split()[1])))
    return normalized


def _config_diff_summary(running_config: str, startup_config: str, max_lines: int = 80, device: Optional[Device] = None) -> str:
    running_lines = _normalize_config_for_compare(running_config, device)
    startup_lines = _normalize_config_for_compare(startup_config, device)
    diff_lines = list(
        difflib.unified_diff(
            startup_lines,
            running_lines,
            fromfile="startup(saved)",
            tofile="running(current)",
            lineterm="",
        )
    )
    return "\n".join(diff_lines[:max_lines])


def _handle_login_prompts(shell: Any, initial_output: str) -> str:
    output = initial_output
    if "do you want to change the password" in output.lower() or "[y/n]" in output.lower():
        shell.send("n\n")
        output += _read_shell_output(shell, idle_seconds=0.8, timeout_seconds=8)
    return output


def _config_read_tuning(device: Device, command: str) -> Tuple[int, float, float, float]:
    normalized_command = command.strip().lower()
    if _is_asteros(device):
        return 3, 4.0, 150.0, 90.0
    if _is_h3c(device) and normalized_command in {"dis current-configuration", "display current-configuration", "display saved-configuration"}:
        # H3C 大配置输出中途可能有 1~2 秒空隙，idle 太短会把半截配置误认为结束。
        # 这里用更长 idle 和总超时，优先保证配置完整性。
        return 2, 3.0, 240.0, 90.0
    if _is_hillstone(device):
        return 2, 2.0, 120.0, 60.0
    return 1, 1.2, 60.0, 30.0


def _run_shell_config_command(shell: Any, device: Device, command: str) -> str:
    attempts, idle_seconds, timeout_seconds, confirm_timeout_seconds = _config_read_tuning(device, command)
    last_error: Optional[Exception] = None
    for attempt in range(1, attempts + 1):
        # 清空上一次非法命令/提示符残留，避免把残留误认为配置内容。
        shell.send("\n")
        _read_shell_output(shell, idle_seconds=0.8, timeout_seconds=4)
        if _is_asteros(device):
            time.sleep(3 if attempt == 1 else 5)
        shell.send(command + "\n")
        output = _read_shell_output(
            shell,
            idle_seconds=idle_seconds,
            timeout_seconds=timeout_seconds,
        )
        if re.search(r"press\s+(return|enter)", output, re.IGNORECASE):
            shell.send("\n")
            output += _read_shell_output(
                shell,
                idle_seconds=idle_seconds,
                timeout_seconds=confirm_timeout_seconds,
            )
        config = _clean_config_output(output, command)
        try:
            _validate_config_content(device, command, config)
            return config
        except Exception as exc:
            # 部分设备的 startup/saved 输出正常返回设备提示符，但尾部不一定带 end/return。
            # 只要已经回到提示符，就认为不是分页/截断。这个对 Asteros 和部分 H3C saved-config 都适用。
            if "结束标记" in str(exc) and _output_returned_to_prompt(output):
                logger.info(
                    "配置未包含结束标记但已返回提示符，按完整输出处理",
                    device_id=getattr(device, "id", None),
                    ip=getattr(device, "ip_address", None),
                    command=command,
                    attempt=attempt,
                )
                return config
            last_error = exc
            logger.info(
                "配置输出校验失败，准备重试",
                device_id=getattr(device, "id", None),
                ip=getattr(device, "ip_address", None),
                attempt=attempt,
                attempts=attempts,
                error=str(exc),
            )
            time.sleep(3)
    raise RuntimeError(str(last_error) if last_error else "配置备份失败")


def _run_shell_save_command(shell: Any, device: Device, command: str) -> str:
    shell.send("\n")
    _read_shell_output(shell, idle_seconds=0.5, timeout_seconds=4)
    shell.send(command + "\n")
    output = _read_shell_output(shell, idle_seconds=1.0, timeout_seconds=30)
    if _is_asteros(device):
        # Asteros write running-config 会提示确认，按一次回车确认。
        shell.send("\n")
        output += _read_shell_output(shell, idle_seconds=1.2, timeout_seconds=45)
    if _is_ruijie(device):
        # Ruijie write 可能直接保存，也可能要求 y/回车确认；两种都兼容。
        if re.search(r"\b(?:y/n|yes/no|continue|confirm|overwrite)\b|\[y/n\]", output, re.IGNORECASE):
            shell.send("y\n")
            output += _read_shell_output(shell, idle_seconds=1.2, timeout_seconds=45)
        elif not _output_returned_to_prompt(output):
            shell.send("\n")
            output += _read_shell_output(shell, idle_seconds=1.2, timeout_seconds=45)
    if _is_hillstone(device):
        # 山石 save 需要两次 y 确认。
        for _ in range(2):
            if _output_returned_to_prompt(output) and not re.search(r"\b(?:y/n|yes/no|continue|overwrite)\\b|\\[y/n\\]", output, re.IGNORECASE):
                break
            shell.send("y\n")
            output += _read_shell_output(shell, idle_seconds=1.2, timeout_seconds=45)
    normalized = output.lower()
    failure_markers = ["error", "failed", "invalid", "permission denied", "incomplete", "unknown command"]
    if any(marker in normalized for marker in failure_markers):
        raise RuntimeError(_strip_backspace_overstrikes(output).strip()[-500:] or "保存配置失败")
    return _strip_backspace_overstrikes(output).strip()


def _build_sync_payload(
    device: Device,
    running_command: str,
    running_config: str,
    startup_command: Optional[str],
    startup_config: Optional[str],
    save_command: Optional[str],
    save_status: Optional[str],
    save_message: Optional[str],
    sync_error: Optional[str] = None,
) -> Dict[str, Any]:
    running_normalized = "\n".join(_normalize_config_for_compare(running_config, device))
    startup_normalized = "\n".join(_normalize_config_for_compare(startup_config or "", device)) if startup_config is not None else ""

    if sync_error:
        sync_status = "check_failed"
        diff = sync_error[:2000]
    elif not startup_command:
        sync_status = "unsupported"
        diff = "当前厂商暂未配置 running/startup 一致性检查命令"
    elif running_normalized == startup_normalized:
        sync_status = "matched"
        diff = None
    else:
        sync_status = "changed_saved" if save_status == "success" else "changed_save_failed"
        diff = _config_diff_summary(running_config, startup_config or "", device=device)

    return {
        "command": running_command,
        "config_content": running_config,
        "config_hash": hashlib.sha256(running_config.encode("utf-8", errors="ignore")).hexdigest(),
        "line_count": len(running_config.splitlines()),
        "startup_command": startup_command,
        "startup_config_content": startup_config,
        "startup_config_hash": hashlib.sha256((startup_config or "").encode("utf-8", errors="ignore")).hexdigest() if startup_config is not None else None,
        "startup_line_count": len((startup_config or "").splitlines()) if startup_config is not None else 0,
        "config_sync_status": sync_status,
        "config_sync_diff": diff,
        "config_save_command": save_command,
        "config_save_status": save_status,
        "config_save_message": save_message,
    }


def _collect_config(device: Device) -> Tuple[str, str]:
    netmiko_error: Optional[Exception] = None
    if not _prefer_paramiko_shell(device):
        try:
            return _collect_config_netmiko(device)
        except Exception as exc:
            netmiko_error = exc
            logger.info("Netmiko配置备份失败，回退Paramiko", device_id=device.id, ip=device.ip_address, error=str(exc))
    else:
        logger.info("设备使用Paramiko交互模式备份配置", device_id=device.id, ip=device.ip_address, vendor=getattr(device, "vendor", None), model=getattr(device, "model", None))

    try:
        import paramiko
    except ImportError as exc:
        if netmiko_error:
            raise RuntimeError(f"Netmiko失败：{netmiko_error}；且缺少 paramiko 依赖，无法回退") from exc
        raise RuntimeError("缺少 paramiko 依赖，无法执行 SSH 配置备份") from exc

    username = (device.ssh_username or "").strip()
    password = device.ssh_password or None
    key_text = (device.ssh_key or "").strip()
    if not username:
        raise RuntimeError("设备未配置 SSH 用户名")
    if not password and not key_text:
        raise RuntimeError("设备未配置 SSH 密码或密钥")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connect_kwargs: Dict[str, Any] = {
        "hostname": device.ip_address,
        "port": int(device.ssh_port or 22),
        "username": username,
        "timeout": 12,
        "banner_timeout": 12,
        "auth_timeout": 12,
        "look_for_keys": False,
        "allow_agent": False,
    }
    if key_text:
        key_file = io.StringIO(key_text)
        key = None
        key_errors = []
        for key_cls in (paramiko.RSAKey, paramiko.ECDSAKey, paramiko.Ed25519Key):
            key_file.seek(0)
            try:
                key = key_cls.from_private_key(key_file, password=password)
                break
            except Exception as exc:
                key_errors.append(str(exc))
        if not key:
            raise RuntimeError(f"SSH 密钥解析失败：{'; '.join(key_errors[:2])}")
        connect_kwargs["pkey"] = key
    else:
        connect_kwargs["password"] = password

    command = _backup_command(device)
    try:
        client.connect(**connect_kwargs)
        shell = client.invoke_shell(width=240, height=5000)
        initial_output = _read_shell_output(shell, idle_seconds=0.8, timeout_seconds=8)
        _handle_login_prompts(shell, initial_output)
        for disable_command in _screen_disable_commands(device):
            shell.send(disable_command + "\n")
            _read_shell_output(shell, idle_seconds=0.5, timeout_seconds=4)
        config = _run_shell_config_command(shell, device, command)
        return command, config
    finally:
        client.close()


def _collect_config_with_sync(device: Device) -> Dict[str, Any]:
    """采集 running 配置，并检查 startup 是否一致；不一致时自动保存。"""
    # 目前只有 H3C/Asteros 明确要求自动保存。其他厂商仍走原有备份逻辑，避免误操作。
    startup_command = _startup_config_command(device)
    save_command = _save_config_command(device)
    if not startup_command or not save_command:
        command, config = _collect_config(device)
        return _build_sync_payload(device, command, config, None, None, None, None, None)

    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError("缺少 paramiko 依赖，无法执行 SSH 配置备份") from exc

    username = (device.ssh_username or "").strip()
    password = device.ssh_password or None
    key_text = (device.ssh_key or "").strip()
    if not username:
        raise RuntimeError("设备未配置 SSH 用户名")
    if not password and not key_text:
        raise RuntimeError("设备未配置 SSH 密码或密钥")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connect_kwargs: Dict[str, Any] = {
        "hostname": device.ip_address,
        "port": int(device.ssh_port or 22),
        "username": username,
        "timeout": 12,
        "banner_timeout": 12,
        "auth_timeout": 12,
        "look_for_keys": False,
        "allow_agent": False,
    }
    if key_text:
        key_file = io.StringIO(key_text)
        key = None
        key_errors = []
        for key_cls in (paramiko.RSAKey, paramiko.ECDSAKey, paramiko.Ed25519Key):
            key_file.seek(0)
            try:
                key = key_cls.from_private_key(key_file, password=password)
                break
            except Exception as exc:
                key_errors.append(str(exc))
        if not key:
            raise RuntimeError(f"SSH 密钥解析失败：{'; '.join(key_errors[:2])}")
        connect_kwargs["pkey"] = key
    else:
        connect_kwargs["password"] = password

    running_command = _backup_command(device)
    try:
        client.connect(**connect_kwargs)
        shell = client.invoke_shell(width=240, height=5000)
        initial_output = _read_shell_output(shell, idle_seconds=0.8, timeout_seconds=8)
        _handle_login_prompts(shell, initial_output)
        for disable_command in _screen_disable_commands(device):
            shell.send(disable_command + "\n")
            _read_shell_output(shell, idle_seconds=0.5, timeout_seconds=4)

        running_config = _run_shell_config_command(shell, device, running_command)
        startup_config: Optional[str] = None
        save_status: Optional[str] = None
        save_message: Optional[str] = None
        sync_error: Optional[str] = None
        try:
            startup_config = _run_shell_config_command(shell, device, startup_command)
            running_normalized = _normalize_config_for_compare(running_config, device)
            startup_normalized = _normalize_config_for_compare(startup_config, device)
            if running_normalized != startup_normalized:
                try:
                    save_message = _run_shell_save_command(shell, device, save_command)
                    save_status = "success"
                except Exception as exc:
                    save_status = "failed"
                    save_message = str(exc)[:2000]
        except Exception as exc:
            sync_error = f"启动配置检查失败：{exc}"
        return _build_sync_payload(
            device,
            running_command,
            running_config,
            startup_command,
            startup_config,
            save_command,
            save_status,
            save_message,
            sync_error=sync_error,
        )
    finally:
        client.close()


def _build_notification_content(job: ConfigBackupJob, datacenter_stats: Dict[str, Dict[str, int]]) -> Tuple[str, List[Dict[str, str]]]:
    lines = [
        f"配置备份完成：共 {job.total_devices} 台，成功 {job.success_count} 台，失败 {job.failed_count} 台。",
        f"运行/启动配置检查：不一致 {getattr(job, 'config_changed_count', 0) or 0} 台，已自动保存 {getattr(job, 'config_saved_count', 0) or 0} 台，保存失败 {getattr(job, 'config_save_failed_count', 0) or 0} 台。",
    ]
    rows: List[Dict[str, str]] = [
        {"label": "任务ID", "value": str(job.id)},
        {"label": "触发方式", "value": "定时" if job.trigger_type == "scheduled" else "手动"},
        {"label": "总设备", "value": str(job.total_devices)},
        {"label": "成功", "value": str(job.success_count)},
        {"label": "失败", "value": str(job.failed_count)},
        {"label": "配置不一致", "value": str(getattr(job, "config_changed_count", 0) or 0)},
        {"label": "已自动保存", "value": str(getattr(job, "config_saved_count", 0) or 0)},
        {"label": "保存失败", "value": str(getattr(job, "config_save_failed_count", 0) or 0)},
    ]
    for datacenter, stats in sorted(datacenter_stats.items(), key=lambda item: item[0]):
        rows.append({
            "label": datacenter,
            "value": (
                f"成功 {stats.get('success', 0)} / 失败 {stats.get('failed', 0)} / "
                f"不一致 {stats.get('changed', 0)} / 已保存 {stats.get('saved', 0)} / 保存失败 {stats.get('save_failed', 0)}"
            ),
        })
    return "\n".join(lines), rows


def _seconds_between(started_at: Optional[datetime], finished_at: Optional[datetime]) -> Optional[int]:
    if not started_at or not finished_at:
        return None
    try:
        return max(0, int((finished_at - started_at).total_seconds()))
    except Exception:
        return None


def _format_duration(seconds: Optional[int]) -> str:
    if seconds is None:
        return "-"
    if seconds < 60:
        return f"{seconds}秒"
    minutes, rest = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}分{rest}秒" if rest else f"{minutes}分"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}小时{minutes}分" if minutes else f"{hours}小时"


def _split_network_owner_emails(value: Optional[str]) -> List[str]:
    if not value:
        return []
    mentions = []
    for item in re.split(r"[,，;；\s]+", value):
        item = item.strip().lstrip("@")
        if item:
            mentions.append(item)
    return list(dict.fromkeys(mentions))


def _load_datacenter_contacts(datacenter_names: List[str]) -> Dict[str, Dict[str, Any]]:
    names = [name for name in dict.fromkeys(datacenter_names) if name and name != "未设置机房"]
    if not names:
        return {}
    db = SessionLocal()
    try:
        rows = db.query(Datacenter).filter(Datacenter.name.in_(names)).all()
        return {
            row.name: {
                "network_owner": row.network_owner or row.contact_person,
                "network_owner_emails": _split_network_owner_emails(getattr(row, "network_owner_email", None)),
            }
            for row in rows
        }
    finally:
        db.close()


def _build_backup_card_data(job: ConfigBackupJob, datacenter_stats: Dict[str, Dict[str, int]]) -> Dict[str, Any]:
    failed = job.failed_count or 0
    save_failed = getattr(job, "config_save_failed_count", 0) or 0
    changed = getattr(job, "config_changed_count", 0) or 0
    saved = getattr(job, "config_saved_count", 0) or 0
    duration_seconds = _seconds_between(job.started_at, job.finished_at)
    contact_map = _load_datacenter_contacts(list(datacenter_stats.keys()))
    datacenter_rows = []
    for datacenter, stats in sorted(datacenter_stats.items(), key=lambda item: item[0]):
        contact_info = contact_map.get(datacenter, {})
        datacenter_rows.append({
            "name": datacenter,
            "success": stats.get("success", 0),
            "failed": stats.get("failed", 0),
            "changed": stats.get("changed", 0),
            "saved": stats.get("saved", 0),
            "save_failed": stats.get("save_failed", 0),
            "network_owner": contact_info.get("network_owner"),
            "network_owner_emails": contact_info.get("network_owner_emails") or [],
        })
    return {
        "severity": "P2" if failed == 0 and save_failed == 0 else "P1",
        "notification_kind": "config_backup",
        "backup_summary": {
            "job_id": job.id,
            "trigger_type": "定时" if job.trigger_type == "scheduled" else "手动",
            "started_by": job.started_by or "-",
            "duration": _format_duration(duration_seconds),
            "total": job.total_devices or 0,
            "success": job.success_count or 0,
            "failed": failed,
            "changed": changed,
            "saved": saved,
            "save_failed": save_failed,
        },
        "datacenters": datacenter_rows,
    }


def _send_backup_notification(job: ConfigBackupJob, datacenter_stats: Dict[str, Dict[str, int]]) -> None:
    channels = config_backup_notification_channels() or _notification_channels()
    if not channels:
        logger.warning("配置备份完成但没有配置机器人通知", job_id=job.id)
        return

    content, rows = _build_notification_content(job, datacenter_stats)
    has_save_failed = (getattr(job, "config_save_failed_count", 0) or 0) > 0
    title = "网络设备配置备份完成" if job.failed_count == 0 and not has_save_failed else "网络设备配置备份完成（存在失败/保存异常）"
    card_data = _build_backup_card_data(job, datacenter_stats)
    card_data["rows"] = rows

    async def _send_all() -> None:
        for channel in channels:
            channel_config = dict(channel["config"] or {})
            await notification_manager.send_notification(
                channel["type"],
                channel_config,
                title,
                content,
                card_data,
            )

    asyncio.run(_send_all())


def _device_payload(device: Device) -> Dict[str, Any]:
    return {
        "id": device.id,
        "name": device.name,
        "ip_address": device.ip_address,
        "datacenter_name": device.datacenter_ref.name if device.datacenter_ref else "未设置机房",
        "vendor": device.vendor,
        "model": device.model,
        "ssh_port": device.ssh_port,
        "ssh_username": device.ssh_username,
        "ssh_password": device.ssh_password,
        "ssh_key": device.ssh_key,
    }


def _backup_device_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    started_at = _utc_now()
    device = SimpleNamespace(**payload)
    try:
        sync_payload = _collect_config_with_sync(device)
        return {
            **payload,
            "status": "success",
            **sync_payload,
            "error_message": None,
            "started_at": started_at,
            "finished_at": _utc_now(),
        }
    except Exception as exc:
        logger.warning("设备配置备份失败", device_id=payload.get("id"), ip=payload.get("ip_address"), error=str(exc))
        return {
            **payload,
            "status": "failed",
            "command": None,
            "config_content": None,
            "config_hash": None,
            "line_count": 0,
            "startup_command": None,
            "startup_config_hash": None,
            "startup_line_count": 0,
            "config_sync_status": "check_failed",
            "config_sync_diff": None,
            "config_save_command": None,
            "config_save_status": None,
            "config_save_message": None,
            "error_message": str(exc)[:2000],
            "started_at": started_at,
            "finished_at": _utc_now(),
        }


@celery_app.task(bind=True, name="app.tasks.config_backup_tasks.run_config_backup", time_limit=7200, soft_time_limit=6900)
def run_config_backup(self, job_id: Optional[int] = None, trigger_type: str = "manual", actor: Optional[str] = None) -> Dict[str, Any]:
    db = SessionLocal()
    datacenter_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"success": 0, "failed": 0, "changed": 0, "saved": 0, "save_failed": 0})
    try:
        if job_id:
            job = db.query(ConfigBackupJob).filter(ConfigBackupJob.id == int(job_id)).first()
        else:
            job = None
        if not job:
            job = ConfigBackupJob(status="running", trigger_type=trigger_type, started_by=actor, started_at=_utc_now())
            db.add(job)
            db.commit()
            db.refresh(job)
        else:
            if job.status == "cancelled":
                return {
                    "job_id": job.id,
                    "status": job.status,
                    "total_devices": job.total_devices,
                    "success_count": job.success_count,
                    "failed_count": job.failed_count,
                }
            job.status = "running"
            job.started_at = _utc_now()
            job.error_message = None
            db.commit()

        devices = (
            db.query(Device)
            .filter(Device.status.in_(["active", "online"]))
            .order_by(Device.ip_address.asc())
            .all()
        )
        job.total_devices = len(devices)
        job.success_count = 0
        job.failed_count = 0
        job.config_changed_count = 0
        job.config_saved_count = 0
        job.config_save_failed_count = 0
        db.commit()

        payloads = [_device_payload(device) for device in devices]
        result_ids: Dict[int, int] = {}
        for payload in payloads:
            result = ConfigBackupResult(
                job_id=job.id,
                device_id=payload["id"],
                device_name=payload["name"],
                device_ip=payload["ip_address"],
                datacenter_name=payload["datacenter_name"],
                vendor=payload.get("vendor"),
                model=payload.get("model"),
                status="pending",
                started_at=_utc_now(),
            )
            db.add(result)
            db.flush()
            result_ids[payload["id"]] = result.id
        db.commit()

        max_workers = min(CONFIG_BACKUP_CONCURRENCY, max(1, len(payloads)))
        executor = ThreadPoolExecutor(max_workers=max_workers)
        try:
            future_map = {executor.submit(_backup_device_payload, payload): payload for payload in payloads}
            pending_futures = set(future_map.keys())
            while pending_futures:
                db.refresh(job)
                if job.status == "cancelled":
                    for future in pending_futures:
                        future.cancel()
                    for future in pending_futures:
                        payload = future_map[future]
                        result_id = result_ids.get(payload["id"])
                        if not result_id:
                            continue
                        result = db.query(ConfigBackupResult).filter(ConfigBackupResult.id == result_id).first()
                        if result and result.status in {"pending", "running"}:
                            result.status = "failed"
                            result.error_message = "任务已手动取消"
                            result.finished_at = _utc_now()
                    job.error_message = "任务已手动取消"
                    job.finished_at = _utc_now()
                    db.commit()
                    executor.shutdown(wait=False, cancel_futures=True)
                    return {
                        "job_id": job.id,
                        "status": job.status,
                        "total_devices": job.total_devices,
                        "success_count": job.success_count,
                        "failed_count": job.failed_count,
                    }

                done, pending_futures = wait(
                    pending_futures,
                    timeout=CONFIG_BACKUP_WAIT_TIMEOUT_SECONDS,
                    return_when=FIRST_COMPLETED,
                )
                if not done:
                    continue

                for future in done:
                    if future.cancelled():
                        continue
                    try:
                        backup_result = future.result()
                    except Exception as exc:
                        payload = future_map.get(future, {})
                        backup_result = {
                            **payload,
                            "status": "failed",
                            "command": None,
                            "config_content": None,
                            "config_hash": None,
                            "line_count": 0,
                            "startup_command": None,
                            "startup_config_content": None,
                            "startup_config_hash": None,
                            "startup_line_count": 0,
                            "config_sync_status": "check_failed",
                            "config_sync_diff": None,
                            "config_save_command": None,
                            "config_save_status": None,
                            "config_save_message": None,
                            "error_message": str(exc)[:2000],
                            "started_at": _utc_now(),
                            "finished_at": _utc_now(),
                        }
                    result_id = result_ids.get(backup_result["id"])
                    if not result_id:
                        continue

                    result = db.query(ConfigBackupResult).filter(ConfigBackupResult.id == result_id).first()
                    if not result:
                        continue

                    result.status = backup_result["status"]
                    result.command = backup_result.get("command")
                    result.config_content = backup_result.get("config_content")
                    result.config_hash = backup_result.get("config_hash")
                    result.line_count = backup_result.get("line_count") or 0
                    result.startup_command = backup_result.get("startup_command")
                    result.startup_config_content = backup_result.get("startup_config_content")
                    result.startup_config_hash = backup_result.get("startup_config_hash")
                    result.startup_line_count = backup_result.get("startup_line_count") or 0
                    result.config_sync_status = backup_result.get("config_sync_status")
                    result.config_sync_diff = backup_result.get("config_sync_diff")
                    result.config_save_command = backup_result.get("config_save_command")
                    result.config_save_status = backup_result.get("config_save_status")
                    result.config_save_message = backup_result.get("config_save_message")
                    result.error_message = backup_result.get("error_message")
                    result.started_at = backup_result.get("started_at")
                    result.finished_at = backup_result.get("finished_at")

                    datacenter = backup_result.get("datacenter_name") or "未设置机房"
                    if result.status == "success":
                        job.success_count += 1
                        datacenter_stats[datacenter]["success"] += 1
                        sync_status = result.config_sync_status or ""
                        save_status = result.config_save_status or ""
                        if sync_status in {"changed_saved", "changed_save_failed"}:
                            job.config_changed_count = (job.config_changed_count or 0) + 1
                            datacenter_stats[datacenter]["changed"] += 1
                        if save_status == "success":
                            job.config_saved_count = (job.config_saved_count or 0) + 1
                            datacenter_stats[datacenter]["saved"] += 1
                        elif save_status == "failed":
                            job.config_save_failed_count = (job.config_save_failed_count or 0) + 1
                            datacenter_stats[datacenter]["save_failed"] += 1
                    else:
                        job.failed_count += 1
                        datacenter_stats[datacenter]["failed"] += 1
                    db.commit()

                    if (job.success_count + job.failed_count) % 10 == 0:
                        logger.info(
                            "配置备份进度",
                            job_id=job.id,
                            success=job.success_count,
                            failed=job.failed_count,
                            total=job.total_devices,
                        )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        save_failed_count = getattr(job, "config_save_failed_count", 0) or 0
        job.status = "success" if job.failed_count == 0 and save_failed_count == 0 else ("partial_failed" if job.success_count else "failed")
        job.finished_at = _utc_now()
        content, _rows = _build_notification_content(job, datacenter_stats)
        job.summary = content
        db.commit()
        _send_backup_notification(job, datacenter_stats)
        return {
            "job_id": job.id,
            "status": job.status,
            "total_devices": job.total_devices,
            "success_count": job.success_count,
            "failed_count": job.failed_count,
        }
    except Exception as exc:
        db.rollback()
        if job_id:
            job = db.query(ConfigBackupJob).filter(ConfigBackupJob.id == int(job_id)).first()
            if job:
                job.status = "failed"
                job.error_message = str(exc)
                job.finished_at = _utc_now()
                db.commit()
        logger.error("配置备份任务失败", job_id=job_id, error=str(exc))
        raise
    finally:
        db.close()


@celery_app.task(
    name="app.tasks.config_backup_tasks.run_scheduled_config_backup",
    time_limit=7200,
    soft_time_limit=6900,
)
def run_scheduled_config_backup() -> Dict[str, Any]:
    return run_config_backup(trigger_type="scheduled", actor="system")
