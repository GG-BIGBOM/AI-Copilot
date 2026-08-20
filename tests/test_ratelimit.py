"""IP 级限流（M11 P1）。

在此之前 `/api/auth/login` 是**裸奔**的：没有失败计数、没有 429、
没有任何东西挡住一个对着登录接口跑字典的脚本。已有的 `usage.py` 是
按 user_id 记的成本保险丝，而撞库的人还没有 user_id——那道闸门根本不生效。

这一组题量的是三件事，缺一件限流就等于没做：
    1. 超了真的会 429（验收标准第 2 条）
    2. 本机不被误伤（评测脚本一次跑 55 题，被自己的限流打断会极难排查）
    3. 429 也带 X-Request-Id（被拒的请求同样要能在 journal 里对上号）
"""

from __future__ import annotations

import pytest

from copilot.api import ratelimit


@pytest.fixture(autouse=True)
def clean_counters():
    """每个用例都从零开始。

    ⚠️ 计数在**进程内存**里（单进程 uvicorn，一个 dict 就等于全局计数），
    所以用例之间会互相污染：上一个打满了配额，下一个莫名其妙 429。
    """
    ratelimit.reset()
    yield
    ratelimit.reset()


def test_under_the_limit_passes():
    rule = ratelimit.RULES["/api/auth/login"]
    for _ in range(rule.limit):
        assert ratelimit.check("/api/auth/login", "203.0.113.7") is None


def test_over_the_limit_is_caught():
    rule = ratelimit.RULES["/api/auth/login"]
    for _ in range(rule.limit):
        ratelimit.check("/api/auth/login", "203.0.113.7")
    hit = ratelimit.check("/api/auth/login", "203.0.113.7")
    assert hit is not None
    assert "5 分钟" in hit.message, "给用户看的必须是人话，他要知道等多久"


def test_being_blocked_still_counts():
    """⭐ 被拒的那一次**也要计数**。

    不计的话就成了「撞满 → 被拒 → 拒的不算 → 每个窗口都能再撞一批」，
    一个死循环的脚本会稳定地拿到 limit 次/窗口，而不是被关在外面。
    """
    rule = ratelimit.RULES["/api/auth/login"]
    for _ in range(rule.limit * 3):
        ratelimit.check("/api/auth/login", "203.0.113.9")
    assert ratelimit.check("/api/auth/login", "203.0.113.9") is not None


def test_different_ips_have_their_own_budget():
    rule = ratelimit.RULES["/api/auth/login"]
    for _ in range(rule.limit + 5):
        ratelimit.check("/api/auth/login", "198.51.100.1")
    assert ratelimit.check("/api/auth/login", "198.51.100.2") is None, "别人不该被连坐"


def test_paths_do_not_share_a_budget():
    """登录打满了，提问不该跟着被关。两条路的阈值本来就差一个数量级。"""
    for _ in range(ratelimit.RULES["/api/auth/login"].limit + 5):
        ratelimit.check("/api/auth/login", "198.51.100.3")
    assert ratelimit.check("/api/chat", "198.51.100.3") is None


def test_localhost_is_never_limited():
    """⭐ 评测脚本一次跑 55 题、跑两轮，全从本机打。

    误伤它的表现是「评测跑到第 20 题开始全变成错误」，而排查方向会被带到
    模型和检索上去——真正的原因在限流表里。
    """
    for _ in range(500):
        assert ratelimit.check("/api/chat", "127.0.0.1") is None


def test_unlisted_paths_are_untouched():
    """没列进 RULES 的接口一概不限。**限流是点名制，不是默认制**——
    默认限流的话，某天加了个新接口就会莫名其妙地被自己挡住。"""
    for _ in range(1000):
        assert ratelimit.check("/api/conversations", "203.0.113.50") is None


def test_client_ip_prefers_forwarded_header():
    """⚠️ 线上一定要读 XFF。

    nginx 反代之后 `request.client.host` 恒等于 127.0.0.1——那意味着
    全站共用一个计数器（而且正好落在豁免名单里，等于限流整个失效）。
    """
    from types import SimpleNamespace

    req = SimpleNamespace(
        headers={"x-forwarded-for": "203.0.113.11, 10.0.0.1"},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    assert ratelimit.client_ip(req) == "203.0.113.11"

    bare = SimpleNamespace(headers={}, client=SimpleNamespace(host="203.0.113.12"))
    assert ratelimit.client_ip(bare) == "203.0.113.12"


# ---------- 打到真接口上 ----------


async def test_login_returns_429_after_too_many_tries(api_client, monkeypatch):
    """验收标准第 2 条：脚本连打 /api/auth/login → 429。

    ⚠️ 得先把本机豁免掉，否则 ASGI 测试客户端的 IP（"testclient"）在
    豁免名单里，这道题永远绿——**绿得毫无意义**。
    """
    monkeypatch.setattr(ratelimit, "EXEMPT_IPS", set())
    rule = ratelimit.RULES["/api/auth/login"]

    codes = []
    for _ in range(rule.limit + 2):
        r = await api_client.post(
            "/api/auth/login", json={"email": "nobody@test.local", "password": "wrong-pass-1"}
        )
        codes.append(r.status_code)

    assert 429 in codes, f"打了 {len(codes)} 次一个 429 都没有：{codes}"
    assert codes[0] == 401, "前几次该照常走认证逻辑（密码错 → 401）"

    blocked = await api_client.post(
        "/api/auth/login", json={"email": "nobody@test.local", "password": "wrong-pass-1"}
    )
    assert blocked.status_code == 429
    assert blocked.headers.get("Retry-After"), "得告诉客户端等多久"
    # 被拒的请求也要能在 journal 里对上号
    assert blocked.headers.get("X-Request-Id"), "429 也必须带 request id"


async def test_get_requests_are_not_rate_limited(api_client, monkeypatch):
    """只拦 POST。GET 打的是读接口，本来就有登录态挡着，
    而误伤一次列表刷新会让整个页面看起来坏了。"""
    monkeypatch.setattr(ratelimit, "EXEMPT_IPS", set())
    for _ in range(ratelimit.RULES["/api/chat"].limit + 5):
        r = await api_client.get("/api/health")
        assert r.status_code == 200
