"""灌入演示数据：库存、规则、几段模拟对话。

用法：python scripts/seed_demo.py
可重复执行（库存编号冲突会跳过）。
"""

import asyncio
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 控制台默认编码打不了中文

from partspilot import db
from partspilot.channels.base import IncomingMessage
from partspilot.config import get_config
from partspilot.services import store
from partspilot.services.pipeline import process_message

INVENTORY = [
    ("engine", "迈腾 EA888 2.0T 发动机总成", "大众", "迈腾", "2018", "2.0T", "CUF", "", "E001", "拆车件 9成新", 8500),
    ("engine", "凯美瑞 2.5L 6AR-FSE 发动机", "丰田", "凯美瑞", "2019", "2.5L", "6AR-FSE", "", "E002", "拆车件 带附件", 9200),
    ("engine", "宝马 5系 B48 2.0T 发动机", "宝马", "5系", "2020", "2.0T", "B48B20", "", "E003", "精拆 已试机", 16800),
    ("gearbox", "迈腾 DQ381 七速湿式双离合", "大众", "迈腾", "2018", "2.0T", "", "DQ381", "G001", "拆车件 已换离合器", 6800),
    ("gearbox", "轩逸 CVT 变速箱 RE0F11A", "日产", "轩逸", "2017", "1.6L", "", "RE0F11A", "G002", "拆车件", 4300),
    ("accessory", "汉兰达 3.5 空调压缩机", "丰田", "汉兰达", "2016", "3.5L", "", "", "A001", "原厂拆车", 850),
]

RULES = [
    ("门店地址", "keyword", "地址|在哪|位置|怎么走", "地址：汽配城东区3排18号「顺发汽配」，导航搜店名即可，到了给我打电话。", 10, "all"),
    ("质保政策", "keyword", "质保|保修|保多久", "发动机/变速箱质保3个月，附件质保1个月，装车视频留好，有问题包换。", 20, "all"),
]

DIALOGUES = [
    ("王师傅", "private", "", ["你们店在哪啊", "要一个大众迈腾2018年2.0T的发动机，多少钱", "车架号 LFV3A23C8J3000001"]),
    ("李老板", "private", "", ["轩逸CVT波箱有没有", "17年的1.6"]),
    ("张哥修理厂", "group", "汽配同行交流群", ["谁有汉兰达3.5的空调压缩机", "急用，今天要"]),
    ("陈师傅", "private", "", ["上次买的机头有点异响，帮忙看看"]),
    ("刘总", "group", "汽配同行交流群", ["宝马5系B48发动机什么价"]),
]


async def main() -> None:
    config = get_config()
    db.init_db(config.db_path)
    conn = db.connect(config.db_path)
    store.ensure_settings(conn)
    store.set_setting(conn, "shop_name", "顺发汽配")
    store.set_setting(conn, "vin_mock", "1")  # 未配置 17vin 时演示解码效果

    for row in INVENTORY:
        try:
            conn.execute(
                "INSERT INTO inventory_items (part_type, display_name, brand, vehicle_model, "
                "year, displacement, engine_model, gearbox_model, internal_code, quality_grade, price) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                row,
            )
        except sqlite3.IntegrityError:
            pass
    for name, kind, pattern, template, priority, scope in RULES:
        exists = conn.execute("SELECT 1 FROM reply_rules WHERE name=?", (name,)).fetchone()
        if not exists:
            conn.execute(
                "INSERT INTO reply_rules (name, kind, pattern, template, priority, scope) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (name, kind, pattern, template, priority, scope),
            )
    conn.commit()

    for name, chat_type, group, texts in DIALOGUES:
        for text in texts:
            result = await process_message(
                conn,
                config,
                IncomingMessage(
                    channel="webhook",
                    external_id=f"demo-{name}",
                    display_name=name,
                    chat_type=chat_type,
                    group_name=group,
                    text=text,
                ),
            )
            action = result.decision.action if result.decision else "none"
            print(f"  [{name}] {text[:24]:<26} → {action}")

    conn.close()
    print("\n演示数据已就绪，运行 python run.py 打开工作台查看。")


if __name__ == "__main__":
    asyncio.run(main())
