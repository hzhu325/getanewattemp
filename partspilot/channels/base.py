"""通道层基础类型：所有入口（ClawBot / Webhook / 模拟器）归一为 IncomingMessage。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class IncomingMessage:
    channel: str                  # clawbot / webhook / simulator
    external_id: str              # 对方在该通道下的唯一标识（微信ID等）
    display_name: str = ""
    chat_type: str = "private"    # private / group
    group_name: str = ""
    msg_type: str = "text"        # text / image / voice / other
    text: str = ""
    context_token: str = ""       # ClawBot 回复关联令牌
    raw: dict = field(default_factory=dict)
