"""可选的管理后台登录。

设了 PARTSPILOT_PASSWORD → 启用登录，签发 HMAC 签名 cookie；
没设 → 不鉴权（默认只监听 127.0.0.1，界面会提示）。
Webhook 接口走自己的 X-Webhook-Token，不受此影响。
"""

from __future__ import annotations

import hashlib
import hmac

COOKIE_NAME = "pp_session"

# 无需登录的路径前缀
PUBLIC_PATHS = (
    "/api/auth/login",
    "/api/auth/status",
    "/api/channels/webhook/",
)


def session_token(secret: bytes) -> str:
    return hmac.new(secret, b"partspilot-session-v1", hashlib.sha256).hexdigest()


def verify_password(expected: str, provided: str) -> bool:
    return hmac.compare_digest(expected.encode(), provided.encode())


def verify_session(secret: bytes, cookie_value: str | None) -> bool:
    if not cookie_value:
        return False
    return hmac.compare_digest(session_token(secret), cookie_value)
