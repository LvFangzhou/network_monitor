"""
gNMI连接池管理器
管理多个设备的gNMI连接，支持动态添加/删除设备
"""
import asyncio
from typing import Dict, Optional, List, Any
from dataclasses import dataclass
from datetime import datetime

from app.collectors.gnmi_collector import GNMICollector, GNMIConfig
from app.core import LoggerMixin, get_logger
from app.utils import influx_client

logger = get_logger(__name__)


@dataclass
class DeviceGNMIConfig:
    """设备gNMI配置"""
    device_id: int
    ip_address: str
    port: int = 57400
    username: Optional[str] = None
    password: Optional[str] = None
    tls_enabled: bool = False
    tls_cert: Optional[str] = None
    skip_verify: bool = True
    subscriptions: Optional[List[Dict[str, Any]]] = None


class GNMIManager(LoggerMixin):
    """
    gNMI连接池管理器
    
    特性:
    - 管理多个设备的gNMI连接
    - 支持动态添加/删除设备
    - 自动重连和故障恢复
    - 统一的统计和监控
    """
    
    def __init__(self):
        self._collectors: Dict[int, GNMICollector] = {}
        self._lock = asyncio.Lock()
        self._running = False
        self._monitor_task: Optional[asyncio.Task] = None
    
    async def start(self):
        """启动管理器"""
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        self.logger.info("gNMI管理器已启动")
    
    async def stop(self):
        """停止管理器"""
        self._running = False
        
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        
        # 停止所有采集器
        async with self._lock:
            stop_tasks = []
            for collector in self._collectors.values():
                stop_tasks.append(collector.stop())
            
            if stop_tasks:
                await asyncio.gather(*stop_tasks, return_exceptions=True)
            
            self._collectors.clear()
        
        self.logger.info("gNMI管理器已停止")
    
    async def add_device(self, config: DeviceGNMIConfig) -> bool:
        """
        添加设备到gNMI管理
        
        Args:
            config: 设备gNMI配置
        
        Returns:
            是否成功添加
        """
        async with self._lock:
            # 如果设备已存在，先移除
            if config.device_id in self._collectors:
                await self._remove_device_internal(config.device_id)
            
            # 创建gNMI配置
            gnmi_config = GNMIConfig(
                target=config.ip_address,
                port=config.port,
                username=config.username,
                password=config.password,
                tls_enabled=config.tls_enabled,
                tls_cert=config.tls_cert,
                skip_verify=config.skip_verify,
                subscriptions=config.subscriptions
            )
            
            # 创建采集器
            collector = GNMICollector(config.device_id, gnmi_config)
            self._collectors[config.device_id] = collector
            
            # 启动采集器
            await collector.start()
            
            self.logger.info(
                "设备已添加到gNMI管理",
                device_id=config.device_id,
                ip=config.ip_address
            )
            return True
    
    async def remove_device(self, device_id: int) -> bool:
        """从gNMI管理中移除设备"""
        async with self._lock:
            return await self._remove_device_internal(device_id)
    
    async def _remove_device_internal(self, device_id: int) -> bool:
        """内部移除设备方法"""
        if device_id not in self._collectors:
            return False
        
        collector = self._collectors.pop(device_id)
        await collector.stop()
        
        self.logger.info("设备已从gNMI管理移除", device_id=device_id)
        return True
    
    async def update_device(self, config: DeviceGNMIConfig) -> bool:
        """更新设备gNMI配置"""
        async with self._lock:
            if config.device_id not in self._collectors:
                # 设备不存在，添加新设备
                return await self.add_device(config)
            
            # 停止现有采集器
            await self._remove_device_internal(config.device_id)
            
            # 重新添加
            return await self.add_device(config)
    
    def get_collector(self, device_id: int) -> Optional[GNMICollector]:
        """获取指定设备的采集器"""
        return self._collectors.get(device_id)
    
    def get_all_stats(self) -> Dict[int, Dict[str, Any]]:
        """获取所有采集器的统计"""
        return {
            device_id: collector.get_stats()
            for device_id, collector in self._collectors.items()
        }
    
    def get_connected_count(self) -> int:
        """获取已连接的设备数量"""
        return sum(
            1 for c in self._collectors.values()
            if c.connected
        )

    def _write_telemetry_reachability(self, device_id: int, collector: GNMICollector) -> None:
        """将gNMI连接状态写入时序库，供Telemetry不可达告警使用。"""
        try:
            influx_client.write_point(
                measurement="telemetry_reachability",
                tags={
                    "device_id": str(device_id),
                    "device_ip": collector.config.target,
                },
                fields={
                    "reachable": 1.0 if collector.connected else 0.0,
                    "running": 1.0 if collector.running else 0.0,
                    "errors": float((collector.get_stats() or {}).get("errors") or 0),
                },
                timestamp=datetime.utcnow(),
                sync=False,
            )
        except Exception as exc:
            self.logger.warning("写入Telemetry可达性指标失败", device_id=device_id, error=str(exc))
    
    async def _monitor_loop(self):
        """监控循环 - 定期检查连接状态"""
        while self._running:
            try:
                await asyncio.sleep(30)  # 每30秒检查一次
                
                async with self._lock:
                    for device_id, collector in self._collectors.items():
                        self._write_telemetry_reachability(device_id, collector)

                        if not collector.connected and collector.running:
                            self.logger.warning(
                                "gNMI连接断开",
                                device_id=device_id
                            )
                        
                        # 检查队列积压
                        stats = collector.get_stats()
                        queue_size = stats.get("queue_size", 0)
                        if queue_size > 5000:
                            self.logger.warning(
                                "gNMI队列积压严重",
                                device_id=device_id,
                                queue_size=queue_size
                            )
                            
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error("gNMI监控循环异常", error=str(e))
    
    async def sync_devices(self, devices: List[DeviceGNMIConfig]):
        """
        同步设备列表
        
        Args:
            devices: 当前需要管理的设备列表
        """
        async with self._lock:
            current_ids = set(self._collectors.keys())
            new_ids = set(d.device_id for d in devices)
            
            # 需要移除的设备
            to_remove = current_ids - new_ids
            for device_id in to_remove:
                await self._remove_device_internal(device_id)
            
            # 需要添加或更新的设备
            for config in devices:
                if config.device_id in self._collectors:
                    # 检查配置是否变化
                    # TODO: 实现配置比较逻辑
                    pass
                else:
                    # 添加新设备
                    await self.add_device(config)
        
        self.logger.info(
            "设备同步完成",
            total=len(devices),
            removed=len(to_remove),
            added=len(new_ids - current_ids)
        )


# 全局gNMI管理器实例
gnmi_manager = GNMIManager()
