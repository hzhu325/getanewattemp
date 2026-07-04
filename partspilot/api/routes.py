"""REST API 全部路由。

分区：auth / dashboard / conversations / drafts / inquiries / inventory
      / rules / vin / settings / channels(webhook+clawbot) / simulator
"""

from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from partspilot.api import auth
from partspilot.channels.base import IncomingMessage
from partspilot.nlu.analyzer import analyze
from partspilot.reply.engine import decide
from partspilot.services import store
from partspilot.services.pipeline import process_message
from partspilot.vin.providers import decode_vin

router = APIRouter(prefix="/api")


def _db(request: Request) -> sqlite3.Connection:
    return request.state.db


# ─────────────────────────── auth ───────────────────────────


class LoginBody(BaseModel):
    password: str


@router.get("/auth/status")
def auth_status(request: Request):
    config = request.app.state.config
    logged_in = (not config.admin_password) or auth.verify_session(
        request.app.state.secret, request.cookies.get(auth.COOKIE_NAME)
    )
    return {"auth_required": bool(config.admin_password), "logged_in": logged_in}


@router.post("/auth/login")
def login(body: LoginBody, request: Request, response: Response):
    config = request.app.state.config
    if not config.admin_password:
        return {"ok": True}
    if not auth.verify_password(config.admin_password, body.password):
        raise HTTPException(401, "密码不对")
    response.set_cookie(
        auth.COOKIE_NAME,
        auth.session_token(request.app.state.secret),
        httponly=True,
        samesite="lax",
        max_age=30 * 24 * 3600,
    )
    return {"ok": True}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(auth.COOKIE_NAME)
    return {"ok": True}


# ─────────────────────────── dashboard ───────────────────────────


@router.get("/dashboard/summary")
def dashboard_summary(request: Request):
    conn = _db(request)
    today = datetime.now().strftime("%Y-%m-%d")

    def one(sql: str, *params) -> int:
        return conn.execute(sql, params).fetchone()[0]

    daily = []
    for delta in range(13, -1, -1):
        day = (datetime.now() - timedelta(days=delta)).strftime("%Y-%m-%d")
        daily.append({
            "date": day[5:],
            "incoming": one(
                "SELECT COUNT(*) FROM messages WHERE direction='in' AND date(created_at)=?", day
            ),
            "auto": one(
                "SELECT COUNT(*) FROM messages WHERE direction='out' AND is_auto=1 AND date(created_at)=?",
                day,
            ),
        })

    attention = conn.execute(
        "SELECT c.id, c.tags, c.priority, c.chat_type, c.group_name, c.last_message_at, "
        "cu.name AS customer_name, "
        "(SELECT content FROM messages WHERE conversation_id=c.id AND direction='in' "
        " ORDER BY id DESC LIMIT 1) AS last_text "
        "FROM conversations c JOIN customers cu ON cu.id=c.customer_id "
        "WHERE c.needs_attention=1 ORDER BY c.priority DESC, c.last_message_at DESC LIMIT 20"
    ).fetchall()

    return {
        "today_incoming": one(
            "SELECT COUNT(*) FROM messages WHERE direction='in' AND date(created_at)=?", today
        ),
        "today_auto_replies": one(
            "SELECT COUNT(*) FROM messages WHERE direction='out' AND is_auto=1 AND date(created_at)=?",
            today,
        ),
        "pending_drafts": one("SELECT COUNT(*) FROM drafts WHERE status='pending'"),
        "attention_count": one("SELECT COUNT(*) FROM conversations WHERE needs_attention=1"),
        "open_inquiries": one(
            "SELECT COUNT(*) FROM inquiries WHERE status IN ('new','quoted','following')"
        ),
        "in_stock": one("SELECT COUNT(*) FROM inventory_items WHERE status='in_stock'"),
        "vin_today": one("SELECT COUNT(*) FROM vin_lookups WHERE date(created_at)=?", today),
        "daily": daily,
        "attention": [
            {**dict(row), "tags": json.loads(row["tags"])} for row in attention
        ],
    }


# ─────────────────────────── conversations ───────────────────────────


@router.get("/conversations")
def list_conversations(request: Request, filter: str = "all", q: str = ""):
    conn = _db(request)
    sql = (
        "SELECT c.*, cu.name AS customer_name, cu.external_id, "
        "(SELECT content FROM messages WHERE conversation_id=c.id ORDER BY id DESC LIMIT 1) AS last_text, "
        "(SELECT COUNT(*) FROM drafts WHERE conversation_id=c.id AND status='pending') AS pending_drafts "
        "FROM conversations c JOIN customers cu ON cu.id=c.customer_id"
    )
    where, params = [], []
    if filter == "attention":
        where.append("c.needs_attention=1")
    if q:
        where.append("(cu.name LIKE ? OR c.group_name LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY c.needs_attention DESC, c.last_message_at DESC LIMIT 100"
    rows = conn.execute(sql, params).fetchall()
    return [{**dict(r), "tags": json.loads(r["tags"])} for r in rows]


@router.get("/conversations/{conversation_id}/messages")
def conversation_messages(conversation_id: int, request: Request):
    conn = _db(request)
    conversation = conn.execute(
        "SELECT c.*, cu.name AS customer_name FROM conversations c "
        "JOIN customers cu ON cu.id=c.customer_id WHERE c.id=?",
        (conversation_id,),
    ).fetchone()
    if conversation is None:
        raise HTTPException(404, "会话不存在")
    messages = conn.execute(
        "SELECT id, direction, msg_type, content, is_auto, tags, created_at "
        "FROM messages WHERE conversation_id=? ORDER BY id DESC LIMIT 200",
        (conversation_id,),
    ).fetchall()
    drafts = conn.execute(
        "SELECT * FROM drafts WHERE conversation_id=? AND status='pending' ORDER BY id",
        (conversation_id,),
    ).fetchall()
    return {
        "conversation": {**dict(conversation), "tags": json.loads(conversation["tags"])},
        "messages": [
            {**dict(m), "tags": json.loads(m["tags"])} for m in reversed(messages)
        ],
        "drafts": [dict(d) for d in drafts],
    }


@router.post("/conversations/{conversation_id}/read")
def mark_read(conversation_id: int, request: Request):
    store.mark_conversation_read(_db(request), conversation_id)
    return {"ok": True}


class ModeBody(BaseModel):
    mode: str  # '' / auto / draft / off


@router.post("/conversations/{conversation_id}/mode")
def set_mode(conversation_id: int, body: ModeBody, request: Request):
    if body.mode not in ("", "auto", "draft", "off"):
        raise HTTPException(400, "mode 只能是 ''/auto/draft/off")
    conn = _db(request)
    conn.execute("UPDATE conversations SET reply_mode=? WHERE id=?", (body.mode, conversation_id))
    conn.commit()
    return {"ok": True}


class SendBody(BaseModel):
    text: str


async def _deliver(request: Request, conversation: sqlite3.Row, text: str) -> bool:
    """尽力通过原通道投递；返回是否已确认送达。"""
    clawbot = request.app.state.clawbot
    if (
        conversation["channel"] == "clawbot"
        and clawbot is not None
        and clawbot.bot_token
        and conversation["last_context_token"]
    ):
        return await clawbot.client.send_message(
            clawbot.bot_token, conversation["last_context_token"], text
        )
    return False  # webhook/simulator：走 outbox 由桥接方拉取


@router.post("/conversations/{conversation_id}/send")
async def manual_send(conversation_id: int, body: SendBody, request: Request):
    conn = _db(request)
    conversation = conn.execute(
        "SELECT * FROM conversations WHERE id=?", (conversation_id,)
    ).fetchone()
    if conversation is None:
        raise HTTPException(404, "会话不存在")
    text = body.text.strip()
    if not text:
        raise HTTPException(400, "内容为空")
    delivered = await _deliver(request, conversation, text)
    message_id = store.add_message(conn, conversation_id, "out", text, is_auto=False)
    if not delivered:
        conn.execute("UPDATE messages SET delivered=0 WHERE id=?", (message_id,))
    conn.commit()
    return {"ok": True, "delivered": delivered, "message_id": message_id}


# ─────────────────────────── drafts ───────────────────────────


@router.get("/drafts")
def list_drafts(request: Request):
    rows = _db(request).execute(
        "SELECT d.*, cu.name AS customer_name, c.chat_type, c.group_name, c.channel "
        "FROM drafts d JOIN conversations c ON c.id=d.conversation_id "
        "JOIN customers cu ON cu.id=c.customer_id "
        "WHERE d.status='pending' ORDER BY d.id DESC LIMIT 100"
    ).fetchall()
    return [dict(r) for r in rows]


class DraftEditBody(BaseModel):
    content: str


@router.put("/drafts/{draft_id}")
def edit_draft(draft_id: int, body: DraftEditBody, request: Request):
    conn = _db(request)
    cursor = conn.execute(
        "UPDATE drafts SET content=? WHERE id=? AND status='pending'",
        (body.content, draft_id),
    )
    if cursor.rowcount == 0:
        raise HTTPException(404, "草稿不存在或已处理")
    conn.commit()
    return {"ok": True}


@router.post("/drafts/{draft_id}/send")
async def send_draft(draft_id: int, request: Request):
    conn = _db(request)
    draft = store.resolve_draft(conn, draft_id, "sent")
    if draft is None:
        raise HTTPException(404, "草稿不存在或已处理")
    conversation = conn.execute(
        "SELECT * FROM conversations WHERE id=?", (draft["conversation_id"],)
    ).fetchone()
    # 剥掉内部备注，只发对客部分
    text = re.split(r"\n*【内部备注", draft["content"])[0].strip()
    delivered = await _deliver(request, conversation, text)
    message_id = store.add_message(conn, draft["conversation_id"], "out", text, is_auto=False)
    if not delivered:
        conn.execute("UPDATE messages SET delivered=0 WHERE id=?", (message_id,))
    conn.commit()
    return {"ok": True, "delivered": delivered}


@router.post("/drafts/{draft_id}/discard")
def discard_draft(draft_id: int, request: Request):
    if store.resolve_draft(_db(request), draft_id, "discarded") is None:
        raise HTTPException(404, "草稿不存在或已处理")
    return {"ok": True}


# ─────────────────────────── inquiries ───────────────────────────


@router.get("/inquiries")
def list_inquiries(request: Request, status: str = ""):
    sql = (
        "SELECT i.*, cu.name AS customer_name FROM inquiries i "
        "LEFT JOIN customers cu ON cu.id=i.customer_id"
    )
    params = []
    if status:
        sql += " WHERE i.status=?"
        params.append(status)
    sql += " ORDER BY i.updated_at DESC LIMIT 100"
    rows = _db(request).execute(sql, params).fetchall()
    return [
        {**dict(r), "missing_fields": json.loads(r["missing_fields"]),
         "vin_decode": json.loads(r["vin_decode"])}
        for r in rows
    ]


class InquiryStatusBody(BaseModel):
    status: str


@router.post("/inquiries/{inquiry_id}/status")
def set_inquiry_status(inquiry_id: int, body: InquiryStatusBody, request: Request):
    if body.status not in ("new", "quoted", "following", "closed", "invalid"):
        raise HTTPException(400, "非法状态")
    conn = _db(request)
    cursor = conn.execute(
        "UPDATE inquiries SET status=?, updated_at=datetime('now','localtime') WHERE id=?",
        (body.status, inquiry_id),
    )
    if cursor.rowcount == 0:
        raise HTTPException(404, "询价单不存在")
    conn.commit()
    return {"ok": True}


# ─────────────────────────── inventory ───────────────────────────

_INVENTORY_FIELDS = [
    "part_type", "display_name", "brand", "vehicle_model", "year", "displacement",
    "engine_model", "gearbox_model", "internal_code", "quality_grade", "price", "note",
]


class InventoryBody(BaseModel):
    part_type: str = "engine"
    display_name: str
    brand: str = ""
    vehicle_model: str = ""
    year: str = ""
    displacement: str = ""
    engine_model: str = ""
    gearbox_model: str = ""
    internal_code: str
    quality_grade: str = ""
    price: float | None = None
    note: str = ""


@router.get("/inventory")
def list_inventory(request: Request, q: str = "", status: str = ""):
    sql = "SELECT * FROM inventory_items"
    where, params = [], []
    if status:
        where.append("status=?")
        params.append(status)
    if q:
        like = f"%{q}%"
        where.append(
            "(display_name LIKE ? OR brand LIKE ? OR vehicle_model LIKE ? "
            "OR engine_model LIKE ? OR gearbox_model LIKE ? OR internal_code LIKE ?)"
        )
        params += [like] * 6
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_at DESC LIMIT 200"
    return [dict(r) for r in _db(request).execute(sql, params).fetchall()]


@router.post("/inventory")
def create_inventory(body: InventoryBody, request: Request):
    conn = _db(request)
    try:
        cursor = conn.execute(
            f"INSERT INTO inventory_items ({', '.join(_INVENTORY_FIELDS)}) "
            f"VALUES ({', '.join('?' * len(_INVENTORY_FIELDS))})",
            [getattr(body, f) for f in _INVENTORY_FIELDS],
        )
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"编号 {body.internal_code} 已存在")
    conn.commit()
    return {"ok": True, "id": cursor.lastrowid}


@router.put("/inventory/{item_id}")
def update_inventory(item_id: int, body: InventoryBody, request: Request):
    conn = _db(request)
    try:
        cursor = conn.execute(
            "UPDATE inventory_items SET "
            + ", ".join(f"{f}=?" for f in _INVENTORY_FIELDS)
            + ", updated_at=datetime('now','localtime') WHERE id=?",
            [getattr(body, f) for f in _INVENTORY_FIELDS] + [item_id],
        )
    except sqlite3.IntegrityError:
        raise HTTPException(409, f"编号 {body.internal_code} 已存在")
    if cursor.rowcount == 0:
        raise HTTPException(404, "库存不存在")
    conn.commit()
    return {"ok": True}


class InventoryStatusBody(BaseModel):
    status: str


@router.post("/inventory/{item_id}/status")
def set_inventory_status(item_id: int, body: InventoryStatusBody, request: Request):
    if body.status not in ("in_stock", "reserved", "sold", "inactive"):
        raise HTTPException(400, "非法状态")
    conn = _db(request)
    cursor = conn.execute(
        "UPDATE inventory_items SET status=?, updated_at=datetime('now','localtime') WHERE id=?",
        (body.status, item_id),
    )
    if cursor.rowcount == 0:
        raise HTTPException(404, "库存不存在")
    conn.commit()
    return {"ok": True}


# ─────────────────────────── reply rules ───────────────────────────


class RuleBody(BaseModel):
    name: str
    kind: str = "keyword"
    pattern: str
    template: str
    priority: int = 100
    scope: str = "all"
    is_active: bool = True


@router.get("/rules")
def list_rules(request: Request):
    return [dict(r) for r in _db(request).execute(
        "SELECT * FROM reply_rules ORDER BY priority, id"
    ).fetchall()]


@router.post("/rules")
def create_rule(body: RuleBody, request: Request):
    if body.kind not in ("keyword", "regex"):
        raise HTTPException(400, "kind 只能是 keyword/regex")
    if body.kind == "regex":
        try:
            re.compile(body.pattern)
        except re.error as exc:
            raise HTTPException(400, f"正则不合法：{exc}")
    conn = _db(request)
    cursor = conn.execute(
        "INSERT INTO reply_rules (name, kind, pattern, template, priority, scope, is_active) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (body.name, body.kind, body.pattern, body.template, body.priority, body.scope,
         int(body.is_active)),
    )
    conn.commit()
    return {"ok": True, "id": cursor.lastrowid}


@router.put("/rules/{rule_id}")
def update_rule(rule_id: int, body: RuleBody, request: Request):
    if body.kind == "regex":
        try:
            re.compile(body.pattern)
        except re.error as exc:
            raise HTTPException(400, f"正则不合法：{exc}")
    conn = _db(request)
    cursor = conn.execute(
        "UPDATE reply_rules SET name=?, kind=?, pattern=?, template=?, priority=?, scope=?, "
        "is_active=? WHERE id=?",
        (body.name, body.kind, body.pattern, body.template, body.priority, body.scope,
         int(body.is_active), rule_id),
    )
    if cursor.rowcount == 0:
        raise HTTPException(404, "规则不存在")
    conn.commit()
    return {"ok": True}


@router.delete("/rules/{rule_id}")
def delete_rule(rule_id: int, request: Request):
    conn = _db(request)
    conn.execute("DELETE FROM reply_rules WHERE id=?", (rule_id,))
    conn.commit()
    return {"ok": True}


class RuleTestBody(BaseModel):
    text: str
    chat_type: str = "private"


@router.post("/rules/test")
def test_rules(body: RuleTestBody, request: Request):
    """预览：这条消息会命中哪条规则、引擎会怎么回。"""
    conn = _db(request)
    analysis = analyze(body.text)
    decision = decide(
        analysis,
        text=body.text,
        chat_type=body.chat_type,
        conversation_mode="",
        settings=store.get_settings(conn),
        rules=[dict(r) for r in conn.execute(
            "SELECT * FROM reply_rules WHERE is_active=1"
        ).fetchall()],
    )
    return {
        "analysis": analysis.to_dict(),
        "decision": {"action": decision.action, "text": decision.text, "reason": decision.reason},
    }


# ─────────────────────────── vin ───────────────────────────


class VinBody(BaseModel):
    vin: str


@router.post("/vin/decode")
async def vin_decode_api(body: VinBody, request: Request):
    conn = _db(request)
    settings = store.get_settings(conn)
    vin = body.vin.strip().upper()
    result = await decode_vin(vin, request.app.state.config, mock=settings.get("vin_mock") == "1")
    conn.execute(
        "INSERT INTO vin_lookups (vin, valid, source, decode) VALUES (?, ?, ?, ?)",
        (vin, int(result.get("valid", False)), result.get("source", ""),
         json.dumps(result, ensure_ascii=False)),
    )
    conn.commit()
    return result


@router.get("/vin/history")
def vin_history(request: Request):
    rows = _db(request).execute(
        "SELECT * FROM vin_lookups ORDER BY id DESC LIMIT 50"
    ).fetchall()
    return [{**dict(r), "decode": json.loads(r["decode"])} for r in rows]


# ─────────────────────────── settings ───────────────────────────


@router.get("/settings")
def get_settings_api(request: Request):
    config = request.app.state.config
    settings = store.get_settings(_db(request))
    settings["_seventeen_vin_configured"] = bool(
        config.seventeen_vin_user and config.seventeen_vin_password
    )
    return settings


@router.put("/settings")
def put_settings(body: dict, request: Request):
    conn = _db(request)
    allowed = set(store.DEFAULT_SETTINGS) | {"webhook_token"}
    for key, value in body.items():
        if key in allowed:
            store.set_setting(conn, key, str(value))
    return {"ok": True}


# ─────────────────────────── channels ───────────────────────────


@router.get("/channels/status")
def channels_status(request: Request):
    clawbot = request.app.state.clawbot
    return {
        "clawbot": clawbot.status() if clawbot else {"state": "disabled", "bound": False},
        "webhook": {"enabled": True},
    }


@router.post("/channels/clawbot/bind")
async def clawbot_bind(request: Request):
    clawbot = request.app.state.clawbot
    if clawbot is None:
        raise HTTPException(400, "ClawBot 通道未启用（设置 CLAWBOT_ENABLED=1 后重启）")
    return await clawbot.start_binding()


@router.post("/channels/clawbot/poll")
async def clawbot_poll(request: Request):
    clawbot = request.app.state.clawbot
    if clawbot is None:
        raise HTTPException(400, "ClawBot 通道未启用")
    return await clawbot.poll_binding()


@router.post("/channels/clawbot/unbind")
def clawbot_unbind(request: Request):
    clawbot = request.app.state.clawbot
    if clawbot is None:
        raise HTTPException(400, "ClawBot 通道未启用")
    clawbot.unbind()
    return {"ok": True}


class WebhookIncoming(BaseModel):
    external_id: str
    name: str = ""
    text: str
    chat_type: str = "private"
    group_name: str = ""
    msg_type: str = "text"


@router.post("/channels/webhook/incoming")
async def webhook_incoming(body: WebhookIncoming, request: Request):
    conn = _db(request)
    token = request.headers.get("X-Webhook-Token", "")
    expected = store.get_settings(conn).get("webhook_token", "")
    if not token or token != expected:
        raise HTTPException(401, "X-Webhook-Token 不对")
    incoming = IncomingMessage(
        channel="webhook",
        external_id=body.external_id,
        display_name=body.name,
        chat_type=body.chat_type if body.chat_type in ("private", "group") else "private",
        group_name=body.group_name,
        msg_type=body.msg_type,
        text=body.text,
    )
    result = await process_message(conn, request.app.state.config, incoming)
    payload = result.to_dict()
    decision = payload["decision"]
    # 桥接方拿到 reply 就地发送；draft/none 时 reply 为 null
    payload["reply"] = decision["text"] if decision and decision["action"] == "send" else None
    return payload


@router.get("/channels/webhook/outbox")
def webhook_outbox(request: Request, since_id: int = 0):
    """桥接方轮询待投递消息（人工/草稿回复 webhook 会话时产生）。"""
    conn = _db(request)
    token = request.headers.get("X-Webhook-Token", "")
    expected = store.get_settings(conn).get("webhook_token", "")
    if not token or token != expected:
        raise HTTPException(401, "X-Webhook-Token 不对")
    rows = conn.execute(
        "SELECT m.id, m.content, m.created_at, cu.external_id, c.chat_type, c.group_name "
        "FROM messages m JOIN conversations c ON c.id=m.conversation_id "
        "JOIN customers cu ON cu.id=c.customer_id "
        "WHERE m.direction='out' AND m.delivered=0 AND c.channel='webhook' AND m.id>? "
        "ORDER BY m.id LIMIT 50",
        (since_id,),
    ).fetchall()
    return [dict(r) for r in rows]


@router.post("/channels/webhook/outbox/{message_id}/ack")
def webhook_outbox_ack(message_id: int, request: Request):
    conn = _db(request)
    token = request.headers.get("X-Webhook-Token", "")
    expected = store.get_settings(conn).get("webhook_token", "")
    if not token or token != expected:
        raise HTTPException(401, "X-Webhook-Token 不对")
    conn.execute("UPDATE messages SET delivered=1 WHERE id=?", (message_id,))
    conn.commit()
    return {"ok": True}


# ─────────────────────────── simulator ───────────────────────────


class SimulatorBody(BaseModel):
    name: str = "测试客户"
    text: str
    chat_type: str = "private"
    group_name: str = ""


@router.post("/simulator/message")
async def simulator_message(body: SimulatorBody, request: Request):
    """浏览器聊天模拟器：跑真实管线，返回完整 trace。"""
    incoming = IncomingMessage(
        channel="simulator",
        external_id=f"sim-{body.name}",
        display_name=body.name,
        chat_type=body.chat_type if body.chat_type in ("private", "group") else "private",
        group_name=body.group_name or ("测试群" if body.chat_type == "group" else ""),
        text=body.text,
    )
    result = await process_message(_db(request), request.app.state.config, incoming)
    return result.to_dict()
