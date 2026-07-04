"""从自由文本中提取 VIN 候选。

微信里客户发 VIN 的真实形态：夹在中文句子里、带空格或横线分段、
全角字符、手抄铭牌导致的 O/0、I/1、Q/0 混淆。策略：

1. 归一化：全角→半角、转大写
2. 扫描字母数字连续段（允许段内空格/横线/点分隔），拼接后按 17 位滑窗
3. 含 I/O/Q 的候选做混淆纠错（I→1, O→0, Q→0），仅当纠错后校验位通过才采纳
4. 去重，校验位通过的优先
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from partspilot.vin.validator import check_digit_ok, is_valid_vin_format

# 字母数字段，段内允许被空格/横线/点打断（最多连续 2 个分隔符）
_RUN_PATTERN = re.compile(r"[A-Z0-9](?:[ \-.]{0,2}[A-Z0-9]){10,}")

_CONFUSION_MAP = str.maketrans({"I": "1", "O": "0", "Q": "0"})


@dataclass
class VinCandidate:
    vin: str
    check_digit_ok: bool
    corrected_from: str = ""
    notes: list[str] = field(default_factory=list)


def _normalize(text: str) -> str:
    out = []
    for ch in text:
        code = ord(ch)
        if 0xFF01 <= code <= 0xFF5E:  # 全角 → 半角
            ch = chr(code - 0xFEE0)
        elif ch == "　":
            ch = " "
        out.append(ch)
    return "".join(out).upper()


def _windows(run: str) -> list[str]:
    """对拼接后的连续段取所有 17 位窗口；恰好 17 位就是它本身。"""
    if len(run) < 17:
        return []
    return [run[i : i + 17] for i in range(len(run) - 16)]


def extract_vins(text: str) -> list[VinCandidate]:
    normalized = _normalize(text)
    seen: set[str] = set()
    exact: list[VinCandidate] = []
    loose: list[VinCandidate] = []

    for match in _RUN_PATTERN.finditer(normalized):
        run = re.sub(r"[ \-.]", "", match.group())
        had_separator = len(run) != len(match.group())
        for window in _windows(run):
            candidate = _build_candidate(window)
            if candidate is None or candidate.vin in seen:
                continue
            # 长段滑窗（正文数字流里凑出来的）只有校验位通过才可信；
            # 恰好 17 位的独立段则始终采纳
            is_exact_run = len(run) == 17
            if not is_exact_run and not candidate.check_digit_ok:
                continue
            if had_separator and is_exact_run:
                candidate.notes.append("原文含分隔符，已拼接")
            seen.add(candidate.vin)
            (exact if candidate.check_digit_ok else loose).append(candidate)

    return exact + loose


def _build_candidate(window: str) -> VinCandidate | None:
    # 真实 VIN 必含字母（WMI 厂商码），纯数字都是订单号/运单号之类
    if window.isdigit():
        return None
    if is_valid_vin_format(window):
        return VinCandidate(vin=window, check_digit_ok=check_digit_ok(window))

    # 含 I/O/Q → 尝试混淆纠错，只有纠错后校验位通过才认，防止误纠
    if re.fullmatch(r"[A-Z0-9]{17}", window) and any(c in window for c in "IOQ"):
        repaired = window.translate(_CONFUSION_MAP)
        if is_valid_vin_format(repaired) and check_digit_ok(repaired):
            return VinCandidate(
                vin=repaired,
                check_digit_ok=True,
                corrected_from=window,
                notes=[f"已自动纠正易混字符（{window} → {repaired}）"],
            )
    return None
