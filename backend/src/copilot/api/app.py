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
from fastapi.staticfiles import StaticFiles

from copilot.api import logging_setup, providers, ratelimit
from copilot.api.routes import admin as admin_routes
from copilot.api.routes import answer_corrections as answer_correction_routes
from copilot.api.routes import auth as auth_routes
from copilot.api.routes import chat as chat_routes
from copilot.api.routes import corrections as corrections_routes
from copilot.api.routes import docs as docs_routes
from copilot.api.routes import feedback as feedback_routes
from copilot.api.routes import images as images_routes
from copilot.api.routes import invites as invites_routes
from copilot.api.routes import spaces as spaces_routes
from copilot.api.routes import verified as verified_routes
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
    logging_setup.configure_logging()

    app = FastAPI(
        title="知识库 Agent API",
        version="0.1.0",
        lifespan=lifespan,
        # 线上关掉交互式文档：这是个邀请码制的内部工具，接口清单不必公开
        docs_url="/api/docs" if not s.cookie_secure else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if not s.cookie_secure else None,
    )

    # ⚠️ 中间件的执行顺序和注册顺序**相反**：后注册的在外层。
    # request id 要先挂，CORS 后挂——这样 CORS 在最外层，
    # 连中间件自己抛出的 500 都会被加上 CORS 头，否则浏览器只报一句
    # "CORS error"，真正的错误信息看不到（本地开发时尤其坑）
    #
    # 三层从内到外：限流 → request id → CORS。
    # ⭐ 限流在最内层是有意的：被 429 掉的请求**也要**带上 X-Request-Id
    # （靠外面那层加）和 CORS 头（靠最外层加）。反过来的话，
    # 前端收到的 429 会因为缺 CORS 头而变成一句 "CORS error"，
    # 用户看到的是「网站坏了」而不是「你点太快了」。
    ratelimit.install(app)
    logging_setup.install(app)

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
    app.include_router(docs_routes.router)
    app.include_router(corrections_routes.router)
    app.include_router(answer_correction_routes.router)
    app.include_router(invites_routes.router)
    app.include_router(verified_routes.router)
    app.include_router(feedback_routes.router)
    app.include_router(spaces_routes.router)
    app.include_router(images_routes.router)
    app.include_router(admin_routes.router)

    # 镜像下来的语雀配图。**只有公共图走这里。**
    # 私有图（M17 从上传文档里解出来的）一律走 `/api/images/{id}`，
    # 由后端逐次校验 owner——这个 mount 是没有鉴权的，别往里塞私有内容。
    #
    # ⚠️ 线上由 **nginx** 直接发（`location /images/ { alias .../data/images/; }`），
    # 根本到不了这里；挂在这儿是给本地开发用的——前端在 3000、后端在 8000，
    # 图片地址拼上 API_BASE 就能取到。1.6GB 的机器上让 Python 发静态文件是浪费。
    s.image_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/images", StaticFiles(directory=s.image_dir), name="images")

    @app.get("/api/health", tags=["ops"])
    async def health() -> dict[str, str]:
        """给 systemd / nginx / 上线后手动排查用。不碰数据库，永远秒回。"""
        return {"status": "ok"}

    return app


# uvicorn 的 --reload 和 systemd 都按 "copilot.api.app:app" 找它
app = create_app()
