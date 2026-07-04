"""消息分析：品类识别、字段提取、意图标签、优先级评分。

纯规则实现（词典 + 正则），毫秒级、零外部依赖——适合跑在办公本上。
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

from partspilot.nlu import dictionaries as d
from partspilot.vin.extractor import VinCandidate, extract_vins

_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*(?:年|款)?(?!\d)")
_DISPLACEMENT_RE = re.compile(r"(?<![\d.])(\d\.\d)\s*([TtLl])?(?![\d.])")
_NAMED_VALUE_RE = {
    "engine_model": re.compile(r"发动机(?:型号|号)?\s*[:：]?\s*([A-Za-z][A-Za-z0-9\-/_.]{1,15})"),
    "gearbox_model": re.compile(r"变速箱(?:型号|号)?\s*[:：]?\s*([A-Za-z0-9][A-Za-z0-9\-/_.]{1,15})"),
}


@dataclass
class Analysis:
    part_type: str = "unknown"          # engine / gearbox / accessory / unknown
    matched_keywords: list[str] = field(default_factory=list)
    intents: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)   # 中文标签，给界面看
    fields: dict = field(default_factory=dict)      # brand/model/year/displacement/engine_model/gearbox_model
    vins: list[dict] = field(default_factory=list)  # VinCandidate 序列化
    missing_fields: list[str] = field(default_factory=list)
    priority: int = 0
    urgent: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _detect_part_type(text: str) -> tuple[str, list[str]]:
    best_type, best_hits = "unknown", []
    for part_type, keywords in d.PART_KEYWORDS.items():
        hits = [kw for kw in keywords if kw.lower() in text.lower()]
        if len(hits) > len(best_hits):
            best_type, best_hits = part_type, hits
    return best_type, best_hits


def _detect_intents(text: str) -> list[str]:
    intents = []
    for intent, keywords in d.INTENT_KEYWORDS.items():
        if any(kw in text for kw in keywords):
            intents.append(intent)
    return intents


def _extract_fields(text: str) -> dict:
    fields: dict[str, str] = {}
    for brand in d.BRANDS:
        if brand.lower() in text.lower():
            fields["brand"] = brand
            break
    for model in d.MODELS:
        if model.lower() in text.lower():
            fields["model"] = model
            break
    if match := _YEAR_RE.search(text):
        fields["year"] = match.group(1)
    if match := _DISPLACEMENT_RE.search(text):
        fields["displacement"] = match.group(1) + (match.group(2) or "L").upper()
    for key, pattern in _NAMED_VALUE_RE.items():
        if match := pattern.search(text):
            fields[key] = match.group(1).upper()
    return fields


def _score(part_type: str, intents: list[str], vins: list[VinCandidate], msg_type: str, urgent: bool) -> int:
    score = 0
    if vins:
        score += 3
    if part_type != "unknown":
        score += 3
    if "inquiry" in intents:
        score += 2
    if "urge" in intents:
        score += 2
    if "aftersale" in intents:
        score += 2
    if msg_type == "image":
        score += 1
    if urgent:
        score += 1
    return score


def analyze(text: str, msg_type: str = "text") -> Analysis:
    text = (text or "").strip()
    part_type, matched_keywords = _detect_part_type(text)
    intents = _detect_intents(text)
    vins = extract_vins(text)
    fields = _extract_fields(text)
    urgent = any(kw in text for kw in d.URGENT_KEYWORDS)

    if vins:
        fields["vin"] = vins[0].vin

    # 缺件清单：识别到品类才有意义
    missing = []
    if part_type != "unknown":
        required = d.REQUIRED_FIELDS_BY_TYPE[part_type]
        missing = [f for f in required if not fields.get(f)]

    tags = []
    if part_type != "unknown":
        tags.append(d.PART_TYPE_LABELS[part_type])
    if vins:
        tags.append("VIN")
    tags += [d.INTENT_LABELS[i] for i in intents]
    if urgent:
        tags.append("急")
    if not tags:
        tags.append("闲聊")

    return Analysis(
        part_type=part_type,
        matched_keywords=matched_keywords,
        intents=intents,
        tags=tags,
        fields=fields,
        vins=[
            {
                "vin": c.vin,
                "check_digit_ok": c.check_digit_ok,
                "corrected_from": c.corrected_from,
                "notes": c.notes,
            }
            for c in vins
        ],
        missing_fields=missing,
        priority=_score(part_type, intents, vins, msg_type, urgent),
        urgent=urgent,
    )
