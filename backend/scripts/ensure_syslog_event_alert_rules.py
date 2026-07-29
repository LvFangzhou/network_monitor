"""预创建 Syslog 事件型告警规则。"""
from __future__ import annotations

from app.database import SessionLocal
from app.services.syslog_alert_engine import ensure_default_syslog_alert_rules


def main() -> None:
    db = SessionLocal()
    try:
        result = ensure_default_syslog_alert_rules(db)
        print(
            f"Syslog event alert rules ensured: "
            f"created={result['created']} existing={result['existing']} total={result['total']}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
