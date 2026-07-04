"""客服话术模板。

话术来自实际业务（旧项目沉淀），口吻：礼貌、专业、不过度承诺。
原则：自动发送的话术绝不包含价格和库存数量——那些只进草稿。
"""

from __future__ import annotations

from partspilot.nlu.dictionaries import FIELD_LABELS, PART_TYPE_LABELS

_CATEGORY_SUFFIX = {
    "engine": "发齐后我先帮您核对发动机匹配情况，再回复您有没有货、成色、价格、质保和发货时效。",
    "gearbox": "发齐后我先帮您核对变速箱匹配情况，再回复您库存、成色、价格、质保和发货时间。",
    "accessory": "发齐后我先按旧件信息帮您核对匹配，再回复您库存、成色、价格、质保和发货时间。",
}

_PHOTO_HINT = {
    "engine": "如果方便，发动机铭牌或旧件整体照片拍一张，核对更快。",
    "gearbox": "如果方便，变速箱铭牌或旧件照片拍一张，核对更快。",
    "accessory": "如果方便，旧件铭牌或件号照片拍一张，核对更快。",
}


def render_welcome(shop_name: str) -> str:
    prefix = f"您好，这里是{shop_name}。" if shop_name else "您好。"
    return (
        f"{prefix}需要配件麻烦发一下：配件名称 + 品牌车型 + 年份排量 + 车辆VIN 17位编码，"
        "或者直接拍旧件铭牌照片，我核对后回复您库存和价格。"
    )


def render_category_reply(part_type: str, missing_fields: list[str]) -> str:
    label = PART_TYPE_LABELS.get(part_type, "配件")
    if not missing_fields:
        return (
            f"您好，{label}的信息收到了，这边先帮您核对匹配情况，"
            "确认后回复您库存、成色、价格、质保和发货时效。"
        )
    missing = "、".join(FIELD_LABELS[f] for f in missing_fields)
    suffix = _CATEGORY_SUFFIX.get(part_type, "")
    photo = _PHOTO_HINT.get(part_type, "")
    return f"您好，这边可以帮您核对，麻烦再补充一下：{missing}。{photo}{suffix}"


def render_vin_reply(vin: str, decode: dict, corrected_from: str = "") -> str:
    parts = []
    if corrected_from:
        parts.append(f"已识别到车架号 {vin}（原文 {corrected_from}，已自动纠正易混字符，请顺手确认一下）。")
    else:
        parts.append(f"已识别到车架号 {vin}。")

    vehicle_bits = [
        decode.get("brand") or decode.get("manufacturer") or "",
        decode.get("model") or "",
        f"{decode['year']}款" if decode.get("year") else "",
        decode.get("displacement") or "",
    ]
    vehicle = " ".join(b for b in vehicle_bits if b)
    detail_bits = []
    if decode.get("engine_model"):
        detail_bits.append(f"发动机 {decode['engine_model']}")
    if decode.get("gearbox_model"):
        detail_bits.append(f"变速箱 {decode['gearbox_model']}")

    if vehicle:
        line = f"查到车型：{vehicle}"
        if detail_bits:
            line += "，" + "、".join(detail_bits)
        parts.append(line + "。")
        parts.append("请问您需要哪个配件？我帮您核对库存后回复。")
    else:
        parts.append("这边帮您核对具体配置，确认后回复您。请问需要哪个配件？")
    return "".join(parts)


def render_urge_reply() -> str:
    return "收到，这边马上帮您核实进度，稍后回复您，请稍等。"


def render_aftersale_reply() -> str:
    return "收到，师傅看到后会尽快联系您处理。麻烦先把问题拍个照片或视频发过来，方便这边判断。"


def render_inventory_note(matches: list[dict]) -> str:
    """库存核对摘要——只进草稿，绝不自动发送（涉及报价）。"""
    if not matches:
        return "\n\n【内部备注·勿直接发送】库存未匹配到现货，需人工确认。"
    lines = ["\n\n【内部备注·勿直接发送】库存匹配到：\n"]
    for m in matches[:3]:
        price = f"，参考价 {m['price']:g} 元" if m.get("price") else ""
        lines.append(f"· {m['display_name']}（编号 {m['internal_code']}{price}）\n")
    return "".join(lines).rstrip()
