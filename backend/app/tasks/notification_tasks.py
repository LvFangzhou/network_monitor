"""
通知发送任务
"""
from celery import shared_task
from typing import Dict, Any, List
import asyncio

from app.utils import notification_manager
from app.core import get_logger

logger = get_logger(__name__)


@shared_task
def send_notification(
    channel_type: str,
    config: Dict[str, Any],
    title: str,
    content: str
):
    """
    发送通知任务
    
    Args:
        channel_type: 渠道类型 (wechat, dingtalk, email, webhook)
        config: 渠道配置
        title: 通知标题
        content: 通知内容
    """
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            result = loop.run_until_complete(
                notification_manager.send_notification(
                    channel_type, config, title, content
                )
            )
            
            if result:
                logger.info("通知发送成功", 
                          channel=channel_type, 
                          title=title)
            else:
                logger.warning("通知发送失败", 
                             channel=channel_type, 
                             title=title)
            
            return {"success": result}
            
        finally:
            loop.close()
            
    except Exception as e:
        logger.error("通知发送异常", 
                    channel=channel_type, 
                    error=str(e))
        return {"success": False, "error": str(e)}


@shared_task
def send_test_notification(channel_type: str, config: Dict[str, Any]):
    """发送测试通知"""
    title = "网络监控系统测试通知"
    content = f"这是一条测试通知，用于验证{channel_type}渠道配置是否正确。"
    
    return send_notification.apply_async(
        args=[channel_type, config, title, content],
        queue="notification",
        expires=300,
    )


@shared_task
def batch_send_notifications(
    notifications: List[Dict[str, Any]]
):
    """
    批量发送通知
    
    Args:
        notifications: 通知列表，每个元素包含channel_type, config, title, content
    """
    results = []
    
    for notif in notifications:
        result = send_notification.apply_async(
            args=[
                notif.get("channel_type"),
                notif.get("config", {}),
                notif.get("title", ""),
                notif.get("content", ""),
            ],
            queue="notification",
            expires=300,
        )
        results.append(result.id)
    
    return {
        "total": len(notifications),
        "task_ids": results
    }
