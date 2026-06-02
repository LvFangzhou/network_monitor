"""
gNMI Telemetry 采集器 - 独立异步协程架构
支持毫秒级数据订阅，不经过Celery任务队列
"""
import asyncio
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime
from dataclasses import dataclass
import json

try:
    from pygnmi.client import gNMIclient
    GNMI_AVAILABLE = True
except ImportError:
    GNMI_AVAILABLE = False

from app.config import settings
from app.utils import influx_client
from app.core import LoggerMixin


@dataclass
class GNMIConfig:
    """gNMI配置数据类"""
    target: str
    port: int = 57400
    username: Optional[str] = None
    password: Optional[str] = None
    tls_enabled: bool = False
    tls_cert: Optional[str] = None
    skip_verify: bool = True
    subscriptions: List[Dict[str, Any]] = None
    
    def __post_init__(self):
        if self.subscriptions is None:
            self.subscriptions = []


class GNMICollector(LoggerMixin):
    """
    gNMI采集器 - 独立异步协程架构
    
    特性:
    - 独立gRPC长连接，不经过Celery任务队列
    - 内存队列缓冲，批量写入InfluxDB
    - 自动重连与故障恢复
    - 支持STREAM/ONCE/POLL订阅模式
    """
    
    # 默认订阅路径 (OpenConfig YANG模型)
    DEFAULT_SUBSCRIPTIONS = [
        {
            "path": "/interfaces/interface/state/counters",
            "mode": "sample",
            "interval": 10000000000  # 10秒，单位纳秒
        },
        {
            "path": "/system/cpu/state",
            "mode": "sample",
            "interval": 10000000000
        },
        {
            "path": "/system/memory/state",
            "mode": "sample",
            "interval": 10000000000
        }
    ]
    
    def __init__(self, device_id: int, config: GNMIConfig):
        self.device_id = device_id
        self.config = config
        self.client: Optional[Any] = None
        self.connected = False
        self.running = False
        self._task: Optional[asyncio.Task] = None
        
        # 内存队列缓冲
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=10000)
        self._batch_size = settings.GNMI_BATCH_SIZE
        self._batch_timeout = settings.GNMI_BATCH_TIMEOUT
        
        # 重连配置
        self._reconnect_delay = settings.GNMI_RECONNECT_DELAY
        self._max_reconnect_delay = settings.GNMI_MAX_RECONNECT_DELAY
        self._current_reconnect_delay = self._reconnect_delay
        
        # 统计
        self._stats = {
            "messages_received": 0,
            "points_written": 0,
            "errors": 0,
            "reconnects": 0
        }
    
    async def start(self):
        """启动采集器"""
        if self.running:
            return
        
        self.running = True
        self._task = asyncio.create_task(self._run())
        self.logger.info(
            "gNMI采集器已启动",
            device_id=self.device_id,
            target=self.config.target
        )
    
    async def stop(self):
        """停止采集器"""
        self.running = False
        
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        
        if self.client:
            try:
                self.client.close()
            except:
                pass
        
        self.connected = False
        self.logger.info("gNMI采集器已停止", device_id=self.device_id)
    
    async def _run(self):
        """主运行循环"""
        # 启动批量写入任务
        batch_task = asyncio.create_task(self._batch_writer())
        
        try:
            while self.running:
                try:
                    await self._connect_and_subscribe()
                except Exception as e:
                    self.logger.error(
                        "gNMI连接异常",
                        device_id=self.device_id,
                        error=str(e)
                    )
                    self._stats["errors"] += 1
                    self.connected = False
                    
                    # 指数退避重连
                    await asyncio.sleep(self._current_reconnect_delay)
                    self._current_reconnect_delay = min(
                        self._current_reconnect_delay * 2,
                        self._max_reconnect_delay
                    )
        finally:
            batch_task.cancel()
            try:
                await batch_task
            except asyncio.CancelledError:
                pass
    
    async def _connect_and_subscribe(self):
        """连接并订阅gNMI数据"""
        if not GNMI_AVAILABLE:
            self.logger.error("pygnmi未安装，无法使用gNMI功能")
            await asyncio.sleep(60)
            return
        
        target = f"{self.config.target}:{self.config.port}"
        
        # 构建连接配置
        conn_config = {
            "target": target,
            "username": self.config.username,
            "password": self.config.password,
            "insecure": not self.config.tls_enabled,
            "skip_verify": self.config.skip_verify
        }
        
        if self.config.tls_cert:
            conn_config["path_cert"] = self.config.tls_cert
        
        try:
            with gNMIclient(**conn_config) as client:
                self.client = client
                self.connected = True
                self._current_reconnect_delay = self._reconnect_delay
                
                self.logger.info(
                    "gNMI连接成功",
                    device_id=self.device_id,
                    target=target
                )
                
                # 构建订阅列表
                subscribe_list = []
                for sub in self.config.subscriptions or self.DEFAULT_SUBSCRIPTIONS:
                    subscribe_list.append({
                        "path": sub["path"],
                        "mode": sub.get("mode", "sample"),
                        "interval": sub.get("interval", 10000000000)
                    })
                
                # 开始订阅 (STREAM模式)
                for response in client.subscribe(
                    subscribe=subscribe_list,
                    target="",
                    extension=None
                ):
                    if not self.running:
                        break
                    
                    self._stats["messages_received"] += 1
                    
                    # 解析响应并入队
                    await self._parse_and_queue(response)
                    
        except Exception as e:
            self.logger.error(
                "gNMI订阅异常",
                device_id=self.device_id,
                error=str(e)
            )
            raise
    
    async def _parse_and_queue(self, response: Dict[str, Any]):
        """解析gNMI响应并入队"""
        try:
            timestamp = datetime.now()
            
            # 解析Update消息
            if "update" in response:
                updates = response["update"]
                for update in updates:
                    path = update.get("path", "")
                    values = update.get("values", {})
                    
                    # 转换为InfluxDB数据点
                    for key, value in values.items():
                        point = self._convert_to_point(path, key, value, timestamp)
                        if point:
                            try:
                                await asyncio.wait_for(
                                    self._queue.put(point),
                                    timeout=1.0
                                )
                            except asyncio.TimeoutError:
                                self.logger.warning(
                                    "gNMI队列已满，丢弃数据",
                                    device_id=self.device_id
                                )
            
            # 解析Delete消息
            if "delete" in response:
                pass  # 处理删除事件
                
        except Exception as e:
            self.logger.error(
                "解析gNMI响应失败",
                device_id=self.device_id,
                error=str(e)
            )
    
    def _convert_to_point(self, path: str, key: str, value: Any, timestamp: datetime) -> Optional[Dict[str, Any]]:
        """将gNMI数据转换为InfluxDB数据点"""
        try:
            # 确定metric_type
            metric_type = "gnmi_unknown"
            if "interface" in path.lower():
                metric_type = "gnmi_interface"
            elif "cpu" in path.lower():
                metric_type = "gnmi_cpu"
            elif "memory" in path.lower():
                metric_type = "gnmi_memory"
            
            # 提取字段名
            field_name = key.split("/")[-1] if "/" in key else key
            
            # 转换值为数字
            if isinstance(value, (int, float)):
                field_value = float(value)
            elif isinstance(value, str):
                try:
                    field_value = float(value)
                except ValueError:
                    return None  # 跳过非数值数据
            else:
                return None
            
            # 提取接口名（如果是接口数据）
            tags = {
                "device_id": str(self.device_id),
                "gnmi_path": path
            }
            
            if "interface" in path:
                # 尝试从路径提取接口名
                parts = path.split("/")
                for i, part in enumerate(parts):
                    if part == "interface" and i + 1 < len(parts):
                        tags["interface"] = parts[i + 1]
                        break
            
            return {
                "measurement": "gnmi_telemetry",
                "tags": tags,
                "fields": {field_name: field_value},
                "timestamp": timestamp
            }
            
        except Exception as e:
            self.logger.error(
                "转换gNMI数据点失败",
                device_id=self.device_id,
                error=str(e)
            )
            return None
    
    async def _batch_writer(self):
        """批量写入任务"""
        batch = []
        last_flush = asyncio.get_event_loop().time()
        
        while self.running:
            try:
                # 等待数据或超时
                timeout = self._batch_timeout - (asyncio.get_event_loop().time() - last_flush)
                if timeout <= 0:
                    timeout = 0.001
                
                try:
                    point = await asyncio.wait_for(
                        self._queue.get(),
                        timeout=timeout
                    )
                    batch.append(point)
                except asyncio.TimeoutError:
                    pass
                
                # 检查是否需要刷新
                now = asyncio.get_event_loop().time()
                should_flush = (
                    len(batch) >= self._batch_size or
                    (batch and now - last_flush >= self._batch_timeout)
                )
                
                if should_flush and batch:
                    success = influx_client.write_points(batch)
                    if success:
                        self._stats["points_written"] += len(batch)
                        self.logger.debug(
                            "gNMI批量写入成功",
                            device_id=self.device_id,
                            count=len(batch)
                        )
                    batch = []
                    last_flush = now
                    
            except Exception as e:
                self.logger.error(
                    "批量写入异常",
                    device_id=self.device_id,
                    error=str(e)
                )
                batch = []  # 清空批次避免重复错误
    
    def get_stats(self) -> Dict[str, Any]:
        """获取采集器统计"""
        return {
            "device_id": self.device_id,
            "connected": self.connected,
            "running": self.running,
            "queue_size": self._queue.qsize(),
            **self._stats
        }


# 模拟gNMI客户端（用于测试）
class MockGNMIClient:
    """模拟gNMI客户端"""
    
    def __init__(self, target: str, **kwargs):
        self.target = target
    
    def __enter__(self):
        return self
    
    def __exit__(self, *args):
        pass
    
    def subscribe(self, subscribe: List[Dict], **kwargs):
        """模拟订阅 - 生成测试数据"""
        import random
        import time
        
        while True:
            time.sleep(1)
            yield {
                "update": [{
                    "path": "/interfaces/interface[state/name=eth0]/state/counters",
                    "values": {
                        "in-octets": random.randint(1000000, 10000000),
                        "out-octets": random.randint(1000000, 10000000),
                        "in-pkts": random.randint(1000, 10000),
                        "out-pkts": random.randint(1000, 10000)
                    }
                }]
            }


if not GNMI_AVAILABLE:
    # 使用模拟客户端
    gNMIclient = MockGNMIClient
