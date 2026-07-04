"""VIN 引擎测试：校验位、年款、提取、混淆纠错、离线解码。"""

import pytest

from partspilot.config import Config
from partspilot.vin.extractor import extract_vins
from partspilot.vin.providers import build_17vin_token, decode_offline, decode_vin
from partspilot.vin.validator import (
    check_digit_ok,
    compute_check_digit,
    decode_year,
    is_valid_vin_format,
)

# 业界公认的合法样例（本田雅阁，校验位=3，已独立手算验证）
KNOWN_VALID = "1HGCM82633A004352"


def make_valid_vin(base: str) -> str:
    """把任意 17 位骨架的校验位改正确，得到合法 VIN。"""
    assert len(base) == 17
    return base[:8] + compute_check_digit(base) + base[9:]


class TestValidator:
    def test_known_valid_vin(self):
        assert is_valid_vin_format(KNOWN_VALID)
        assert check_digit_ok(KNOWN_VALID)

    def test_constructed_vin_check_digit(self):
        vin = make_valid_vin("LFV3A23C8J3000001")
        assert check_digit_ok(vin)
        # 改动任何一位都应导致校验失败
        tampered = vin[:12] + ("2" if vin[12] != "2" else "3") + vin[13:]
        assert not check_digit_ok(tampered)

    def test_rejects_illegal_chars_and_length(self):
        assert not is_valid_vin_format("1HGCM82633A00435")  # 16 位
        assert not is_valid_vin_format("IHGCM82633A004352")  # 含 I
        assert not is_valid_vin_format("OHGCM82633A004352")  # 含 O
        assert not is_valid_vin_format("QHGCM82633A004352")  # 含 Q

    def test_decode_year(self):
        from datetime import date

        today = date(2026, 7, 4)
        # KNOWN_VALID 第10位是 '3' → 2003（2033 超过明年，被过滤）
        assert decode_year(KNOWN_VALID, today) == [2003]
        # 'A' → 2010 / 1980 两个候选，新的在前
        vin_a = KNOWN_VALID[:9] + "A" + KNOWN_VALID[10:]
        assert decode_year(vin_a, today) == [2010, 1980]


class TestExtractor:
    def test_vin_inside_chinese_sentence(self):
        text = f"师傅你好，我的车架号是{KNOWN_VALID}，帮我查下发动机"
        found = extract_vins(text)
        assert [c.vin for c in found] == [KNOWN_VALID]
        assert found[0].check_digit_ok

    def test_vin_with_spaces_and_dashes(self):
        text = "VIN: 1HGCM826 33A-004352 麻烦看下"
        found = extract_vins(text)
        assert [c.vin for c in found] == [KNOWN_VALID]

    def test_fullwidth_and_lowercase(self):
        text = "车架号１hgcm82633a004352"
        found = extract_vins(text)
        assert [c.vin for c in found] == [KNOWN_VALID]

    def test_confusion_repair_only_when_check_digit_passes(self):
        # O→0、I→1 纠错后恰好校验通过 → 采纳并标注
        confused = KNOWN_VALID.replace("0", "O").replace("1", "I")
        assert confused != KNOWN_VALID
        found = extract_vins(f"车架号 {confused}")
        assert len(found) == 1
        assert found[0].vin == KNOWN_VALID
        assert found[0].corrected_from == confused

    def test_no_false_positive_from_number_streams(self):
        text = "手机13812345678，订单号20260704000123456789，运单9902038475610238845"
        assert extract_vins(text) == []

    def test_two_vins_in_one_message(self):
        second = make_valid_vin("LSVDU2A48C2105789")
        text = f"这两台都要：{KNOWN_VALID} 和 {second}"
        found = [c.vin for c in extract_vins(text)]
        assert KNOWN_VALID in found and second in found
        assert len(found) == 2

    def test_dedupe_same_vin(self):
        text = f"{KNOWN_VALID} 再确认一遍 {KNOWN_VALID}"
        assert len(extract_vins(text)) == 1


class TestOfflineDecode:
    def test_wmi_and_year(self):
        vin = make_valid_vin("LFV3A23C8J3000001")  # 一汽-大众，J=2018
        result = decode_offline(vin)
        assert result["valid"]
        assert result["country"] == "中国"
        assert result["manufacturer"] == "一汽-大众"
        assert result["year"] == "2018"

    def test_check_digit_warning_not_rejection(self):
        vin = "WBA3A23C8J3000001"  # 校验位大概率不对（欧洲车场景）
        result = decode_offline(vin)
        if not result["check_digit_ok"]:
            assert any("校验位" in n for n in result["notes"])
        assert result["valid"]
        assert result["manufacturer"] == "宝马"

    def test_invalid_vin(self):
        result = decode_offline("ABC")
        assert not result["valid"]


class TestDecodeVin:
    @pytest.mark.anyio
    async def test_mock_mode_fills_fields(self):
        result = await decode_vin(KNOWN_VALID, Config(), mock=True)
        assert result["source"] == "mock"
        assert result["engine_model"]

    @pytest.mark.anyio
    async def test_offline_fallback_without_credentials(self):
        config = Config()
        config.seventeen_vin_user = ""
        result = await decode_vin(KNOWN_VALID, config)
        assert result["source"] == "offline"


def test_17vin_token_algorithm():
    # 与旧项目 TS 实现对齐：md5(md5(user)+md5(pass)+"/?vin="+vin)
    token = build_17vin_token("demo", "secret", KNOWN_VALID)
    assert len(token) == 32
    import hashlib

    m = hashlib.md5
    expected = m(
        (
            m(b"demo").hexdigest() + m(b"secret").hexdigest() + f"/?vin={KNOWN_VALID}"
        ).encode()
    ).hexdigest()
    assert token == expected
