"""在线 VIN 数据源适配器测试：MockTransport 仿真极速数据/天行数据/17vin。"""

import httpx
import pytest

from partspilot.config import Config
from partspilot.vin import providers

VALID_VIN = "1HGCM82633A004352"


@pytest.fixture
def config():
    c = Config()
    c.vin_provider = ""
    c.jisu_vin_appkey = ""
    c.tianapi_key = ""
    c.seventeen_vin_user = ""
    c.seventeen_vin_password = ""
    return c


@pytest.fixture
def transport():
    """测试结束后清理注入的 transport。"""
    yield
    providers.TRANSPORT = None


def mock_response(payload):
    providers.TRANSPORT = httpx.MockTransport(lambda r: httpx.Response(200, json=payload))


class TestJisuapi:
    @pytest.mark.anyio
    async def test_success_mapping(self, config, transport):
        config.jisu_vin_appkey = "k"
        mock_response({
            "status": 0, "msg": "ok",
            "result": {"brand": "大众", "name": "迈腾", "typename": "迈腾 380TSI 尊贵型",
                       "yeartype": "2018", "displacement": "2.0", "engine": "EA888 DKX",
                       "gearbox": "7挡双离合", "manufacturer": "一汽-大众"},
        })
        result = await providers.decode_vin(VALID_VIN, config)
        assert result["source"] == "jisuapi"
        assert result["brand"] == "大众"
        assert result["engine_model"] == "EA888 DKX"
        assert result["gearbox_model"] == "7挡双离合"

    @pytest.mark.anyio
    async def test_business_failure_falls_back_offline(self, config, transport):
        config.jisu_vin_appkey = "k"
        mock_response({"status": 210, "msg": "无信息"})
        result = await providers.decode_vin(VALID_VIN, config)
        assert result["source"] == "offline"
        assert any("jisuapi" in n for n in result["notes"])

    @pytest.mark.anyio
    async def test_network_failure_falls_back_offline(self, config, transport):
        config.jisu_vin_appkey = "k"
        providers.TRANSPORT = httpx.MockTransport(
            lambda r: (_ for _ in ()).throw(httpx.ConnectError("boom"))
        )
        result = await providers.decode_vin(VALID_VIN, config)
        assert result["source"] == "offline"
        assert result["valid"]  # 离线结果照常可用


class TestTianapi:
    @pytest.mark.anyio
    async def test_success_mapping_with_gear_combine(self, config, transport):
        config.tianapi_key = "k"
        mock_response({
            "code": 200, "msg": "success",
            "result": {"brandname": "本田", "modelname": "雅阁", "carline": "雅阁",
                       "year": "2019", "displacement": "1.5", "engine": "L15BL",
                       "geartype": "CVT", "gearsnum": "", "manufacturer": "广汽本田"},
        })
        result = await providers.decode_vin(VALID_VIN, config)
        assert result["source"] == "tianapi"
        assert result["brand"] == "本田"
        assert result["engine_model"] == "L15BL"
        assert result["gearbox_model"] == "CVT"

    @pytest.mark.anyio
    async def test_error_code(self, config, transport):
        config.tianapi_key = "k"
        mock_response({"code": 250, "msg": "数据返回为空"})
        result = await providers.decode_vin(VALID_VIN, config)
        assert result["source"] == "offline"


class TestSeventeenVin:
    @pytest.mark.anyio
    async def test_success_mapping(self, config, transport):
        config.seventeen_vin_user = "u"
        config.seventeen_vin_password = "p"
        mock_response({
            "code": 1, "msg": "success",
            "data": {"model_list": [{"Brand": "宝马", "Model": "5系", "Model_year": "2020",
                                     "Cc": "2.0T", "Engine_no": "B48B20", "Factory": "华晨宝马"}]},
        })
        result = await providers.decode_vin(VALID_VIN, config)
        assert result["source"] == "17vin"
        assert result["engine_model"] == "B48B20"


class TestProviderSelection:
    def test_auto_order_prefers_cheapest(self, config):
        config.jisu_vin_appkey = "a"
        config.tianapi_key = "b"
        config.seventeen_vin_user = "u"
        config.seventeen_vin_password = "p"
        assert providers.pick_provider(config)[0] == "jisuapi"

    def test_forced_provider(self, config):
        config.jisu_vin_appkey = "a"
        config.tianapi_key = "b"
        config.vin_provider = "tianapi"
        assert providers.pick_provider(config)[0] == "tianapi"

    def test_forced_but_unconfigured_returns_none(self, config):
        config.jisu_vin_appkey = "a"
        config.vin_provider = "17vin"
        assert providers.pick_provider(config) is None

    def test_nothing_configured(self, config):
        assert providers.pick_provider(config) is None
