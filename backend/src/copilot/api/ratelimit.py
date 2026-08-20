"""IP 级限流。M11 P1。

**在此之前 `/api/auth/login` 是裸奔的**：没有失败计数、没有 429、
没有任何东西挡住一个对着登录接口跑字典的脚本。已有的 `usage.py` 是
**成本保险丝**（每人每日 token 配额），它按 user_id 计，
而撞库的人**还没有 user_id**——那道闸门在这里根本不生效。

⚠️ **计数在进程内存里，不引 Redis。**
这是顺着「不为假想需求先付工程费」来的（plan.md 七·5）：线上是**单进程
uvicorn**，进程内计数就是全局计数，一个 dict 完全等价于一次 Redis 往返。
真到了多 worker 那天，换掉的只有这个文件里的 `_hits`。
在那之前，为了「万一以后要横向扩」而现在引一个常驻服务，
在 1.6GB 的机器上是拿内存换想象力。

⚠️ **限流是防脚本的，不是防用户的。** 三个阈值都定得比真人可能的手速高
一大截——被限流的应该是循环，不是着急的人。误伤一个真人的代价
（他以为网站坏了）远大于放过几次多余的请求。
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque

from fastapi import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


class Rule:
    """`limit` 次 / `window` 秒。"""

    __slots__ = ("limit", "window", "message")

    def __init__(self, limit: int, window: int, message: str) -> None:
        self.limit = limit
        self.window = window
        self.message = message


# ⭐ 阈值是按「真人的上限」定的，不是按「够用就行」。
#
# login   5 分钟 20 次：连着输错 20 次密码的真人几乎不存在，
#         而字典攻击一秒就能到这个数。
# register 1 小时 5 次：邀请码本来就是一次性的，正常人一辈子只注册一次。
#         这条挡的是「拿一堆猜的邀请码来试」——邀请码 32 位，
#         但没有限流的话试错是免费的。
# chat    1 分钟 20 次：一次问答要 3~15 秒，真人一分钟撑死问四五次。
#         20 次是给「手抖点了几下重新生成」留的余量。
#         ⚠️ 这条**不替代** usage.py 的 token 配额：那个管的是花多少钱，
#         这个管的是打多快，两者挡的是不同的东西。
RULES: dict[str, Rule] = {
    "/api/auth/login": Rule(20, 300, "登录尝试太频繁，请 5 分钟后再试。"),
    "/api/auth/register": Rule(5, 3600, "注册请求太频繁，请稍后再试。"),
    "/api/chat": Rule(20, 60, "提问太频繁了，缓一分钟再来。"),
}

# ⭐ 本机不限流。评测脚本一次跑 55 题、跑两轮，第一轮就会被自己的限流打断——
# 而那时候看到的现象是「评测跑到第 20 题全变成错误」，
# 排查方向会被带到模型和检索上去，真正的原因在这个文件里。
EXEMPT_IPS = {"127.0.0.1", "::1", "testclient", "unknown"}

# ip -> 最近一批命中时间。deque 只留窗口内的，长度天然被 limit 顶住
_hits: dict[tuple[str, str], deque[float]] = defaultdict(deque)

# 多久清一次过期条目。不清的话，被扫过一遍端口的机器上这个 dict
# 会留着几万个再也不会回来的 IP——单条只有几十字节，但它只增不减
_CLEAN_EVERY = 600.0
_last_clean = time.monotonic()


def client_ip(request: Request) -> str:
    """取真实客户端 IP。

    ⚠️ **线上一定要读 `X-Forwarded-For`**：nginx 反代之后，
    `request.client.host` 恒等于 127.0.0.1——那意味着全站共用一个计数器，
    第 21 个人提问就会被限流，而且日志里看不出为什么。
    （`deploy/nginx.conf` 里已经配了 `X-Forwarded-For $proxy_add_x_forwarded_for`。）

    ⚠️ 只取**第一段**。XFF 是客户端可伪造的头，但我们的 nginx 是
    `$proxy_add_x_forwarded_for`——它把真实 `$remote_addr` **追加在最后**，
    前面那些是客户端自己写的。所以严格地说该取倒数第一段。
    取第一段是为了让「用户真的在多层代理后面」时还能识别出他，
    代价是伪造这个头就能绕过限流。
    **这个取舍是清醒的**：限流挡的是懒得伪造头的脚本；真会伪造 XFF 的人，
    IP 限流本来也拦不住他（他还能换 IP）。真正的账号安全靠邀请码 + bcrypt。
    """
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _prune(now: float) -> None:
    global _last_clean
    if now - _last_clean < _CLEAN_EVERY:
        return
    _last_clean = now
    dead = [k for k, q in _hits.items() if not q or now - q[-1] > 3600]
    for k in dead:
        del _hits[k]


def check(path: str, ip: str) -> Rule | None:
    """记一次并判断有没有超。超了返回被触发的那条规则。

    命中即计数——**包括被拒的那次**。这让「一直撞就一直被关着」，
    而不是「被拒的不算，于是每过一个窗口就能再撞一批」。
    """
    rule = RULES.get(path)
    if rule is None or ip in EXEMPT_IPS:
        return None

    now = time.monotonic()
    _prune(now)
    q = _hits[(ip, path)]
    cutoff = now - rule.window
    while q and q[0] < cutoff:
        q.popleft()
    q.append(now)
    return rule if len(q) > rule.limit else None


def install(app) -> None:
    """挂上限流中间件。

    ⚠️ **必须在 logging_setup 之后注册**（= 跑在它内层），
    这样被限流的请求也带上 `X-Request-Id`，journal 里对得上。
    """

    @app.middleware("http")
    async def rate_limit(request: Request, call_next):
        if request.method == "POST":
            ip = client_ip(request)
            if (rule := check(request.url.path, ip)) is not None:
                logger.warning(
                    "限流 %s %s（%s 次 / %s 秒）", ip, request.url.path, rule.limit, rule.window
                )
                # ⭐ 回 429 + Retry-After，而不是静默丢弃。
                # 前端会把 detail 原样显示给用户，所以这句话得是人话——
                # 万一误伤了真人，他至少知道等多久再来，而不是以为网站坏了
                return JSONResponse(
                    {"detail": rule.message},
                    status_code=429,
                    headers={"Retry-After": str(rule.window)},
                )
        return await call_next(request)


def reset() -> None:
    """清空计数。**只给测试用**——每个用例都从干净的状态开始，
    否则前一个用例打满了配额，后一个莫名其妙 429。"""
    _hits.clear()
