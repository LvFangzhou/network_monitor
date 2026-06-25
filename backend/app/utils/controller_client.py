"""
H3C 控制器/分析器北向 API 客户端。
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx


@dataclass
class ControllerCheck:
    name: str
    ok: bool
    detail: str
    elapsed_ms: int
    preview: Optional[str] = None


def _normalize_base_url(base_url: str) -> str:
    base_url = (base_url or "").strip().rstrip("/")
    if base_url and not base_url.startswith(("http://", "https://")):
        base_url = f"http://{base_url}"
    return base_url


def _preview(value: Any, limit: int = 260) -> str:
    text = str(value).replace("\n", " ")
    return text[:limit]


def _business_ok(status_code: int, data: Any) -> tuple[bool, str]:
    if not 200 <= status_code < 300:
        return False, f"HTTP {status_code}"
    if isinstance(data, dict):
        if data.get("success") is False or data.get("status") is False:
            return False, f"HTTP {status_code}，业务状态失败"
        code = data.get("code")
        if code is not None and code not in (0, 200, "0", "200"):
            return False, f"HTTP {status_code}，业务 code={code}"
        error_code = data.get("errorCode")
        if error_code not in (None, 0, "0"):
            return False, f"HTTP {status_code}，业务 errorCode={error_code}"
    return True, f"HTTP {status_code}"


def _find_token(data: Any) -> Optional[str]:
    if isinstance(data, dict):
        for key in ("token", "access_token", "X-Auth-Token", "xAuthToken", "subjectToken"):
            value = data.get(key)
            if isinstance(value, str) and value:
                return value
        for value in data.values():
            found = _find_token(value)
            if found:
                return found
    if isinstance(data, list):
        for item in data:
            found = _find_token(item)
            if found:
                return found
    return None


class ControllerClient:
    def __init__(self, settings: Dict[str, Any]):
        self.base_url = _normalize_base_url(settings.get("base_url") or "")
        self.username = settings.get("username") or ""
        self.password = settings.get("password") or ""
        self.user_id = str(settings.get("user_id") or "1")
        self.region_id = str(settings.get("region_id") or "")
        self.effective_time = int(settings.get("effective_time") or 7200)
        self.timeout_seconds = float(settings.get("timeout") or 5)
        self.area_type = int(settings.get("area_type") if settings.get("area_type") is not None else 1)
        self.insecure = bool(settings.get("insecure"))
        self._token: Optional[str] = None

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout_seconds, connect=self.timeout_seconds),
            verify=not self.insecure,
        )

    def _auth_headers(self) -> Dict[str, str]:
        if not self._token:
            raise RuntimeError("尚未获取控制器 Token")
        return {
            "X-Auth-Token": self._token,
            "Cookie": f"X-Subject-Token={self._token}",
        }

    async def fetch_token(self) -> str:
        if not self.base_url:
            raise ValueError("控制器地址不能为空")
        if not self.username or not self.password:
            raise ValueError("控制器用户名/密码不能为空")
        payload = {
            "userName": self.username,
            "passWord": self.password,
            "id": self.user_id,
            "regionId": self.region_id,
            "effectiveTime": self.effective_time,
            "effectiveUrl": "",
        }
        async with self._client() as client:
            response = await client.post("/token/generate", json=payload)
            data = response.json()
        token = _find_token(data)
        if not token:
            raise RuntimeError(f"未从控制器响应中解析到 Token：{_preview(data)}")
        self._token = token
        return token

    async def get(self, path: str, params: Dict[str, Any] | None = None) -> Any:
        if not self._token:
            await self.fetch_token()
        async with self._client() as client:
            response = await client.get(path, params=params, headers=self._auth_headers())
            try:
                data = response.json()
            except Exception:
                data = response.text
        ok, detail = _business_ok(response.status_code, data)
        if not ok:
            raise RuntimeError(f"{detail}: {_preview(data)}")
        return data

    async def check(self) -> Dict[str, Any]:
        checks: list[ControllerCheck] = []
        token: Optional[str] = None

        start = time.monotonic()
        try:
            async with self._client() as client:
                response = await client.get("/")
                preview = _preview(response.text)
            checks.append(ControllerCheck("HTTP 探测", True, f"HTTP {response.status_code}", int((time.monotonic() - start) * 1000), preview))
        except Exception as exc:
            checks.append(ControllerCheck("HTTP 探测", False, str(exc), int((time.monotonic() - start) * 1000)))

        start = time.monotonic()
        try:
            token = await self.fetch_token()
            checks.append(ControllerCheck("Token 获取", True, f"已获取 Token，长度 {len(token)}", int((time.monotonic() - start) * 1000)))
        except Exception as exc:
            checks.append(ControllerCheck("Token 获取", False, str(exc), int((time.monotonic() - start) * 1000)))

        if token:
            for name, path, params in self.probe_definitions():
                start = time.monotonic()
                try:
                    data = await self.get(path, params=params)
                    checks.append(ControllerCheck(name, True, "接口可用", int((time.monotonic() - start) * 1000), _preview(data)))
                except Exception as exc:
                    checks.append(ControllerCheck(name, False, str(exc), int((time.monotonic() - start) * 1000)))

        return {
            "ok": all(item.ok for item in checks),
            "base_url": self.base_url,
            "checks": [item.__dict__ for item in checks],
        }

    def probe_definitions(self) -> list[tuple[str, str, Dict[str, Any]]]:
        end_time = int(time.time() * 1000)
        start_time = end_time - 3 * 3600 * 1000
        return [
            ("资产接口", "/DataCore/DataStream/asset/assetManager/getAssetListByPage", {"pageNum": 1, "pageSize": 1}),
            (
                "光模块接口",
                "/DataCore/healthAnalysis/v1/optical/page",
                {
                    "currentPage": 1,
                    "pageSize": 1,
                    "beginTime": start_time,
                    "endTime": end_time,
                    "level": 0,
                    "history": "false",
                    "interval": 1800000,
                },
            ),
            (
                "无损/拥塞设备接口",
                "/DataCore/healthAnalysis/telemetry/getInterfaceOverrunDevice",
                {"startTime": start_time, "endTime": end_time, "tag": "3h", "areaType": self.area_type},
            ),
        ]

    async def list_assets(self, page_num: int = 1, page_size: int = 20, filter_text: str | None = None) -> Dict[str, Any]:
        params: Dict[str, Any] = {"pageNum": page_num, "pageSize": page_size}
        if filter_text:
            params["filter"] = filter_text
        data = await self.get("/DataCore/DataStream/asset/assetManager/getAssetListByPage", params=params)
        payload = data.get("data") if isinstance(data, dict) else {}
        return {
            "total": int((payload or {}).get("count") or 0),
            "items": (payload or {}).get("assetList") or [],
        }

    async def list_opticals(
        self,
        page: int = 1,
        page_size: int = 20,
        search: str | None = None,
        device_ip: str | None = None,
        interface_name: str | None = None,
        vendor_name: str | None = None,
        level: int = 0,
        hours: int = 3,
    ) -> Dict[str, Any]:
        end_time = int(time.time() * 1000)
        start_time = end_time - max(hours, 1) * 3600 * 1000
        params: Dict[str, Any] = {
            "currentPage": page,
            "pageSize": page_size,
            "beginTime": start_time,
            "endTime": end_time,
            "level": level,
            "history": "false",
            "interval": 1800000,
        }
        if search:
            params["searchName"] = search
            params["searchInterface"] = search
            params["searchIp"] = search
        if device_ip:
            params["deviceIp"] = device_ip
        if interface_name:
            params["interfaceName"] = interface_name
        if vendor_name:
            params["vendorName"] = vendor_name
        data = await self.get("/DataCore/healthAnalysis/v1/optical/page", params=params)
        result = data.get("result") if isinstance(data, dict) else {}
        return {
            "total": int((result or {}).get("totalSize") or 0),
            "items": (result or {}).get("recordList") or [],
            "currentPage": (result or {}).get("currentPage") or page,
            "pageSize": (result or {}).get("pageSize") or page_size,
        }

    async def list_lossless_overrun_devices(self, hours: int = 3, tag: str = "3h") -> Dict[str, Any]:
        end_time = int(time.time() * 1000)
        start_time = end_time - max(hours, 1) * 3600 * 1000
        data = await self.get(
            "/DataCore/healthAnalysis/telemetry/getInterfaceOverrunDevice",
            params={
                "startTime": start_time,
                "endTime": end_time,
                "tag": tag,
                "areaType": self.area_type,
            },
        )
        result = data.get("result") if isinstance(data, dict) else {}
        ip_list = (result or {}).get("ip") or []
        name_list = (result or {}).get("name") or []
        items = [
            {"ip": ip, "name": name_list[index] if index < len(name_list) else ""}
            for index, ip in enumerate(ip_list)
        ]
        return {"total": len(items), "items": items, "raw": result or {}}

    async def list_lossless_buffer_details(
        self,
        asset_id: str,
        page: int = 1,
        page_size: int = 20,
        hours: int = 3,
        if_index: str | None = None,
        sort_column: str = "outDroppedPkts",
        order_type: str = "desc",
    ) -> Dict[str, Any]:
        end_time = int(time.time() * 1000)
        start_time = end_time - max(hours, 1) * 3600 * 1000
        params: Dict[str, Any] = {
            "startTime": start_time,
            "endTime": end_time,
            "pageNum": page,
            "pageSize": page_size,
            "assetId": asset_id,
            "sortColumn": sort_column,
            "orderType": order_type,
        }
        if if_index:
            params["ifIndex"] = if_index
        data = await self.get("/DataCore/healthAnalysis/buffer/getBuffMonitorDetail", params=params)
        raw_items = []
        if isinstance(data, dict):
            data_payload = data.get("data") or {}
            if isinstance(data_payload, dict):
                raw_items = data_payload.get("ifIndexList") or []
            elif isinstance(data_payload, list):
                raw_items = data_payload
        return {
            "total": int((data or {}).get("total") or len(raw_items)) if isinstance(data, dict) else len(raw_items),
            "items": raw_items,
            "page": page,
            "pageSize": page_size,
        }
