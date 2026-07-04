"""询价单：把同一会话里陆续到达的信息合并进一张打开的询价单。

规则：
- 会话存在 48h 内更新过、状态未关闭的询价单 → 合并（只填空字段，VIN 例外可覆盖）
- 否则新建
- 每次合并后按品类重算缺件清单
"""

from __future__ import annotations

import json
import sqlite3

from partspilot.nlu.analyzer import Analysis
from partspilot.nlu.dictionaries import REQUIRED_FIELDS_BY_TYPE

_MERGE_FIELDS = ["brand", "model", "year", "displacement", "engine_model", "gearbox_model"]
# inquiries 表里 model 存在 vehicle_model 列
_COLUMN_OF = {f: ("vehicle_model" if f == "model" else f) for f in _MERGE_FIELDS}


def _recompute_missing(part_type: str, values: dict) -> list[str]:
    if part_type not in REQUIRED_FIELDS_BY_TYPE:
        return []
    return [
        f
        for f in REQUIRED_FIELDS_BY_TYPE[part_type]
        if not values.get(_COLUMN_OF.get(f, f), "")
    ]


def upsert_inquiry(
    conn: sqlite3.Connection,
    conversation_id: int,
    customer_id: int,
    analysis: Analysis,
    vin_decode: dict | None,
    raw_message: str,
) -> int | None:
    """只有出现业务信号（品类/VIN/询价意图）才建询价单，闲聊不建。"""
    has_signal = (
        analysis.part_type != "unknown" or analysis.vins or "inquiry" in analysis.intents
    )
    if not has_signal:
        return None

    open_row = conn.execute(
        "SELECT * FROM inquiries WHERE conversation_id = ? "
        "AND status IN ('new', 'quoted', 'following') "
        "AND updated_at >= datetime('now', 'localtime', '-48 hours') "
        "ORDER BY id DESC LIMIT 1",
        (conversation_id,),
    ).fetchone()

    # 待写入的值：分析字段 + VIN 解码补全（解码字段优先级低于客户明说的）
    incoming: dict[str, str] = {}
    for field in _MERGE_FIELDS:
        value = analysis.fields.get(field, "")
        if not value and vin_decode:
            value = str(vin_decode.get(field, "") or "")
        if value:
            incoming[_COLUMN_OF[field]] = value
    if analysis.vins:
        incoming["vin"] = analysis.vins[0]["vin"]
    if vin_decode and vin_decode.get("manufacturer"):
        incoming["manufacturer"] = vin_decode["manufacturer"]
    if vin_decode and vin_decode.get("series"):
        incoming["series"] = str(vin_decode["series"])

    if open_row is None:
        part_type = analysis.part_type
        values = {c: incoming.get(c, "") for c in
                  ("brand", "vehicle_model", "year", "displacement", "vin",
                   "engine_model", "gearbox_model", "manufacturer", "series")}
        missing = _recompute_missing(part_type, values)
        cursor = conn.execute(
            "INSERT INTO inquiries (conversation_id, customer_id, part_type, brand, series, "
            "vehicle_model, year, displacement, vin, engine_model, gearbox_model, manufacturer, "
            "missing_fields, vin_decoded, vin_decode, raw_message) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                conversation_id,
                customer_id,
                part_type,
                values["brand"],
                values["series"],
                values["vehicle_model"],
                values["year"],
                values["displacement"],
                values["vin"],
                values["engine_model"],
                values["gearbox_model"],
                values["manufacturer"],
                json.dumps(missing, ensure_ascii=False),
                int(bool(vin_decode and vin_decode.get("valid"))),
                json.dumps(vin_decode or {}, ensure_ascii=False),
                raw_message,
            ),
        )
        return cursor.lastrowid

    # 合并：只填空字段；VIN 有新值就覆盖（客户可能纠正）
    current = dict(open_row)
    updates: dict[str, str] = {}
    for column, value in incoming.items():
        if column == "vin":
            if value and value != current["vin"]:
                updates["vin"] = value
        elif value and not current[column]:
            updates[column] = value

    part_type = current["part_type"]
    if part_type == "unknown" and analysis.part_type != "unknown":
        part_type = analysis.part_type
        updates["part_type"] = part_type

    if vin_decode and vin_decode.get("valid"):
        updates["vin_decoded"] = 1
        updates["vin_decode"] = json.dumps(vin_decode, ensure_ascii=False)

    merged = {**current, **updates}
    missing = _recompute_missing(part_type, merged)
    updates["missing_fields"] = json.dumps(missing, ensure_ascii=False)

    set_clause = ", ".join(f"{col} = ?" for col in updates)
    conn.execute(
        f"UPDATE inquiries SET {set_clause}, updated_at = datetime('now','localtime') WHERE id = ?",
        (*updates.values(), open_row["id"]),
    )
    return open_row["id"]
