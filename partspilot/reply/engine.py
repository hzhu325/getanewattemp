"""回复决策引擎。

输入一条消息的分析结果 + 会话/策略上下文，产出 ReplyDecision：
    action = send   直接自动发送
             draft  存为草稿，人一键确认
             none   不回复（只标记）

规则链（先命中先用）：
    1. 自定义规则（后台可配，keyword/regex → 模板，数字越小越优先）
    2. VIN 应答（解码结果确认 + 追问）
    3. 品类应答（按缺件字段引导）
    4. 售后/催单安抚
    5. 兜底欢迎语（每会话冷却）

策略闸门（决定 send 还是 draft）：
    - 会话模式 off → none；draft → 最多 draft
    - 静默时段 / 每小时限流 → send 降级 draft
    - 回复文本含库存/价格备注 → 强制 draft（价格绝不自动发）
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time

from partspilot.nlu.analyzer import Analysis
from partspilot.reply import templates


@dataclass
class ReplyDecision:
    action: str            # send / draft / none
    text: str = ""
    reason: str = ""
    rule_id: int | None = None


def _match_rule(rule: dict, text: str, chat_type: str) -> bool:
    if not rule.get("is_active", 1):
        return False
    scope = rule.get("scope", "all")
    if scope != "all" and scope != chat_type:
        return False
    pattern = rule.get("pattern", "")
    if rule.get("kind") == "regex":
        try:
            return bool(re.search(pattern, text))
        except re.error:
            return False
    # keyword：竖线分隔多个关键词，任一命中
    return any(kw and kw in text for kw in pattern.split("|"))


def _in_quiet_hours(now: datetime, start: str, end: str) -> bool:
    try:
        start_t = time.fromisoformat(start)
        end_t = time.fromisoformat(end)
    except ValueError:
        return False
    current = now.time()
    if start_t <= end_t:
        return start_t <= current < end_t
    return current >= start_t or current < end_t  # 跨午夜（如 22:30–07:30）


def decide(
    analysis: Analysis,
    *,
    text: str,
    chat_type: str,
    conversation_mode: str,
    settings: dict,
    rules: list[dict],
    vin_decode: dict | None = None,
    inventory_matches: list[dict] | None = None,
    recent_auto_replies: int = 0,
    welcomed_recently: bool = False,
    now: datetime | None = None,
) -> ReplyDecision:
    now = now or datetime.now()
    mode = conversation_mode or settings.get(f"{chat_type}_reply_mode", "draft")
    if mode == "off":
        return ReplyDecision(action="none", reason="该会话已关闭自动回复")

    reply_text = ""
    reason = ""
    rule_id = None

    # 1. 自定义规则
    for rule in sorted(rules, key=lambda r: r.get("priority", 100)):
        if _match_rule(rule, text, chat_type):
            reply_text = rule["template"]
            reason = f"命中规则「{rule.get('name', '')}」"
            rule_id = rule.get("id")
            break

    # 2. VIN 应答
    if not reply_text and analysis.vins:
        first = analysis.vins[0]
        reply_text = templates.render_vin_reply(
            first["vin"], vin_decode or {}, first.get("corrected_from", "")
        )
        reason = "识别到 VIN"

    # 3. 品类应答
    if not reply_text and analysis.part_type != "unknown":
        reply_text = templates.render_category_reply(analysis.part_type, analysis.missing_fields)
        reason = "识别到配件品类"

    # 4. 售后 / 催单
    if not reply_text and "aftersale" in analysis.intents:
        reply_text = templates.render_aftersale_reply()
        reason = "售后问题"
    if not reply_text and "urge" in analysis.intents:
        reply_text = templates.render_urge_reply()
        reason = "催单安抚"

    # 5. 兜底欢迎语（带冷却，避免刷屏）
    if not reply_text and "inquiry" in analysis.intents:
        reply_text = templates.render_welcome(settings.get("shop_name", ""))
        reason = "询价引导"
    elif not reply_text and not welcomed_recently and chat_type == "private":
        reply_text = templates.render_welcome(settings.get("shop_name", ""))
        reason = "首次欢迎语"

    if not reply_text:
        return ReplyDecision(action="none", reason="无需回复")

    action = "send" if mode == "auto" else "draft"

    # 策略闸门：静默时段 / 限流 → 降级草稿
    if action == "send":
        quiet_start = settings.get("quiet_start", "")
        quiet_end = settings.get("quiet_end", "")
        if quiet_start and quiet_end and _in_quiet_hours(now, quiet_start, quiet_end):
            action, reason = "draft", reason + "（静默时段，转草稿）"
        elif recent_auto_replies >= int(settings.get("rate_limit_per_hour", 6)):
            action, reason = "draft", reason + "（触发限流，转草稿）"

    # 草稿附上库存核对内部备注；含价格信息绝不自动发送
    if action == "draft" and inventory_matches is not None and (
        analysis.part_type != "unknown" or analysis.vins
    ):
        reply_text += templates.render_inventory_note(inventory_matches)

    return ReplyDecision(action=action, text=reply_text, reason=reason, rule_id=rule_id)
