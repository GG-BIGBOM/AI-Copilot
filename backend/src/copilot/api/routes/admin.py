"""只读管理台的后端（M15-A）。

四个接口，全部只读、全部 `CurrentAdmin`：

    GET  /api/admin/overview          全站概览（24h / 7d / 30d）
    GET  /api/admin/users             用户列表，分页
    GET  /api/admin/users/{id}        单个用户的使用情况
    GET  /api/admin/feedback          反馈中心，分页
    GET  /api/admin/corrections       纠错审核队列，分页（M16）
    GET  /api/admin/corrections/{id}  一条纠错的全部内容（M16）
    POST /api/admin/corrections/{id}/review    通过 / 拒绝（M16）
    POST /api/admin/corrections/{id}/publish   发布成标准答案（M16）

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

**只读**是 M15-A 的边界：启用/禁用用户仍然属于 M15-B，那要配审计记录。
M16 加进来的两个写接口（review / publish）是例外，而它们各自都在纠错行上留了
`reviewed_by` / `reviewed_at` / `review_note`，发布还额外留一版
`VerifiedAnswerRevision`——**那就是这两个动作的审计记录**。
除此之外这里不该再多一个写接口：留一个"暂时没人调用"的，
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

from copilot import corrections_flow as flow
from copilot import metrics
from copilot import verified as verified_svc
from copilot.api import providers
from copilot.auth.deps import CurrentAdmin, SessionDep
from copilot.db.models import (
    AnswerCorrection,
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


# ─────────────────────────── 纠错审核（M16）───────────────────────────


class CorrectionRow(BaseModel):
    """审核队列里的一行。列表只给「该不该点进去」需要的东西。"""

    id: uuid.UUID
    status: str
    version: int
    created_at: datetime
    updated_at: datetime
    submitted_by_email: str | None
    knowledge_space: str | None
    original_question: str
    reason: str
    reviewed_at: datetime | None


class CorrectionDetail(CorrectionRow):
    """详情：左右对比要用的全部内容。

    ⭐ 原引用和原配图是**提交那一刻的快照**，不是现查的。原答案所在的消息
    随时可能被用户删掉、trace 也会被 `prune-traces` 清掉——现查的话，
    审核界面会在最需要它的时候是空的。
    """

    original_answer: str
    original_citations: list | None
    original_images: list | None
    corrected_answer_markdown: str
    review_note: str | None
    trace_id: uuid.UUID | None
    message_id: uuid.UUID | None
    markdown: str


class CorrectionPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[CorrectionRow]


class ReviewIn(BaseModel):
    """审核一条纠错。

    ⚠️ `corrected_answer_markdown` 是**管理员的二次修改**（路线图 21.1）：
    用户写的十有八九不能直接发布——错别字、少一步、把客户名写了进去。
    只给「通过 / 拒绝」两个按钮的话，管理员为了改一个字只能拒绝再让人重提。
    """

    decision: Literal["approve", "reject"]
    note: str = ""
    corrected_answer_markdown: str | None = None
    # 乐观锁：两个管理员同时点「通过」和「拒绝」，后到的那个必须失败，
    # 而不是默默覆盖前一个的结论
    version: int | None = None


class PublishIn(BaseModel):
    version: int | None = None


class PublishOut(BaseModel):
    correction_id: uuid.UUID
    verified_id: uuid.UUID
    verified_version: int
    knowledge_space: str | None
    chunks: int
    applied: bool
    note: str


async def _space_codes(session, ids: set[uuid.UUID]) -> dict[uuid.UUID, str]:
    if not ids:
        return {}
    return dict(
        (
            await session.execute(
                select(KnowledgeSpace.id, KnowledgeSpace.code).where(KnowledgeSpace.id.in_(ids))
            )
        ).all()
    )


def _row(c: AnswerCorrection, email: str | None, space: str | None) -> CorrectionRow:
    return CorrectionRow(
        id=c.id,
        status=c.status,
        version=c.version,
        created_at=c.created_at,
        updated_at=c.updated_at,
        submitted_by_email=email,
        knowledge_space=space,
        original_question=c.original_question,
        reason=c.reason,
        reviewed_at=c.reviewed_at,
    )


@router.get("/corrections", response_model=CorrectionPage)
async def list_corrections(
    admin: CurrentAdmin,
    session: SessionDep,
    status_filter: Annotated[str, Query(alias="status", max_length=16)] = flow.PENDING,
    limit: Annotated[int, Query(ge=1, le=_MAX_LIMIT)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CorrectionPage:
    """审核队列。默认只看 `pending`——那是唯一需要人动手的一档。

    `status=all` 看全部。别的值必须是合法状态，否则 422：拼错一个字母就
    静默返回空列表的话，你会以为「没有待审的」，而其实是查错了。
    """
    where = []
    if status_filter != "all":
        if status_filter not in flow.STATUSES:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"没有这个状态：{status_filter}（可选 {'/'.join(flow.STATUSES)} 或 all）",
            )
        where.append(AnswerCorrection.status == status_filter)

    total = await _count(session, select(AnswerCorrection.id).where(*where))
    rows = list(
        (
            await session.execute(
                select(AnswerCorrection)
                .where(*where)
                .order_by(AnswerCorrection.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars()
    )
    emails = (
        dict(
            (
                await session.execute(
                    select(User.id, User.email).where(
                        User.id.in_({r.submitted_by for r in rows if r.submitted_by})
                    )
                )
            ).all()
        )
        if rows
        else {}
    )
    spaces = await _space_codes(
        session, {r.knowledge_space_id for r in rows if r.knowledge_space_id}
    )

    return CorrectionPage(
        total=total,
        limit=limit,
        offset=offset,
        items=[
            _row(c, emails.get(c.submitted_by), spaces.get(c.knowledge_space_id)) for c in rows
        ],
    )


async def _get_correction(session, correction_id: uuid.UUID) -> AnswerCorrection:
    row = await session.get(AnswerCorrection, correction_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "这条纠错不存在")
    return row


async def _detail(session, c: AnswerCorrection) -> CorrectionDetail:
    email = (
        await session.scalar(select(User.email).where(User.id == c.submitted_by))
        if c.submitted_by
        else None
    )
    spaces = await _space_codes(session, {c.knowledge_space_id} if c.knowledge_space_id else set())
    base = _row(c, email, spaces.get(c.knowledge_space_id))
    return CorrectionDetail(
        **base.model_dump(),
        original_answer=c.original_answer,
        original_citations=c.original_citations,
        original_images=c.original_images,
        corrected_answer_markdown=c.corrected_answer_markdown,
        review_note=c.review_note,
        trace_id=c.trace_id,
        message_id=c.message_id,
        markdown=flow.snapshot_markdown(c, submitted_by=email),
    )


@router.get("/corrections/{correction_id}", response_model=CorrectionDetail)
async def correction_detail(
    correction_id: uuid.UUID, admin: CurrentAdmin, session: SessionDep
) -> CorrectionDetail:
    return await _detail(session, await _get_correction(session, correction_id))


@router.post("/corrections/{correction_id}/review", response_model=CorrectionDetail)
async def review_correction(
    correction_id: uuid.UUID, body: ReviewIn, admin: CurrentAdmin, session: SessionDep
) -> CorrectionDetail:
    """通过或拒绝。**通过不等于发布**——发布是下一个接口，理由见状态机那一节。"""
    c = await _get_correction(session, correction_id)
    if body.version is not None and body.version != c.version:
        raise HTTPException(status.HTTP_409_CONFLICT, "这条纠错刚被人改过，请刷新后再看")

    target = flow.APPROVED if body.decision == "approve" else flow.REJECTED
    try:
        flow.check_transition(c.status, target)
    except flow.TransitionError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e

    # 管理员的二次修改（路线图 21.1）。改了什么写在 `review_note` 里——
    # 提交人回头看自己那条纠错时，得看得出被改过
    if body.corrected_answer_markdown is not None:
        if not (text := body.corrected_answer_markdown.strip()):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "修正内容不能为空")
        c.corrected_answer_markdown = text

    c.status = target
    c.version += 1
    c.reviewed_by = admin.id
    c.reviewed_at = datetime.now(UTC)
    c.review_note = body.note.strip() or None
    await session.commit()
    await session.refresh(c)
    return await _detail(session, c)


@router.post("/corrections/{correction_id}/publish", response_model=PublishOut)
async def publish_correction(
    correction_id: uuid.UUID, body: PublishIn, admin: CurrentAdmin, session: SessionDep
) -> PublishOut:
    """发布成标准答案：**从这一刻起同一个知识版本下的所有人都用它。**

    ⚠️ 一个事务：改纠错状态、写/更新标准答案、留一版修订、进索引。
    拆开的话会出现「纠错标成 published、标准答案却没建出来」，
    而那条纠错从此再也走不到发布——状态机不允许 published 再发布一次。
    """
    c = await _get_correction(session, correction_id)
    if body.version is not None and body.version != c.version:
        raise HTTPException(status.HTTP_409_CONFLICT, "这条纠错刚被人改过，请刷新后再看")
    if c.knowledge_space_id is None:
        # 没有空间的标准答案等于「谁都搜不到」（检索缺空间是 fail closed），
        # 而它看起来是发布成功的
        raise HTTPException(status.HTTP_409_CONFLICT, "这条纠错没有知识版本，不能发布")

    try:
        row, chunks = await verified_svc.publish_correction(
            session, c, admin_id=admin.id, embedder=providers.get_embedder()
        )
    except flow.TransitionError as e:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
    except Exception:
        # ⚠️ 进索引要打 embedding 接口，是这条路上唯一会因为外部原因失败的一步。
        # 失败必须**整个回滚**——半发布（状态变了、索引没建）比没发布糟得多：
        # 那条纠错从此卡在 published，而它的答案一个字都没生效
        await session.rollback()
        raise

    await session.commit()
    spaces = await _space_codes(
        session, {row.knowledge_space_id} if row.knowledge_space_id else set()
    )
    return PublishOut(
        correction_id=c.id,
        verified_id=row.id,
        verified_version=row.version,
        knowledge_space=spaces.get(row.knowledge_space_id),
        chunks=chunks,
        applied=chunks > 0,
        note=(
            "已发布。这个知识版本下的所有人，下次问到这个问题就会拿到这个答案。"
            if chunks > 0
            else "已发布，但索引没建成——下一次知识库同步会补上。"
        ),
    )
