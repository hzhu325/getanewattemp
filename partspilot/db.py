"""SQLite 存储层：连接管理 + 建表 + 轻量迁移。

单店规模（日消息量千级）下 SQLite WAL 绰绰有余；每次操作取新连接，
避免跨线程共享连接的问题（FastAPI 线程池 + 后台轮询任务并存）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS customers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    channel TEXT NOT NULL,
    external_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    phone TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (channel, external_id)
);

CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    customer_id INTEGER NOT NULL REFERENCES customers (id),
    channel TEXT NOT NULL,
    chat_type TEXT NOT NULL DEFAULT 'private' CHECK (chat_type IN ('private', 'group')),
    group_name TEXT NOT NULL DEFAULT '',
    reply_mode TEXT NOT NULL DEFAULT '' CHECK (reply_mode IN ('', 'auto', 'draft', 'off')),
    needs_attention INTEGER NOT NULL DEFAULT 0,
    priority INTEGER NOT NULL DEFAULT 0,
    tags TEXT NOT NULL DEFAULT '[]',
    last_context_token TEXT NOT NULL DEFAULT '',
    last_message_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    last_welcome_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (customer_id, chat_type, group_name)
);
CREATE INDEX IF NOT EXISTS idx_conversations_attention
    ON conversations (needs_attention, priority DESC, last_message_at DESC);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations (id),
    direction TEXT NOT NULL CHECK (direction IN ('in', 'out')),
    msg_type TEXT NOT NULL DEFAULT 'text',
    content TEXT NOT NULL DEFAULT '',
    is_auto INTEGER NOT NULL DEFAULT 0,
    delivered INTEGER NOT NULL DEFAULT 1,
    tags TEXT NOT NULL DEFAULT '[]',
    analysis TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages (conversation_id, id);
CREATE INDEX IF NOT EXISTS idx_messages_created ON messages (created_at);

CREATE TABLE IF NOT EXISTS drafts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations (id),
    content TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'sent', 'discarded')),
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_drafts_pending ON drafts (status, conversation_id);

CREATE TABLE IF NOT EXISTS inquiries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER REFERENCES conversations (id),
    customer_id INTEGER REFERENCES customers (id),
    status TEXT NOT NULL DEFAULT 'new'
        CHECK (status IN ('new', 'quoted', 'following', 'closed', 'invalid')),
    part_type TEXT NOT NULL DEFAULT 'unknown'
        CHECK (part_type IN ('engine', 'gearbox', 'accessory', 'unknown')),
    brand TEXT NOT NULL DEFAULT '',
    series TEXT NOT NULL DEFAULT '',
    vehicle_model TEXT NOT NULL DEFAULT '',
    year TEXT NOT NULL DEFAULT '',
    displacement TEXT NOT NULL DEFAULT '',
    vin TEXT NOT NULL DEFAULT '',
    engine_model TEXT NOT NULL DEFAULT '',
    gearbox_model TEXT NOT NULL DEFAULT '',
    manufacturer TEXT NOT NULL DEFAULT '',
    region TEXT NOT NULL DEFAULT '',
    missing_fields TEXT NOT NULL DEFAULT '[]',
    vin_decoded INTEGER NOT NULL DEFAULT 0,
    vin_decode TEXT NOT NULL DEFAULT '{}',
    raw_message TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_inquiries_status ON inquiries (status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_inquiries_conversation ON inquiries (conversation_id);

CREATE TABLE IF NOT EXISTS inventory_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    part_type TEXT NOT NULL DEFAULT 'engine'
        CHECK (part_type IN ('engine', 'gearbox', 'accessory')),
    display_name TEXT NOT NULL,
    brand TEXT NOT NULL DEFAULT '',
    vehicle_model TEXT NOT NULL DEFAULT '',
    year TEXT NOT NULL DEFAULT '',
    displacement TEXT NOT NULL DEFAULT '',
    engine_model TEXT NOT NULL DEFAULT '',
    gearbox_model TEXT NOT NULL DEFAULT '',
    internal_code TEXT NOT NULL UNIQUE,
    quality_grade TEXT NOT NULL DEFAULT '',
    price REAL,
    status TEXT NOT NULL DEFAULT 'in_stock'
        CHECK (status IN ('in_stock', 'reserved', 'sold', 'inactive')),
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_inventory_status ON inventory_items (status, part_type);

CREATE TABLE IF NOT EXISTS reply_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'keyword' CHECK (kind IN ('keyword', 'regex')),
    pattern TEXT NOT NULL,
    template TEXT NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    scope TEXT NOT NULL DEFAULT 'all' CHECK (scope IN ('all', 'private', 'group')),
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);

CREATE TABLE IF NOT EXISTS vin_lookups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vin TEXT NOT NULL,
    valid INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'offline',
    decode TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE INDEX IF NOT EXISTS idx_vin_lookups_vin ON vin_lookups (vin);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def connect(db_path: Path | str) -> sqlite3.Connection:
    # 每个请求/任务独享连接且顺序使用，跨线程传递是安全的
    # （FastAPI 同步路由在线程池执行，连接在事件循环线程创建）
    conn = sqlite3.connect(str(db_path), timeout=10, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db(db_path: Path | str) -> None:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    try:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version < SCHEMA_VERSION:
            conn.executescript(SCHEMA)
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
    finally:
        conn.close()
