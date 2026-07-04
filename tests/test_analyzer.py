"""NLU 分析器测试：真实话术输入。"""

from partspilot.nlu.analyzer import analyze


def test_engine_inquiry_full_info():
    a = analyze("你好，要一个大众迈腾2018年2.0T的发动机，发动机型号CUF，多少钱")
    assert a.part_type == "engine"
    assert "inquiry" in a.intents
    assert a.fields["brand"] == "大众"
    assert a.fields["model"] == "迈腾"
    assert a.fields["year"] == "2018"
    assert a.fields["displacement"] == "2.0T"
    assert a.fields["engine_model"] == "CUF"
    assert "vin" in a.missing_fields
    assert a.priority >= 5  # 品类(3)+询价(2)


def test_gearbox_keywords():
    a = analyze("波箱有没有？CVT的")
    assert a.part_type == "gearbox"
    assert "询价" in a.tags or "inquiry" in a.intents


def test_urge_intent():
    a = analyze("师傅发货了吗？到哪了，急用")
    assert "urge" in a.intents
    assert a.urgent
    assert "催单" in a.tags
    assert "急" in a.tags


def test_chitchat_low_priority():
    a = analyze("哈哈好的，谢谢啊")
    assert a.part_type == "unknown"
    assert a.priority == 0
    assert a.tags == ["闲聊"]


def test_vin_in_message_sets_field():
    a = analyze("车架号1HGCM82633A004352，缸盖有吗")
    assert a.fields["vin"] == "1HGCM82633A004352"
    assert a.part_type == "accessory"
    assert "VIN" in a.tags
    assert a.priority >= 6


def test_aftersale_intent():
    a = analyze("上次买的机头有异响，漏油了")
    assert "aftersale" in a.intents
    assert a.part_type == "engine"


def test_displacement_variants():
    assert analyze("1.6L自吸的").fields["displacement"] == "1.6L"
    assert analyze("排量2.0T").fields["displacement"] == "2.0T"
    assert analyze("3.0 机械增压").fields["displacement"] == "3.0L"


def test_year_with_kuan():
    assert analyze("2016款途观").fields["year"] == "2016"
    assert analyze("16年的不要").fields.get("year") is None  # 两位年份不猜
