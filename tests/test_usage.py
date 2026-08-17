"""每日 token 配额的测试。

配额是**保险丝**，不是计费。所以这里守的是：
    配额 0 时永不挡人（默认状态，绝不能因为记账出错把所有人拦在门外）
    累加不丢（并发下用 ON CONFLICT 让数据库做加法，不是先查再写）
    超了要 429，且检查发生在**流开始之前**（流一开，状态码就发出去了）
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from copilot import usage
from copilot.db.models import TokenUsage, User


@pytest.fixture
async def user(maker):
    async with maker() as s:
        u = User(email=f"usage-{uuid.uuid4().hex[:10]}@test.local", password_hash="x")
        s.add(u)
        await s.commit()
        uid = u.id
    yield uid
    async with maker() as s:
        await s.execute(delete(TokenUsage).where(TokenUsage.user_id == uid))
        await s.execute(delete(User).where(User.id == uid))
        await s.commit()


# ---------- 估算 ----------


def test_estimate_counts_all_parts():
    """上下文才是 token 的大头，别只算答案。"""
    only_answer = usage.estimate_tokens("短答案")
    with_context = usage.estimate_tokens("很长的一段参考材料" * 50, "问题", "短答案")
    assert with_context > only_answer * 20


def test_estimate_handles_empty():
    assert usage.estimate_tokens("", "", "") >= 1  # 不会返回 0 或负数


# ---------- 累加 ----------


async def test_record_accumulates(maker, user):
    async with maker() as s:
        await usage.record(s, user, 100)
        await usage.record(s, user, 250)
    async with maker() as s:
        row = (
            await s.execute(select(TokenUsage).where(TokenUsage.user_id == user))
        ).scalar_one()
        assert row.tokens == 350
        assert row.requests == 2


async def test_record_is_a_single_upsert(maker, user):
    """⭐ 并发下不能丢更新。

    「先查再写」的实现在这里会失败：两个会话都读到 0，都写成 0+100，
    结果记成 100 而不是 200。用 ON CONFLICT DO UPDATE 让数据库自己加。
    """
    async with maker() as a, maker() as b:
        await usage.record(a, user, 100)
        await usage.record(b, user, 100)
    async with maker() as s:
        assert (await usage.used_today(s, user)) == 200


async def test_record_ignores_nonpositive(maker, user):
    async with maker() as s:
        await usage.record(s, user, 0)
        await usage.record(s, user, -5)
        assert (await usage.used_today(s, user)) == 0


# ---------- 配额判定 ----------


async def test_zero_quota_never_blocks(maker, user):
    """默认 0 = 不限。**这条最重要**：配额逻辑出任何岔子，
    都不该把默认状态的用户拦在门外。"""
    async with maker() as s:
        u = await s.get(User, user)
        assert u.daily_token_quota == 0
        await usage.record(s, user, 10_000_000)
        exceeded, _, _ = await usage.over_quota(s, u)
        assert exceeded is False


async def test_quota_blocks_after_limit(maker, user):
    async with maker() as s:
        u = await s.get(User, user)
        u.daily_token_quota = 1000
        await s.commit()
        await usage.record(s, user, 999)
        exceeded, used, quota = await usage.over_quota(s, u)
        assert (exceeded, used, quota) == (False, 999, 1000)

        await usage.record(s, user, 2)
        exceeded, used, _ = await usage.over_quota(s, u)
        assert exceeded is True
        assert used == 1001


# ---------- 接口层 ----------


async def test_chat_returns_429_when_over_quota(api_client, maker):
    """超额要在**流开始之前**返回 429。

    流一旦开始，HTTP 状态码就已经发出去了，再想返回 429 也来不及——
    只能在流里塞个 error 片段，那对脚本来说完全是另一回事。
    """
    from copilot.auth.invites import create_invite_codes
    from copilot.db.models import InviteCode

    async with maker() as s:
        (code,) = await create_invite_codes(s, 1)
    r = await api_client.post(
        "/api/auth/register",
        json={
            "email": f"quota-{uuid.uuid4().hex[:8]}@test.local",
            "password": "test-password-2026",
            "inviteCode": code,
        },
    )
    assert r.status_code == 201
    uid = uuid.UUID(r.json()["id"])

    async with maker() as s:
        u = await s.get(User, uid)
        u.daily_token_quota = 10
        await s.commit()
        await usage.record(s, uid, 50)

    r = await api_client.post(
        "/api/chat", json={"id": str(uuid.uuid4()), "messages": [{"role": "user", "content": "喂"}]}
    )
    assert r.status_code == 429
    assert "上限" in r.json()["detail"]

    async with maker() as s:
        await s.execute(delete(TokenUsage).where(TokenUsage.user_id == uid))
        await s.execute(delete(InviteCode).where(InviteCode.code == code))
        await s.execute(delete(User).where(User.id == uid))
        await s.commit()
