"""基础存取：设置、客户、会话、消息、草稿。"""

from __future__ import annotations

import json
import secrets
import sqlite3
from datetime import datetime, timedelta

DEFAULT_SETTINGS = {
    "shop_name": "",
    "private_reply_mode": "auto",   # 私聊默认自动回复
    "group_reply_mode": "draft",    # 群聊默认只出草稿
    "quiet_start": "22:30",
    "quiet_end": "07:30",
    "welcome_cooldown_hours": "12",
    "rate_limit_per_hour": "6",
    "attention_threshold": "3",     # 优先级达到该值 → 标记待处理
    "vin_mock": "0",                # 1 = 未配置 17vin 时用演示数据补全
}


def ensure_settings(conn: sqlite3.Connection) -> None:
    for key, value in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", (key, value))
    # webhook token 首次生成
    conn.execute(
        "INSERT OR IGNORE INTO settings (key, value) VALUES ('webhook_token', ?)",
        (secrets.token_urlsafe(24),),
    )
    conn.commit()


def get_settings(conn: sqlite3.Connection) -> dict:
    rows = conn.execute("SELECT key, value FROM settings").fetchall()
    settings = dict(DEFAULT_SETTINGS)
    settings.update({r["key"]: r["value"] for r in rows})
    return settings


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


def upsert_customer(conn: sqlite3.Connection, channel: str, external_id: str, name: str) -> int:
    row = conn.execute(
        "SELECT id, name FROM customers WHERE channel = ? AND external_id = ?",
        (channel, external_id),
    ).fetchone()
    if row:
        if name and name != row["name"]:
            conn.execute(
                "UPDATE customers SET name = ?, updated_at = datetime('now','localtime') WHERE id = ?",
                (name, row["id"]),
            )
        return row["id"]
    cursor = conn.execute(
        "INSERT INTO customers (channel, external_id, name) VALUES (?, ?, ?)",
        (channel, external_id, name or external_id),
    )
    return cursor.lastrowid


def get_or_create_conversation(
    conn: sqlite3.Connection,
    customer_id: int,
    channel: str,
    chat_type: str = "private",
    group_name: str = "",
) -> sqlite3.Row:
    row = conn.execute(
        "SELECT * FROM conversations WHERE customer_id = ? AND chat_type = ? AND group_name = ?",
        (customer_id, chat_type, group_name),
    ).fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO conversations (customer_id, channel, chat_type, group_name) VALUES (?, ?, ?, ?)",
            (customer_id, channel, chat_type, group_name),
        )
        row = conn.execute(
            "SELECT * FROM conversations WHERE customer_id = ? AND chat_type = ? AND group_name = ?",
            (customer_id, chat_type, group_name),
        ).fetchone()
    return row


def add_message(
    conn: sqlite3.Connection,
    conversation_id: int,
    direction: str,
    content: str,
    msg_type: str = "text",
    is_auto: bool = False,
    tags: list[str] | None = None,
    analysis: dict | None = None,
) -> int:
    cursor = conn.execute(
        "INSERT INTO messages (conversation_id, direction, msg_type, content, is_auto, tags, analysis) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            conversation_id,
            direction,
            msg_type,
            content,
            int(is_auto),
            json.dumps(tags or [], ensure_ascii=False),
            json.dumps(analysis or {}, ensure_ascii=False),
        ),
    )
    return cursor.lastrowid


def touch_conversation(
    conn: sqlite3.Connection,
    conversation_id: int,
    priority: int,
    tags: list[str],
    needs_attention: bool,
) -> None:
    row = conn.execute("SELECT tags, priority FROM conversations WHERE id = ?", (conversation_id,)).fetchone()
    merged = list(dict.fromkeys(json.loads(row["tags"]) + tags))[:8]
    conn.execute(
        "UPDATE conversations SET priority = ?, tags = ?, "
        "needs_attention = CASE WHEN ? THEN 1 ELSE needs_attention END, "
        "last_message_at = datetime('now','localtime') WHERE id = ?",
        (
            max(priority, row["priority"]),
            json.dumps(merged, ensure_ascii=False),
            int(needs_attention),
            conversation_id,
        ),
    )


def update_context_token(conn: sqlite3.Connection, conversation_id: int, token: str) -> None:
    if token:
        conn.execute(
            "UPDATE conversations SET last_context_token = ? WHERE id = ?",
            (token, conversation_id),
        )


def mark_conversation_read(conn: sqlite3.Connection, conversation_id: int) -> None:
    conn.execute(
        "UPDATE conversations SET needs_attention = 0, priority = 0 WHERE id = ?",
        (conversation_id,),
    )
    conn.commit()


def count_recent_auto_replies(conn: sqlite3.Connection, conversation_id: int, hours: int = 1) -> int:
    since = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM messages "
        "WHERE conversation_id = ? AND direction = 'out' AND is_auto = 1 AND created_at >= ?",
        (conversation_id, since),
    ).fetchone()
    return row["n"]


def welcomed_recently(conn: sqlite3.Connection, conversation: sqlite3.Row, cooldown_hours: int) -> bool:
    value = conversation["last_welcome_at"]
    if not value:
        return False
    try:
        last = datetime.fromisoformat(value)
    except ValueError:
        return False
    return datetime.now() - last < timedelta(hours=cooldown_hours)


def record_welcome(conn: sqlite3.Connection, conversation_id: int) -> None:
    conn.execute(
        "UPDATE conversations SET last_welcome_at = datetime('now','localtime') WHERE id = ?",
        (conversation_id,),
    )


def create_draft(conn: sqlite3.Connection, conversation_id: int, content: str, reason: str) -> int:
    cursor = conn.execute(
        "INSERT INTO drafts (conversation_id, content, reason) VALUES (?, ?, ?)",
        (conversation_id, content, reason),
    )
    return cursor.lastrowid


def resolve_draft(conn: sqlite3.Connection, draft_id: int, status: str) -> sqlite3.Row | None:
    row = conn.execute("SELECT * FROM drafts WHERE id = ? AND status = 'pending'", (draft_id,)).fetchone()
    if row is None:
        return None
    conn.execute(
        "UPDATE drafts SET status = ?, resolved_at = datetime('now','localtime') WHERE id = ?",
        (status, draft_id),
    )
    conn.commit()
    return row
