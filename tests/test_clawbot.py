"""ClawBot 通道测试：用 httpx.MockTransport 仿真 ilink 服务器，全程离线。"""

import json

import httpx
import pytest

from partspilot.channels.clawbot import ClawBotChannel, ClawBotClient


def make_client(handler) -> ClawBotClient:
    return ClawBotClient(
        "https://ilinkai.weixin.qq.com",
        poll_timeout=1,
        transport=httpx.MockTransport(handler),
    )


class TestBindingFlow:
    @pytest.mark.anyio
    async def test_qrcode_and_token(self, tmp_path):
        def handler(request: httpx.Request) -> httpx.Response:
            if "get_bot_qrcode" in request.url.path:
                return httpx.Response(200, json={"data": {"qrcode_url": "https://wx.qq.com/qr/abc", "session_id": "s1"}})
            if "get_qrcode_status" in request.url.path:
                body = json.loads(request.content)
                assert "s1" in body.values()
                return httpx.Response(200, json={"status": "confirmed", "bot_token": "tok123"})
            raise AssertionError(f"unexpected {request.url}")

        channel = ClawBotChannel(make_client(handler), tmp_path / "account.json")
        qr = await channel.start_binding()
        assert qr["qrcode"] == "https://wx.qq.com/qr/abc"
        assert channel.state == "awaiting_scan"

        result = await channel.poll_binding()
        assert result["bot_token"] == "tok123"
        assert channel.state == "connected"
        # 凭据落盘，重启后自动恢复
        restored = ClawBotChannel(make_client(handler), tmp_path / "account.json")
        assert restored.bot_token == "tok123"
        assert restored.state == "connected"

    @pytest.mark.anyio
    async def test_unbind_removes_credentials(self, tmp_path):
        path = tmp_path / "account.json"
        path.write_text(json.dumps({"bot_token": "x", "offset": 5}))
        channel = ClawBotChannel(make_client(lambda r: httpx.Response(200, json={})), path)
        assert channel.bot_token == "x"
        channel.unbind()
        assert not path.exists()
        assert channel.state == "idle"


class TestUpdates:
    @pytest.mark.anyio
    async def test_get_updates_shapes_and_offset(self):
        """兼容多种响应形态：顶层 updates / data.updates / result。"""
        payloads = [
            {"updates": [{"update_id": 7, "message": {"text": "你好", "from": {"id": "wx1", "nickname": "王师傅"}}, "context_token": "ctx7"}]},
            {"data": {"updates": [{"seq": 8, "text": "在吗", "from_user": "wx2"}]}},
            {"result": [{"msg_id": 9, "content": "波箱多少钱", "sender": {"id": "wx3", "name": "李老板"}}]},
        ]
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            payload = payloads[calls["n"]]
            calls["n"] += 1
            return httpx.Response(200, json=payload)

        client = make_client(handler)
        updates, offset = await client.get_updates("tok", 0)
        assert offset == 8
        msg = client.normalize_update(updates[0])
        assert msg.text == "你好" and msg.external_id == "wx1"
        assert msg.display_name == "王师傅" and msg.context_token == "ctx7"

        updates, offset = await client.get_updates("tok", offset)
        assert offset == 9
        msg = client.normalize_update(updates[0])
        assert msg.text == "在吗" and msg.external_id == "wx2"

        updates, offset = await client.get_updates("tok", offset)
        assert offset == 10
        msg = client.normalize_update(updates[0])
        assert msg.text == "波箱多少钱" and msg.external_id == "wx3"

    def test_normalize_skips_junk(self):
        client = ClawBotClient("https://x")
        assert client.normalize_update({}) is None
        assert client.normalize_update({"text": "hi"}) is None  # 没有发送者
        assert client.normalize_update({"from": {"id": "a"}}) is None  # 没有内容


class TestSendMessage:
    @pytest.mark.anyio
    async def test_send_ok_and_auth_header(self):
        captured = {}

        def handler(request: httpx.Request) -> httpx.Response:
            captured["auth"] = request.headers.get("Authorization")
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"code": 0})

        client = make_client(handler)
        ok = await client.send_message("tok", "ctx", "您好")
        assert ok
        assert captured["auth"] == "Bearer tok"
        assert captured["body"]["context_token"] == "ctx"
        assert captured["body"]["text"] == "您好"

    @pytest.mark.anyio
    async def test_send_business_failure(self):
        client = make_client(lambda r: httpx.Response(200, json={"code": -1, "error": "invalid token"}))
        assert not await client.send_message("tok", "ctx", "hi")

    @pytest.mark.anyio
    async def test_send_http_failure(self):
        client = make_client(lambda r: httpx.Response(500, text="oops"))
        assert not await client.send_message("tok", "ctx", "hi")
