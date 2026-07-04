"""库存：CRUD 由 API 层直接走 SQL，这里只放匹配逻辑。

匹配评分（在售库存内）：
    发动机/变速箱型号吻合 +5（最强信号）
    品牌吻合 +2，车型吻合 +2，排量吻合 +1，年份±2 +1
得分 ≥3 才算候选（避免只靠"品牌相同"乱推荐）。
"""

from __future__ import annotations

import sqlite3


def _norm(value: str) -> str:
    return (value or "").strip().upper().replace(" ", "")


def match_inventory(conn: sqlite3.Connection, part_type: str, fields: dict, limit: int = 5) -> list[dict]:
    query = "SELECT * FROM inventory_items WHERE status = 'in_stock'"
    params: list = []
    if part_type in ("engine", "gearbox", "accessory"):
        query += " AND part_type = ?"
        params.append(part_type)
    rows = conn.execute(query, params).fetchall()

    want_engine = _norm(fields.get("engine_model", ""))
    want_gearbox = _norm(fields.get("gearbox_model", ""))
    want_brand = _norm(fields.get("brand", ""))
    want_model = _norm(fields.get("model", ""))
    want_disp = _norm(fields.get("displacement", "")).rstrip("LT")
    want_year = fields.get("year", "")

    scored = []
    for row in rows:
        score = 0
        item_engine = _norm(row["engine_model"])
        item_gearbox = _norm(row["gearbox_model"])
        if want_engine and item_engine and (want_engine in item_engine or item_engine in want_engine):
            score += 5
        if want_gearbox and item_gearbox and (want_gearbox in item_gearbox or item_gearbox in want_gearbox):
            score += 5
        if want_brand and want_brand == _norm(row["brand"]):
            score += 2
        item_model = _norm(row["vehicle_model"])
        if want_model and item_model and (want_model in item_model or item_model in want_model):
            score += 2
        if want_disp and want_disp == _norm(row["displacement"]).rstrip("LT"):
            score += 1
        if want_year and row["year"]:
            try:
                if abs(int(want_year) - int(row["year"])) <= 2:
                    score += 1
            except ValueError:
                pass
        if score >= 3:
            item = dict(row)
            item["match_score"] = score
            scored.append(item)

    scored.sort(key=lambda x: x["match_score"], reverse=True)
    return scored[:limit]
