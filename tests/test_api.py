"""REST API 测试：TestClient 全栈（含鉴权、webhook、草稿流转）。"""

import pytest
from fastapi.testclient import TestClient

from partspilot import db
from partspilot.api.app import create_app
from partspilot.config import Config
from partspilot.services import store

VALID_VIN = "1HGCM82633A004352"


def make_client(tmp_path, password=""):
    config = Config()
    config.data_dir = tmp_path
    config.admin_password = password
    config.seventeen_vin_user = ""
    config.clawbot_enabled = False
    app = create_app(config)
    return TestClient(app), config


@pytest.fixture
def client(tmp_path):
    test_client, config = make_client(tmp_path)
    with test_client:
        # 测试与真实时钟解耦：关掉静默时段
        test_client.put("/api/settings", json={"quiet_start": "", "quiet_end": ""})
        yield test_client


def get_webhook_token(client) -> str:
    return client.get("/api/settings").json()["webhook_token"]


class TestSimulatorFlow:
    def test_simulator_full_trace(self, client):
        response = client.post(
            "/api/simulator/message",
            json={"name": "李老板", "text": f"要迈腾发动机，车架号{VALID_VIN}，多少钱"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["decision"]["action"] == "send"
        assert data["analysis"]["part_type"] == "engine"
        assert data["vin_decode"]["valid"] is True
        assert data["inquiry_id"] is not None

    def test_dashboard_reflects_activity(self, client):
        client.post("/api/simulator/message", json={"text": "波箱多少钱", "chat_type": "group"})
        summary = client.get("/api/dashboard/summary").json()
        assert summary["today_incoming"] >= 1
        assert summary["pending_drafts"] >= 1
        assert summary["attention_count"] >= 1
        assert len(summary["daily"]) == 14


class TestConversations:
    def test_list_and_read(self, client):
        client.post("/api/simulator/message", json={"name": "张三", "text": "发动机有吗"})
        conversations = client.get("/api/conversations").json()
        assert len(conversations) == 1
        cid = conversations[0]["id"]

        detail = client.get(f"/api/conversations/{cid}/messages").json()
        assert len(detail["messages"]) == 2  # 进 + 自动回

        assert client.post(f"/api/conversations/{cid}/read").json()["ok"]
        assert client.get("/api/conversations?filter=attention").json() == []

    def test_mode_switch_off(self, client):
        client.post("/api/simulator/message", json={"name": "张三", "text": "发动机有吗"})
        cid = client.get("/api/conversations").json()[0]["id"]
        client.post(f"/api/conversations/{cid}/mode", json={"mode": "off"})
        result = client.post(
            "/api/simulator/message", json={"name": "张三", "text": "变速箱呢？多少钱"}
        ).json()
        assert result["decision"]["action"] == "none"

    def test_manual_send_recorded(self, client):
        client.post("/api/simulator/message", json={"name": "张三", "text": "在吗"})
        cid = client.get("/api/conversations").json()[0]["id"]
        response = client.post(f"/api/conversations/{cid}/send", json={"text": "好的，马上看"})
        assert response.status_code == 200
        detail = client.get(f"/api/conversations/{cid}/messages").json()
        assert detail["messages"][-1]["content"] == "好的，马上看"


class TestDrafts:
    def test_draft_edit_send_flow(self, client):
        client.post(
            "/api/simulator/message",
            json={"text": "迈腾发动机多少钱", "chat_type": "group", "group_name": "配件群"},
        )
        drafts = client.get("/api/drafts").json()
        assert len(drafts) == 1
        draft_id = drafts[0]["id"]

        client.put(f"/api/drafts/{draft_id}", json={"content": "改过的回复【内部备注】不发这段"})
        assert client.post(f"/api/drafts/{draft_id}/send").json()["ok"]
        assert client.get("/api/drafts").json() == []
        # 已发送的消息剥掉了内部备注
        conversations = client.get("/api/conversations").json()
        detail = client.get(f"/api/conversations/{conversations[0]['id']}/messages").json()
        assert detail["messages"][-1]["content"] == "改过的回复"

    def test_discard(self, client):
        client.post("/api/simulator/message", json={"text": "波箱价格", "chat_type": "group"})
        draft_id = client.get("/api/drafts").json()[0]["id"]
        assert client.post(f"/api/drafts/{draft_id}/discard").json()["ok"]
        assert client.post(f"/api/drafts/{draft_id}/discard").status_code == 404


class TestInventoryAndRules:
    def test_inventory_crud(self, client):
        body = {
            "part_type": "engine", "display_name": "迈腾EA888发动机", "brand": "大众",
            "vehicle_model": "迈腾", "displacement": "2.0T", "engine_model": "CUF",
            "internal_code": "E001", "price": 8500,
        }
        item_id = client.post("/api/inventory", json=body).json()["id"]
        assert client.post("/api/inventory", json=body).status_code == 409  # 编号重复

        assert len(client.get("/api/inventory?q=迈腾").json()) == 1
        client.post(f"/api/inventory/{item_id}/status", json={"status": "sold"})
        assert client.get("/api/inventory?status=in_stock").json() == []

    def test_rule_crud_and_test_endpoint(self, client):
        rule = {
            "name": "地址", "kind": "keyword", "pattern": "地址|在哪",
            "template": "汽配城3区18号", "priority": 10,
        }
        rule_id = client.post("/api/rules", json=rule).json()["id"]
        preview = client.post("/api/rules/test", json={"text": "你们店在哪"}).json()
        assert preview["decision"]["text"] == "汽配城3区18号"

        assert client.post(
            "/api/rules", json={**rule, "kind": "regex", "pattern": "[无效("}
        ).status_code == 400

        client.delete(f"/api/rules/{rule_id}")
        assert client.get("/api/rules").json() == []


class TestVinApi:
    def test_decode_and_history(self, client):
        result = client.post("/api/vin/decode", json={"vin": VALID_VIN.lower()}).json()
        assert result["valid"] is True
        assert result["check_digit_ok"] is True
        history = client.get("/api/vin/history").json()
        assert history[0]["vin"] == VALID_VIN


class TestWebhook:
    def test_requires_token(self, client):
        response = client.post(
            "/api/channels/webhook/incoming",
            json={"external_id": "wx1", "text": "发动机多少钱"},
        )
        assert response.status_code == 401

    def test_sync_reply_and_outbox(self, client):
        token = get_webhook_token(client)
        headers = {"X-Webhook-Token": token}
        response = client.post(
            "/api/channels/webhook/incoming",
            json={"external_id": "wx1", "name": "王师傅", "text": "发动机多少钱"},
            headers=headers,
        )
        assert response.status_code == 200
        assert response.json()["reply"]  # 私聊自动回复同步返回

        # 群消息 → draft → reply 为 null
        group = client.post(
            "/api/channels/webhook/incoming",
            json={"external_id": "wx1", "name": "王师傅", "text": "波箱多少钱",
                  "chat_type": "group", "group_name": "群A"},
            headers=headers,
        ).json()
        assert group["reply"] is None

        # 后台把群草稿发出去 → 进 outbox 供桥接方拉取
        draft_id = client.get("/api/drafts").json()[0]["id"]
        client.post(f"/api/drafts/{draft_id}/send")
        outbox = client.get("/api/channels/webhook/outbox", headers=headers).json()
        assert len(outbox) == 1
        client.post(f"/api/channels/webhook/outbox/{outbox[0]['id']}/ack", headers=headers)
        assert client.get("/api/channels/webhook/outbox", headers=headers).json() == []


class TestAuth:
    def test_login_required_when_password_set(self, tmp_path):
        test_client, _ = make_client(tmp_path, password="s3cret")
        with test_client as client:
            assert client.get("/api/dashboard/summary").status_code == 401
            status = client.get("/api/auth/status").json()
            assert status["auth_required"] and not status["logged_in"]

            assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
            assert client.post("/api/auth/login", json={"password": "s3cret"}).json()["ok"]
            assert client.get("/api/dashboard/summary").status_code == 200

            # webhook 不受登录影响（有自己的 token）
            fresh, _ = make_client(tmp_path, password="s3cret")
            with fresh:
                response = fresh.post(
                    "/api/channels/webhook/incoming",
                    json={"external_id": "x", "text": "hi"},
                )
                assert response.status_code == 401  # token 错，而不是 401 登录


def test_settings_roundtrip(tmp_path):
    test_client, _ = make_client(tmp_path)
    with test_client as client:
        client.put("/api/settings", json={"shop_name": "老周汽配", "group_reply_mode": "off"})
        settings = client.get("/api/settings").json()
        assert settings["shop_name"] == "老周汽配"
        assert settings["group_reply_mode"] == "off"
        # 未知键被忽略
        client.put("/api/settings", json={"evil_key": "x"})
        assert "evil_key" not in client.get("/api/settings").json()
