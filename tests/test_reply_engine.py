"""回复决策引擎测试：规则链、模式、闸门。"""

from datetime import datetime

from partspilot.nlu.analyzer import analyze
from partspilot.reply.engine import decide

SETTINGS = {
    "shop_name": "老周汽配",
    "private_reply_mode": "auto",
    "group_reply_mode": "draft",
    "quiet_start": "22:30",
    "quiet_end": "07:30",
    "rate_limit_per_hour": "6",
}

DAYTIME = datetime(2026, 7, 4, 10, 0)
MIDNIGHT = datetime(2026, 7, 4, 23, 30)


def run(text, **kwargs):
    defaults = dict(
        text=text,
        chat_type="private",
        conversation_mode="",
        settings=SETTINGS,
        rules=[],
        now=DAYTIME,
    )
    defaults.update(kwargs)
    return decide(analyze(text), **defaults)


def test_private_auto_send():
    d = run("要个迈腾的发动机多少钱")
    assert d.action == "send"
    assert "补充" in d.text  # 缺件引导


def test_group_defaults_to_draft():
    d = run("要个迈腾的发动机多少钱", chat_type="group")
    assert d.action == "draft"


def test_conversation_mode_off():
    d = run("发动机多少钱", conversation_mode="off")
    assert d.action == "none"


def test_custom_rule_wins_over_builtin():
    rules = [
        {"id": 1, "name": "地址", "kind": "keyword", "pattern": "地址|在哪|位置",
         "template": "地址：XX市汽配城3区18号，导航搜「老周汽配」。", "priority": 10,
         "scope": "all", "is_active": 1},
    ]
    d = run("你们店在哪啊？发动机多少钱", rules=rules)
    assert d.action == "send"
    assert "汽配城" in d.text
    assert d.rule_id == 1


def test_inactive_rule_skipped():
    rules = [
        {"id": 1, "name": "地址", "kind": "keyword", "pattern": "在哪",
         "template": "地址...", "priority": 10, "scope": "all", "is_active": 0},
    ]
    d = run("你们在哪", rules=rules)
    assert "地址..." not in d.text


def test_vin_reply_with_decode():
    decode = {"brand": "大众", "model": "迈腾", "year": "2018", "displacement": "2.0T",
              "engine_model": "CUF", "valid": True}
    d = run("1HGCM82633A004352", vin_decode=decode)
    assert d.action == "send"
    assert "CUF" in d.text
    assert "迈腾" in d.text


def test_quiet_hours_downgrade():
    d = run("发动机多少钱", now=MIDNIGHT)
    assert d.action == "draft"
    assert "静默" in d.reason


def test_rate_limit_downgrade():
    d = run("发动机多少钱", recent_auto_replies=6)
    assert d.action == "draft"
    assert "限流" in d.reason


def test_inventory_note_only_in_draft():
    matches = [{"display_name": "迈腾 EA888 发动机", "internal_code": "E001", "price": 8500}]
    # 群聊（draft）→ 带内部备注
    d_draft = run("迈腾发动机多少钱", chat_type="group", inventory_matches=matches)
    assert "内部备注" in d_draft.text and "8500" in d_draft.text
    # 私聊自动发送 → 绝不带价格
    d_send = run("迈腾发动机多少钱", inventory_matches=matches)
    assert d_send.action == "send"
    assert "8500" not in d_send.text and "内部备注" not in d_send.text


def test_welcome_cooldown():
    first = run("在吗")
    assert first.action == "send" and "您好" in first.text
    second = run("在吗", welcomed_recently=True)
    assert second.action == "none"


def test_group_chitchat_no_welcome():
    d = run("哈哈哈", chat_type="group")
    assert d.action == "none"
