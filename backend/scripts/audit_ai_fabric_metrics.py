import asyncio
from collections import Counter

from app.database import SessionLocal
from app.models import Device
from app.utils import influx_client
from app.utils.asternos_exporter_client import asternos_exporter_client


KEYWORDS = (
    "pfc",
    "ecn",
    "pause",
    "headroom",
    "buffer",
    "drop",
    "discard",
    "crc",
    "fcs",
    "symbol",
    "fec",
    "ber",
    "esnr",
    "optic",
    "power",
    "temperature",
    "bias",
    "bgp",
    "route",
    "evpn",
    "vxlan",
    "vtep",
    "ecmp",
    "flow",
)


def relevant(value):
    text = str(value or "").lower()
    return any(keyword in text for keyword in KEYWORDS)


def main():
    db = SessionLocal()
    try:
        devices = db.query(Device).all()
        print("DEVICE_VENDOR_COUNTS")
        print(dict(Counter(str(device.vendor or "-") for device in devices)))
        print("MONITOR_SOURCE_COUNTS")
        print(dict(Counter(str(device.monitor_source or "-") for device in devices)))

        measurements_query = f'''import "influxdata/influxdb/schema"
schema.measurements(bucket: "{influx_client.bucket}", start: -30d)'''
        measurements = [row.get("value") for row in influx_client.query(measurements_query) if row.get("value")]
        print("RELEVANT_INFLUX_MEASUREMENTS")
        print(sorted(value for value in measurements if relevant(value)))
        print("RELEVANT_INFLUX_FIELDS")
        for measurement in measurements:
            fields_query = f'''import "influxdata/influxdb/schema"
schema.measurementFieldKeys(
  bucket: "{influx_client.bucket}",
  measurement: "{measurement}",
  start: -30d,
)'''
            try:
                fields = [row.get("value") for row in influx_client.query(fields_query) if row.get("value")]
            except Exception:
                continue
            matched = sorted(field for field in fields if relevant(field))
            if matched:
                print(measurement, matched)

        asternos = next(
            (
                device
                for device in devices
                if str(device.monitor_source or "").lower() == "asternos_exporter" and device.is_monitored
            ),
            None,
        )
        if asternos:
            print("ASTERNOS_SAMPLE_DEVICE", asternos.id, asternos.name, asternos.ip_address)
            try:
                metrics = asyncio.run(asternos_exporter_client.scrape(asternos))
                base_names = sorted(
                    {
                        str(name).removeprefix(asternos_exporter_client.ASTERNOS_PREFIX)
                        for name in metrics.keys()
                        if relevant(name)
                    }
                )
                print("RELEVANT_EXPORTER_METRICS")
                for name in base_names:
                    print(name)
            except Exception as exc:
                print("EXPORTER_SCRAPE_ERROR", str(exc))
    finally:
        db.close()


if __name__ == "__main__":
    main()
