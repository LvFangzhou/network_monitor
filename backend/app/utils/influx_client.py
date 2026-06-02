"""
InfluxDB2 客户端封装
"""
from influxdb_client import InfluxDBClient as InfluxClient
from influxdb_client.client.write_api import SYNCHRONOUS, ASYNCHRONOUS
from influxdb_client.domain.write_precision import WritePrecision
from datetime import datetime
from typing import List, Dict, Any, Optional
import httpx
from app.config import settings
from app.core import get_logger

logger = get_logger(__name__)


class InfluxDBClient:
    """InfluxDB2 客户端"""

    DEFAULT_RETENTION_DAYS = 30
    
    def __init__(self):
        self.client = InfluxClient(
            url=settings.INFLUXDB_URL,
            token=settings.INFLUXDB_TOKEN,
            org=settings.INFLUXDB_ORG
        )
        self.bucket = settings.INFLUXDB_BUCKET
        self.org = settings.INFLUXDB_ORG
        
        # 同步写入API（用于告警等关键数据）
        self.write_api_sync = self.client.write_api(write_options=SYNCHRONOUS)
        # 异步写入API（用于高频采集数据）
        self.write_api_async = self.client.write_api(write_options=ASYNCHRONOUS)
        self.query_api = self.client.query_api()
        self.ensure_bucket_retention()
        
    def close(self):
        """关闭连接"""
        self.write_api_sync.close()
        self.write_api_async.close()
        self.client.close()

    def ensure_bucket_retention(self):
        """确保 bucket 保留策略满足端口历史保存周期。"""
        try:
            retention_days = max(int(settings.INFLUXDB_RETENTION_DAYS or self.DEFAULT_RETENTION_DAYS), 1)
            retention_seconds = retention_days * 24 * 60 * 60
            headers = {
                "Authorization": f"Token {settings.INFLUXDB_TOKEN}",
                "Content-Type": "application/json",
            }
            with httpx.Client(timeout=10.0) as client:
                response = client.get(
                    f"{settings.INFLUXDB_URL}/api/v2/buckets",
                    params={"name": self.bucket},
                    headers=headers,
                )
                response.raise_for_status()
                buckets = response.json().get("buckets", [])
                bucket = next((item for item in buckets if item.get("name") == self.bucket), None)
                if not bucket:
                    return

                retention_rules = bucket.get("retentionRules") or []
                current_every_seconds = None
                for rule in retention_rules:
                    if rule.get("type") == "expire":
                        current_every_seconds = rule.get("everySeconds")
                        break

                if current_every_seconds == retention_seconds:
                    return

                patch_response = client.patch(
                    f"{settings.INFLUXDB_URL}/api/v2/buckets/{bucket['id']}",
                    headers=headers,
                    json={
                        "retentionRules": [
                            {
                                "type": "expire",
                                "everySeconds": retention_seconds,
                            }
                        ]
                    },
                )
                patch_response.raise_for_status()
                logger.info(
                    "InfluxDB bucket 保留策略已更新",
                    bucket=self.bucket,
                    retention_seconds=retention_seconds,
                    retention_days=retention_days,
                )
        except Exception as e:
            logger.warning("更新InfluxDB bucket保留策略失败", bucket=self.bucket, error=str(e))
    
    def write_point(self, measurement: str, tags: Dict[str, str], 
                    fields: Dict[str, Any], timestamp: Optional[datetime] = None,
                    sync: bool = False) -> bool:
        """
        写入单条数据点
        
        Args:
            measurement: 测量名称
            tags: 标签字典
            fields: 字段字典
            timestamp: 时间戳（默认当前时间）
            sync: 是否同步写入
        """
        from influxdb_client import Point
        
        try:
            point = Point(measurement)
            
            # 添加标签
            for key, value in tags.items():
                if value is not None:
                    point = point.tag(key, str(value))
            
            # 添加字段
            for key, value in fields.items():
                if value is not None:
                    if isinstance(value, int):
                        point = point.field(key, float(value))
                    elif isinstance(value, (float, bool)):
                        point = point.field(key, value)
                    else:
                        point = point.field(key, float(value))
            
            # 设置时间戳
            if timestamp:
                point = point.time(timestamp, WritePrecision.NS)
            
            if sync:
                self.write_api_sync.write(bucket=self.bucket, org=self.org, record=point)
            else:
                self.write_api_async.write(bucket=self.bucket, org=self.org, record=point)
            
            return True
            
        except Exception as e:
            logger.error("写入InfluxDB失败", 
                        measurement=measurement, 
                        error=str(e))
            return False
    
    def write_points(self, points: List[Dict[str, Any]], sync: bool = False) -> bool:
        """
        批量写入数据点
        
        Args:
            points: 数据点列表
            sync: 是否同步写入
        """
        from influxdb_client import Point
        
        def build_point(p: Dict[str, Any]) -> Point:
            point = Point(p['measurement'])

            # 添加标签
            for key, value in p.get('tags', {}).items():
                if value is not None:
                    point = point.tag(key, str(value))

            # 添加字段
            for key, value in p.get('fields', {}).items():
                if value is not None:
                    if isinstance(value, bool):
                        point = point.field(key, value)
                    elif isinstance(value, int):
                        point = point.field(key, float(value))
                    elif isinstance(value, float):
                        point = point.field(key, value)
                    else:
                        point = point.field(key, float(value))

            # 设置时间戳
            if 'timestamp' in p:
                point = point.time(p['timestamp'], WritePrecision.NS)

            return point

        try:
            influx_points = [build_point(p) for p in points]

            if sync:
                self.write_api_sync.write(bucket=self.bucket, org=self.org, record=influx_points)
            else:
                self.write_api_async.write(bucket=self.bucket, org=self.org, record=influx_points)

            return True

        except Exception as e:
            response = getattr(e, "response", None)
            response_body = getattr(response, "data", None) or getattr(response, "text", None)
            logger.error("批量写入InfluxDB失败", error=str(e), response_body=response_body)
            if not sync:
                return False

            success_count = 0
            for p in points:
                try:
                    self.write_api_sync.write(bucket=self.bucket, org=self.org, record=build_point(p))
                    success_count += 1
                except Exception as item_error:
                    item_response = getattr(item_error, "response", None)
                    item_response_body = getattr(item_response, "data", None) or getattr(item_response, "text", None)
                    logger.error(
                        "单点写入InfluxDB失败",
                        measurement=p.get("measurement"),
                        tags=p.get("tags"),
                        fields=list((p.get("fields") or {}).keys()),
                        error=str(item_error),
                        response_body=item_response_body,
                    )
            return success_count > 0
    
    def query(self, query: str) -> List[Dict[str, Any]]:
        """
        执行Flux查询
        
        Args:
            query: Flux查询语句
            
        Returns:
            查询结果列表
        """
        try:
            result = self.query_api.query(org=self.org, query=query)
            
            data = []
            for table in result:
                for record in table.records:
                    values = dict(record.values)
                    data.append({
                        'time': values.get('_time'),
                        'measurement': values.get('_measurement'),
                        'field': values.get('_field'),
                        'value': values.get('_value'),
                        **values
                    })
            
            return data
            
        except Exception as e:
            logger.error("InfluxDB查询失败", error=str(e))
            return []
    
    def query_metrics(self, measurement: str, device_id: Optional[int] = None,
                      start: str = "-1h", stop: Optional[str] = None,
                      fields: Optional[List[str]] = None,
                      aggregation: Optional[str] = None,
                      interval: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        查询指标数据
        
        Args:
            measurement: 测量名称
            device_id: 设备ID过滤
            start: 开始时间（Flux duration格式）
            stop: 结束时间
            fields: 字段列表
            aggregation: 聚合函数
            interval: 聚合间隔
        """
        try:
            # 构建Flux查询
            flux = f'from(bucket: "{self.bucket}")\\n'
            flux += f'  |> range(start: {start}'
            if stop:
                flux += f', stop: {stop}'
            flux += ')\\n'
            
            flux += f'  |> filter(fn: (r) => r._measurement == "{measurement}")\\n'
            
            if device_id:
                flux += f'  |> filter(fn: (r) => r.device_id == "{device_id}")\\n'
            
            if fields:
                fields_filter = ' or '.join([f'r._field == "{f}"' for f in fields])
                flux += f'  |> filter(fn: (r) => {fields_filter})\\n'
            
            if aggregation and interval:
                flux += f'  |> aggregateWindow(every: {interval}, fn: {aggregation}, createEmpty: false)\\n'
                flux += '  |> yield(name: "result")'
            
            return self.query(flux)
            
        except Exception as e:
            logger.error("查询指标失败", 
                        measurement=measurement, 
                        error=str(e))
            return []
    
    def get_last_value(self, measurement: str, device_id: int, 
                       field: str) -> Optional[float]:
        """获取最新值"""
        try:
            flux = f'''
            from(bucket: "{self.bucket}")
              |> range(start: -1h)
              |> filter(fn: (r) => r._measurement == "{measurement}")
              |> filter(fn: (r) => r.device_id == "{device_id}")
              |> filter(fn: (r) => r._field == "{field}")
              |> last()
            '''
            result = self.query(flux)
            if result:
                return result[0].get('value')
            return None
        except Exception as e:
            logger.error("获取最新值失败", error=str(e))
            return None


# 全局InfluxDB客户端实例
influx_client = InfluxDBClient()
