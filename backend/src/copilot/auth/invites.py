"""邀请码的发放与核销。

**核销必须是原子的。** 两个人同时拿同一个码注册，只能成一个。
所以用带条件的 UPDATE 做 compare-and-set：

    UPDATE invite_codes SET used_by=..., used_at=now()
     WHERE code=... AND used_by IS NULL

先 SELECT 查一下「有没有被用过」再 UPDATE，中间那一瞬就是竞态窗口，
两个人都会查到「没用过」。这里不留那个窗口。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from copilot.auth.security import generate_invite_code, normalize_invite_code
from copilot.db.models import InviteCode


async def create_invite_codes(session: AsyncSession, count: int) -> list[str]:
    """生成 count 个邀请码并落库。"""
    codes: list[str] = []
    existing = set()
    while len(codes) < count:
        code = generate_invite_code()
        if code in existing:
            continue  # 31^8 的空间，撞了纯属运气，重摇一个就是
        existing.add(code)
        session.add(InviteCode(code=code))
        codes.append(code)
    await session.commit()
    return codes


async def count_unused_codes(session: AsyncSession) -> int:
    stmt = select(func.count()).select_from(InviteCode).where(InviteCode.used_by.is_(None))
    return int((await session.execute(stmt)).scalar_one())


async def list_unused_codes(session: AsyncSession, limit: int = 50) -> list[str]:
    stmt = (
        select(InviteCode.code)
        .where(InviteCode.used_by.is_(None))
        .order_by(InviteCode.created_at)
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())


async def redeem_invite_code(session: AsyncSession, code: str, user_id: uuid.UUID) -> bool:
    """核销邀请码。成功返回 True；码不存在或已被用过返回 False。

    不提交事务——由调用方（注册接口）连同新建的用户一起提交，
    这样「用户建好了但码没核销」不可能发生。
    """
    normalized = normalize_invite_code(code)
    if not normalized:
        return False

    stmt = (
        update(InviteCode)
        .where(InviteCode.code == normalized, InviteCode.used_by.is_(None))
        .values(used_by=user_id, used_at=datetime.now(UTC))
    )
    result = await session.execute(stmt)
    return bool(result.rowcount)
