"""VIN 解码编排：本地离线解码打底 + 可插拔在线数据源增强。

支持的在线数据源（配了哪家用哪家，可用 VIN_PROVIDER 强制指定）：

| 名称     | 环境变量                                  | 价格参考（2026-07）              |
|----------|-------------------------------------------|----------------------------------|
| jisuapi  | JISU_VIN_APPKEY                           | 送100次，¥450/万次（≈4.5分/次）  |
| tianapi  | TIANAPI_KEY                               | 送5次，¥10≈150次（≈6.5分/次）    |
| 17vin    | SEVENTEEN_VIN_USER / SEVENTEEN_VIN_PASSWORD | 需一次性开户（约¥3000）        |

所有数据源失败都降级为离线结果，绝不阻塞消息管线。
"""

from __future__ import annotations

import hashlib
import logging

import httpx

from partspilot.config import Config
from partspilot.vin.validator import check_digit_ok, decode_year, is_valid_vin_format
from partspilot.vin.wmi import lookup_wmi

logger = logging.getLogger(__name__)

# 测试注入用：设为 httpx.MockTransport 可离线仿真所有数据源
TRANSPORT: httpx.AsyncBaseTransport | None = None

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


def _pick(value) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    return ""


async def _get_json(url: str, params: dict, timeout: float) -> dict | None:
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=TRANSPORT) as client:
            response = await client.get(url, params=params)
            response.raise_for_status()
            return response.json()
    except Exception as exc:
        logger.warning("VIN 在线查询请求失败 %s: %s", url, exc)
        return None


# ─────────────────── 极速数据 jisuapi.com ───────────────────


async def _decode_jisuapi(vin: str, config: Config) -> dict | None:
    payload = await _get_json(
        config.jisu_vin_url,
        {"appkey": config.jisu_vin_appkey, "vin": vin},
        config.vin_online_timeout,
    )
    if not payload:
        return None
    if payload.get("status") not in (0, "0"):
        logger.warning("jisuapi 业务失败: status=%s msg=%s", payload.get("status"), payload.get("msg"))
        return None
    r = payload.get("result") or {}
    return {
        "fields": {
            "brand": _pick(r.get("brand")),
            "model": _pick(r.get("name")),
            "series": _pick(r.get("typename")),
            "year": _pick(r.get("yeartype")),
            "displacement": _pick(r.get("displacement")),
            "engine_model": _pick(r.get("engine")),
            "gearbox_model": _pick(r.get("gearbox")),
            "manufacturer": _pick(r.get("manufacturer")),
        },
        "notes": [],
    }


# ─────────────────── 天行数据 tianapi.com ───────────────────


async def _decode_tianapi(vin: str, config: Config) -> dict | None:
    payload = await _get_json(
        config.tianapi_vin_url,
        {"key": config.tianapi_key, "vincode": vin},
        config.vin_online_timeout,
    )
    if not payload:
        return None
    if payload.get("code") != 200:
        logger.warning("tianapi 业务失败: code=%s msg=%s", payload.get("code"), payload.get("msg"))
        return None
    r = payload.get("result") or {}
    gearbox = _pick(r.get("geartype"))
    gears = _pick(r.get("gearsnum"))
    if gearbox and gears:
        gearbox = f"{gears}挡{gearbox}"
    return {
        "fields": {
            "brand": _pick(r.get("brandname")),
            "model": _pick(r.get("modelname")),
            "series": _pick(r.get("carline")) or _pick(r.get("salename")),
            "year": _pick(r.get("year")) or _pick(r.get("madeyear")),
            "displacement": _pick(r.get("displacement")),
            "engine_model": _pick(r.get("engine")),
            "gearbox_model": gearbox,
            "manufacturer": _pick(r.get("manufacturer")),
        },
        "notes": [],
    }


# ─────────────────── 17vin.com（3001 接口） ───────────────────


def _md5(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest()


def build_17vin_token(user: str, password: str, vin: str) -> str:
    return _md5(f"{_md5(user)}{_md5(password)}/?vin={vin}")


async def _decode_17vin(vin: str, config: Config) -> dict | None:
    payload = await _get_json(
        config.seventeen_vin_base_url,
        {
            "vin": vin,
            "user": config.seventeen_vin_user,
            "token": build_17vin_token(config.seventeen_vin_user, config.seventeen_vin_password, vin),
        },
        config.vin_online_timeout,
    )
    if not payload:
        return None
    if payload.get("code") != 1:
        logger.warning("17vin 业务失败: code=%s msg=%s", payload.get("code"), payload.get("msg"))
        return None
    data = payload.get("data") or {}
    model_list = data.get("model_list") or []
    if not isinstance(model_list, list) or not model_list:
        return None
    primary = model_list[0] or {}
    notes = []
    if len(model_list) > 1:
        notes.append(f"17vin 返回 {len(model_list)} 个候选车型，已取第一个，建议人工确认")
    return {
        "fields": {
            "brand": _pick(primary.get("Brand")),
            "model": _pick(primary.get("Model")),
            "series": _pick(primary.get("Series")),
            "year": _pick(primary.get("Model_year")),
            "displacement": _pick(primary.get("Cc")),
            "engine_model": _pick(primary.get("Engine_no")),
            "gearbox_model": _pick(primary.get("Transmission_detail")),
            "manufacturer": _pick(primary.get("Factory")),
        },
        "notes": notes,
    }


# ─────────────────── 编排 ───────────────────

# 注册顺序即自动选择的优先级（便宜/免费额度多的在前）
_REGISTRY = [
    ("jisuapi", lambda c: bool(c.jisu_vin_appkey), _decode_jisuapi),
    ("tianapi", lambda c: bool(c.tianapi_key), _decode_tianapi),
    ("17vin", lambda c: bool(c.seventeen_vin_user and c.seventeen_vin_password), _decode_17vin),
]


def pick_provider(config: Config) -> tuple[str, object] | None:
    """选在线数据源：VIN_PROVIDER 强制指定，否则按注册顺序取第一个已配置的。"""
    if config.vin_provider:
        for name, configured, fn in _REGISTRY:
            if name == config.vin_provider:
                return (name, fn) if configured(config) else None
        return None
    for name, configured, fn in _REGISTRY:
        if configured(config):
            return name, fn
    return None


async def decode_vin(vin: str, config: Config, mock: bool = False) -> dict:
    """完整解码：离线打底 + 在线增强。永不抛异常。"""
    result = decode_offline(vin)
    if not result["valid"]:
        return result

    if mock:
        result.update({k: v for k, v in _MOCK_FIELDS.items() if not result.get(k)})
        result["source"] = "mock"
        result["notes"].append("演示数据（未接真实在线数据源）")
        return result

    picked = pick_provider(config)
    if picked:
        name, decoder = picked
        online = await decoder(vin, config)
        if online:
            for key, value in online["fields"].items():
                if value:
                    result[key] = value
            result["notes"].extend(online["notes"])
            result["source"] = name
        else:
            result["notes"].append(f"在线数据源（{name}）查询失败，以下为本地解码结果")
    return result
