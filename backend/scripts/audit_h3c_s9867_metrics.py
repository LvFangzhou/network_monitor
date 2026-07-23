"""Audit recent InfluxDB fields for monitored H3C S9867 devices."""
from collections import Counter

from app.database import SessionLocal
from app.models import Device
from app.utils import influx_client


FIELDS = (
    "in_discards_delta",
    "out_discards_delta",
    "in_errors_delta",
    "out_errors_delta",
    "queue_ingress_dropped_pkts_delta",
    "queue_egress_dropped_pkts_delta",
    "pfc_rx_pkts_delta",
    "pfc_tx_pkts_delta",
    "ecn_marked_pkts_delta",
    "buffer_usage",
)


def main():
    db = SessionLocal()
    try:
        devices = (
            db.query(Device)
            .filter(Device.model.ilike("%S9867-128DH%"), Device.is_monitored.is_(True))
            .order_by(Device.id.asc())
            .all()
        )
        print("devices", len(devices), "sample", [(d.id, d.name, d.ip_address) for d in devices[:5]])
        field_filter = " or ".join(f'r._field == "{field}"' for field in FIELDS)
        rows = []
        for device in devices[:5]:
            flux = f'''
from(bucket: "{influx_client.bucket}")
  |> range(start: -15m)
  |> filter(fn: (r) => r._measurement == "interface_monitoring")
  |> filter(fn: (r) => r.device_id == "{device.id}")
  |> filter(fn: (r) => {field_filter})
  |> keep(columns: ["_time", "_field", "_value", "device_id", "interface_name"])
  |> limit(n: 2000)
'''
            rows.extend(influx_client.query(flux))
        counts = Counter(str(row.get("_field") or "") for row in rows)
        device_counts = Counter(str(row.get("device_id") or "") for row in rows)
        nonzero = Counter(
            str(row.get("_field") or "")
            for row in rows
            if isinstance(row.get("_value"), (int, float)) and float(row.get("_value")) > 0
        )
        print("field_points", dict(counts))
        print("field_nonzero_points", dict(nonzero))
        print("devices_with_points", len(device_counts))
    finally:
        db.close()


if __name__ == "__main__":
    main()
