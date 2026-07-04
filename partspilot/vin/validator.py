"""VIN 结构校验：字符集、校验位（ISO 3779 / GB 16735）、年款解码。

注意：校验位在中国国标与北美是强制的，但欧洲车（如部分奔驰/宝马欧规）
不一定满足——所以校验位失败只作为"警告"，不能直接判定 VIN 无效。
"""

from __future__ import annotations

import re
from datetime import date

VIN_LENGTH = 17
# VIN 字符集不含 I O Q
VIN_PATTERN = re.compile(r"^[A-HJ-NPR-Z0-9]{17}$")

# 码值表（ISO 3779）
_TRANSLITERATION = {
    "A": 1, "B": 2, "C": 3, "D": 4, "E": 5, "F": 6, "G": 7, "H": 8,
    "J": 1, "K": 2, "L": 3, "M": 4, "N": 5, "P": 7, "R": 9,
    "S": 2, "T": 3, "U": 4, "V": 5, "W": 6, "X": 7, "Y": 8, "Z": 9,
    **{str(d): d for d in range(10)},
}

# 各位权重，第 9 位（校验位本身）权重为 0
_WEIGHTS = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2]

# 第 10 位年款码，30 年一循环：A=1980/2010, B=1981/2011, ... 9=2009/2039
_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"


def is_valid_vin_format(vin: str) -> bool:
    return bool(VIN_PATTERN.match(vin))


def compute_check_digit(vin: str) -> str:
    total = sum(_TRANSLITERATION[ch] * w for ch, w in zip(vin, _WEIGHTS))
    remainder = total % 11
    return "X" if remainder == 10 else str(remainder)


def check_digit_ok(vin: str) -> bool:
    if not is_valid_vin_format(vin):
        return False
    return vin[8] == compute_check_digit(vin)


def decode_year(vin: str, today: date | None = None) -> list[int]:
    """第 10 位 → 候选年款（30 年循环产生歧义，返回不超过明年的候选，新在前）。"""
    code = vin[9]
    if code not in _YEAR_CODES:
        return []
    base = 1980 + _YEAR_CODES.index(code)
    limit = (today or date.today()).year + 1
    candidates = [year for year in (base, base + 30, base + 60) if year <= limit]
    candidates.sort(reverse=True)
    return candidates
