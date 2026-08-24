"""答案纠错：用户提交，管理员审核（M16，用户这一侧）。

    POST   /api/answer-corrections            提交（只能纠自己会话里的回答）
    POST   /api/answer-corrections/images     贴一张截图（先传，提交时才绑定）
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

import hashlib
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, UploadFile, status
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import func, select

from copilot import assets
from copilot import corrections_flow as flow
from copilot.auth.deps import CurrentUser, SessionDep
from copilot.config import get_settings
from copilot.db.models import AnswerCorrection, Conversation, ImageAsset, Message, RequestTrace

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


class CorrectionImageOut(BaseModel):
    """传完一张图之后给前端的东西。

    `markdown` 是一段可以直接插进光标处的文本——前端不该自己去拼这个格式，
    拼错了（比如少个感叹号）表现是正文里出现一段裸链接，而不是一张图。
    """

    id: uuid.UUID
    url: str
    markdown: str


# 正文里引用一张纠错图的形状：`/api/images/{uuid}`。
# ⚠️ 只认这一种。用户可以在 Markdown 里写任何图片地址（外链、公共图路径），
# 那些**不进绑定流程**：绑定的意思是"这张图归这条纠错管，删纠错时一起删"，
# 而我们只对自己收下来的那些负这个责
_IMAGE_REF_RE = re.compile(r"/api/images/([0-9a-fA-F-]{36})")


def referenced_image_ids(markdown: str) -> list[uuid.UUID]:
    """正文里引用到的纠错图 id，按出现顺序去重。"""
    out: list[uuid.UUID] = []
    for raw in _IMAGE_REF_RE.findall(markdown or ""):
        try:
            ident = uuid.UUID(raw)
        except ValueError:  # pragma: no cover - 正则已经限定了形状
            continue
        if ident not in out:
            out.append(ident)
    return out


async def bind_images(session, row: AnswerCorrection, user) -> None:
    """把正文里引用到的图绑到这条纠错上，并解绑已经被删掉的那些。

    ⚠️ **别人的图一律拒绝，而且是 400 不是"悄悄忽略"。** 悄悄忽略的表现是
    审核界面上有一张图、而它属于另一个用户——图片本身仍然由
    `/api/images/{id}` 按 owner 鉴权（管理员也看不到别人的私有图），
    于是审核的人看到的是一个裂图，还以为是自己网络的问题。

    ⚠️ **解绑不删文件。** 用户改稿时删掉一张图、又改回来是常事；
    行留着（悬空），由 `prune-junk` 按时间清。这里立刻删的话，
    撤销一步就再也找不回来了。
    """
    wanted = referenced_image_ids(row.corrected_answer_markdown)
    if wanted:
        rows = list(
            (
                await session.execute(select(ImageAsset).where(ImageAsset.id.in_(wanted)))
            ).scalars()
        )
        found = {r.id for r in rows}
        if missing := [i for i in wanted if i not in found]:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"引用了不存在的图片：{missing[0]}")
        for asset in rows:
            if asset.owner_id != user.id:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "引用了不属于你的图片")
            if asset.correction_id not in (None, row.id):
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "这张图已经挂在另一条纠错上了")
            asset.correction_id = row.id
            asset.source = "correction"

    # 稿子里已经不再引用的，解回悬空
    for asset in list(
        (
            await session.execute(
                select(ImageAsset).where(ImageAsset.correction_id == row.id)
            )
        ).scalars()
    ):
        if asset.id not in wanted:
            asset.correction_id = None


@router.post(
    "/images", response_model=CorrectionImageOut, status_code=status.HTTP_201_CREATED
)
async def upload_image(
    file: UploadFile, user: CurrentUser, session: SessionDep
) -> CorrectionImageOut:
    """贴一张截图。**先传、后绑**：这一刻还没有 correction 行可挂。

    ⚠️ **按魔数收，不按扩展名收**（`assets.sniff_image`）。文件名和
    Content-Type 都是上传方写的——一个叫 `x.png`、内容是 HTML 的文件，
    会被我们以 `image/png` 发回给别人的浏览器。

    ⚠️ **落在私有目录**（`data/private-images/`），和上传文档里的嵌图同一处。
    公共目录是 nginx 直发的，谁猜中文件名谁就能取；纠错稿在**审核通过之前**
    只有本人和管理员该看得到。发布时才搬到公共目录去（那一步在 M17.1 P1）。
    """
    s = get_settings()
    data = await file.read(s.correction_image_max_bytes + 1)
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "文件是空的")
    if len(data) > s.correction_image_max_bytes:
        raise HTTPException(
            status.HTTP_413_CONTENT_TOO_LARGE,
            f"图片超过 {s.correction_image_max_bytes // 1024 // 1024}MB 上限",
        )
    kind = assets.sniff_image(data)
    if kind is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "只收 png / jpg / gif / webp / bmp 图片")
    suffix, mime = kind

    # 悬空图的配额。传了不提交的图没有任何行指向它，只能靠时间清——
    # 没有这道闸，清理之前的那段时间是敞开的
    pending = await session.scalar(
        select(func.count(ImageAsset.id)).where(
            ImageAsset.owner_id == user.id,
            ImageAsset.correction_id.is_(None),
            ImageAsset.document_id.is_(None),
        )
    )
    if (pending or 0) >= s.correction_images_pending_max:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"还没提交的截图太多了（上限 {s.correction_images_pending_max} 张），先把纠错提交掉",
        )

    rel, _url = assets.store_bytes(data, suffix, private=True)
    # ⭐ 同一张图传两次只留一行。图按内容寻址，两次传同一张截图落的是同一个
    # 文件——再建一行的话，两行指着同一个文件，删掉其中一条纠错时另一行就
    # 指向了一个已经被删掉的文件。而且同一条纠错里绑两行同路径会直接撞上
    # `ux_image_assets_correction_path`，表现是"再传一次就 500"。
    if existing := (
        await session.execute(
            select(ImageAsset).where(
                ImageAsset.owner_id == user.id,
                ImageAsset.storage_path == rel,
                ImageAsset.correction_id.is_(None),
                ImageAsset.document_id.is_(None),
            )
        )
    ).scalars().first():
        return CorrectionImageOut(
            id=existing.id,
            url=f"{assets.API_PREFIX}/{existing.id}",
            markdown=f"![截图]({assets.API_PREFIX}/{existing.id})",
        )

    row = ImageAsset(
        document_id=None,
        correction_id=None,
        source="correction",
        # ⚠️ 鉴权只看这一列（见 `routes/images.py`）。写成 None 就是把这张
        # 截图变成公共图——而它现在物理上躺在私有目录里，表现会是"图裂了"，
        # 但那是运气好：路径规则一变就是真泄漏
        owner_id=user.id,
        storage_path=rel,
        mime_type=mime,
        sha256=hashlib.sha256(data).hexdigest(),
        file_size=len(data),
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return CorrectionImageOut(
        id=row.id,
        url=f"{assets.API_PREFIX}/{row.id}",
        markdown=f"![截图]({assets.API_PREFIX}/{row.id})",
    )


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
    # 先 flush 拿到 id，图才有东西可绑
    await session.flush()
    await bind_images(session, row, user)
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
        # 改了稿子就要重新绑一遍：新贴的图要挂上，删掉的那些要解回悬空。
        # 漏了这一步的表现是"删掉的图在审核界面上还在"——审的和发的不是同一份
        await bind_images(session, row, user)
    if body.reason is not None:
        if not (reason := body.reason.strip()):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "修改原因不能为空")
        row.reason = reason

    row.version += 1
    row.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(row)
    return row
