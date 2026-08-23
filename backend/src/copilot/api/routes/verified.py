"""标准答案（VerifiedAnswer）：某个问题的正确答案，由人写定。

和 `Correction` 的区别，一句话：

    Correction        改的是「哪一篇文档」    要选文档、重写整篇正文
    VerifiedAnswer    改的是「哪一个问题」    这个问题以后就照这么答

⚠️⚠️ **M16 起这条路只对管理员开放。**
在那之前，任何登录用户点「答错了，我来改」就能让一段文字对全站立刻生效、
无人审核——任何注册用户都能往公共知识库里塞任意内容，而站上没有任何地方
看得出来。现在普通用户走的是 `POST /api/answer-corrections`（进审核队列），
管理员审完再发布；这里剩下的是管理员**直接写一条**的入口，
它等价于「自己提、自己审、自己发」，所以照样留修订记录。

⚠️ **它不是检索之外的另一条路。** 发布后会写成一篇 `source_type="verified"`
的公共文档 + 若干块，照常向量化、照常参与检索、照常被引用。**别为它单开一套
召回**——单开就意味着两条召回路径、两套隔离规则，而隔离是这个项目里唯一
一条错了就不可挽回的规则。（终结命中那条路见 `copilot/verified.py` 的
`lookup()`：它命中时直接返回人写定的答案，不再交给模型改写，但**不改变
任何可见性规则**——只在同一个知识版本里查。）

⚠️ **删除是退役，不是抹掉。** 行留着、块删掉：半年后要能回答
「当初这条是怎么写的、谁发布的、为什么退役」。
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from copilot import spaces
from copilot import verified as verified_svc
from copilot.api import providers
from copilot.api.schemas import VerifiedIn, VerifiedOut, VerifiedSaved
from copilot.auth.deps import CurrentAdmin, CurrentUser, SessionDep
from copilot.db.models import KnowledgeSpace, VerifiedAnswer, VerifiedAnswerRevision

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/verified", tags=["verified"])

# 兼容旧引用：这两个常量原来定义在本模块
SOURCE_TYPE = verified_svc.SOURCE_TYPE
BODY_TEMPLATE = verified_svc.BODY_TEMPLATE


class RevisionOut(BaseModel):
    version: int
    question: str
    answer: str
    status: str
    note: str | None
    created_at: datetime


@router.get("", response_model=list[VerifiedOut])
async def list_verified(user: CurrentUser, session: SessionDep) -> list[VerifiedAnswer]:
    """所有人的标准答案都列出来——它们影响的是同一个知识库。

    普通用户也能看：**同事可见性就是这个内部工具的 review**（同勘误层的取舍）。
    能看不等于能改，写入口在上面那段注释里说清了。
    """
    stmt = select(VerifiedAnswer).order_by(VerifiedAnswer.updated_at.desc())
    return list((await session.execute(stmt)).scalars())


@router.get("/{verified_id}/revisions", response_model=list[RevisionOut])
async def list_revisions(
    verified_id: uuid.UUID, user: CurrentUser, session: SessionDep
) -> list[VerifiedAnswerRevision]:
    """这条标准答案改过几版、每版是谁改的、为什么。

    ⭐ 「修订可追溯」的读侧。半年后有人问「这一步为什么是这样写的」，
    要的不是「最后一次是谁改的」，而是每一次改了什么。
    """
    stmt = (
        select(VerifiedAnswerRevision)
        .where(VerifiedAnswerRevision.verified_answer_id == verified_id)
        .order_by(VerifiedAnswerRevision.version.desc())
    )
    return list((await session.execute(stmt)).scalars())


@router.post("", response_model=VerifiedSaved, status_code=status.HTTP_201_CREATED)
async def save_verified(
    body: VerifiedIn, admin: CurrentAdmin, session: SessionDep
) -> VerifiedSaved:
    """管理员直接写一条标准答案，并**立刻**让它生效。

    ⚠️ 普通用户到这里是 403，不是 404：这个接口存不存在不是秘密，
    藏起来只会让人以为是自己的问题。前端也不该把这个入口摆给普通用户
    ——他们那边是「答错了，我来改」，走审核队列。

    同一个空间里同一个问题再写一次是**更新 + 加一版**，不是新增：
    两条互相打架的标准答案会让检索随机命中其中一条，
    表现是「答案时好时坏」，最难查。
    """
    question = body.question.strip()
    space_code = (body.space or spaces.FLAGSHIP).strip()
    space_id = await session.scalar(
        select(KnowledgeSpace.id).where(KnowledgeSpace.code == space_code)
    )
    if space_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"没有这个知识版本：{space_code}")

    row = (
        await session.execute(
            select(VerifiedAnswer).where(
                VerifiedAnswer.question == question,
                VerifiedAnswer.knowledge_space_id == space_id,
            )
        )
    ).scalar_one_or_none()

    if row is None:
        row = VerifiedAnswer(question=question, knowledge_space_id=space_id, version=0)
        session.add(row)

    row.answer = body.answer.strip()
    row.author_id = admin.id
    row.status = verified_svc.ACTIVE
    row.version = (row.version or 0) + 1
    await session.flush()
    verified_svc.record_revision(session, row, editor_id=admin.id, note="管理员直接写入")
    # ⭐ **内容先落地，再建索引。** 顺序反过来（一个事务包住两件事）的话，
    # embedding 接口一抽风，管理员刚写的那段文字就跟着回滚没了——
    # 而他不会知道，只会看到一句「保存失败」。内容在库里的话，
    # 下一次全量 ingest 一样能把索引补上
    await session.commit()

    try:
        chunks = await verified_svc.sync_index(session, row, providers.get_embedder())
        await session.commit()
    except Exception:  # noqa: BLE001 - embedding 挂了不该让保存看起来失败
        logger.exception("标准答案已保存但进索引失败：%s", question[:60])
        await session.rollback()
        chunks = -1

    await session.refresh(row)
    return VerifiedSaved(
        verified=VerifiedOut.model_validate(row),
        applied=chunks > 0,
        note=(
            "已生效。这个知识版本下的所有人，下次问到这个问题就会拿到这个答案。"
            if chunks > 0
            else "已保存，但索引没建成，下一次知识库同步会自动补上。"
        ),
    )


@router.delete("/{verified_id}", status_code=status.HTTP_204_NO_CONTENT)
async def retire_verified(verified_id: str, admin: CurrentAdmin, session: SessionDep) -> None:
    """退役一条标准答案：**立刻从检索里消失，行留着。**

    留行是为了追溯（谁发布的、当初写了什么、什么时候退的）。块必须删干净，
    否则「我退役了它，答案却没变」——而那时你会去查检索、查 prompt，
    查不到这里。
    """
    try:
        vid = uuid.UUID(verified_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "这条标准答案不存在") from e

    row = await session.get(VerifiedAnswer, vid)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "这条标准答案不存在")

    row.status = verified_svc.RETIRED
    row.version += 1
    verified_svc.record_revision(session, row, editor_id=admin.id, note="退役")
    await verified_svc.sync_index(session, row, providers.get_embedder())
    await session.commit()
