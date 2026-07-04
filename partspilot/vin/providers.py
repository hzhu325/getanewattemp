"""VIN 解码编排：本地离线解码打底，17vin.com 在线增强（配置后启用）。

17vin 3001 接口（沿用旧项目验证过的协议）：
GET {base}/?vin={vin}&user={user}&token=md5(md5(user)+md5(password)+"/?vin="+vin)
成功: {"code":1,"msg":"success","data":{"model_list":[{Brand,Model,Model_year,
Cc,Engine_no,Transmission_detail,Factory,Gear_num,Series,...}],...}}
"""

from __future__ import annotations

import hashlib
import logging

import httpx

from partspilot.config import Config
from partspilot.vin.validator import check_digit_ok, decode_year, is_valid_vin_format
from partspilot.vin.wmi import lookup_wmi

logger = logging.getLogger(__name__)

# 演示/测试模式的固定样例（PARTSPILOT_VIN_MOCK=1 时命中任意合法 VIN）
_MOCK_FIELDS = {
    "brand": "大众",
    "model": "迈腾",
    "series": "迈腾 B8",
    "displacement": "2.0T",
    "engine_model": "EA888 DKX",
    "gearbox_model": "DQ381 七速湿式双离合",
    "manufacturer": "一汽-大众",
}


def decode_offline(vin: str) -> dict:
    """纯本地解码：合法性、校验位、产地/厂商、候选年款。"""
    vin = vin.upper()
    valid = is_valid_vin_format(vin)
    result = {
        "vin": vin,
        "valid": valid,
        "check_digit_ok": check_digit_ok(vin) if valid else False,
        "source": "offline",
        "country": "",
        "manufacturer": "",
        "brand": "",
        "model": "",
        "series": "",
        "year": "",
        "year_candidates": [],
        "displacement": "",
        "engine_model": "",
        "gearbox_model": "",
        "notes": [],
    }
    if not valid:
        result["notes"].append("不是合法的 17 位 VIN（含非法字符或长度不对）")
        return result

    wmi = lookup_wmi(vin)
    result["country"] = wmi["country"]
    result["manufacturer"] = wmi["manufacturer"]
    years = decode_year(vin)
    result["year_candidates"] = years
    if years:
        result["year"] = str(years[0])
        if len(years) > 1:
            result["notes"].append(f"年款存在 30 年循环歧义，可能为 {'/'.join(map(str, years))}")
    if not result["check_digit_ok"]:
        result["notes"].append("校验位不匹配（欧洲车常见，建议人工核对一遍）")
    return result


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def build_17vin_token(user: str, password: str, vin: str) -> str:
    return _md5(f"{_md5(user)}{_md5(password)}/?vin={vin}")


def _pick(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return ""


async def _decode_17vin(vin: str, config: Config) -> dict | None:
    """调 17vin，失败返回 None（由调用方降级为离线结果）。"""
    params = {
        "vin": vin,
        "user": config.seventeen_vin_user,
        "token": build_17vin_token(config.seventeen_vin_user, config.seventeen_vin_password, vin),
    }
    try:
        async with httpx.AsyncClient(timeout=config.seventeen_vin_timeout) as client:
            response = await client.get(config.seventeen_vin_base_url, params=params)
            response.raise_for_status()
            payload = response.json()
    except Exception as exc:  # 网络/超时/非JSON —— 在线增强失败不阻塞主流程
        logger.warning("17vin 请求失败: %s", exc)
        return None

    if payload.get("code") != 1:
        logger.warning("17vin 业务失败: code=%s msg=%s", payload.get("code"), payload.get("msg"))
        return None

    data = payload.get("data") or {}
    model_list = data.get("model_list") or []
    if not isinstance(model_list, list) or not model_list:
        return None
    primary = model_list[0] or {}

    fields = {
        "brand": _pick(primary.get("Brand")),
        "model": _pick(primary.get("Model")),
        "series": _pick(primary.get("Series")),
        "year": _pick(primary.get("Model_year")),
        "displacement": _pick(primary.get("Cc")),
        "engine_model": _pick(primary.get("Engine_no")),
        "gearbox_model": _pick(primary.get("Transmission_detail")),
        "manufacturer": _pick(primary.get("Factory")),
        "gear_num": _pick(primary.get("Gear_num")),
    }
    notes = []
    if len(model_list) > 1:
        notes.append(f"17vin 返回 {len(model_list)} 个候选车型，已取第一个，建议人工确认")
    return {"fields": fields, "notes": notes}


async def decode_vin(vin: str, config: Config, mock: bool = False) -> dict:
    """完整解码：离线打底 + 在线增强。永不抛异常。"""
    result = decode_offline(vin)
    if not result["valid"]:
        return result

    if mock:
        result.update({k: v for k, v in _MOCK_FIELDS.items() if not result.get(k)})
        result["source"] = "mock"
        result["notes"].append("演示数据（未配置 17vin 账号）")
        return result

    if config.seventeen_vin_user and config.seventeen_vin_password:
        online = await _decode_17vin(vin, config)
        if online:
            for key, value in online["fields"].items():
                if value:
                    result[key] = value
            result["notes"].extend(online["notes"])
            result["source"] = "17vin"
        else:
            result["notes"].append("17vin 在线查询失败，以下为本地解码结果")
    return result
