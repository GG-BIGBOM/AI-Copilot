"""每人每日 token 用量与配额。M8 的成本兜底。

这不是计费系统，是**保险丝**。它要挡的是三种情形：
    有人写脚本对着 /api/chat 循环发问
    某个前端 bug 把同一条请求重放几百次
    一个用户善意地把整本手册当问题贴进来

配额默认 0 = 不限（`users.daily_token_quota`）。真出事时把某个人的额度调下来，
或者给所有人设个上限，不用改代码。

⚠️ **token 数是估的**，见 `estimate_tokens`。这在计费场景不可接受，
但保险丝只需要「量级对」——估成 8000 还是 9000 不影响它该不该断。
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from copilot.db.models import TokenUsage, User

logger = logging.getLogger(__name__)

# DeepSeek 对中文大约 1.5 个字符一个 token，英文约 4 个字符一个。
# 这里按中文语料取 1.5，宁可估高——保险丝估高只会早断一点，估低就形同虚设。
CHARS_PER_TOKEN = 1.5


def estimate_tokens(*texts: str) -> int:
    """按字符数估 token。

    为什么不用 tiktoken 之类精确算：那要为每个模型引一份词表（几 MB），
    而 DeepSeek 的分词器根本没公开。真要精确数字，该做的是解析流式响应
    末尾的 usage 字段（`stream_options.include_usage`），那是另一件事——
    但 usage 只有在整段生成完之后才拿得到，挡不住「这一次请求要不要放行」。
    """
    total = sum(len(t or "") for t in texts)
    return int(total / CHARS_PER_TOKEN) + 1


def _today() -> date:
    """按 UTC 算日期。

    服务器跑 UTC，本机是 +08。用本地日期的话，同一份代码在两地会把
    「今天」切在不同时刻，配额的重置时间就飘了。统一 UTC，反正它只是个
    重置周期，不需要和用户所在时区对齐。
    """
    return datetime.now(UTC).date()


async def used_today(session: AsyncSession, user_id: uuid.UUID) -> int:
    row = await session.scalar(
        select(TokenUsage.tokens).where(
            TokenUsage.user_id == user_id, TokenUsage.day == _today()
        )
    )
    return int(row or 0)


async def over_quota(session: AsyncSession, user: User) -> tuple[bool, int, int]:
    """今天还能不能问。返回 (是否超了, 已用, 上限)。上限 0 表示不限。"""
    quota = int(user.daily_token_quota or 0)
    if quota <= 0:
        return False, 0, 0
    used = await used_today(session, user.id)
    return used >= quota, used, quota


async def record(session: AsyncSession, user_id: uuid.UUID, tokens: int) -> None:
    """累加今天的用量。

    用 `ON CONFLICT DO UPDATE` 而不是「先查再写」：同一个人同时开两个标签页
    提问时，先查再写会有一次丢失更新——两边都读到 100，都写成 100+n，
    少记了一次。这条 SQL 让数据库自己做加法。

    ⚠️ 记账失败**不能影响答题**。用量记漏一次的代价远小于「答案生成好了、
    却因为写台账报错而返回 500」。
    """
    if tokens <= 0:
        return
    stmt = (
        insert(TokenUsage)
        .values(user_id=user_id, day=_today(), tokens=tokens, requests=1)
        .on_conflict_do_update(
            index_elements=[TokenUsage.user_id, TokenUsage.day],
            set_={
                "tokens": TokenUsage.tokens + tokens,
                "requests": TokenUsage.requests + 1,
            },
        )
    )
    try:
        await session.execute(stmt)
        await session.commit()
    except Exception:  # noqa: BLE001 - 见 docstring
        logger.warning("记录 token 用量失败 user=%s tokens=%s", user_id, tokens, exc_info=True)
        await session.rollback()
