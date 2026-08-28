"""注册 / 登录 / 登出 的端到端测试（打真实 ASGI + 真实数据库）。

这里守两条线：
    邀请码**一次性**——同一个码注册第二个人必须失败
    未登录**进不去**——/api/chat 和 /api/auth/me 无 cookie 一律 401
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from copilot.auth.invites import create_invite_codes
from copilot.config import get_settings
from copilot.db.models import Conversation, InviteCode, Message, User

PASSWORD = "test-password-2026"


@pytest.fixture
async def invite(maker):
    """发一个邀请码，测试结束连同注册出来的用户一起删掉。"""
    async with maker() as s:
        (code,) = await create_invite_codes(s, 1)

    yield code

    async with maker() as s:
        used_by = (
            await s.execute(select(InviteCode.used_by).where(InviteCode.code == code))
        ).scalar_one_or_none()
        if used_by:
            convs = list(
                (
                    await s.execute(select(Conversation.id).where(Conversation.user_id == used_by))
                ).scalars()
            )
            if convs:
                await s.execute(delete(Message).where(Message.conversation_id.in_(convs)))
                await s.execute(delete(Conversation).where(Conversation.id.in_(convs)))
        await s.execute(delete(InviteCode).where(InviteCode.code == code))
        if used_by:
            await s.execute(delete(User).where(User.id == used_by))
        await s.commit()


def _email() -> str:
    return f"m3-{uuid.uuid4().hex[:10]}@test.local"


# ---------- 注册 ----------


async def test_register_then_logged_in(api_client, invite):
    email = _email()
    r = await api_client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD, "inviteCode": invite},
    )
    assert r.status_code == 201, r.text
    assert r.json()["email"] == email
    assert "password_hash" not in r.text, "响应里泄漏了密码哈希"

    # 注册即登录态，不用再登一次
    assert (await api_client.get("/api/auth/me")).status_code == 200


async def test_auth_cookie_is_httponly(api_client, invite):
    r = await api_client.post(
        "/api/auth/register",
        json={"email": _email(), "password": PASSWORD, "inviteCode": invite},
    )
    raw = r.headers["set-cookie"].lower()
    assert get_settings().cookie_name in raw
    # HttpOnly：JS 读不到，XSS 也偷不走。这是"不放 localStorage"的全部意义
    assert "httponly" in raw
    assert "samesite=lax" in raw


async def test_invite_code_is_single_use(api_client, invite):
    """核心用例：一个码只能换一个账号。"""
    first = await api_client.post(
        "/api/auth/register",
        json={"email": _email(), "password": PASSWORD, "inviteCode": invite},
    )
    assert first.status_code == 201

    second = await api_client.post(
        "/api/auth/register",
        json={"email": _email(), "password": PASSWORD, "inviteCode": invite},
    )
    assert second.status_code == 400, "同一个邀请码注册出了第二个账号"


async def test_invite_code_stays_consumed_after_user_deletion(api_client, invite, maker):
    """⭐⭐ **删掉用户之后，他用过的邀请码不能复活**（M13 P8）。

    `invite_codes.used_by` 是 `ON DELETE SET NULL` —— 删号时数据库会把它清空。
    在 M13 之前，「这个码用过没有」判的正是 `used_by IS NULL`，于是：

        用邀请码注册  →  used_by = 那个人
        删掉那个人    →  used_by 被置回 NULL
        同一个码      →  又能注册了

    一次性的邀请码因为删号而复活，而邀请制是这个站唯一的准入闸门。
    本机库里当时就有 1 个这样的码（16 条有 used_at，只有 15 条有 used_by）。

    判据换成 `used_at` 之后，`used_by` 被清空不再影响任何事。
    """
    first = await api_client.post(
        "/api/auth/register",
        json={"email": _email(), "password": PASSWORD, "inviteCode": invite},
    )
    assert first.status_code == 201
    user_id = uuid.UUID(first.json()["id"])

    # 直接删这个用户 —— 数据库会把 used_by 置空（ON DELETE SET NULL）
    async with maker() as s:
        await s.execute(delete(User).where(User.id == user_id))
        await s.commit()

    async with maker() as s:
        row = (
            await s.execute(select(InviteCode).where(InviteCode.code == invite))
        ).scalar_one()
        assert row.used_by is None, "外键就是 SET NULL，这一列被清空是预期行为"
        assert row.used_at is not None, "**这一列一旦写上就不能被清除**，它才是作废的凭据"

    again = await api_client.post(
        "/api/auth/register",
        json={"email": _email(), "password": PASSWORD, "inviteCode": invite},
    )
    assert again.status_code == 400, "删掉用户之后，他用过的邀请码又能注册了"


async def test_consumed_code_not_offered_as_available(api_client, invite, maker):
    """⚠️ 核销的判据换了，**列表和计数也要跟着换**——否则管理员会看到一批
    已经作废的码还挂在「未使用」里，把它们发出去，对方注册全部失败。

    ⚠️⚠️ **`limit` 由计数推出来，不能写死一个数。**
    `list_unused_codes` 是 `order_by(created_at) + LIMIT`，而夹具刚发的这个码
    是**最新**的那一个——它排在最后。写死 `limit=200` 的话，只要开发库里
    攒够 200 个没用掉的码，这道题就从「偶发红」变成「天天红」，
    而红的原因和它想验的判据毫无关系（2026-08-29 实测：库里 204 个）。

    这和 `c7363af` 修的是同一类事：**断言不能靠开发库的历史撑着。**
    拿 `before`（此刻未用的总数）当上限，等于"全部列出来"，
    库里有多少个历史码都不影响结论。
    """
    from copilot.auth.invites import count_unused_codes, list_unused_codes

    async with maker() as s:
        before = await count_unused_codes(s)
        assert invite in await list_unused_codes(s, limit=before)

    r = await api_client.post(
        "/api/auth/register",
        json={"email": _email(), "password": PASSWORD, "inviteCode": invite},
    )
    assert r.status_code == 201
    user_id = uuid.UUID(r.json()["id"])

    async with maker() as s:
        assert await count_unused_codes(s) == before - 1
        assert invite not in await list_unused_codes(s, limit=before)

    # 删号之后仍然不算「未使用」
    async with maker() as s:
        await s.execute(delete(User).where(User.id == user_id))
        await s.commit()
    async with maker() as s:
        assert await count_unused_codes(s) == before - 1, "删号把作废的码放回了可用池"
        assert invite not in await list_unused_codes(s, limit=before)


async def test_registration_requires_valid_invite(api_client, maker):
    email = _email()
    r = await api_client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD, "inviteCode": "ZZZZ-ZZZZ"},
    )
    assert r.status_code == 400

    # 事务必须整个回滚——留下一个"没用邀请码就存在"的用户是最难发现的漏洞
    async with maker() as s:
        leaked = (
            await s.execute(select(User).where(User.email == email))
        ).scalar_one_or_none()
    assert leaked is None, "邀请码校验失败了，用户却建出来了"


async def test_invite_code_accepts_sloppy_input(api_client, invite):
    """用户从微信里手抄的码：小写、带空格，都得认。"""
    sloppy = f" {invite.replace('-', ' ').lower()} "
    r = await api_client.post(
        "/api/auth/register",
        json={"email": _email(), "password": PASSWORD, "inviteCode": sloppy},
    )
    assert r.status_code == 201, r.text


async def test_duplicate_email_rejected(api_client, invite, maker):
    email = _email()
    assert (
        await api_client.post(
            "/api/auth/register",
            json={"email": email, "password": PASSWORD, "inviteCode": invite},
        )
    ).status_code == 201

    async with maker() as s:
        (code2,) = await create_invite_codes(s, 1)
    try:
        r = await api_client.post(
            "/api/auth/register",
            json={"email": email.upper(), "password": PASSWORD, "inviteCode": code2},
        )
        assert r.status_code == 409, "大小写不同的同一个邮箱注册成功了"
    finally:
        async with maker() as s:
            await s.execute(delete(InviteCode).where(InviteCode.code == code2))
            await s.commit()


async def test_short_password_rejected(api_client, invite):
    r = await api_client.post(
        "/api/auth/register",
        json={"email": _email(), "password": "123", "inviteCode": invite},
    )
    assert r.status_code == 422


async def test_bad_email_rejected(api_client, invite):
    r = await api_client.post(
        "/api/auth/register",
        json={"email": "不是邮箱", "password": PASSWORD, "inviteCode": invite},
    )
    assert r.status_code == 422


# ---------- 登录 / 登出 ----------


async def test_login_logout_cycle(api_client, invite):
    email = _email()
    await api_client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD, "inviteCode": invite},
    )

    assert (await api_client.post("/api/auth/logout")).status_code == 204
    assert (await api_client.get("/api/auth/me")).status_code == 401, "登出后 cookie 还在"

    r = await api_client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 200
    assert (await api_client.get("/api/auth/me")).json()["email"] == email


async def test_login_is_case_insensitive_on_email(api_client, invite):
    email = _email()
    await api_client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD, "inviteCode": invite},
    )
    await api_client.post("/api/auth/logout")

    r = await api_client.post(
        "/api/auth/login", json={"email": email.upper(), "password": PASSWORD}
    )
    assert r.status_code == 200


async def test_wrong_password_rejected(api_client, invite):
    email = _email()
    await api_client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD, "inviteCode": invite},
    )
    await api_client.post("/api/auth/logout")

    r = await api_client.post("/api/auth/login", json={"email": email, "password": "错的密码xx"})
    assert r.status_code == 401


async def test_login_error_does_not_reveal_whether_email_exists(api_client, invite):
    """两种失败必须给同一句话，否则等于送人一个用户名枚举接口。"""
    email = _email()
    await api_client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD, "inviteCode": invite},
    )
    await api_client.post("/api/auth/logout")

    wrong_pw = await api_client.post(
        "/api/auth/login", json={"email": email, "password": "错的密码xx"}
    )
    no_such = await api_client.post(
        "/api/auth/login", json={"email": _email(), "password": PASSWORD}
    )
    assert wrong_pw.status_code == no_such.status_code == 401
    assert wrong_pw.json() == no_such.json()


# ---------- 未登录一律挡在门外 ----------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/auth/me"),
        ("POST", "/api/chat"),
        ("GET", "/api/conversations"),
        ("GET", f"/api/conversations/{uuid.uuid4()}/messages"),
    ],
)
async def test_protected_endpoints_require_login(api_client, method, path):
    r = await api_client.request(method, path, json={"messages": []})
    assert r.status_code == 401, f"{method} {path} 没登录也放行了"


async def test_forged_token_rejected(api_client):
    import jwt

    forged = jwt.encode({"sub": str(uuid.uuid4())}, "假密钥", algorithm="HS256")
    r = await api_client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert r.status_code == 401


async def test_health_needs_no_login(api_client):
    r = await api_client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


# ---------- 管理员守卫（M14-A）----------


async def test_require_admin_rejects_a_regular_user(api_client, logged_in, maker):
    """⭐ 普通用户过不了 `require_admin`。

    ⚠️ 这里断言的是**服务端**的判定。前端的 `/admin` guard 只负责体验，
    挡不住任何一个会开控制台的人。
    """
    from fastapi import HTTPException

    from copilot.auth.deps import require_admin
    from copilot.db.models import User

    async with maker() as s:
        user = await s.get(User, logged_in)
        assert user.is_admin is False, "新注册用户不该是管理员"
        with pytest.raises(HTTPException) as e:
            await require_admin(user)
        assert e.value.status_code == 403


async def test_require_admin_lets_an_admin_through(api_client, logged_in, maker):
    from copilot.auth.deps import require_admin
    from copilot.db.models import User

    async with maker() as s:
        user = await s.get(User, logged_in)
        user.is_admin = True
        await s.commit()
        assert await require_admin(user) is user


async def test_a_disabled_account_cannot_even_authenticate(api_client, logged_in, maker):
    """⭐ 停用之后，手里的旧 JWT 立刻失效——管理员守卫不必重复判 `is_active`。

    这一条是 `require_admin` 不再重复检查停用状态的**前提**。两处都写等于
    两处都要维护，而漏掉一处的表现是「停用了还能用管理台」。
    """
    from copilot.db.models import User

    async with maker() as s:
        user = await s.get(User, logged_in)
        user.is_active = False
        await s.commit()

    r = await api_client.get("/api/knowledge-spaces")
    assert r.status_code == 401
