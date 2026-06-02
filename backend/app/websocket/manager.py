"""
WebSocket 连接管理器
支持实时推送设备状态变更和告警
"""
from fastapi import WebSocket
from typing import List, Dict, Set
import json
import asyncio
from datetime import datetime

from app.core import get_logger

logger = get_logger(__name__)


class WebSocketManager:
    """
    WebSocket连接管理器
    
    功能:
    - 管理客户端连接
    - 广播消息到所有客户端
    - 支持按主题订阅
    """
    
    def __init__(self):
        # 所有活跃连接
        self.active_connections: List[WebSocket] = []
        # 按主题订阅的连接
        self.topic_subscribers: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()
    
    async def connect(self, websocket: WebSocket):
        """接受新连接"""
        await websocket.accept()
        async with self._lock:
            self.active_connections.append(websocket)
        logger.info(f"WebSocket连接建立，当前连接数: {len(self.active_connections)}")
    
    async def disconnect(self, websocket: WebSocket):
        """断开连接"""
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
            
            # 从所有主题中移除
            for subscribers in self.topic_subscribers.values():
                subscribers.discard(websocket)
        
        logger.info(f"WebSocket连接断开，当前连接数: {len(self.active_connections)}")
    
    async def subscribe(self, websocket: WebSocket, topic: str):
        """订阅主题"""
        async with self._lock:
            if topic not in self.topic_subscribers:
                self.topic_subscribers[topic] = set()
            self.topic_subscribers[topic].add(websocket)
        
        logger.debug(f"WebSocket订阅主题: {topic}")
    
    async def unsubscribe(self, websocket: WebSocket, topic: str):
        """取消订阅主题"""
        async with self._lock:
            if topic in self.topic_subscribers:
                self.topic_subscribers[topic].discard(websocket)
    
    async def send_personal_message(self, message: dict, websocket: WebSocket):
        """发送个人消息"""
        try:
            await websocket.send_json(message)
        except Exception as e:
            logger.error(f"发送WebSocket消息失败: {e}")
    
    async def broadcast(self, message: dict):
        """广播消息到所有连接"""
        disconnected = []
        
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"广播消息失败: {e}")
                disconnected.append(connection)
        
        # 清理断开的连接
        for conn in disconnected:
            await self.disconnect(conn)
    
    async def broadcast_to_topic(self, topic: str, message: dict):
        """广播消息到指定主题"""
        if topic not in self.topic_subscribers:
            return
        
        disconnected = []
        subscribers = list(self.topic_subscribers[topic])
        
        for connection in subscribers:
            try:
                await connection.send_json(message)
            except Exception as e:
                logger.error(f"发送主题消息失败: {e}")
                disconnected.append(connection)
        
        # 清理断开的连接
        for conn in disconnected:
            await self.disconnect(conn)
    
    # ========== 业务消息推送 ==========
    
    async def notify_device_status_change(self, device_id: int, device_name: str, 
                                          old_status: str, new_status: str):
        """推送设备状态变更"""
        message = {
            "type": "device_status_change",
            "timestamp": datetime.now().isoformat(),
            "data": {
                "device_id": device_id,
                "device_name": device_name,
                "old_status": old_status,
                "new_status": new_status
            }
        }
        await self.broadcast_to_topic("device_status", message)
        await self.broadcast(message)  # 同时广播到所有客户端
    
    async def notify_alert_triggered(self, alert_data: dict):
        """推送告警触发"""
        message = {
            "type": "alert_triggered",
            "timestamp": datetime.now().isoformat(),
            "data": alert_data
        }
        await self.broadcast_to_topic("alerts", message)
        await self.broadcast(message)
    
    async def notify_alert_resolved(self, alert_id: int, device_id: int):
        """推送告警恢复"""
        message = {
            "type": "alert_resolved",
            "timestamp": datetime.now().isoformat(),
            "data": {
                "alert_id": alert_id,
                "device_id": device_id
            }
        }
        await self.broadcast_to_topic("alerts", message)
        await self.broadcast(message)
    
    async def notify_metric_update(self, device_id: int, metric_type: str, values: dict):
        """推送指标更新"""
        message = {
            "type": "metric_update",
            "timestamp": datetime.now().isoformat(),
            "data": {
                "device_id": device_id,
                "metric_type": metric_type,
                "values": values
            }
        }
        await self.broadcast_to_topic(f"metrics:{device_id}", message)
    
    def get_connection_count(self) -> int:
        """获取当前连接数"""
        return len(self.active_connections)


# 全局WebSocket管理器实例
ws_manager = WebSocketManager()
