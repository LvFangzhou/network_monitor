#!/usr/bin/env python3
"""
控制器北向 API 连通性检查脚本。

用途：
1. 检查控制器 API 地址是否可达；
2. 可选：使用 /token/generate 测试账号密码能否获取 Token；
3. 可选：携带 Token 探测几个常用控制器接口。

示例：
  CONTROLLER_USERNAME=admin CONTROLLER_PASSWORD='******' \\
    python3 scripts/check_controller_connectivity.py --base-url http://10.239.16.1:30000

也可以只做端口/HTTP 探测：
  python3 scripts/check_controller_connectivity.py --base-url http://10.239.16.1:30000
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import ssl
import sys
import time
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPSConnection, HTTPResponse
from typing import Any
from urllib.parse import urlparse


DEFAULT_BASE_URL = "http://10.239.16.1:30000"


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    elapsed_ms: int | None = None


def print_result(result: CheckResult) -> None:
    marker = "OK" if result.ok else "FAIL"
    elapsed = f" ({result.elapsed_ms}ms)" if result.elapsed_ms is not None else ""
    print(f"[{marker}] {result.name}{elapsed}: {result.detail}")


def normalize_base_url(base_url: str) -> str:
    base_url = base_url.strip().rstrip("/")
    if not base_url.startswith(("http://", "https://")):
        base_url = f"http://{base_url}"
    return base_url


def parse_host_port(base_url: str) -> tuple[str, int, str]:
    parsed = urlparse(base_url)
    scheme = parsed.scheme or "http"
    if not parsed.hostname:
        raise ValueError(f"无法解析控制器地址：{base_url}")
    if parsed.port:
        port = parsed.port
    else:
        port = 443 if scheme == "https" else 80
    return parsed.hostname, port, scheme


def tcp_check(host: str, port: int, timeout: float) -> CheckResult:
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            elapsed = int((time.monotonic() - start) * 1000)
            return CheckResult("TCP 连通性", True, f"{host}:{port} 可连接", elapsed)
    except Exception as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        return CheckResult("TCP 连通性", False, f"{host}:{port} 不可连接：{exc}", elapsed)


def make_connection(base_url: str, timeout: float, insecure: bool) -> HTTPConnection | HTTPSConnection:
    parsed = urlparse(base_url)
    host = parsed.hostname
    if not host:
        raise ValueError(f"无法解析控制器地址：{base_url}")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if parsed.scheme == "https":
        context = ssl._create_unverified_context() if insecure else ssl.create_default_context()
        return HTTPSConnection(host, port, timeout=timeout, context=context)
    return HTTPConnection(host, port, timeout=timeout)


def request_json(
    base_url: str,
    method: str,
    path: str,
    timeout: float,
    insecure: bool,
    headers: dict[str, str] | None = None,
    payload: dict[str, Any] | None = None,
) -> tuple[int, dict[str, str], str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req_headers = {"Accept": "application/json"}
    if body is not None:
        req_headers["Content-Type"] = "application/json"
    if headers:
        req_headers.update(headers)

    conn = make_connection(base_url, timeout=timeout, insecure=insecure)
    try:
        conn.request(method.upper(), path, body=body, headers=req_headers)
        resp: HTTPResponse = conn.getresponse()
        raw = resp.read(4096)
        text = raw.decode("utf-8", errors="replace")
        return resp.status, {k: v for k, v in resp.getheaders()}, text
    finally:
        conn.close()


def parse_json_object(text: str) -> dict[str, Any] | None:
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def business_success(status: int, text: str) -> tuple[bool, str]:
    if not (200 <= status < 300):
        return False, f"HTTP {status}"
    data = parse_json_object(text)
    if data is None:
        return True, f"HTTP {status}"

    if data.get("success") is False or data.get("status") is False:
        return False, f"HTTP {status}，业务状态失败"

    code = data.get("code")
    if code is not None and code not in (0, 200, "0", "200"):
        return False, f"HTTP {status}，业务 code={code}"

    error_code = data.get("errorCode")
    if error_code not in (None, 0, "0"):
        return False, f"HTTP {status}，业务 errorCode={error_code}"

    return True, f"HTTP {status}"


def http_probe(base_url: str, timeout: float, insecure: bool) -> CheckResult:
    start = time.monotonic()
    try:
        status, headers, text = request_json(base_url, "GET", "/", timeout, insecure)
        elapsed = int((time.monotonic() - start) * 1000)
        server = headers.get("Server") or headers.get("server") or "-"
        preview = text.replace("\n", " ")[:120]
        return CheckResult("HTTP 探测", True, f"状态码 {status}，Server={server}，响应预览={preview!r}", elapsed)
    except Exception as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        return CheckResult("HTTP 探测", False, str(exc), elapsed)


def find_token(data: Any) -> str | None:
    if isinstance(data, dict):
        for key in ("token", "access_token", "X-Auth-Token", "xAuthToken", "subjectToken"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
        for value in data.values():
            found = find_token(value)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = find_token(item)
            if found:
                return found
    return None


def token_check(
    base_url: str,
    timeout: float,
    insecure: bool,
    username: str,
    password: str,
    user_id: str,
    region_id: str,
    effective_time: int,
) -> tuple[CheckResult, str | None]:
    payload = {
        "userName": username,
        "passWord": password,
        "id": user_id,
        "regionId": region_id,
        "effectiveTime": effective_time,
        "effectiveUrl": "",
    }
    start = time.monotonic()
    try:
        status, _headers, text = request_json(base_url, "POST", "/token/generate", timeout, insecure, payload=payload)
        elapsed = int((time.monotonic() - start) * 1000)
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = {}
        token = find_token(data)
        if 200 <= status < 300 and token:
            return CheckResult("Token 获取", True, f"状态码 {status}，已获取 Token，长度 {len(token)}", elapsed), token
        preview = text.replace("\n", " ")[:240]
        return CheckResult("Token 获取", False, f"状态码 {status}，未解析到 Token，响应预览={preview!r}", elapsed), None
    except Exception as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        return CheckResult("Token 获取", False, str(exc), elapsed), None


def api_probe(base_url: str, timeout: float, insecure: bool, token: str, path: str, name: str) -> CheckResult:
    start = time.monotonic()
    headers = {
        "X-Auth-Token": token,
        "Cookie": f"X-Subject-Token={token}",
    }
    try:
        status, _headers, text = request_json(base_url, "GET", path, timeout, insecure, headers=headers)
        elapsed = int((time.monotonic() - start) * 1000)
        preview = text.replace("\n", " ")[:220]
        ok, status_detail = business_success(status, text)
        return CheckResult(name, ok, f"{status_detail}，响应预览={preview!r}", elapsed)
    except Exception as exc:
        elapsed = int((time.monotonic() - start) * 1000)
        return CheckResult(name, False, str(exc), elapsed)


def get_first_asset_id(base_url: str, timeout: float, insecure: bool, token: str) -> tuple[str | None, str]:
    headers = {
        "X-Auth-Token": token,
        "Cookie": f"X-Subject-Token={token}",
    }
    status, _headers, text = request_json(
        base_url,
        "GET",
        "/DataCore/DataStream/asset/assetManager/getAssetListByPage?pageNum=1&pageSize=1",
        timeout,
        insecure,
        headers=headers,
    )
    data = parse_json_object(text) or {}
    asset_list = ((data.get("data") or {}).get("assetList") or []) if isinstance(data.get("data"), dict) else []
    if 200 <= status < 300 and asset_list and isinstance(asset_list[0], dict):
        asset = asset_list[0]
        return asset.get("id"), f"{asset.get('name') or '-'} / {asset.get('ip') or '-'}"
    return None, text.replace("\n", " ")[:180]


def main() -> int:
    parser = argparse.ArgumentParser(description="检查控制器北向 API 连通性")
    parser.add_argument("--base-url", default=os.getenv("CONTROLLER_BASE_URL", DEFAULT_BASE_URL), help="控制器北向 API 地址，默认 http://10.239.16.1:30000")
    parser.add_argument("--username", default=os.getenv("CONTROLLER_USERNAME", ""), help="控制器 API 用户名，也可用 CONTROLLER_USERNAME")
    parser.add_argument("--password", default=os.getenv("CONTROLLER_PASSWORD", ""), help="控制器 API 密码，也可用 CONTROLLER_PASSWORD")
    parser.add_argument("--user-id", default=os.getenv("CONTROLLER_USER_ID", "1"), help="token/generate 请求中的 id，默认 1")
    parser.add_argument("--region-id", default=os.getenv("CONTROLLER_REGION_ID", ""), help="token/generate 请求中的 regionId，默认空")
    parser.add_argument("--effective-time", type=int, default=int(os.getenv("CONTROLLER_EFFECTIVE_TIME", "7200")), help="Token 有效期，默认 7200")
    parser.add_argument("--timeout", type=float, default=float(os.getenv("CONTROLLER_TIMEOUT", "5")), help="单次请求超时秒数，默认 5")
    parser.add_argument("--area-type", type=int, default=int(os.getenv("CONTROLLER_AREA_TYPE", "1")), help="区域类型，控制器接口常用 0/1/2，默认 1")
    parser.add_argument("--hours", type=int, default=int(os.getenv("CONTROLLER_QUERY_HOURS", "3")), help="业务接口探测查询最近多少小时，默认 3")
    parser.add_argument("--insecure", action="store_true", default=os.getenv("CONTROLLER_INSECURE", "").lower() in {"1", "true", "yes"}, help="HTTPS 时忽略证书校验")
    parser.add_argument("--skip-api-probes", action="store_true", help="获取 Token 后不探测业务接口")
    args = parser.parse_args()

    base_url = normalize_base_url(args.base_url)
    try:
        host, port, scheme = parse_host_port(base_url)
    except ValueError as exc:
        print(f"[FAIL] 参数错误：{exc}", file=sys.stderr)
        return 2

    print(f"控制器地址：{base_url}")
    print(f"目标：{host}:{port} ({scheme})")
    print()

    results: list[CheckResult] = []
    tcp_result = tcp_check(host, port, args.timeout)
    results.append(tcp_result)
    print_result(tcp_result)

    http_result = http_probe(base_url, args.timeout, args.insecure)
    results.append(http_result)
    print_result(http_result)

    token: str | None = None
    if args.username and args.password:
        token_result, token = token_check(
            base_url,
            args.timeout,
            args.insecure,
            args.username,
            args.password,
            args.user_id,
            args.region_id,
            args.effective_time,
        )
        results.append(token_result)
        print_result(token_result)
    else:
        print("[SKIP] Token 获取: 未提供 CONTROLLER_USERNAME / CONTROLLER_PASSWORD")

    if token and not args.skip_api_probes:
        end_time = int(time.time() * 1000)
        start_time = end_time - args.hours * 3600 * 1000
        probes = [
            ("/DataCore/DataStream/asset/assetManager/getAssetListByPage?pageNum=1&pageSize=1", "资产接口探测"),
            (
                "/DataCore/healthAnalysis/v1/optical/page"
                f"?currentPage=1&pageSize=1&beginTime={start_time}&endTime={end_time}"
                "&level=0&history=false&interval=1800000",
                "光模块接口探测",
            ),
            (
                "/DataCore/healthAnalysis/telemetry/getInterfaceOverrunDevice"
                f"?startTime={start_time}&endTime={end_time}&tag=3h&areaType={args.area_type}",
                "无损/拥塞设备接口探测",
            ),
        ]
        for path, name in probes:
            result = api_probe(base_url, args.timeout, args.insecure, token, path, name)
            results.append(result)
            print_result(result)

        asset_id, asset_preview = get_first_asset_id(base_url, args.timeout, args.insecure, token)
        if asset_id:
            path = (
                "/DataCore/healthAnalysis/buffer/getBuffMonitorDetail"
                f"?startTime={start_time}&endTime={end_time}&pageNum=1&pageSize=1"
                f"&assetId={asset_id}&sortColumn=outDroppedPkts&orderType=desc"
            )
            result = api_probe(base_url, args.timeout, args.insecure, token, path, f"单设备 Buffer/队列接口探测（{asset_preview}）")
            results.append(result)
            print_result(result)
        else:
            print(f"[SKIP] 单设备 Buffer/队列接口探测: 未从资产接口取到 assetId，预览={asset_preview!r}")

    print()
    failed = [item for item in results if not item.ok]
    if failed:
        print(f"检查完成：{len(results) - len(failed)}/{len(results)} 项通过。")
        return 1
    print(f"检查完成：{len(results)}/{len(results)} 项通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
