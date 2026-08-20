"""👍 / 👎。M11 P2。

⭐ **写进 `request_trace` 那张表的两列，不建独立表。**
这是 M11 唯一一个真正的设计决定，理由值得写在这里：

feedback 和 trace 分两张表且不关联的话，一个 👎 就只是个**计数器**——
你复现不了当时检索到了什么、调了什么工具、rerank 打了多少分、
走的是直路还是 Agent。合成一张表，点开一条差评能直接看到全链路，
「用户差评 → 找失败原因 → 加进评测集」这个闭环才转得起来。
分表的代价不是多写一次 join，是**这个闭环根本转不动**。

⚠️ **别指望它近期驱动优化。** 线上 3 个真实账号，一周产不出几条差评。
它现在的价值是「收集机制先在位」+「自己用的时候顺手标记」——
所以这一整块的预算是半天，别当成一个大工程来做。
（同一条逻辑也是 P4 不做百分比灰度的理由：n=3 上的统计都是自欺欺人。）
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from copilot.api.schemas import FeedbackIn, FeedbackOut
from copilot.auth.deps import CurrentUser, SessionDep
from copilot.db.models import RequestTrace

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/feedback", tags=["feedback"])


@router.post("", response_model=FeedbackOut)
async def submit(body: FeedbackIn, user: CurrentUser, session: SessionDep) -> FeedbackOut:
    """给某一轮问答点赞或点踩。

    ⚠️ **只能给自己的那一轮打分。** 别人的 trace id 一律当**不存在**（404），
    和会话接口同一个理由：403 等于告诉对方「这个 id 是有效的」，
    而 trace id 是会随 SSE 发到浏览器里的，比会话 id 更容易被顺手试。

    重复点同一条 = **覆盖**，不是新增一条。用户先点了 👍 再改成 👎 是很常见的
    （读完发现有一句是错的），那时候该留下的是他最后的意思。
    """
    trace = await session.get(RequestTrace, body.trace_id)
    if trace is None or trace.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "这条记录不存在")

    trace.feedback = body.vote
    # 👍 不带原因。带了也没意义，而留着上一次点👎时选的原因会更糟——
    # 表里会出现「vote=up, reason=答错了」这种自相矛盾的行
    trace.feedback_reason = body.reason if body.vote == "down" else None
    trace.feedback_at = datetime.now(UTC)
    await session.commit()

    # 差评单独打一行日志。**这是「差评 → 排查」这条路上唯一的即时信号**——
    # 没有它就得靠人主动去查表，而人不会每天查表。
    # journal 里看到这一行，凭 trace= 那串 id 能把全链路捞出来
    if body.vote == "down":
        logger.warning(
            "收到差评 trace=%s reason=%s route=%s chunks=%s top=%s q=%r",
            trace.id,
            trace.feedback_reason,
            trace.route,
            trace.chunk_count,
            trace.top_score,
            trace.question[:60],
        )

    return FeedbackOut(trace_id=trace.id, vote=trace.feedback)


@router.get("/recent")
async def recent(user: CurrentUser, session: SessionDep, limit: int = 20) -> list[dict]:
    """最近的反馈，**连当时的全链路一起返回**。管理员看，别人只看自己的。

    ⭐ 这个接口就是那个闭环的入口：一条差评点开 → 看到当时检索到几块、
    最高分多少、调了什么工具 → 判断是检索没召回还是模型没答好 →
    补一道评测题。没有这一步，👎 就真的只是个计数器了。

    没做页面，`curl` 或者 `/api/docs` 里点一下就够——线上一周也产不出
    20 条反馈，为它做一个后台页面是明显的过度投入。
    """
    stmt = (
        select(RequestTrace)
        .where(RequestTrace.feedback.is_not(None))
        .order_by(RequestTrace.feedback_at.desc())
        .limit(min(limit, 100))
    )
    if not user.is_admin:
        stmt = stmt.where(RequestTrace.user_id == user.id)

    rows = list((await session.execute(stmt)).scalars())
    return [
        {
            "traceId": str(r.id),
            "vote": r.feedback,
            "reason": r.feedback_reason,
            "at": r.feedback_at,
            "question": r.question,
            "route": r.route,
            "mode": r.mode,
            "tools": r.tools,
            "chunks": r.chunk_count,
            "topScore": r.top_score,
            "privateHits": r.private_hits,
            "noAnswer": r.no_answer,
            "ttfbMs": r.ttfb_ms,
            "totalMs": r.total_ms,
            "ok": r.ok,
            "error": r.error,
            # 拿它去 journalctl 里捞完整堆栈：`journalctl -u copilot-api | grep <id>`
            "requestId": r.request_id,
            "conversationId": str(r.conversation_id) if r.conversation_id else None,
            "messageId": str(r.message_id) if r.message_id else None,
        }
        for r in rows
    ]

