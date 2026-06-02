"""
资源统计任务
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from celery import shared_task

from app.core import get_logger
from app.database import SessionLocal
from app.models import Circuit
from app.utils import influx_client

logger = get_logger(__name__)


def _escape_flux_string(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _query_interface_average_fields(device_id: int, interface_name: str, start: str = "-1h") -> Dict[str, Optional[float]]:
    fields = ["in_bps", "out_bps", "in_utilization_percent", "out_utilization_percent"]
    field_filter = " or ".join([f'r._field == "{field}"' for field in fields])
    escaped_interface = _escape_flux_string(interface_name)
    flux = f'''
    from(bucket: "{influx_client.bucket}")
      |> range(start: {start})
      |> filter(fn: (r) => r._measurement == "interface_monitoring")
      |> filter(fn: (r) => r.device_id == "{device_id}")
      |> filter(fn: (r) => r.interface_name == "{escaped_interface}")
      |> filter(fn: (r) => {field_filter})
      |> group(columns: ["_field"])
      |> mean()
    '''
    result = influx_client.query(flux)
    values: Dict[str, Optional[float]] = {field: None for field in fields}
    for row in result:
        field_name = row.get("field") or row.get("_field")
        value = row.get("value")
        if field_name in values and value is not None:
            values[field_name] = float(value)
    return values


def _circuit_endpoint_rows(circuit: Circuit) -> List[Dict[str, Any]]:
    return [
        {
            "role": "primary",
            "device_id": circuit.primary_device_id,
            "device_name": circuit.primary_device_ref.name if circuit.primary_device_ref else None,
            "device_ip": circuit.primary_device_ref.ip_address if circuit.primary_device_ref else None,
            "port_name": circuit.primary_port_name,
        },
        {
            "role": "secondary",
            "device_id": circuit.secondary_device_id,
            "device_name": circuit.secondary_device_ref.name if circuit.secondary_device_ref else None,
            "device_ip": circuit.secondary_device_ref.ip_address if circuit.secondary_device_ref else None,
            "port_name": circuit.secondary_port_name,
        },
    ]


@shared_task
def collect_circuit_usage_hourly():
    """每小时统计公网/专线过去一小时平均使用情况。"""
    db = SessionLocal()
    try:
        circuits = db.query(Circuit).filter(Circuit.status == "active").all()
        now = datetime.utcnow()
        points: List[Dict[str, Any]] = []
        endpoint_count = 0
        for circuit in circuits:
            endpoint_values = []
            for endpoint in _circuit_endpoint_rows(circuit):
                if not endpoint.get("device_id") or not endpoint.get("port_name"):
                    continue
                values = _query_interface_average_fields(int(endpoint["device_id"]), str(endpoint["port_name"]), "-1h")
                avg_in_bps = values.get("in_bps")
                avg_out_bps = values.get("out_bps")
                if avg_in_bps is None and avg_out_bps is None:
                    continue
                avg_mbps = max(avg_in_bps or 0.0, avg_out_bps or 0.0) / 1_000_000
                avg_utilization = max(values.get("in_utilization_percent") or 0.0, values.get("out_utilization_percent") or 0.0)
                endpoint_values.append(avg_mbps)
                endpoint_count += 1
                points.append({
                    "measurement": "circuit_usage_hourly",
                    "tags": {
                        "circuit_id": str(circuit.id),
                        "circuit_name": circuit.name,
                        "line_type": circuit.line_type,
                        "role": endpoint["role"],
                        "device_id": str(endpoint["device_id"]),
                        "device_ip": endpoint.get("device_ip"),
                        "interface_name": endpoint.get("port_name"),
                    },
                    "fields": {
                        "avg_in_mbps": round((avg_in_bps or 0.0) / 1_000_000, 3),
                        "avg_out_mbps": round((avg_out_bps or 0.0) / 1_000_000, 3),
                        "avg_mbps": round(avg_mbps, 3),
                        "avg_utilization_percent": round(avg_utilization, 3),
                        "bandwidth_mbps": float(circuit.bandwidth_mbps or 0),
                    },
                    "timestamp": now,
                })
            if endpoint_values:
                points.append({
                    "measurement": "circuit_usage_hourly",
                    "tags": {
                        "circuit_id": str(circuit.id),
                        "circuit_name": circuit.name,
                        "line_type": circuit.line_type,
                        "role": "circuit",
                    },
                    "fields": {
                        "avg_mbps": round(max(endpoint_values), 3),
                        "bandwidth_mbps": float(circuit.bandwidth_mbps or 0),
                        "avg_bandwidth_percent": round((max(endpoint_values) / circuit.bandwidth_mbps) * 100, 3) if circuit.bandwidth_mbps else 0.0,
                    },
                    "timestamp": now,
                })

        if points:
            influx_client.write_points(points, sync=False)
        logger.info("线路小时使用率统计完成", circuits=len(circuits), endpoints=endpoint_count, points=len(points))
        return {"circuits": len(circuits), "endpoints": endpoint_count, "points_written": len(points)}
    except Exception as exc:
        logger.error("线路小时使用率统计失败", error=str(exc))
        return {"error": str(exc)}
    finally:
        db.close()
