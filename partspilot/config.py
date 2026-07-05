"""运行配置。

环境变量（部署机密、启动参数）+ settings 表（后台可改的业务策略）两层。
本模块只管前者；后者见 services/store.py 的 get_setting/set_setting。
"""

from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(_env("PARTSPILOT_DATA_DIR") or BASE_DIR / "data"))
    host: str = field(default_factory=lambda: _env("PARTSPILOT_HOST", "127.0.0.1"))
    port: int = field(default_factory=lambda: int(_env("PARTSPILOT_PORT", "8704")))
    # 管理后台密码；留空 = 不启用登录（仅建议本机使用）
    admin_password: str = field(default_factory=lambda: _env("PARTSPILOT_PASSWORD"))

    # 在线 VIN 解码数据源（可选，配哪家用哪家；VIN_PROVIDER 可强制指定）
    vin_provider: str = field(default_factory=lambda: _env("VIN_PROVIDER"))
    vin_online_timeout: float = field(default_factory=lambda: float(_env("VIN_ONLINE_TIMEOUT", "8")))
    # 极速数据 jisuapi.com（送100次，¥450/万次）
    jisu_vin_appkey: str = field(default_factory=lambda: _env("JISU_VIN_APPKEY"))
    jisu_vin_url: str = field(
        default_factory=lambda: _env("JISU_VIN_URL", "https://api.jisuapi.com/vin/query")
    )
    # 天行数据 tianapi.com（送5次，约6.5分/次）
    tianapi_key: str = field(default_factory=lambda: _env("TIANAPI_KEY"))
    tianapi_vin_url: str = field(
        default_factory=lambda: _env("TIANAPI_VIN_URL", "https://apis.tianapi.com/chavin/index")
    )
    # 17vin.com（需一次性开户）
    seventeen_vin_user: str = field(default_factory=lambda: _env("SEVENTEEN_VIN_USER"))
    seventeen_vin_password: str = field(default_factory=lambda: _env("SEVENTEEN_VIN_PASSWORD"))
    seventeen_vin_base_url: str = field(
        default_factory=lambda: _env("SEVENTEEN_VIN_BASE_URL", "http://api.17vin.com:8080/")
    )

    # 微信小龙虾（ClawBot / ilink）直连通道（可选）
    clawbot_enabled: bool = field(default_factory=lambda: _env("CLAWBOT_ENABLED", "0") == "1")
    clawbot_base_url: str = field(
        default_factory=lambda: _env("CLAWBOT_BASE_URL", "https://ilinkai.weixin.qq.com")
    )
    clawbot_poll_timeout: float = field(default_factory=lambda: float(_env("CLAWBOT_POLL_TIMEOUT", "50")))

    @property
    def clawbot_endpoints(self) -> dict:
        """ilink 端点路径覆盖（官方字段如有出入，改环境变量即可，不用改代码）。"""
        overrides = {}
        for key, env_name in {
            "qrcode": "CLAWBOT_EP_QRCODE",
            "qrcode_status": "CLAWBOT_EP_QRCODE_STATUS",
            "get_updates": "CLAWBOT_EP_GET_UPDATES",
            "send_message": "CLAWBOT_EP_SEND_MESSAGE",
        }.items():
            if value := _env(env_name):
                overrides[key] = value
        return overrides

    @property
    def db_path(self) -> Path:
        return self.data_dir / "partspilot.db"

    @property
    def backup_dir(self) -> Path:
        return self.data_dir / "backups"

    @property
    def clawbot_credentials_path(self) -> Path:
        return self.data_dir / "clawbot_account.json"

    @property
    def secret_path(self) -> Path:
        return self.data_dir / "secret_key"

    def load_secret(self) -> bytes:
        """会话签名密钥：首次生成后落盘复用。"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.secret_path.exists():
            return self.secret_path.read_bytes()
        secret = secrets.token_bytes(32)
        self.secret_path.write_bytes(secret)
        return secret


_config: Config | None = None


def get_config() -> Config:
    global _config
    if _config is None:
        _config = Config()
    return _config


def reset_config() -> None:
    """测试用：让下一次 get_config() 重新读环境变量。"""
    global _config
    _config = None
