"""微信小龙虾（ClawBot / ilink）直连适配器。

协议（2026-07 调研结论，详见 docs/RESEARCH.md）：
    GET/POST https://ilinkai.weixin.qq.com/ilink/bot/get_bot_qrcode    → 绑定二维码
    GET/POST .../ilink/bot/get_qrcode_status                           → 长轮询扫码结果 → bot_token
    GET/POST .../ilink/bot/getupdates                                  → 长轮询收消息
    POST     .../ilink/bot/sendmessage                                 → 发消息（context_token 关联会话）

官方消息体的确切字段未完全公开，因此：
    - 端点路径全部可配置（settings 表 clawbot_* 键覆盖默认值）
    - 响应解析走"候选路径"容错策略，实际绑定后如有出入只需调整配置
    - 所有原始收发包记录 debug 日志，便于对照修正
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx

from partspilot.channels.base import IncomingMessage

logger = logging.getLogger(__name__)

DEFAULT_ENDPOINTS = {
    "qrcode": "/ilink/bot/get_bot_qrcode",
    "qrcode_status": "/ilink/bot/get_qrcode_status",
    "get_updates": "/ilink/bot/getupdates",
    "send_message": "/ilink/bot/sendmessage",
}


def _dig(data, *paths, default=None):
    """按候选路径取值：_dig(d, "a.b", "c") 依次尝试，取到第一个非空值。"""
    for path in paths:
        node = data
        ok = True
        for key in path.split("."):
            if isinstance(node, dict) and key in node:
                node = node[key]
            else:
                ok = False
                break
        if ok and node not in (None, ""):
            return node
    return default


class ClawBotClient:
    """ilink HTTP 客户端（无状态，凭据由调用方管理）。"""

    def __init__(
        self,
        base_url: str,
        endpoints: dict | None = None,
        poll_timeout: float = 50,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.endpoints = {**DEFAULT_ENDPOINTS, **(endpoints or {})}
        self.poll_timeout = poll_timeout
        self._transport = transport

    def _client(self, timeout: float) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url, timeout=timeout, transport=self._transport
        )

    async def get_qrcode(self) -> dict:
        async with self._client(15) as client:
            response = await client.post(self.endpoints["qrcode"], json={})
            response.raise_for_status()
            payload = response.json()
        logger.debug("clawbot get_qrcode: %s", payload)
        return {
            "qrcode": _dig(payload, "qrcode", "data.qrcode", "qrcode_url", "data.qrcode_url", "data.url", default=""),
            "session": _dig(payload, "session", "data.session", "session_id", "data.session_id", "qrcode_id", "data.qrcode_id", default=""),
            "raw": payload,
        }

    async def poll_qrcode_status(self, session: str) -> dict:
        async with self._client(self.poll_timeout + 10) as client:
            response = await client.post(
                self.endpoints["qrcode_status"],
                json={"session": session, "session_id": session, "qrcode_id": session},
            )
            response.raise_for_status()
            payload = response.json()
        logger.debug("clawbot qrcode_status: %s", payload)
        return {
            "status": str(_dig(payload, "status", "data.status", "state", "data.state", default="pending")),
            "bot_token": _dig(payload, "bot_token", "data.bot_token", "token", "data.token", default=""),
            "raw": payload,
        }

    async def get_updates(self, bot_token: str, offset: int = 0) -> tuple[list[dict], int]:
        """长轮询收消息。返回 (updates, next_offset)。"""
        async with self._client(self.poll_timeout + 10) as client:
            response = await client.post(
                self.endpoints["get_updates"],
                headers={"Authorization": f"Bearer {bot_token}"},
                json={"bot_token": bot_token, "offset": offset, "timeout": int(self.poll_timeout)},
            )
            response.raise_for_status()
            payload = response.json()

        updates = _dig(payload, "updates", "data.updates", "result", "data.list", default=[])
        if not isinstance(updates, list):
            updates = []
        next_offset = offset
        for update in updates:
            update_id = _dig(update, "update_id", "seq", "msg_id", "id", default=None)
            try:
                next_offset = max(next_offset, int(update_id) + 1)
            except (TypeError, ValueError):
                pass
        return updates, next_offset

    async def send_message(self, bot_token: str, context_token: str, text: str) -> bool:
        async with self._client(15) as client:
            response = await client.post(
                self.endpoints["send_message"],
                headers={"Authorization": f"Bearer {bot_token}"},
                json={"bot_token": bot_token, "context_token": context_token, "text": text},
            )
        if response.status_code != 200:
            logger.warning("clawbot sendmessage HTTP %s: %s", response.status_code, response.text[:300])
            return False
        try:
            payload = response.json()
        except json.JSONDecodeError:
            return True  # 200 无 JSON 视为成功
        code = _dig(payload, "code", "errcode", default=0)
        ok = code in (0, 1, "0", "1", None) and not _dig(payload, "error", default=None)
        if not ok:
            logger.warning("clawbot sendmessage 业务失败: %s", payload)
        return ok

    @staticmethod
    def normalize_update(update: dict) -> IncomingMessage | None:
        """把 ilink update 归一化为 IncomingMessage；无文本内容返回 None。"""
        text = _dig(update, "message.text", "text", "content", "message.content", default="")
        msg_type = str(_dig(update, "message.type", "msg_type", "type", default="text"))
        if not text and msg_type in ("", "text"):
            return None
        external_id = str(
            _dig(update, "from.id", "from_user", "sender.id", "contact_id", "openid",
                 "message.from.id", default="")
        )
        if not external_id:
            return None
        name = str(
            _dig(update, "from.name", "from.nickname", "sender.name", "sender.nickname",
                 "message.from.name", "message.from.nickname", "nickname", default="")
        )
        context_token = str(_dig(update, "context_token", "message.context_token", default=""))
        return IncomingMessage(
            channel="clawbot",
            external_id=external_id,
            display_name=name,
            chat_type="private",  # ClawBot 目前仅开放私聊
            msg_type=msg_type or "text",
            text=str(text),
            context_token=context_token,
            raw=update,
        )


class ClawBotChannel:
    """通道状态机 + 后台长轮询任务。凭据落盘 data/clawbot_account.json。"""

    def __init__(self, client: ClawBotClient, credentials_path: Path):
        self.client = client
        self.credentials_path = credentials_path
        self.state = "idle"          # idle / awaiting_scan / connected / error
        self.error = ""
        self.bot_token = ""
        self._offset = 0
        self._session = ""
        self._task: asyncio.Task | None = None
        self._load_credentials()

    def _load_credentials(self) -> None:
        if self.credentials_path.exists():
            try:
                data = json.loads(self.credentials_path.read_text(encoding="utf-8"))
                self.bot_token = data.get("bot_token", "")
                self._offset = int(data.get("offset", 0))
                if self.bot_token:
                    self.state = "connected"
            except (json.JSONDecodeError, ValueError):
                logger.warning("clawbot 凭据文件损坏，忽略")

    def _save_credentials(self) -> None:
        self.credentials_path.parent.mkdir(parents=True, exist_ok=True)
        self.credentials_path.write_text(
            json.dumps({"bot_token": self.bot_token, "offset": self._offset}),
            encoding="utf-8",
        )

    def status(self) -> dict:
        return {"state": self.state, "error": self.error, "bound": bool(self.bot_token)}

    async def start_binding(self) -> dict:
        result = await self.client.get_qrcode()
        self._session = result["session"]
        self.state = "awaiting_scan"
        self.error = ""
        return result

    async def poll_binding(self) -> dict:
        if not self._session:
            return {"status": "no_session"}
        result = await self.client.poll_qrcode_status(self._session)
        if result["bot_token"]:
            self.bot_token = result["bot_token"]
            self._offset = 0
            self.state = "connected"
            self._save_credentials()
        return result

    def unbind(self) -> None:
        self.bot_token = ""
        self.state = "idle"
        if self.credentials_path.exists():
            self.credentials_path.unlink()

    async def run(self, handle_message) -> None:
        """长轮询主循环。handle_message(incoming, sender) 由装配层注入。"""
        backoff = 2
        while True:
            if not self.bot_token:
                await asyncio.sleep(3)
                continue
            try:
                updates, self._offset = await self.client.get_updates(self.bot_token, self._offset)
                self._save_credentials()
                backoff = 2
                self.state = "connected"
                for update in updates:
                    incoming = self.client.normalize_update(update)
                    if incoming is None:
                        continue

                    async def sender(text: str, _token=incoming.context_token) -> bool:
                        return await self.client.send_message(self.bot_token, _token, text)

                    try:
                        await handle_message(incoming, sender)
                    except Exception:
                        logger.exception("clawbot 消息处理失败: %s", incoming)
            except asyncio.CancelledError:
                raise
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code in (401, 403):
                    self.state = "error"
                    self.error = "bot_token 失效，请重新扫码绑定"
                    logger.error("clawbot 认证失败，停止轮询等待重新绑定")
                    self.bot_token = ""
                else:
                    self.state = "error"
                    self.error = f"HTTP {exc.response.status_code}"
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
            except Exception as exc:
                self.state = "error"
                self.error = str(exc)[:200]
                logger.warning("clawbot 轮询异常: %s（%ss 后重试）", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
