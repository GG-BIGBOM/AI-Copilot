"""台账保留策略（M13 P6）。

⭐ **为什么这条命令值得一组测试，而不是「跑一次看看对不对」。**
它要挂进 systemd timer 每天自动跑，也就是说**没有人会看着它**。
一条清理命令坏掉的两种样子都很难被发现：

    删多了  台账不可再生 —— 它记的是当时那一轮的检索链路，重跑不出来
    删少了  日志每天照写「到期 0 行」，磁盘一直涨，几个月后才有人发现

后一种真的发生过：第一版把「留久一点」写成
`NOT (feedback = 'down' OR ok = false)`，撞上 SQL 的三值逻辑——
`feedback` 是 NULL 时整个表达式是 NULL 而不是 true，于是**一行都删不掉**。
390 行里 376 行的 feedback 都是 NULL，也就是说那一版等于没做。
`test_null_feedback_rows_are_not_swallowed` 就是钉住那一次。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, select

from copilot.cli import TRACE_KEEP_LONGER_DAYS, TRACE_RETENTION_DAYS, _prune_traces
from copilot.db.models import RequestTrace


@pytest.fixture
async def rows(maker, logged_in):
    """造一批不同年龄、不同结局的台账，用完删干净。"""
    made: list[uuid.UUID] = []

    async def add(*, days_ago: int, feedback: str | None = None, ok: bool = True) -> uuid.UUID:
        row_id = uuid.uuid4()
        async with maker() as s:
            s.add(
                RequestTrace(
                    id=row_id,
                    user_id=logged_in,
                    route="direct",
                    question=f"{days_ago} 天前的一行",
                    feedback=feedback,
                    ok=ok,
                    created_at=datetime.now(UTC) - timedelta(days=days_ago),
                )
            )
            await s.commit()
        made.append(row_id)
        return row_id

    yield add

    async with maker() as s:
        await s.execute(delete(RequestTrace).where(RequestTrace.id.in_(made)))
        await s.commit()


async def prune(maker, *, apply: bool, days: int = TRACE_RETENTION_DAYS,
                keep_days: int = TRACE_KEEP_LONGER_DAYS) -> None:
    """跑一次清理。**必须把测试的会话工厂传进去**——模块级的 `SessionLocal`
    绑着共享连接池，而每个用例跑在自己的事件循环上，借它写必崩。"""
    await _prune_traces(apply=apply, days=days, keep_days=keep_days, maker=maker)


async def _alive(maker, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    async with maker() as s:
        return set(
            (await s.execute(select(RequestTrace.id).where(RequestTrace.id.in_(ids)))).scalars()
        )


async def test_dry_run_deletes_nothing(rows, maker):
    """⭐ 默认必须是预演。这条命令要每天自动跑，删错了没法撤回。"""
    old = await rows(days_ago=TRACE_RETENTION_DAYS + 5)
    await prune(maker, apply=False)
    assert await _alive(maker, [old]) == {old}


async def test_plain_traces_expire_but_recent_ones_stay(rows, maker):
    old = await rows(days_ago=TRACE_RETENTION_DAYS + 5)
    fresh = await rows(days_ago=TRACE_RETENTION_DAYS - 5)
    await prune(maker, apply=True)
    assert await _alive(maker, [old, fresh]) == {fresh}


async def test_thumbs_down_is_kept_longer(rows, maker):
    """⭐ 差评是**评测集的原料**：「用户差评 → 找失败原因 → 加进评测集」
    这个闭环有时要跨好几周才走完。按 30 天删，等于把还没消化的失败样本扔了。
    """
    down = await rows(days_ago=TRACE_RETENTION_DAYS + 5, feedback="down")
    plain = await rows(days_ago=TRACE_RETENTION_DAYS + 5)
    await prune(maker, apply=True)
    assert await _alive(maker, [down, plain]) == {down}


async def test_errors_are_kept_longer(rows, maker):
    """出错的行是排查线上事故的原始材料，而复盘常常发生在事发很久以后。"""
    err = await rows(days_ago=TRACE_RETENTION_DAYS + 5, ok=False)
    plain = await rows(days_ago=TRACE_RETENTION_DAYS + 5)
    await prune(maker, apply=True)
    assert await _alive(maker, [err, plain]) == {err}


async def test_kept_longer_rows_do_expire_eventually(rows, maker):
    """留久一点不等于永久留。超过 90 天的差评同样要清掉。"""
    ancient_down = await rows(days_ago=TRACE_KEEP_LONGER_DAYS + 5, feedback="down")
    ancient_err = await rows(days_ago=TRACE_KEEP_LONGER_DAYS + 5, ok=False)
    await prune(maker, apply=True)
    assert await _alive(maker, [ancient_down, ancient_err]) == set()


async def test_thumbs_up_is_not_kept_longer(rows, maker):
    """⚠️ 只有 👎 值得留久一点。👍 说明这一轮没问题，留着它没有排查价值。"""
    up = await rows(days_ago=TRACE_RETENTION_DAYS + 5, feedback="up")
    await prune(maker, apply=True)
    assert await _alive(maker, [up]) == set()


async def test_null_feedback_rows_are_not_swallowed(rows, maker):
    """⭐⭐ **钉住 SQL 三值逻辑那一次。**

    第一版写的是 `NOT (feedback = 'down' OR ok = false)`。`feedback` 是 NULL 时
    （绝大多数行都是），`feedback = 'down'` 的结果是 **NULL** 而不是 false，
    `NULL OR false` 还是 NULL，取反 `NOT NULL` **仍然是 NULL**——
    于是这一行既不算「留久一点」也不算「普通到期」，两边都落空，
    **一行都删不掉，而且不报错**。

    实测那三句在同一张表上的差别：
        where feedback is null                                    389
        where not (feedback = 'down' or ok = false)                  0
        where not (coalesce(feedback,'') = 'down' or ok = false)    376

    这条用例造的正是「feedback 为 NULL 且早就过期」的行——第一版下它活着。
    """
    null_fb = await rows(days_ago=TRACE_RETENTION_DAYS + 5, feedback=None)
    await prune(maker, apply=True)
    assert await _alive(maker, [null_fb]) == set(), "feedback 为 NULL 的过期行没被删掉"
