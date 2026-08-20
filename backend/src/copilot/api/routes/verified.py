"""答案订正：把某个回答改对，下次问到同类问题就照这个答。

**为什么要有它，而勘误层不够用。**
勘误层改的是「某一篇语雀原文」——要选一篇文档、把整篇正文重写一遍。
为了改一句话重写一整篇，太重了。而用户真正想做的是：

    这个答案不对 → 我改成对的 → 下次照这个答

所以这里存的是**问答对**，不是文档。

⚠️ **它就是一条知识，走的是和别的知识完全一样的路。** 存成一篇
`source_type="verified"` 的文档 + 一个块，照常向量化、照常参与检索、
照常被引用。**不另建一套检索机制**——另建一套就意味着两条召回路径、
两套隔离规则，而隔离是这个项目唯一一条错了就不可挽回的规则。

⚠️ **订正是公共的**（`owner_id=None`），因为它要盖住所有人的错误答案。
所以每条都记作者、都能在列表里看到、都能删。这和勘误层同一个取舍：
内部邀请制工具，同事之间的可见性就是 review。
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, select, update

from copilot.api import providers
from copilot.api.schemas import VerifiedIn, VerifiedOut, VerifiedSaved
from copilot.auth.deps import CurrentUser, SessionDep
from copilot.db.models import Chunk, Document, VerifiedAnswer
from copilot.ingest.pipeline import write_chunks

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/verified", tags=["verified"])

SOURCE_TYPE = "verified"

# 进索引的正文。**问题也要写进去**——检索是拿用户这次的问法去比对的，
# 只放答案的话，答案里未必出现问题里的词，相似问题就召不回它。
BODY_TEMPLATE = """问：{question}

答：{answer}"""


def _title(question: str) -> str:
    q = " ".join(question.split())
    return f"已订正 · {q[:60]}"


async def _index(session, row: VerifiedAnswer) -> int:
    """把这条订正写进检索索引（新建或整体替换它自己的块）。"""
    doc = (
        await session.execute(
            select(Document).where(
                Document.source_type == SOURCE_TYPE, Document.source_url == str(row.id)
            )
        )
    ).scalar_one_or_none()

    if doc is None:
        doc = Document(
            owner_id=None,  # 公共：它要盖住所有人的错误答案
            source_type=SOURCE_TYPE,
            title=_title(row.question),
            # 没有真实外链，用订正自己的 id 当对齐键——重复订正时能找回同一篇
            source_url=str(row.id),
            content_hash=uuid.uuid4().hex,
            status="done",
        )
        session.add(doc)
    else:
        doc.title = _title(row.question)
        doc.content_hash = uuid.uuid4().hex

    body = BODY_TEMPLATE.format(question=row.question, answer=row.answer)
    n = await write_chunks(session, doc, body, providers.get_embedder())
    # ⭐ 打上 verified：检索靠它把这条排到语雀原文前面（`retrieve._verified_first`）。
    # 漏了这一步的表现最气人——保存说"已生效"，再问一遍答案却没变
    await session.execute(update(Chunk).where(Chunk.document_id == doc.id).values(verified=True))
    doc.chunk_count = n
    await session.commit()
    return n


async def _unindex(session, row_id: uuid.UUID) -> None:
    doc = (
        await session.execute(
            select(Document).where(
                Document.source_type == SOURCE_TYPE, Document.source_url == str(row_id)
            )
        )
    ).scalar_one_or_none()
    if doc is None:
        return
    await session.execute(delete(Chunk).where(Chunk.document_id == doc.id))
    await session.delete(doc)
    await session.commit()


@router.get("", response_model=list[VerifiedOut])
async def list_verified(user: CurrentUser, session: SessionDep) -> list[VerifiedAnswer]:
    """所有人的订正都列出来——它们影响的是同一个知识库。"""
    stmt = select(VerifiedAnswer).order_by(VerifiedAnswer.updated_at.desc())
    return list((await session.execute(stmt)).scalars())


@router.post("", response_model=VerifiedSaved, status_code=status.HTTP_201_CREATED)
async def save_verified(
    body: VerifiedIn, user: CurrentUser, session: SessionDep
) -> VerifiedSaved:
    """记下一条订正，并**立刻**让它进索引。

    同一个问题再订正一次是更新，不是新增——否则同一个问题会有好几条
    互相打架的"标准答案"，而检索只会随机命中其中一条。
    """
    question = body.question.strip()
    existing = (
        await session.execute(select(VerifiedAnswer).where(VerifiedAnswer.question == question))
    ).scalar_one_or_none()

    if existing is None:
        existing = VerifiedAnswer(question=question)
        session.add(existing)

    existing.answer = body.answer.strip()
    existing.author_id = user.id
    await session.commit()

    # 进索引失败**不回滚订正**：内容已经存下来了，下一次全量 ingest 一样能补上。
    # 反过来把订正也删掉的话，用户白改一遍，还不知道为什么
    try:
        chunks = await _index(session, existing)
    except Exception:  # noqa: BLE001 - embedding 挂了不该让保存看起来失败
        logger.exception("订正已保存但进索引失败：%s", question[:60])
        chunks = -1

    await session.refresh(existing)
    return VerifiedSaved(
        verified=VerifiedOut.model_validate(existing),
        applied=chunks > 0,
        note=(
            "已生效。下次问到这个问题，就会用你改过的答案。"
            if chunks > 0
            else "已保存，但索引没建成，下一次知识库同步会自动补上。"
        ),
    )


@router.delete("/{verified_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_verified(verified_id: str, user: CurrentUser, session: SessionDep) -> None:
    """撤销一条订正：知识库立刻回到原来的样子。"""
    try:
        vid = uuid.UUID(verified_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订正不存在") from e

    row = await session.get(VerifiedAnswer, vid)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "订正不存在")

    await session.delete(row)
    await session.commit()
    # 索引里那一份也要删干净，否则用户以为撤销了、答案却没变
    await _unindex(session, vid)
