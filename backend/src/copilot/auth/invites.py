"""邀请码的发放与核销。

**核销必须是原子的。** 两个人同时拿同一个码注册，只能成一个。
所以用带条件的 UPDATE 做 compare-and-set：

    UPDATE invite_codes SET used_by=..., used_at=now()
     WHERE code=... AND used_at IS NULL

先 SELECT 查一下「有没有被用过」再 UPDATE，中间那一瞬就是竞态窗口，
两个人都会查到「没用过」。这里不留那个窗口。

⚠️⚠️ **「用过没有」看的是 `used_at`，不是 `used_by`（M13 P8）。**

`used_by` 是 `ON DELETE SET NULL` —— 人删号了这一列会被数据库清空。
而在此之前，这三个函数全都按 `used_by IS NULL` 判「还没用过」，于是：

    用邀请码注册  →  used_by = 那个人
    删掉那个人    →  used_by 被置回 NULL
    同一个码      →  又能注册了

**一次性的邀请码因为删号而复活**，而邀请制是这个站唯一的准入闸门。
本机库里此刻就有 1 个这样的码（16 条有 used_at、只有 15 条有 used_by）。

两列的语义现在分得很清：

    used_by  谁用的。人删了就不知道了，**可以**为空
    used_at  什么时候被消费的。**一旦写上永不清除**，它才是「用过了」的判据

顺带说明为什么不是把外键改成 RESTRICT / 不删：台账要留着人走之后的历史
（同 `request_trace.user_id` 的理由），而「谁用的」这个信息本来就该跟着
账号一起消失。要守住的是「这个码不能再用」，不是「必须记得是谁用的」。
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
    stmt = select(func.count()).select_from(InviteCode).where(InviteCode.used_at.is_(None))
    return int((await session.execute(stmt)).scalar_one())


async def list_unused_codes(session: AsyncSession, limit: int = 50) -> list[str]:
    stmt = (
        select(InviteCode.code)
        .where(InviteCode.used_at.is_(None))
        .order_by(InviteCode.created_at)
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())


async def redeem_invite_code(session: AsyncSession, code: str, user_id: uuid.UUID) -> bool:
    """核销邀请码。成功返回 True；码不存在或**曾经**被用过返回 False。

    不提交事务——由调用方（注册接口）连同新建的用户一起提交，
    这样「用户建好了但码没核销」不可能发生。

    ⚠️ 条件是 `used_at IS NULL`，见文件头：按 `used_by` 判的话，
    删一个号就能把他用过的码放回池子里。
    """
    normalized = normalize_invite_code(code)
    if not normalized:
        return False

    stmt = (
        update(InviteCode)
        .where(InviteCode.code == normalized, InviteCode.used_at.is_(None))
        .values(used_by=user_id, used_at=datetime.now(UTC))
    )
    result = await session.execute(stmt)
    return bool(result.rowcount)
