"""管线全链路测试：消息进 → 库内状态 + 决策。"""

import json

import pytest

from partspilot import db
from partspilot.channels.base import IncomingMessage
from partspilot.config import Config
from partspilot.services import store
from partspilot.services.pipeline import process_message

VALID_VIN = "1HGCM82633A004352"


@pytest.fixture
def config(tmp_path):
    c = Config()
    c.data_dir = tmp_path
    c.seventeen_vin_user = ""
    c.seventeen_vin_password = ""
    return c


@pytest.fixture
def conn(config):
    db.init_db(config.db_path)
    connection = db.connect(config.db_path)
    store.ensure_settings(connection)
    # 测试与真实时钟解耦：关掉静默时段（否则晚上跑测试 send 会变 draft）
    store.set_setting(connection, "quiet_start", "")
    store.set_setting(connection, "quiet_end", "")
    yield connection
    connection.close()


def incoming(text, name="王师傅", chat_type="private", channel="webhook", group=""):
    return IncomingMessage(
        channel=channel,
        external_id=f"wx-{name}",
        display_name=name,
        chat_type=chat_type,
        group_name=group,
        text=text,
    )


@pytest.mark.anyio
async def test_private_inquiry_auto_reply_and_inquiry_created(conn, config):
    result = await process_message(conn, config, incoming("要个大众迈腾2018年2.0T发动机，多少钱"))
    assert result.decision.action == "send"
    assert result.inquiry_id is not None
    assert result.reply_message_id is not None

    inquiry = conn.execute("SELECT * FROM inquiries WHERE id=?", (result.inquiry_id,)).fetchone()
    assert inquiry["part_type"] == "engine"
    assert inquiry["brand"] == "大众"
    assert "vin" in json.loads(inquiry["missing_fields"])

    messages = conn.execute("SELECT direction, is_auto FROM messages ORDER BY id").fetchall()
    assert [(m["direction"], m["is_auto"]) for m in messages] == [("in", 0), ("out", 1)]


@pytest.mark.anyio
async def test_group_message_creates_draft_not_send(conn, config):
    result = await process_message(
        conn, config, incoming("迈腾波箱多少钱", chat_type="group", group="汽配交流群")
    )
    assert result.decision.action == "draft"
    assert result.draft_id is not None
    assert result.reply_message_id is None
    draft = conn.execute("SELECT * FROM drafts WHERE id=?", (result.draft_id,)).fetchone()
    assert draft["status"] == "pending"


@pytest.mark.anyio
async def test_vin_message_decodes_and_merges_inquiry(conn, config):
    # 第一条：询价（缺 VIN）
    first = await process_message(conn, config, incoming("要迈腾发动机"))
    # 第二条：补 VIN → 合并进同一张询价单
    second = await process_message(conn, config, incoming(f"车架号 {VALID_VIN}"))
    assert second.inquiry_id == first.inquiry_id
    inquiry = conn.execute("SELECT * FROM inquiries WHERE id=?", (second.inquiry_id,)).fetchone()
    assert inquiry["vin"] == VALID_VIN
    missing = json.loads(inquiry["missing_fields"])
    assert "vin" not in missing

    lookup = conn.execute("SELECT * FROM vin_lookups").fetchone()
    assert lookup["vin"] == VALID_VIN
    assert lookup["valid"] == 1


@pytest.mark.anyio
async def test_inventory_match_in_draft(conn, config):
    conn.execute(
        "INSERT INTO inventory_items (part_type, display_name, brand, vehicle_model, "
        "displacement, engine_model, internal_code, price) "
        "VALUES ('engine', '迈腾 EA888 2.0T 发动机', '大众', '迈腾', '2.0T', 'CUF', 'E001', 8500)"
    )
    conn.commit()
    result = await process_message(
        conn, config, incoming("有没有大众迈腾2.0T发动机", chat_type="group", group="群A")
    )
    assert result.inventory_matches
    assert result.inventory_matches[0]["internal_code"] == "E001"
    draft = conn.execute("SELECT content FROM drafts WHERE id=?", (result.draft_id,)).fetchone()
    assert "E001" in draft["content"]  # 草稿带内部备注
    # 自动发出去的消息里绝不能有价格
    sent = conn.execute("SELECT content FROM messages WHERE direction='out'").fetchall()
    assert all("8500" not in m["content"] for m in sent)


@pytest.mark.anyio
async def test_attention_flag_and_read(conn, config):
    result = await process_message(conn, config, incoming(f"急！{VALID_VIN} 发动机总成有吗"))
    row = conn.execute(
        "SELECT needs_attention, priority, tags FROM conversations WHERE id=?",
        (result.conversation_id,),
    ).fetchone()
    assert row["needs_attention"] == 1
    assert row["priority"] >= 6
    tags = json.loads(row["tags"])
    assert "VIN" in tags and "急" in tags

    store.mark_conversation_read(conn, result.conversation_id)
    row = conn.execute(
        "SELECT needs_attention, priority FROM conversations WHERE id=?",
        (result.conversation_id,),
    ).fetchone()
    assert row["needs_attention"] == 0 and row["priority"] == 0


@pytest.mark.anyio
async def test_welcome_cooldown_across_messages(conn, config):
    first = await process_message(conn, config, incoming("在吗"))
    assert first.decision.action == "send"
    second = await process_message(conn, config, incoming("在不在"))
    assert second.decision.action == "none"  # 冷却期内不再发欢迎语


@pytest.mark.anyio
async def test_send_failure_falls_back_to_draft(conn, config):
    store.set_setting(conn, "reply_delay_max", "0")  # 测试不等随机延迟

    async def failing_sender(text: str) -> bool:
        return False

    result = await process_message(
        conn, config, incoming("发动机多少钱", channel="clawbot"), sender=failing_sender
    )
    assert result.send_failed
    assert result.draft_id is not None
    row = conn.execute(
        "SELECT needs_attention FROM conversations WHERE id=?", (result.conversation_id,)
    ).fetchone()
    assert row["needs_attention"] == 1


@pytest.mark.anyio
async def test_chitchat_creates_no_inquiry(conn, config):
    result = await process_message(conn, config, incoming("好的谢谢"))
    assert result.inquiry_id is None
    assert conn.execute("SELECT COUNT(*) FROM inquiries").fetchone()[0] == 0
