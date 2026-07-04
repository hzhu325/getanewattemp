"""FastAPI 应用装配：DB 中间件、可选登录、静态托管、ClawBot 后台轮询。"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from partspilot import db
from partspilot.api import auth
from partspilot.api.routes import router
from partspilot.channels.clawbot import ClawBotChannel, ClawBotClient
from partspilot.config import Config, get_config
from partspilot.services import store
from partspilot.services.pipeline import process_message

logger = logging.getLogger(__name__)

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def create_app(config: Config | None = None) -> FastAPI:
    config = config or get_config()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.init_db(config.db_path)
        conn = db.connect(config.db_path)
        try:
            store.ensure_settings(conn)
        finally:
            conn.close()

        task = None
        if app.state.clawbot is not None:

            async def handle(incoming, sender):
                message_conn = db.connect(config.db_path)
                try:
                    await process_message(message_conn, config, incoming, sender)
                finally:
                    message_conn.close()

            task = asyncio.create_task(app.state.clawbot.run(handle))
            logger.info("ClawBot 通道已启动（状态: %s）", app.state.clawbot.state)

        yield

        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="PartsPilot 汽配智能工作台", lifespan=lifespan)
    app.state.config = config
    app.state.secret = config.load_secret()
    app.state.clawbot = (
        ClawBotChannel(
            ClawBotClient(
                config.clawbot_base_url,
                endpoints=config.clawbot_endpoints,
                poll_timeout=config.clawbot_poll_timeout,
            ),
            config.clawbot_credentials_path,
        )
        if config.clawbot_enabled
        else None
    )

    @app.middleware("http")
    async def db_and_auth(request: Request, call_next):
        path = request.url.path
        if not path.startswith("/api"):
            return await call_next(request)

        # 登录校验（webhook 等公开路径除外）
        if config.admin_password and not path.startswith(auth.PUBLIC_PATHS):
            if not auth.verify_session(
                app.state.secret, request.cookies.get(auth.COOKIE_NAME)
            ):
                return JSONResponse({"detail": "请先登录"}, status_code=401)

        request.state.db = db.connect(config.db_path)
        try:
            return await call_next(request)
        finally:
            request.state.db.close()

    app.include_router(router)
    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    return app
