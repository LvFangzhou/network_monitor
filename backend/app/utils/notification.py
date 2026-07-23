"""
通知管理器 - 支持企业微信、钉钉、飞书、邮件、Webhook
"""
import httpx
import json
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.config import settings
from app.core import get_logger

logger = get_logger(__name__)


class NotificationManager:
    """通知管理器"""
    
    def __init__(self):
        self.last_error_message: Optional[str] = None

    async def _post_json(self, url: str, payload: Dict[str, Any], headers: Optional[Dict[str, str]] = None) -> httpx.Response:
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await client.post(
                url,
                json=payload,
                headers=headers or {"Content-Type": "application/json"},
            )

    async def _get_request(
        self,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> httpx.Response:
        async with httpx.AsyncClient(timeout=30.0) as client:
            return await client.get(url, params=params, headers=headers or {})

    def _response_preview(self, response: httpx.Response) -> str:
        text = (response.text or "").strip()
        return text[:300] if text else "空响应"

    def _parse_robot_response(self, response: httpx.Response, provider: str) -> Optional[Dict[str, Any]]:
        try:
            return response.json()
        except json.JSONDecodeError:
            preview = self._response_preview(response)
            self.last_error_message = (
                f"{provider}机器人返回非 JSON 响应：HTTP {response.status_code}，响应内容：{preview}。"
                "请检查 webhook 地址是否完整、是否为群机器人 webhook、机器人是否被禁用或地址是否填错。"
            )
            logger.error(
                f"{provider}机器人返回非JSON响应",
                status=response.status_code,
                response=preview,
            )
            return None
    
    async def send_notification(
        self,
        channel_type: str,
        config: Dict[str, Any],
        title: str,
        content: str,
        card_data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """
        发送通知
        
        Args:
            channel_type: 渠道类型 (wechat, dingtalk, email, webhook)
            config: 渠道配置
            title: 通知标题
            content: 通知内容
        """
        try:
            self.last_error_message = None
            if channel_type == "wechat":
                return await self._send_wechat(config, title, content, card_data)
            elif channel_type == "dingtalk":
                return await self._send_dingtalk(config, title, content, card_data)
            elif channel_type == "feishu":
                return await self._send_feishu(config, title, content, card_data)
            elif channel_type == "email":
                return await self._send_email(config, title, content)
            elif channel_type == "webhook":
                return await self._send_webhook(config, title, content, card_data)
            else:
                logger.warning(f"未知的通知渠道类型: {channel_type}")
                return False
        except Exception as e:
            self.last_error_message = str(e)
            logger.error(f"发送通知失败", 
                        channel=channel_type, 
                        error=str(e))
            return False
    
    def _severity_style(self, card_data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if str((card_data or {}).get("notification_type") or "").strip() == "tacacs":
            return {
                "label": "TACACS",
                "hex": "#13C2C2",
                "feishu": "turquoise",
                "wechat_desc_color": 0,
            }
        event_type = str((card_data or {}).get("event_type") or "").strip()
        if event_type == "auto_resolved":
            return {
                "label": "已恢复",
                "hex": "#52C41A",
                "feishu": "green",
                "wechat_desc_color": 2,
            }
        if event_type == "ignored":
            return {
                "label": "已忽略",
                "hex": "#8C8C8C",
                "feishu": "grey",
                "wechat_desc_color": 0,
            }
        severity = str((card_data or {}).get("severity") or "P1").upper()
        mapping = {
            "P0": {
                "label": "P0",
                "hex": "#F53F3F",
                "feishu": "red",
                "wechat_desc_color": 1,
            },
            "P1": {
                "label": "P1",
                "hex": "#FA8C16",
                "feishu": "orange",
                "wechat_desc_color": 2,
            },
            "P2": {
                "label": "P2",
                "hex": "#1677FF",
                "feishu": "blue",
                "wechat_desc_color": 0,
            },
            "P3": {
                "label": "P3",
                "hex": "#8C8C8C",
                "feishu": "grey",
                "wechat_desc_color": 0,
            },
        }
        return mapping.get(severity, mapping["P1"])

    def _build_card_rows_markdown(self, card_data: Optional[Dict[str, Any]]) -> str:
        if card_data and card_data.get("notification_kind") == "config_backup":
            return self._build_config_backup_markdown(card_data)
        rows = (card_data or {}).get("rows") or []
        lines = []
        is_operation = card_data and card_data.get("notification_kind") == "operation"
        for row in rows:
            label = str(row.get("label") or "").strip()
            value = str(row.get("value") or "")
            if not label or not value.strip():
                continue
            if is_operation and label == "变更内容" and len(value) > 300:
                value = value[:300] + "..."
            lines.append(f"**{label}：**{value}")
        if card_data and card_data.get("detail_url"):
            detail_label = "记录详情" if card_data.get("notification_kind") == "operation" else "故障详情"
            lines.append(f"[{detail_label}]({card_data['detail_url']})")
        return "\n".join(lines)

    def _build_compact_rows_markdown(self, card_data: Optional[Dict[str, Any]]) -> str:
        if card_data and card_data.get("notification_kind") == "config_backup":
            return self._build_config_backup_markdown(card_data)
        rows = (card_data or {}).get("rows") or []
        lines = []
        is_operation = card_data and card_data.get("notification_kind") == "operation"
        for row in rows:
            label = str(row.get("label") or "").strip()
            value = str(row.get("value") or "")
            if label and value.strip():
                if is_operation and label == "变更内容" and len(value) > 300:
                    value = value[:300] + "..."
                lines.append(f"**{label}：**{value}")
        return "\n".join(lines)

    def _build_config_backup_markdown(self, card_data: Optional[Dict[str, Any]]) -> str:
        summary = (card_data or {}).get("backup_summary") or {}
        datacenters = (card_data or {}).get("datacenters") or []

        total = int(summary.get("total") or 0)
        success = int(summary.get("success") or 0)
        failed = int(summary.get("failed") or 0)
        changed = int(summary.get("changed") or 0)
        saved = int(summary.get("saved") or 0)
        save_failed = int(summary.get("save_failed") or 0)

        status_line = "✅ 全部成功" if failed == 0 and save_failed == 0 else "⚠️ 存在失败/保存异常"
        lines = [
            f"**{status_line}**",
            "",
            "### 任务概览",
            (
                f"任务 ID：**{summary.get('job_id', '-')}**　"
                f"触发：**{summary.get('trigger_type', '-')}**　"
                f"耗时：**{summary.get('duration', '-')}**　"
                f"总设备：**{total}**"
            ),
            "",
            "### 结果总览",
            f"备份：✅ 成功 **{success}**　❌ 失败 **{failed}**",
            f"配置一致性：⚠️ 不一致 **{changed}**　🔄 已自动保存 **{saved}**　🛑 保存失败 **{save_failed}**",
        ]

        if datacenters:
            lines.extend(["", "### 机房汇总"])
            for item in datacenters:
                dc_failed = int(item.get("failed") or 0)
                dc_changed = int(item.get("changed") or 0)
                dc_save_failed = int(item.get("save_failed") or 0)
                prefix = "✅" if dc_failed == 0 and dc_changed == 0 and dc_save_failed == 0 else "⚠️"
                lines.append(
                    f"{prefix} **{item.get('name') or '未设置机房'}**："
                    f"成功 {int(item.get('success') or 0)} / 失败 {dc_failed} / "
                    f"不一致 {dc_changed} / 已保存 {int(item.get('saved') or 0)} / 保存失败 {dc_save_failed}"
                )
        return "\n".join(lines)

    def _wechat_markdown_title(self, title: str, card_data: Optional[Dict[str, Any]]) -> str:
        if str((card_data or {}).get("notification_type") or "").strip() == "tacacs":
            return f"# <font color=\"#13C2C2\">**{title}**</font>"
        event_type = str((card_data or {}).get("event_type") or "").strip()
        severity = str((card_data or {}).get("severity") or "P1").upper()
        if event_type == "ignored":
            color = "comment"
        elif event_type == "auto_resolved":
            color = "#52C41A"
        elif severity == "P0":
            color = "#F53F3F"
        elif severity == "P1":
            color = "#FA8C16"
        elif severity == "P2":
            color = "comment"
        elif severity == "P3":
            color = "comment"
        else:
            color = "warning"
        return f"# <font color=\"{color}\">**{title}**</font>"

    def _normalize_mention_targets(self, value: Any) -> List[str]:
        if not value:
            return []
        if isinstance(value, str):
            raw_items = re.split(r"[,，;；\s]+", value)
        elif isinstance(value, (list, tuple, set)):
            raw_items = value
        else:
            raw_items = [value]
        targets: List[str] = []
        for item in raw_items:
            raw_text = str(item).strip()
            text = "@all" if raw_text.lower() == "@all" else raw_text.lstrip("@")
            if text:
                targets.append(text)
        return list(dict.fromkeys(targets))

    def _build_wechat_markdown_mentions(self, targets: List[str]) -> str:
        mention_tokens: List[str] = []
        for target in targets:
            text = str(target).strip()
            if not text:
                continue
            if text == "@all":
                mention_tokens.append("<@all>")
            elif text.startswith("<@") and text.endswith(">"):
                mention_tokens.append(text)
            else:
                mention_tokens.append(f"<@{text}>")
        return " ".join(dict.fromkeys(mention_tokens))

    def _inline_wechat_mentions_into_handler(
        self,
        card_data: Optional[Dict[str, Any]],
        markdown_mentions: str,
    ) -> bool:
        """把企业微信 Markdown @ 对象合并到“当前处理人”行，避免卡片底部单独多一行。"""
        if not card_data or not markdown_mentions:
            return False
        rows = card_data.get("rows")
        if not isinstance(rows, list):
            return False
        for row in rows:
            if not isinstance(row, dict):
                continue
            label = str(row.get("label") or "").strip()
            if label == "当前处理人":
                row["value"] = markdown_mentions
                return True
        return False

    async def _send_wechat(
        self,
        config: Dict[str, Any],
        title: str,
        content: str,
        card_data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """发送企业微信消息"""
        webhook_url = config.get("webhook") or settings.WECHAT_WEBHOOK_URL
        if not webhook_url:
            logger.error("企业微信Webhook未配置")
            return False

        mentioned_list = self._normalize_mention_targets(config.get("mentioned_list"))
        mentioned_mobile_list = self._normalize_mention_targets(
            config.get("mentioned_mobile_list") or config.get("mentioned_mobile_lists") or config.get("at_mobiles")
        )
        markdown_mentions = self._build_wechat_markdown_mentions([*mentioned_list, *mentioned_mobile_list])

        mentions_inlined = self._inline_wechat_mentions_into_handler(card_data, markdown_mentions)

        markdown_content = f"{self._wechat_markdown_title(title, card_data)}\n\n"
        if card_data:
            markdown_content += self._build_card_rows_markdown(card_data) or content
        else:
            markdown_content += content

        if markdown_mentions and not mentions_inlined:
            markdown_content = f"{markdown_content}\n\n{markdown_mentions}"

        message = {
            "msgtype": "markdown",
            "markdown": {
                "content": markdown_content
            }
        }
        
        try:
            response = await self._post_json(webhook_url, message)
            result = self._parse_robot_response(response, "企业微信")
            if result is None:
                return False
            if result.get("errcode") == 0:
                logger.info("企业微信消息发送成功", title=title)
                return True
            else:
                self.last_error_message = f"企业微信机器人拒绝请求：{result.get('errmsg') or result}"
                logger.error("企业微信消息发送失败", 
                            status=response.status_code,
                            error=result)
                return False
        except Exception as e:
            self.last_error_message = f"企业微信消息发送异常：{str(e)}"
            logger.error("企业微信消息发送异常", error=str(e))
            return False
    
    async def _send_dingtalk(
        self,
        config: Dict[str, Any],
        title: str,
        content: str,
        card_data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """发送钉钉消息"""
        webhook_url = config.get("webhook") or settings.DINGTALK_WEBHOOK_URL
        if not webhook_url:
            logger.error("钉钉Webhook未配置")
            return False

        if card_data:
            severity_style = self._severity_style(card_data)
            detail_label = "记录详情" if card_data.get("notification_kind") == "operation" else "故障详情"
            markdown_lines = [
                f"## <font color=\"{severity_style['hex']}\">{title}</font>",
            ]
            if card_data.get("notification_kind") == "config_backup":
                markdown_lines.append(self._build_config_backup_markdown(card_data))
            else:
                for row in (card_data.get("rows") or []):
                    label = str(row.get("label") or "").strip()
                    value = str(row.get("value") or "").strip()
                    if label and value:
                        if card_data.get("notification_kind") == "operation" and label == "变更内容" and len(value) > 300:
                            value = value[:300] + "..."
                        markdown_lines.append(f"**{label}：**{value}")
            if card_data.get("detail_url"):
                markdown_lines.append(f"[{detail_label}]({card_data['detail_url']})")
                message = {
                    "msgtype": "actionCard",
                    "actionCard": {
                        "title": title,
                        "text": "\n".join(markdown_lines),
                        "singleTitle": detail_label,
                        "singleURL": card_data["detail_url"],
                        "btnOrientation": "0",
                    },
                }
            else:
                message = {
                    "msgtype": "markdown",
                    "markdown": {
                        "title": title,
                        "text": "\n".join(markdown_lines),
                    },
                }
        else:
            message = {
                "msgtype": "markdown",
                "markdown": {
                    "title": title,
                    "text": f"### {title}\\n\\n{content}"
                }
            }

        at_mobiles = config.get("at_mobiles", [])
        if at_mobiles:
            message["at"] = {
                "atMobiles": at_mobiles,
                "isAtAll": False
            }
        
        try:
            response = await self._post_json(webhook_url, message)
            result = self._parse_robot_response(response, "钉钉")
            if result is None:
                return False
            if result.get("errcode") == 0:
                logger.info("钉钉消息发送成功", title=title)
                return True
            else:
                self.last_error_message = f"钉钉机器人拒绝请求：{result.get('errmsg')}"
                logger.error("钉钉消息发送失败", 
                            error=result.get("errmsg"))
                return False
        except Exception as e:
            self.last_error_message = f"钉钉消息发送异常：{str(e)}"
            logger.error("钉钉消息发送异常", error=str(e))
            return False

    async def _send_feishu(
        self,
        config: Dict[str, Any],
        title: str,
        content: str,
        card_data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """发送飞书机器人消息"""
        webhook_url = config.get("webhook") or config.get("url")
        if not webhook_url:
            logger.error("飞书Webhook未配置")
            return False

        if card_data:
            severity_style = self._severity_style(card_data)
            detail_label = "记录详情" if card_data.get("notification_kind") == "operation" else "故障详情"
            elements = [{
                "tag": "div",
                "text": {
                    "tag": "lark_md",
                    "content": self._build_compact_rows_markdown(card_data),
                },
            }]
            if card_data.get("detail_url"):
                elements.append({
                    "tag": "action",
                    "actions": [
                        {
                            "tag": "button",
                            "type": "primary",
                            "text": {
                                "tag": "plain_text",
                                "content": detail_label,
                            },
                            "url": card_data["detail_url"],
                        }
                    ],
                })
            message = {
                "msg_type": "interactive",
                "card": {
                    "config": {
                        "wide_screen_mode": True,
                        "enable_forward": True,
                    },
                    "header": {
                        "template": severity_style["feishu"],
                        "title": {
                            "tag": "plain_text",
                            "content": title,
                        },
                    },
                    "elements": elements or [
                        {
                            "tag": "div",
                            "text": {
                                "tag": "lark_md",
                                "content": content.replace("\n", "\n"),
                            },
                        }
                    ],
                },
            }
        else:
            message = {
                "msg_type": "post",
                "content": {
                    "post": {
                        "zh_cn": {
                            "title": title,
                            "content": [
                                [
                                    {
                                        "tag": "text",
                                        "text": content,
                                    }
                                ]
                            ],
                        }
                    }
                }
            }

        try:
            response = await self._post_json(webhook_url, message)
            result = self._parse_robot_response(response, "飞书")
            if result is None:
                return False
            if result.get("code") in (0, "0", None):
                logger.info("飞书消息发送成功", title=title)
                return True
            error_msg = result.get("msg") or str(result)
            if result.get("code") == 19024:
                self.last_error_message = "飞书机器人拒绝请求：未命中安全关键词，请在飞书机器人安全设置中补充关键词，或把测试文案包含在允许关键词内。"
            elif result.get("code") == 19022:
                self.last_error_message = "飞书机器人拒绝请求：当前服务器出口 IP 不在飞书机器人白名单中，请把服务器出口 IP 加入机器人白名单。"
            elif result.get("code") == 19001:
                self.last_error_message = "飞书机器人拒绝请求：Webhook token 无效，请检查地址是否填写完整。"
            else:
                self.last_error_message = f"飞书机器人拒绝请求：{error_msg}"
            logger.error("飞书消息发送失败", error=result)
            return False
        except Exception as e:
            self.last_error_message = f"飞书消息发送异常：{str(e)}"
            logger.error("飞书消息发送异常", error=str(e))
            return False
    
    async def _send_email(self, config: Dict[str, Any],
                         title: str, content: str) -> bool:
        """发送邮件"""
        recipients = config.get("recipients", [])
        if not recipients:
            logger.error("邮件收件人未配置")
            return False
        
        smtp_host = settings.SMTP_HOST
        smtp_port = settings.SMTP_PORT
        smtp_user = settings.SMTP_USER
        smtp_password = settings.SMTP_PASSWORD
        smtp_from = settings.SMTP_FROM or smtp_user
        
        if not all([smtp_host, smtp_user, smtp_password]):
            logger.error("SMTP配置不完整")
            return False
        
        try:
            msg = MIMEMultipart('alternative')
            msg['Subject'] = title
            msg['From'] = smtp_from
            msg['To'] = ', '.join(recipients)
            
            # HTML内容
            html_content = f"""
            <html>
            <body>
                <h2>{title}</h2>
                <p>{content.replace(chr(10), '<br>')}</p>
                <hr>
                <p style="color: #666; font-size: 12px;">
                    发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}
                </p>
            </body>
            </html>
            """
            
            msg.attach(MIMEText(content, 'plain', 'utf-8'))
            msg.attach(MIMEText(html_content, 'html', 'utf-8'))
            
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.starttls()
                server.login(smtp_user, smtp_password)
                server.sendmail(smtp_from, recipients, msg.as_string())
            
            logger.info("邮件发送成功", 
                       title=title, 
                       recipients=recipients)
            return True
            
        except Exception as e:
            logger.error("邮件发送失败", error=str(e))
            self.last_error_message = f"邮件发送失败：{str(e)}"
            return False
    
    async def _send_webhook(
        self,
        config: Dict[str, Any],
        title: str,
        content: str,
        card_data: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """发送自定义Webhook"""
        webhook_url = config.get("url")
        if not webhook_url:
            logger.error("Webhook URL未配置")
            return False

        provider = self._detect_webhook_provider(webhook_url)
        if provider == "wechat":
            return await self._send_wechat({"webhook": webhook_url, **config}, title, content, card_data)
        if provider == "dingtalk":
            return await self._send_dingtalk({"webhook": webhook_url, **config}, title, content, card_data)
        if provider == "feishu":
            return await self._send_feishu({"webhook": webhook_url, **config}, title, content, card_data)
        
        method = config.get("method", "POST")
        headers = config.get("headers", {})
        
        payload = {
            "title": title,
            "content": content,
            "timestamp": datetime.now().isoformat(),
            "card_data": card_data or {},
        }
        
        # 支持自定义payload模板
        template = config.get("template")
        if template:
            try:
                payload = template.format(
                    title=title,
                    content=content,
                    timestamp=datetime.now().isoformat()
                )
                if isinstance(payload, str):
                    import json
                    payload = json.loads(payload)
            except Exception as e:
                logger.error("Webhook模板解析失败", error=str(e))
        
        try:
            if method.upper() == "POST":
                response = await self._post_json(webhook_url, payload, headers)
            else:
                response = await self._get_request(webhook_url, payload, headers)
            
            if response.status_code < 400:
                logger.info("Webhook发送成功", url=webhook_url)
                return True
            else:
                self.last_error_message = f"Webhook 返回 HTTP {response.status_code}：{response.text[:200]}"
                logger.error("Webhook发送失败", 
                            status=response.status_code,
                            response=response.text)
                return False
                
        except Exception as e:
            self.last_error_message = f"Webhook 发送异常：{str(e)}"
            logger.error("Webhook发送异常", error=str(e))
            return False

    def _detect_webhook_provider(self, webhook_url: str) -> Optional[str]:
        """根据 webhook URL 粗略识别机器人类型。"""
        url = webhook_url.lower()
        if "work.weixin.qq.com" in url or "qyapi.weixin.qq.com" in url:
            return "wechat"
        if "oapi.dingtalk.com" in url or "api.dingtalk.com" in url:
            return "dingtalk"
        if "open.feishu.cn" in url or "open.larksuite.com" in url:
            return "feishu"
        return None
    
    async def send_alert_notification(self, alert_data: Dict[str, Any],
                                     channels: List[Dict[str, Any]]) -> List[bool]:
        """
        发送告警通知到多个渠道
        
        Args:
            alert_data: 告警数据
            channels: 通知渠道列表
        """
        title = f"【{alert_data.get('severity', '告警')}】{alert_data.get('rule_name', '网络监控告警')}"
        
        content = f"""
**告警设备**: {alert_data.get('device_name', 'Unknown')} ({alert_data.get('device_ip', 'Unknown')})

**告警规则**: {alert_data.get('rule_name', 'Unknown')}

**告警详情**: {alert_data.get('message', '无')}

**当前值**: {alert_data.get('alert_value', 'N/A')}

**阈值**: {alert_data.get('threshold', 'N/A')}

**触发时间**: {alert_data.get('started_at', datetime.now().isoformat())}
"""
        
        results = []
        for channel in channels:
            channel_type = channel.get("type")
            config = channel.get("config", {})
            result = await self.send_notification(channel_type, config, title, content)
            results.append(result)
        
        return results
    
    async def close(self):
        """兼容保留：当前不再持有长生命周期 HTTP client。"""
        return None


# 全局通知管理器实例
notification_manager = NotificationManager()
