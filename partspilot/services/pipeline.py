"""消息处理管线：一条消息进来 → 分析/VIN/询价/库存/回复决策 → 落库执行。

所有通道（ClawBot 长轮询、Webhook 桥接、浏览器模拟器）共用这一个入口。
返回完整 trace，供 Webhook 同步响应和模拟器展示。
"""

from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from partspilot.channels.base import IncomingMessage
from partspilot.config import Config
from partspilot.nlu.analyzer import Analysis, analyze
from partspilot.reply.engine import ReplyDecision, decide
from partspilot.services import inquiries as inquiry_service
from partspilot.services import store
from partspilot.services.inventory import match_inventory
from partspilot.vin.providers import decode_vin

logger = logging.getLogger(__name__)

# 发送函数：由通道注入；返回是否发送成功
Sender = Callable[[str], Awaitable[bool]]


@dataclass
class PipelineResult:
    conversation_id: int
    message_id: int
    analysis: Analysis
    vin_decode: dict | None = None
    inquiry_id: int | None = None
    inventory_matches: list[dict] = field(default_factory=list)
    decision: ReplyDecision | None = None
    draft_id: int | None = None
    reply_message_id: int | None = None
    send_failed: bool = False

    def to_dict(self) -> dict:
        return {
            "conversation_id": self.conversation_id,
            "message_id": self.message_id,
            "analysis": self.analysis.to_dict(),
            "vin_decode": self.vin_decode,
            "inquiry_id": self.inquiry_id,
            "inventory_matches": [
                {k: m[k] for k in ("id", "display_name", "internal_code", "price", "match_score")}
                for m in self.inventory_matches
            ],
            "decision": {
                "action": self.decision.action,
                "text": self.decision.text,
                "reason": self.decision.reason,
            }
            if self.decision
            else None,
            "draft_id": self.draft_id,
            "send_failed": self.send_failed,
        }


async def process_message(
    conn: sqlite3.Connection,
    config: Config,
    incoming: IncomingMessage,
    sender: Sender | None = None,
) -> PipelineResult:
    settings = store.get_settings(conn)

    # 1. 客户 & 会话
    customer_id = store.upsert_customer(conn, incoming.channel, incoming.external_id, incoming.display_name)
    conversation = store.get_or_create_conversation(
        conn, customer_id, incoming.channel, incoming.chat_type, incoming.group_name
    )
    conversation_id = conversation["id"]
    store.update_context_token(conn, conversation_id, incoming.context_token)

    # 2. 分析
    analysis = analyze(incoming.text, incoming.msg_type)

    # 3. 落库入站消息
    message_id = store.add_message(
        conn,
        conversation_id,
        "in",
        incoming.text,
        msg_type=incoming.msg_type,
        tags=analysis.tags,
        analysis=analysis.to_dict(),
    )

    # 4. VIN 解码
    vin_decode = None
    if analysis.vins:
        vin = analysis.vins[0]["vin"]
        vin_decode = await decode_vin(vin, config, mock=settings.get("vin_mock") == "1")
        conn.execute(
            "INSERT INTO vin_lookups (vin, valid, source, decode) VALUES (?, ?, ?, ?)",
            (vin, int(vin_decode.get("valid", False)), vin_decode.get("source", ""),
             json.dumps(vin_decode, ensure_ascii=False)),
        )

    # 5. 询价单合并
    inquiry_id = inquiry_service.upsert_inquiry(
        conn, conversation_id, customer_id, analysis, vin_decode, incoming.text
    )

    # 6. 库存匹配（有品类或 VIN 解码结果时才有意义）
    inventory_matches: list[dict] = []
    if inquiry_id is not None:
        lookup_fields = dict(analysis.fields)
        if vin_decode:
            for key in ("brand", "model", "engine_model", "gearbox_model", "displacement", "year"):
                if not lookup_fields.get(key) and vin_decode.get(key):
                    lookup_fields[key] = str(vin_decode[key])
        inventory_matches = match_inventory(conn, analysis.part_type, lookup_fields)

    # 7. 回复决策
    decision = decide(
        analysis,
        text=incoming.text,
        chat_type=incoming.chat_type,
        conversation_mode=conversation["reply_mode"],
        settings=settings,
        rules=[dict(r) for r in conn.execute(
            "SELECT * FROM reply_rules WHERE is_active = 1"
        ).fetchall()],
        vin_decode=vin_decode,
        inventory_matches=inventory_matches,
        recent_auto_replies=store.count_recent_auto_replies(conn, conversation_id),
        welcomed_recently=store.welcomed_recently(
            conn, conversation, int(settings.get("welcome_cooldown_hours", 12))
        ),
    )

    result = PipelineResult(
        conversation_id=conversation_id,
        message_id=message_id,
        analysis=analysis,
        vin_decode=vin_decode,
        inquiry_id=inquiry_id,
        inventory_matches=inventory_matches,
        decision=decision,
    )

    # 8. 执行决策
    if decision.action == "send":
        sent_ok = True
        if sender is not None:
            try:
                sent_ok = await sender(decision.text)
            except Exception:
                logger.exception("通道发送失败 conversation=%s", conversation_id)
                sent_ok = False
        if sent_ok:
            result.reply_message_id = store.add_message(
                conn, conversation_id, "out", decision.text, is_auto=True
            )
        else:
            # 发送失败 → 降级为草稿并标记待处理
            result.send_failed = True
            result.draft_id = store.create_draft(
                conn, conversation_id, decision.text, decision.reason + "（自动发送失败）"
            )
    elif decision.action == "draft":
        result.draft_id = store.create_draft(conn, conversation_id, decision.text, decision.reason)

    # 欢迎语冷却对 send 和 draft 都生效，避免每条闲聊都生成一份欢迎草稿
    if decision.action in ("send", "draft") and (
        "欢迎语" in decision.reason or "询价引导" in decision.reason
    ):
        store.record_welcome(conn, conversation_id)

    # 9. 会话标记：优先级/标签/待处理
    needs_attention = (
        analysis.priority >= int(settings.get("attention_threshold", 3))
        or result.draft_id is not None
        or result.send_failed
    )
    store.touch_conversation(conn, conversation_id, analysis.priority, analysis.tags, needs_attention)

    conn.commit()
    return result
