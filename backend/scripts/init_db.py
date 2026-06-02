"""
数据库初始化脚本 - 创建表和初始数据
"""
import sys
import os

# 添加项目根目录到Python路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database import engine, SessionLocal, Base
from app.models import Datacenter, DeviceType
from sqlalchemy.exc import IntegrityError


def init_database():
    """初始化数据库,创建所有表"""
    print("创建数据库表...")
    Base.metadata.create_all(bind=engine)
    print("✓ 数据库表创建成功")


def insert_default_data():
    """插入默认数据"""
    db = SessionLocal()
    try:
        # 插入默认设备类型
        default_device_types = [
            {"name": "Firewall", "display_name": "防火墙", "icon": "security-scan", "description": "防火墙设备"},
            {"name": "Switch", "display_name": "交换机", "icon": "apartment", "description": "网络交换机"},
            {"name": "Router", "display_name": "路由器", "icon": "cloud-server", "description": "网络路由器"},
            {"name": "Console", "display_name": "控制台", "icon": "desktop", "description": "控制台服务器"},
        ]
        
        print("\n插入默认设备类型...")
        for dt_data in default_device_types:
            existing = db.query(DeviceType).filter(DeviceType.name == dt_data["name"]).first()
            if not existing:
                dt = DeviceType(**dt_data)
                db.add(dt)
                print(f"  ✓ 添加设备类型: {dt_data['display_name']}")
            else:
                print(f"  - 设备类型已存在: {dt_data['display_name']}")
        
        db.commit()
        print("\n✓ 默认数据插入成功")
        
    except Exception as e:
        db.rollback()
        print(f"\n✗ 插入默认数据失败: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    print("=" * 50)
    print("数据库初始化")
    print("=" * 50)
    
    init_database()
    insert_default_data()
    
    print("\n" + "=" * 50)
    print("数据库初始化完成!")
    print("=" * 50)
