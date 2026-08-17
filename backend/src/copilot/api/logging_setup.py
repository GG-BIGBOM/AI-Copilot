"""日志与错误上报。

**这个项目的「错误上报」就是 journald，不接 Sentry。** 理由：
1.6GB 的机器上再挂一个常驻 agent 不划算，而单机单进程的服务，
`journalctl -u copilot-api` 已经能看到全部信息——真正缺的不是采集端，
是「日志里有没有足够的信息定位问题」。所以这里的重点全在后者：

1. **每个请求一个 request id**，回给客户端（`X-Request-Id`）。用户截个图报错，
   凭那串 id 能在 journal 里直接捞到那一次的完整堆栈。没有它，
   排查就得靠时间戳去猜是哪一条。
2. **未捕获异常一定落日志，且只回一句人话。** FastAPI 默认会把堆栈
   写到 stderr 并返回 500，但响应体里什么都没有；而我们的前端会把错误正文
   直接渲染出来——把内部细节（更别说密钥相关的报错）送到浏览器是不行的。
3. **慢请求单独打一行 WARNING。** 一次问答本来就要几秒到几十秒，
   混在 access log 里根本看不出「这次特别慢」。

⚠️ 不打 access log（uvicorn 起了 `--no-access-log`）：SSE 一个流一条记录，
既刷屏又没信息量。真要追流量看 nginx 的日志。
"""

from __future__ import annotations

import logging
import os
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger("copilot")

# 超过这个秒数的请求打一行 WARNING。一次带检索的问答通常 3–15s，
# 30s 以上基本意味着某个外部依赖在拖后腿
SLOW_SECONDS = 30.0

GENERIC_500 = "服务端出错了，请稍后重试。"


def configure_logging() -> None:
    """进程级日志配置。systemd 会把 stdout/stderr 收进 journald，所以只管格式。

    级别从 `COPILOT_LOG_LEVEL` 读，默认 INFO。**不用 .env**：日志级别是
    「出事时想临时调一下」的东西，改 .env 得重启服务才生效，而
    `systemctl set-environment` + restart 也一样要重启——那就用最直白的环境变量。
    """
    level = os.getenv("COPILOT_LOG_LEVEL", "INFO").upper()
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    # httpx 每次调用都打一行 INFO（"HTTP Request: POST ... 200 OK"）。
    # 一次问答要打三四行，把真正的日志淹掉了
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)


def install(app: FastAPI) -> None:
    """挂上 request id 中间件和兜底异常处理。"""

    @app.middleware("http")
    async def request_context(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request.state.request_id = rid
        started = time.monotonic()
        try:
            response = await call_next(request)
        except Exception:
            # 兜底：中间件这一层再抓一次，保证「有堆栈、有 request id」。
            # 不 re-raise——让它冒到 uvicorn 只会得到一个没有 id 的 500
            logger.exception(
                "未捕获异常 rid=%s %s %s", rid, request.method, request.url.path
            )
            return JSONResponse(
                {"detail": GENERIC_500, "request_id": rid},
                status_code=500,
                headers={"X-Request-Id": rid},
            )

        took = time.monotonic() - started
        response.headers["X-Request-Id"] = rid
        if took > SLOW_SECONDS:
            logger.warning(
                "慢请求 rid=%s %s %s 耗时 %.1fs", rid, request.method, request.url.path, took
            )
        elif response.status_code >= 500:
            logger.error(
                "服务端错误 rid=%s %s %s -> %s",
                rid,
                request.method,
                request.url.path,
                response.status_code,
            )
        return response
