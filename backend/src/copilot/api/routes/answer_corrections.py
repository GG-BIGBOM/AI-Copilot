"""答案纠错：用户提交，管理员审核（M16，用户这一侧）。

    POST   /api/answer-corrections            提交（只能纠自己会话里的回答）
    GET    /api/answer-corrections/mine       我提过的
    GET    /api/answer-corrections/{id}       看一条（自己的，或管理员）
    GET    /api/answer-corrections/{id}/markdown   审核快照
    PATCH  /api/answer-corrections/{id}       改内容 / 撤回（都只在 pending 时）

⚠️⚠️ **这条路取代了「提交即公共生效」。** 在 M16 之前，用户点「答错了，
我来改」会直接写 `verified_answers` 并当场进索引——任何注册用户都能往公共
知识库里塞任意内容，而站上没有任何地方看得出来。现在提交只是排进审核队列，
一个字都不进 RAG（`corrections_flow.LIVE` 里只有 `published`）。

⚠️ **快照是服务端自己取的，不信客户端。** 原问题、原回答、原引用、原配图、
知识版本，全部从「这条 message 所属的会话」上读——客户端只给
`message_id` + 改成什么样 + 为什么。让客户端把原答案一起传上来的话，
它可以伪造一个从未存在过的"原答案"，而审核界面上看不出真假。

⚠️ **只能纠自己会话里的 assistant 消息。** 别人的一律当**不存在**（404）：
403 等于告诉对方「这个 id 是真的」，而 message_id 会随 SSE 发到浏览器里。
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select

from copilot import corrections_flow as flow
from copilot.auth.deps import CurrentUser, SessionDep
from copilot.db.models import AnswerCorrection, Conversation, Message, RequestTrace

router = APIRouter(prefix="/api/answer-corrections", tags=["corrections"])

NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "这条纠错不存在")


class CorrectionIn(BaseModel):
    """提交一条纠错。

    `reason` 是**必填**的，和管理员直接写标准答案那边不一样：那边是
    「我说了算」，这边是「请人看一眼再对全站生效」——审核的人需要知道原来
    错在哪，否则他只能把两段文字读一遍自己猜。

    ⚠️ **`traceId` 和 `messageId` 给一个就行，优先用 traceId。**
    前端手上一直有 trace id（它随 SSE 的第一个片段就发过去了，翻历史时也带），
    而 assistant 消息的库内 id 前端**没有**——为了这个接口专门再往流里塞一个
    id，等于为了后端方便去改一条所有人都在用的协议。
    """

    trace_id: uuid.UUID | None = Field(default=None, alias="traceId")
    message_id: uuid.UUID | None = Field(default=None, alias="messageId")
    corrected_answer_markdown: str = Field(alias="correctedAnswer", min_length=1, max_length=20000)
    reason: str = Field(min_length=1, max_length=1000)

    @field_validator("corrected_answer_markdown", "reason")
    @classmethod
    def _strip(cls, v: str) -> str:
        if not (out := v.strip()):
            raise ValueError("不能只填空白")
        return out

    @model_validator(mode="after")
    def _need_one(self) -> CorrectionIn:
        if self.trace_id is None and self.message_id is None:
            raise ValueError("要么给 traceId，要么给 messageId")
        return self


class CorrectionPatch(BaseModel):
    """改自己的纠错，或者撤回它。两件事共用一个接口——它们都只在 pending 时可做。"""

    corrected_answer_markdown: str | None = Field(
        default=None, alias="correctedAnswer", max_length=20000
    )
    reason: str | None = Field(default=None, max_length=1000)
    action: Literal["withdraw"] | None = None
    # 乐观锁：手上这份是第几版。管理员同时在审的话，后到的那个必须失败
    version: int | None = None


class CorrectionOut(BaseModel):
    id: uuid.UUID
    status: str
    version: int
    trace_id: uuid.UUID | None
    message_id: uuid.UUID | None
    knowledge_space_id: uuid.UUID | None
    original_question: str
    original_answer: str
    original_citations: list | None
    original_images: list | None
    corrected_answer_markdown: str
    reason: str
    review_note: str | None
    reviewed_at: datetime | None
    created_at: datetime
    updated_at: datetime


async def _owned(session, correction_id: uuid.UUID, user) -> AnswerCorrection:
    row = await session.get(AnswerCorrection, correction_id)
    if row is None:
        raise NOT_FOUND
    # 管理员能看所有人的——审核就是这么做的；别人的一律当不存在
    if row.submitted_by != user.id and not user.is_admin:
        raise NOT_FOUND
    return row


@router.post("", response_model=CorrectionOut, status_code=status.HTTP_201_CREATED)
async def submit(body: CorrectionIn, user: CurrentUser, session: SessionDep) -> AnswerCorrection:
    """提交一条纠错。**进审核队列，不立刻生效。**"""
    message_id, trace_question = body.message_id, None
    if body.trace_id is not None:
        trace = await session.get(RequestTrace, body.trace_id)
        # 别人的 trace 一律当不存在。trace id 会随 SSE 发到浏览器里，
        # 403 等于确认「这个 id 是真的」
        if trace is None or trace.user_id != user.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "这条回答不存在")
        if trace.message_id is None:
            # 台账写完了但没记上消息 id（流被打断、或者那条消息已经删了）。
            # 这不是错误，只是这一轮没法纠——说清楚，别让人对着一个失败的按钮猜
            raise HTTPException(status.HTTP_409_CONFLICT, "这一轮的回答没有存下来，没法纠错")
        message_id, trace_question = trace.message_id, trace.question

    msg = await session.get(Message, message_id)
    if msg is None or msg.role != "assistant":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "这条回答不存在")

    conv = await session.get(Conversation, msg.conversation_id)
    if conv is None or conv.user_id != user.id:
        # 别人会话里的消息一律当不存在，理由见模块文件头
        raise HTTPException(status.HTTP_404_NOT_FOUND, "这条回答不存在")

    # 原问题 = 这条回答**前面最近的那句 user 消息**。
    # 不让客户端传：它可以伪造一个从未问过的问题，而那会变成一条
    # 谁都没问过、却对全站生效的标准答案
    question = (
        await session.execute(
            select(Message.content)
            .where(
                Message.conversation_id == conv.id,
                Message.role == "user",
                Message.created_at <= msg.created_at,
                Message.id != msg.id,
            )
            .order_by(Message.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    # 台账里那句是兜底：用户把自己那条提问删掉时，会话里就找不到原问题了，
    # 而台账那份是当时如实记下来的（可能被截断，见 `trace.QUESTION_LIMIT`）
    question = question or trace_question
    if not question:
        raise HTTPException(status.HTTP_409_CONFLICT, "找不到这条回答对应的问题，没法纠错")

    trace_id = body.trace_id or (
        await session.execute(
            select(RequestTrace.id).where(RequestTrace.message_id == msg.id).limit(1)
        )
    ).scalar_one_or_none()

    row = AnswerCorrection(
        trace_id=trace_id,
        conversation_id=conv.id,
        message_id=msg.id,
        submitted_by=user.id,
        # ⚠️ 空间从会话上抄（会话创建时就钉死了）。让客户端传的话，
        # 用户可以把旗舰版的修正提交到企业版下面
        knowledge_space_id=conv.knowledge_space_id,
        original_question=question,
        original_answer=msg.content,
        original_citations=msg.citations,
        original_images=msg.images,
        corrected_answer_markdown=body.corrected_answer_markdown,
        reason=body.reason,
        status=flow.PENDING,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return row


@router.get("/mine", response_model=list[CorrectionOut])
async def mine(
    user: CurrentUser,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[AnswerCorrection]:
    stmt = (
        select(AnswerCorrection)
        .where(AnswerCorrection.submitted_by == user.id)
        .order_by(AnswerCorrection.created_at.desc())
        .limit(limit)
    )
    return list((await session.execute(stmt)).scalars())


@router.get("/{correction_id}", response_model=CorrectionOut)
async def get_one(
    correction_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> AnswerCorrection:
    return await _owned(session, correction_id, user)


@router.get("/{correction_id}/markdown")
async def get_markdown(
    correction_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> dict[str, str]:
    """审核快照（路线图第 15 节）。**只是快照，事实来源仍是数据库。**"""
    row = await _owned(session, correction_id, user)
    return {"markdown": flow.snapshot_markdown(row)}


@router.patch("/{correction_id}", response_model=CorrectionOut)
async def edit(
    correction_id: uuid.UUID,
    body: CorrectionPatch,
    user: CurrentUser,
    session: SessionDep,
) -> AnswerCorrection:
    """改自己的纠错，或者撤回。

    ⚠️ **只在 `pending` 时可做。** 审核通过之后还能改内容的话，
    管理员看过的和最终发布的就不是同一段文字——那等于没有审核。
    """
    row = await _owned(session, correction_id, user)
    if row.submitted_by != user.id and not user.is_admin:
        raise NOT_FOUND
    if body.version is not None and body.version != row.version:
        raise HTTPException(status.HTTP_409_CONFLICT, "这条纠错刚被改过，请刷新后再试")

    if body.action == "withdraw":
        try:
            flow.check_transition(row.status, flow.WITHDRAWN)
        except flow.TransitionError as e:
            raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
        row.status = flow.WITHDRAWN
        row.version += 1
        await session.commit()
        await session.refresh(row)
        return row

    if row.status not in flow.EDITABLE:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"已经是「{row.status}」的纠错不能再改内容"
        )

    if body.corrected_answer_markdown is not None:
        if not (text := body.corrected_answer_markdown.strip()):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "修正内容不能为空")
        row.corrected_answer_markdown = text
    if body.reason is not None:
        if not (reason := body.reason.strip()):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "修改原因不能为空")
        row.reason = reason

    row.version += 1
    row.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(row)
    return row
