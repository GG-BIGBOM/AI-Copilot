"""FastAPI 装配。

线上形态：nginx 收 80/443，`/api` 反代到本进程（127.0.0.1:8000），
`/` 直接吐前端 `out/` 的静态文件。所以这里只管 `/api/*`，不伺候静态资源。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from copilot.api import providers
from copilot.api.routes import auth as auth_routes
from copilot.api.routes import chat as chat_routes
from copilot.auth.security import ensure_production_ready
from copilot.config import get_settings
from copilot.db.session import engine

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    yield
    # 关停时收干净：httpx 连接池、数据库连接池
    providers.close_all()
    await engine.dispose()


def create_app() -> FastAPI:
    s = get_settings()
    # 顶着占位密钥对外服务 = 任何人都能自己签一个登录态。宁可起不来
    ensure_production_ready()

    app = FastAPI(
        title="知识库 Agent API",
        version="0.1.0",
        lifespan=lifespan,
        # 线上关掉交互式文档：这是个邀请码制的内部工具，接口清单不必公开
        docs_url="/api/docs" if not s.cookie_secure else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if not s.cookie_secure else None,
    )

    # 本地开发前端在 3000、后端在 8000，跨源。
    # 带 cookie 的跨源请求要求 allow_credentials=True，而这与 allow_origins=["*"]
    # 互斥（浏览器规范），所以必须逐个列出来源——不能图省事写星号。
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    app.include_router(auth_routes.router)
    app.include_router(chat_routes.router)

    @app.get("/api/health", tags=["ops"])
    async def health() -> dict[str, str]:
        """给 systemd / nginx / 上线后手动排查用。不碰数据库，永远秒回。"""
        return {"status": "ok"}

    return app


# uvicorn 的 --reload 和 systemd 都按 "copilot.api.app:app" 找它
app = create_app()
