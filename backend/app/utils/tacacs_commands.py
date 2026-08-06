"""Tacacs accounting 命令提取和展示过滤。"""

import re


_ACCOUNTING_FIELD_PATTERN = re.compile(r"\s+(cmd-arg|err_msg|start_time)=")
_CMD_ARG_PATTERN = re.compile(
    r"\bcmd-arg=(.*?)(?=\s+(?:err_msg|start_time)=|$)",
)
_SYSTEM_PATH_PREFIXES = ("/usr/bin/", "/usr/sbin/", "/bin/", "/sbin/")


def extract_tacacs_command(raw_command: str) -> str:
    """从 accounting 的 cmd/cmd-arg 字段还原用户执行的完整命令。"""
    text = (raw_command or "").strip()
    if not text:
        return ""

    marker_match = _ACCOUNTING_FIELD_PATTERN.search(text)
    command = text[:marker_match.start()].strip() if marker_match else text
    rest = text[marker_match.start():] if marker_match else ""
    arg_match = _CMD_ARG_PATTERN.search(rest)
    cmd_arg = arg_match.group(1).strip() if arg_match else ""
    return f"{command} {cmd_arg}".strip() if cmd_arg else command


def is_tacacs_system_command(command: str) -> bool:
    """识别登录辅助命令和 NETCONF 自动化会话标记。"""
    text = (command or "").strip()
    if not text:
        return True
    tokens = text.split()
    first = tokens[0].lower()
    # 部分设备把 NETCONF 配置下发记为一条纯 ``xml`` accounting 命令。
    # 它应继续保留在 tacacs.log 中用于原始审计，但不进入页面摘要和机器人通知。
    if len(tokens) == 1 and first == "xml":
        return True
    if first == "startup":
        return True
    if first.startswith(_SYSTEM_PATH_PREFIXES):
        return True
    if first == "sudo" and len(tokens) > 1 and tokens[1].lower().startswith(_SYSTEM_PATH_PREFIXES):
        return True
    return False


def is_tacacs_user_command(command: str) -> bool:
    """仅允许用户在网络设备 CLI 中执行的有效命令进入页面和通知。"""
    return not is_tacacs_system_command(command)
