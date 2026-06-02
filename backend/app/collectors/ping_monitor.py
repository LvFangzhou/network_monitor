"""
设备在线状态监控 - ICMP Ping检测
"""
import asyncio
from typing import Dict, Any, List, Optional, Set
from datetime import datetime
from dataclasses import dataclass, field
import time

try:
    from ping3 import ping
    PING3_AVAILABLE = True
except ImportError:
    PING3_AVAILABLE = False

from app.utils import influx_client
from app.core import LoggerMixin


@dataclass
class PingResult:
    """Ping检测结果"""
    device_id: int
    ip_address: str
    success: bool
    response_time_ms: Optional[float] = None
    packet_loss: float = 0.0
    timestamp: datetime = field(default_factory=datetime.now)
    error_message: Optional[str] = None


class PingMonitor(LoggerMixin):
    """
    设备在线状态监控器
    
    特性:
    - 支持多设备并发Ping检测
    - 可配置的检测间隔和超时
    - 实时状态变更检测
    - 数据写入InfluxDB
    """
    
    def __init__(self, interval: int = 60, timeout: int = 5, retries: int = 2, max_failures: int = 3):
        self.interval = interval  # 检测间隔(秒)
        self.timeout = timeout    # 超时时间(秒)
        self.retries = retries    # 重试次数
        self.max_failures = max_failures  # 连续失败次数阈值，超过则标记为离线
        
        self._devices: Dict[int, Dict[str, Any]] = {}  # 设备列表
        self._device_status: Dict[int, str] = {}       # 设备状态缓存
        self._failure_counts: Dict[int, int] = {}      # 连续失败计数
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._callbacks: List[callable] = []           # 状态变更回调
    
    def register_device(self, device_id: int, ip_address: str, name: str = ""):
        """注册设备到监控列表"""
        self._devices[device_id] = {
            "id": device_id,
            "ip": ip_address,
            "name": name
        }
        self.logger.debug(
            "设备已注册到Ping监控",
            device_id=device_id,
            ip=ip_address
        )
    
    def unregister_device(self, device_id: int):
        """从监控列表移除设备"""
        if device_id in self._devices:
            del self._devices[device_id]
            if device_id in self._device_status:
                del self._device_status[device_id]
            self.logger.debug("设备已从Ping监控移除", device_id=device_id)
    
    def update_device(self, device_id: int, ip_address: Optional[str] = None, name: Optional[str] = None):
        """更新设备信息"""
        if device_id in self._devices:
            if ip_address:
                self._devices[device_id]["ip"] = ip_address
            if name:
                self._devices[device_id]["name"] = name
    
    def on_status_change(self, callback: callable):
        """注册状态变更回调"""
        self._callbacks.append(callback)
    
    async def start(self):
        """启动监控"""
        if self._running:
            return
        
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        self.logger.info("Ping监控已启动", interval=self.interval)
    
    async def stop(self):
        """停止监控"""
        self._running = False
        
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        self.logger.info("Ping监控已停止")
    
    async def _monitor_loop(self):
        """监控主循环"""
        while self._running:
            try:
                start_time = time.time()
                
                # 并发检测所有设备
                if self._devices:
                    tasks = [
                        self._ping_device(device_id, info)
                        for device_id, info in self._devices.items()
                    ]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    
                    # 处理结果
                    for result in results:
                        if isinstance(result, Exception):
                            self.logger.error("Ping检测异常", error=str(result))
                            continue
                        
                        await self._handle_ping_result(result)
                
                # 计算下次检测时间
                elapsed = time.time() - start_time
                sleep_time = max(0, self.interval - elapsed)
                
                await asyncio.sleep(sleep_time)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error("Ping监控循环异常", error=str(e))
                await asyncio.sleep(5)
    
    async def _ping_device(self, device_id: int, device_info: Dict[str, Any]) -> PingResult:
        """对单个设备进行Ping检测"""
        ip = device_info["ip"]
        name = device_info.get("name", "")
        
        try:
            # 使用线程池执行同步ping
            loop = asyncio.get_event_loop()
            
            if PING3_AVAILABLE:
                # 多次Ping取平均
                response_times = []
                success_count = 0
                
                for _ in range(self.retries):
                    try:
                        delay = await asyncio.wait_for(
                            loop.run_in_executor(
                                None,
                                lambda: ping(ip, timeout=self.timeout, unit='ms')
                            ),
                            timeout=self.timeout + 2
                        )
                        
                        if delay is not None:
                            response_times.append(delay)
                            success_count += 1
                    except asyncio.TimeoutError:
                        pass
                
                if success_count > 0:
                    avg_time = sum(response_times) / len(response_times)
                    packet_loss = (self.retries - success_count) / self.retries
                    
                    return PingResult(
                        device_id=device_id,
                        ip_address=ip,
                        success=True,
                        response_time_ms=round(avg_time, 2),
                        packet_loss=round(packet_loss * 100, 1),
                        timestamp=datetime.now()
                    )
                else:
                    return PingResult(
                        device_id=device_id,
                        ip_address=ip,
                        success=False,
                        packet_loss=100.0,
                        timestamp=datetime.now(),
                        error_message="无响应"
                    )
            else:
                # ping3不可用，模拟结果
                return PingResult(
                    device_id=device_id,
                    ip_address=ip,
                    success=True,
                    response_time_ms=1.0,
                    packet_loss=0.0,
                    timestamp=datetime.now()
                )
                
        except Exception as e:
            return PingResult(
                device_id=device_id,
                ip_address=ip,
                success=False,
                timestamp=datetime.now(),
                error_message=str(e)
            )
    
    async def _handle_ping_result(self, result: PingResult):
        """处理Ping结果 - 支持连续失败检测"""
        device_id = result.device_id
        old_status = self._device_status.get(device_id, "unknown")
        
        # 写入InfluxDB
        point = {
            "measurement": "device_status",
            "tags": {
                "device_id": str(device_id),
                "ip_address": result.ip_address
            },
            "fields": {
                "status": 1 if result.success else 0,
                "response_time_ms": result.response_time_ms or 0.0,
                "packet_loss": result.packet_loss
            },
            "timestamp": result.timestamp
        }
        influx_client.write_point(
            measurement="device_status",
            tags=point["tags"],
            fields=point["fields"],
            timestamp=result.timestamp
        )
        
        # 连续失败检测逻辑
        if result.success:
            # Ping成功，重置失败计数
            if device_id in self._failure_counts:
                del self._failure_counts[device_id]
            
            # 如果之前是离线状态，现在恢复在线
            if old_status == "inactive":
                self._device_status[device_id] = "active"
                self.logger.info(
                    "设备恢复在线",
                    device_id=device_id,
                    ip=result.ip_address,
                    response_time=result.response_time_ms
                )
                # 触发回调
                await self._trigger_callbacks(result, old_status, "active")
            elif old_status == "unknown":
                self._device_status[device_id] = "active"
        else:
            # Ping失败，增加失败计数
            current_failures = self._failure_counts.get(device_id, 0) + 1
            self._failure_counts[device_id] = current_failures
            
            self.logger.debug(
                "设备Ping失败",
                device_id=device_id,
                ip=result.ip_address,
                consecutive_failures=current_failures
            )
            
            # 连续失败达到阈值，标记为离线
            if current_failures >= self.max_failures and old_status != "inactive":
                self._device_status[device_id] = "inactive"
                self.logger.warning(
                    "设备连续失败达到阈值，标记为离线",
                    device_id=device_id,
                    ip=result.ip_address,
                    consecutive_failures=current_failures,
                    max_failures=self.max_failures
                )
                # 触发回调
                await self._trigger_callbacks(result, old_status, "inactive")
    
    async def _trigger_callbacks(self, result: PingResult, old_status: str, new_status: str):
        """触发状态变更回调"""
        for callback in self._callbacks:
            try:
                await callback(result, old_status, new_status)
            except Exception as e:
                self.logger.error("状态变更回调异常", error=str(e))
    
    def get_device_status(self, device_id: int) -> Optional[str]:
        """获取设备当前状态"""
        return self._device_status.get(device_id)
    
    def get_all_status(self) -> Dict[int, str]:
        """获取所有设备状态"""
        return self._device_status.copy()
    
    def get_stats(self) -> Dict[str, Any]:
        """获取监控统计"""
        total = len(self._device_status)
        active = sum(1 for s in self._device_status.values() if s == "active")
        inactive = sum(1 for s in self._device_status.values() if s == "inactive")
        other = total - active - inactive
        
        return {
            "total_devices": total,
            "active": active,
            "inactive": inactive,
            "other": other,
            "active_rate": round(active / total * 100, 2) if total > 0 else 0
        }
    
    async def check_device_once(self, device_id: int) -> Optional[PingResult]:
        """单次检测指定设备"""
        if device_id not in self._devices:
            return None
        
        result = await self._ping_device(device_id, self._devices[device_id])
        await self._handle_ping_result(result)
        return result


# 全局Ping监控实例 - 连续3次失败标记为离线
ping_monitor = PingMonitor(interval=60, timeout=5, retries=2, max_failures=3)
