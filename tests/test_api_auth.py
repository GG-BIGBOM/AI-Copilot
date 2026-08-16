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
