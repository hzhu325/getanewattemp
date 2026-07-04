"""启动 PartsPilot：python run.py"""

import uvicorn

from partspilot.api.app import create_app
from partspilot.config import get_config


def main() -> None:
    config = get_config()
    print(f"PartsPilot 汽配智能工作台  →  http://{config.host}:{config.port}")
    if not config.admin_password:
        print("提示：未设置 PARTSPILOT_PASSWORD，管理后台无需登录（仅建议本机使用）")
    uvicorn.run(create_app(config), host=config.host, port=config.port, log_level="info")


if __name__ == "__main__":
    main()
