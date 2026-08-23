"""只读管理台的后端（M15-A）。

四个接口，全部只读、全部 `CurrentAdmin`：

    GET /api/admin/overview        全站概览（24h / 7d / 30d）
    GET /api/admin/users           用户列表，分页
    GET /api/admin/users/{id}      单个用户的使用情况
    GET /api/admin/feedback        反馈中心，分页

⚠️⚠️ **三条规矩，每一条都写着"不这么做会怎样"：**

1. **鉴权在服务端，前端的 `/admin` guard 只管体验。** 前端那道 guard 挡的是
   「点进去看到一片报错」，挡不住任何一个会开控制台的人。所以这里每个接口
   都挂 `CurrentAdmin`（见 `auth/deps.require_admin`），一个都不能漏。

2. **概览页不出现任何原始问题文本。** 一个人问过的问题连起来，就是他在处理
   哪个客户、哪个故障。仪表盘是一眼扫过去的东西，不该顺带把这些摊开；
   管理员点进用户详情或反馈详情是一次**明确的动作**，那里才给全文
   （路线图第 9.1 节：「普通 Overview 不默认展示完整问题文本」）。
   `metrics.Summary` 因此只给 `bypass_ids`，不给问题。

3. **列表一律分页，且分页在 SQL 里做。** 这台机器 1.6GB 内存，
   `select(...).scalars().all()` 再在 Python 里切片，等于把整张表读进内存——
   今天 285 行没事，攒到几十万行就是一次 OOM，而 OOM 会把**问答服务**
   一起带走。上限写死在 `_MAX_LIMIT`，客户端传再大也没用。

**只读**是这一步的边界：启用/禁用用户、审核纠错都属于 M15-B，那些要配审计
记录。这里连一个 PATCH 都不给——留一个"暂时没人调用"的写接口，
就是留一个没人测过的提权入口。

**没有 `/api/admin/evaluations`。** 路线图把「已发布评测结果」列进了 M15-A，
但今天评测结果只是本机 `eval/` 目录下的文件，`evaluation_runs` 那张表要到
M19 才建。现在临时做一个读文件的页面，M19-A 定契约时会整个推翻——
它进 M19-B，和评测中心一起。
"""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import Select, func, select

from copilot import metrics
from copilot.auth.deps import CurrentAdmin, SessionDep
from copilot.db.models import (
    Conversation,
    Document,
    Job,
    KnowledgeSpace,
    Message,
    RequestTrace,
    TokenUsage,
    User,
    VerifiedAnswer,
)

router = APIRouter(prefix="/api/admin", tags=["admin"])

# 客户端能要的最大一页。**写死在服务端**：分页的意义是「不把整张表读进内存」，
# 而一个由客户端决定的上限等于没有上限
_MAX_LIMIT = 200

Range = Literal["24h", "7d", "30d"]
_WINDOW = {"24h": timedelta(hours=24), "7d": timedelta(days=7), "30d": timedelta(days=30)}


def _since(window: Range) -> datetime:
    return datetime.now(UTC) - _WINDOW[window]


async def _count(session, stmt: Select) -> int:
    """数一个查询有多少行。**分页的总数只能这么拿**——不能把行读回来再 len()。"""
    return await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0


# ─────────────────────────── Overview ───────────────────────────


class LatencyOut(BaseModel):
    p50: int | None
    p95: int | None
    count: int


class OverviewOut(BaseModel):
    """⚠️ 这里的每一个字段都是**聚合数**，没有一条原始问题文本。见模块文件头第 2 条。"""

    range: Range
    since: datetime

    questions: int
    active_users: int
    by_source: dict[str, int]

    thumbs_up: int
    thumbs_down: int
    feedback_rate: str

    agent_requests: int
    agent_without_tools: int
    tool_bypass: int
    interrupted: int
    errors: int

    ttfb: LatencyOut
    duration: LatencyOut

    tokens: int

    uploaded_documents: int
    failed_jobs: int
    verified_answers: int

    # 全站存量，不随时间范围变
    users_total: int
    documents_total: int


@router.get("/overview", response_model=OverviewOut)
async def overview(admin: CurrentAdmin, session: SessionDep, range: Range = "7d") -> OverviewOut:
    """全站概览。`range` 只认 24h / 7d / 30d——别的值 FastAPI 直接 422。

    ⚠️ 这里**把窗口内的 trace 行全读进来**再算，和 `quality-report` 一样。
    口径复用（`metrics.summarize`）比 SQL 聚合重要得多：那些定义
    （差评率的分母、延迟不含寒暄、越过工具直答怎么判）一旦在 SQL 里重写一遍，
    就会和命令行那份慢慢漂移。30 天的量级是几千行，读得起；
    真到了读不起的那天，该做的是给 trace 建汇总表，而不是在这里手写一遍口径。
    """
    since = _since(range)
    rows = list(
        (
            await session.execute(select(RequestTrace).where(RequestTrace.created_at >= since))
        ).scalars()
    )
    stat = metrics.summarize(rows)

    uploaded = await _count(
        session,
        select(Document.id).where(
            Document.source_type == "upload", Document.created_at >= since
        ),
    )
    failed_jobs = await _count(
        session, select(Job.id).where(Job.status == "failed", Job.created_at >= since)
    )
    # ⚠️ 这是**这段时间里写下的人工订正条数**，不是「有多少个回答用上了订正」。
    # 后者今天量不出来：`request_trace` 上没有这一列，检索里订正块和语雀原文
    # 一起参与召回（见 `retrieve._verified_first`）。**宁可少报一个数，
    # 也不要给一个看起来像那么回事的编造值。**
    verified = await _count(
        session, select(VerifiedAnswer.id).where(VerifiedAnswer.created_at >= since)
    )

    return OverviewOut(
        range=range,
        since=since,
        questions=stat.total,
        active_users=stat.users,
        by_source=stat.by_source,
        thumbs_up=stat.up,
        thumbs_down=stat.down,
        feedback_rate=stat.feedback_rate,
        agent_requests=stat.agent_total,
        agent_without_tools=stat.agent_no_tool,
        tool_bypass=stat.bypass,
        interrupted=stat.interrupted,
        errors=stat.errors,
        ttfb=LatencyOut(**asdict(stat.ttfb)),
        duration=LatencyOut(**asdict(stat.duration)),
        tokens=stat.tokens,
        uploaded_documents=uploaded,
        failed_jobs=failed_jobs,
        verified_answers=verified,
        users_total=await _count(session, select(User.id)),
        documents_total=await _count(session, select(Document.id)),
    )


# ─────────────────────────── 用户列表 ───────────────────────────


class AdminUserRow(BaseModel):
    id: uuid.UUID
    email: str
    is_admin: bool
    is_active: bool
    created_at: datetime
    last_active_at: datetime | None

    requests: int  # 时间范围内
    thumbs_up: int
    thumbs_down: int
    no_answer: int
    agent_requests: int
    ttfb_p95: int | None

    tokens: int  # 时间范围内，来自 token_usage
    uploads: int  # 存量：这个人现在有几篇文档
    storage_bytes: int


class UserPage(BaseModel):
    total: int
    limit: int
    offset: int
    range: Range
    items: list[AdminUserRow]


@router.get("/users", response_model=UserPage)
async def list_users(
    admin: CurrentAdmin,
    session: SessionDep,
    range: Range = "7d",
    q: Annotated[str, Query(max_length=255)] = "",
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserPage:
    """用户列表。`q` 按邮箱模糊过滤。

    每人的使用数据用**一次分组查询**拿，不在循环里逐个查——
    50 行 × 3 个指标就是 150 次往返，而这是个内部管理页，
    没必要为它把连接池占满。
    """
    base = select(User.id)
    if q:
        base = base.where(User.email.ilike(f"%{q.strip()}%"))
    total = await _count(session, base)

    page_stmt = select(User).order_by(User.created_at.desc()).limit(limit).offset(offset)
    if q:
        page_stmt = page_stmt.where(User.email.ilike(f"%{q.strip()}%"))
    users = list((await session.execute(page_stmt)).scalars())
    if not users:
        return UserPage(total=total, limit=limit, offset=offset, range=range, items=[])

    ids = [u.id for u in users]
    since = _since(range)

    trace_rows = (
        await session.execute(
            select(
                RequestTrace.user_id,
                func.count(RequestTrace.id),
                func.count(RequestTrace.id).filter(RequestTrace.feedback == "up"),
                func.count(RequestTrace.id).filter(RequestTrace.feedback == "down"),
                func.count(RequestTrace.id).filter(RequestTrace.no_answer.is_(True)),
                func.count(RequestTrace.id).filter(RequestTrace.route == "agent"),
                # ⚠️ p95 在 SQL 里算，用的是 `percentile_disc`（取真实存在的那个值），
                # 和 `metrics.percentile` 的最近邻取法一致。用 `percentile_cont`
                # 会插值出一个从未发生过的耗时——延迟这种数，编出来的中间值
                # 比少一个数更糟
                func.percentile_disc(0.95)
                .within_group(RequestTrace.ttfb_ms)
                .filter(RequestTrace.ttfb_ms.isnot(None), RequestTrace.route != "canned"),
                func.max(RequestTrace.created_at),
            )
            .where(RequestTrace.user_id.in_(ids), RequestTrace.created_at >= since)
            .group_by(RequestTrace.user_id)
        )
    ).all()
    traces = {r[0]: r for r in trace_rows}

    # last_active_at 不受时间范围限制：「上次出现是三个月前」正是要看的信息
    last_seen = dict(
        (
            await session.execute(
                select(RequestTrace.user_id, func.max(RequestTrace.created_at))
                .where(RequestTrace.user_id.in_(ids))
                .group_by(RequestTrace.user_id)
            )
        ).all()
    )

    tokens = dict(
        (
            await session.execute(
                select(TokenUsage.user_id, func.coalesce(func.sum(TokenUsage.tokens), 0))
                .where(TokenUsage.user_id.in_(ids), TokenUsage.day >= since.date())
                .group_by(TokenUsage.user_id)
            )
        ).all()
    )

    docs = {
        row[0]: (row[1], row[2])
        for row in (
            await session.execute(
                select(
                    Document.owner_id,
                    func.count(Document.id),
                    func.coalesce(func.sum(Document.size_bytes), 0),
                )
                .where(Document.owner_id.in_(ids))
                .group_by(Document.owner_id)
            )
        ).all()
    }

    items = []
    for u in users:
        t = traces.get(u.id)
        uploads, storage = docs.get(u.id, (0, 0))
        items.append(
            AdminUserRow(
                id=u.id,
                email=u.email,
                is_admin=u.is_admin,
                is_active=u.is_active,
                created_at=u.created_at,
                last_active_at=last_seen.get(u.id),
                requests=t[1] if t else 0,
                thumbs_up=t[2] if t else 0,
                thumbs_down=t[3] if t else 0,
                no_answer=t[4] if t else 0,
                agent_requests=t[5] if t else 0,
                ttfb_p95=int(t[6]) if t and t[6] is not None else None,
                tokens=int(tokens.get(u.id, 0)),
                uploads=uploads,
                storage_bytes=int(storage),
            )
        )
    return UserPage(total=total, limit=limit, offset=offset, range=range, items=items)


# ─────────────────────────── 用户详情 ───────────────────────────


class DayPoint(BaseModel):
    day: date
    requests: int


class RecentRequest(BaseModel):
    """⚠️ 这里**有**问题原文。管理员点进某个人的详情是一次明确的动作，
    不是仪表盘顺带展示——概览页那边一个字都不给。"""

    id: uuid.UUID
    created_at: datetime
    route: str
    answer_source: str | None
    question: str
    ttfb_ms: int | None
    total_ms: int | None
    ok: bool
    feedback: str | None


class AdminDocRow(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    chunk_count: int
    size_bytes: int | None
    created_at: datetime
    error: str | None


class UserDetail(BaseModel):
    id: uuid.UUID
    email: str
    is_admin: bool
    is_active: bool
    created_at: datetime
    daily_token_quota: int

    range: Range
    questions: int
    by_source: dict[str, int]
    by_route: dict[str, int]
    by_space: dict[str, int]
    thumbs_up: int
    thumbs_down: int
    errors: int
    ttfb: LatencyOut
    duration: LatencyOut
    tokens: int

    trend: list[DayPoint]
    recent: list[RecentRequest]
    documents: list[AdminDocRow]


@router.get("/users/{user_id}", response_model=UserDetail)
async def user_detail(
    user_id: uuid.UUID,
    admin: CurrentAdmin,
    session: SessionDep,
    range: Range = "30d",
) -> UserDetail:
    """一个人的使用情况。查无此人 → 404。"""
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")

    since = _since(range)
    rows = list(
        (
            await session.execute(
                select(RequestTrace)
                .where(RequestTrace.user_id == user_id, RequestTrace.created_at >= since)
                .order_by(RequestTrace.created_at.desc())
            )
        ).scalars()
    )
    stat = metrics.summarize(rows)

    by_route: dict[str, int] = {}
    for r in rows:
        by_route[r.route] = by_route.get(r.route, 0) + 1

    # 知识版本分布：trace 上没有这一列，从会话那边取（会话建的时候就钉死了）
    by_space: dict[str, int] = {}
    conv_ids = {r.conversation_id for r in rows if r.conversation_id}
    if conv_ids:
        space_of = dict(
            (
                await session.execute(
                    select(Conversation.id, KnowledgeSpace.code).join(
                        KnowledgeSpace, KnowledgeSpace.id == Conversation.knowledge_space_id
                    ).where(Conversation.id.in_(conv_ids))
                )
            ).all()
        )
        for r in rows:
            code = space_of.get(r.conversation_id)
            if code:
                by_space[code] = by_space.get(code, 0) + 1

    trend: dict[date, int] = {}
    for r in rows:
        day = r.created_at.date()
        trend[day] = trend.get(day, 0) + 1

    tokens = int(
        await session.scalar(
            select(func.coalesce(func.sum(TokenUsage.tokens), 0)).where(
                TokenUsage.user_id == user_id, TokenUsage.day >= since.date()
            )
        )
        or 0
    )

    documents = list(
        (
            await session.execute(
                select(Document)
                .where(Document.owner_id == user_id)
                .order_by(Document.created_at.desc())
                .limit(100)
            )
        ).scalars()
    )

    return UserDetail(
        id=user.id,
        email=user.email,
        is_admin=user.is_admin,
        is_active=user.is_active,
        created_at=user.created_at,
        daily_token_quota=user.daily_token_quota,
        range=range,
        questions=stat.total,
        by_source=stat.by_source,
        by_route=by_route,
        by_space=by_space,
        thumbs_up=stat.up,
        thumbs_down=stat.down,
        errors=stat.errors,
        ttfb=LatencyOut(**asdict(stat.ttfb)),
        duration=LatencyOut(**asdict(stat.duration)),
        tokens=tokens,
        trend=[DayPoint(day=d, requests=n) for d, n in sorted(trend.items())],
        # 20 条够看清"最近在问什么"，再多就是把台账整个搬到前端
        recent=[
            RecentRequest(
                id=r.id,
                created_at=r.created_at,
                route=r.route,
                answer_source=r.answer_source,
                question=r.question,
                ttfb_ms=r.ttfb_ms,
                total_ms=r.total_ms,
                ok=r.ok,
                feedback=r.feedback,
            )
            for r in rows[:20]
        ],
        documents=[
            AdminDocRow(
                id=d.id,
                title=d.title,
                status=d.status,
                chunk_count=d.chunk_count,
                size_bytes=d.size_bytes,
                created_at=d.created_at,
                error=d.error,
            )
            for d in documents
        ],
    )


# ─────────────────────────── 反馈中心 ───────────────────────────


class FeedbackRow(BaseModel):
    """一条反馈连着它的全链路。

    ⭐ 这正是「👍👎 不另建表」那个决定换来的东西（见 `api/routes/feedback.py`）：
    点开一条差评能直接看到当时检索到几块、rerank 最高分多少、调了什么工具。
    分表的话这里只能显示一个计数器。
    """

    id: uuid.UUID
    created_at: datetime
    feedback: str
    feedback_reason: str | None
    feedback_at: datetime | None

    user_email: str | None
    question: str
    answer: str | None

    knowledge_space: str | None
    route: str
    answer_source: str | None
    tools: list | None

    chunk_count: int
    top_score: float | None
    private_hits: int
    citations: list | None
    images: list | None

    ttfb_ms: int | None
    total_ms: int | None
    model: str | None
    ok: bool
    error: str | None


class FeedbackPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[FeedbackRow]


@router.get("/feedback", response_model=FeedbackPage)
async def list_feedback(
    admin: CurrentAdmin,
    session: SessionDep,
    kind: Literal["down", "up", "all"] = "down",
    reason: Annotated[str, Query(max_length=24)] = "",
    range: Range = "30d",
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> FeedbackPage:
    """反馈中心。默认只看差评——好评没什么可查的，差评才是排查入口。"""
    since = _since(range)
    where = [RequestTrace.feedback.isnot(None), RequestTrace.created_at >= since]
    if kind != "all":
        where.append(RequestTrace.feedback == kind)
    if reason:
        where.append(RequestTrace.feedback_reason == reason)

    total = await _count(session, select(RequestTrace.id).where(*where))
    rows = list(
        (
            await session.execute(
                select(RequestTrace)
                .where(*where)
                .order_by(RequestTrace.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars()
    )
    if not rows:
        return FeedbackPage(total=total, limit=limit, offset=offset, items=[])

    emails = dict(
        (
            await session.execute(
                select(User.id, User.email).where(
                    User.id.in_({r.user_id for r in rows if r.user_id})
                )
            )
        ).all()
    )

    # 答案正文在 messages 表里（trace 只存问题）。
    # ⚠️ `message_id` **没有外键**（消息删了 trace 要留着），所以这里可能
    # 指向一条已经不存在的消息——读不到就当没有，不要因此让整页报错
    msg_ids = {r.message_id for r in rows if r.message_id}
    messages = {
        m.id: m
        for m in (
            await session.execute(select(Message).where(Message.id.in_(msg_ids)))
        ).scalars()
    } if msg_ids else {}

    conv_ids = {r.conversation_id for r in rows if r.conversation_id}
    spaces = (
        dict(
            (
                await session.execute(
                    select(Conversation.id, KnowledgeSpace.code).join(
                        KnowledgeSpace, KnowledgeSpace.id == Conversation.knowledge_space_id
                    ).where(Conversation.id.in_(conv_ids))
                )
            ).all()
        )
        if conv_ids
        else {}
    )

    items = []
    for r in rows:
        msg = messages.get(r.message_id) if r.message_id else None
        items.append(
            FeedbackRow(
                id=r.id,
                created_at=r.created_at,
                feedback=r.feedback or "",
                feedback_reason=r.feedback_reason,
                feedback_at=r.feedback_at,
                user_email=emails.get(r.user_id) if r.user_id else None,
                question=r.question,
                answer=msg.content if msg else None,
                knowledge_space=spaces.get(r.conversation_id),
                route=r.route,
                answer_source=r.answer_source,
                tools=r.tools,
                chunk_count=r.chunk_count,
                top_score=r.top_score,
                private_hits=r.private_hits,
                citations=msg.citations if msg else None,
                images=msg.images if msg else None,
                ttfb_ms=r.ttfb_ms,
                total_ms=r.total_ms,
                model=r.model,
                ok=r.ok,
                error=r.error,
            )
        )
    return FeedbackPage(total=total, limit=limit, offset=offset, items=items)
