"""勘误接口：知识库里某一篇写错了，在网页上改掉它。

**为什么需要这条路。** 勘误层本来只有一条路：编辑 `corrections/<slug>.md`
→ 重新 ingest → 部署。那要求你有仓库、会跑命令、还得等一次上线。
实施顾问在客户现场发现「原文写的上限是 100，实际是 300」时，这条路用不上。

三个必须记住的点：

1. **不能往服务器的 `corrections/` 目录写文件。**
   deploy.sh 第 4 步是 `rm -rf $APP_DIR/corrections` 再从仓库解包——
   写进去的下次上线就没了，而且**没有任何提示**。所以落库。

2. **写完要立刻生效。** 勘误是在 ingest 时盖到原文上的，光写库的话，
   用户改完再问同一个问题，答案一个字都不会变——他只会以为这个功能是假的。
   所以保存之后**当场把那一篇重新入库**（切分 + 向量化 + 换掉旧块）。
   一篇文档大约 5–10 块，一次 embedding 批量就够，几秒的事。

3. **改的是公共知识库，对所有人生效。** 所以每条都记作者、都要求写理由，
   列表页人人可见、可删。这是内部邀请制工具，同事之间的可见性就是 review。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from copilot.api import providers
from copilot.api.schemas import CorrectionIn, CorrectionOut, CorrectionSaved
from copilot.auth.deps import CurrentUser, SessionDep
from copilot.config import get_settings
from copilot.db.models import Correction as CorrectionRow

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/corrections", tags=["corrections"])


async def _reingest_one(session, target_url: str) -> int:
    """把这一篇语雀文档按最新勘误重新入库，返回块数。

    ⭐ 走的是和 `copilot ingest` **完全一样**的那条链路（load → apply → ingest），
    只是把输入缩到一篇。另写一套简化版的话，两条路迟早在切分参数或
    隔离规则上分叉——而分叉的表现是「网页改的和命令行改的效果不一样」。

    找不到那篇（语雀换了地址、或本机没同步过原文）返回 -1，由调用方决定怎么说。
    """
    from copilot.ingest.corrections import apply_corrections, load_db_corrections
    from copilot.ingest.pipeline import ingest_documents, load_yuque_dir

    root = get_settings().data_dir / "raw" / "yuque"
    if not root.exists():
        return -1

    docs = [d for d in load_yuque_dir(root) if d.source_url == target_url]
    if not docs:
        return -1

    corrections = await load_db_corrections(session)
    docs, _applied, _missed = apply_corrections(docs, corrections)
    if not docs:
        return 0  # 这条勘误是 retired，整篇作废，没有内容再入库

    # force=True：内容变了但 content_hash 的判定走的是原文，不强制会被跳过
    stats = await ingest_documents(
        session, docs, providers.get_embedder(), owner_id=None, force=True
    )
    return stats.chunks


@router.get("", response_model=list[CorrectionOut])
async def list_corrections(user: CurrentUser, session: SessionDep) -> list[CorrectionRow]:
    """所有人的勘误都列出来。

    **不按作者过滤**：它们改的是同一个公共知识库，谁改了什么大家都该看得见。
    藏起来的话，两个人对同一篇的不同理解会在库里打架，而谁都看不出来。
    """
    stmt = select(CorrectionRow).order_by(CorrectionRow.updated_at.desc())
    return list((await session.execute(stmt)).scalars())


@router.post("", response_model=CorrectionSaved, status_code=status.HTTP_201_CREATED)
async def save_correction(
    body: CorrectionIn, user: CurrentUser, session: SessionDep
) -> CorrectionSaved:
    """新建或更新一条勘误，并**立刻**让它生效。"""
    existing = (
        await session.execute(
            select(CorrectionRow).where(CorrectionRow.target_url == body.target_url)
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = CorrectionRow(target_url=body.target_url)
        session.add(existing)

    existing.author_id = user.id
    existing.title = body.title
    existing.reason = body.reason
    existing.body = body.body
    await session.commit()

    # 重新入库失败**不回滚勘误**：内容已经存下来了，下一次全量 ingest 一样会生效。
    # 反过来把勘误也删掉的话，用户白写一遍，还不知道为什么
    try:
        chunks = await _reingest_one(session, body.target_url)
    except Exception:  # noqa: BLE001 - embedding 挂了不该让保存看起来失败
        logger.exception("勘误已保存但重新入库失败：%s", body.target_url)
        chunks = -2

    await session.refresh(existing)
    return CorrectionSaved(
        correction=CorrectionOut.model_validate(existing),
        chunks=max(chunks, 0),
        applied=chunks >= 0,
        note=_note_for(chunks),
    )


def _note_for(chunks: int) -> str:
    if chunks >= 0:
        return "已生效，现在提问就会用改过的内容。"
    if chunks == -1:
        return "已保存，但没在语雀原文里找到这个地址，暂时不会生效——检查一下链接对不对。"
    return "已保存，但重新入库失败了，下一次同步会自动补上。"


@router.delete("/{correction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_correction(correction_id: str, user: CurrentUser, session: SessionDep) -> None:
    """撤销一条勘误：删掉之后那一篇立刻回到语雀原文。"""
    import uuid as _uuid

    try:
        cid = _uuid.UUID(correction_id)
    except ValueError as e:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "勘误不存在") from e

    row = await session.get(CorrectionRow, cid)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "勘误不存在")

    target_url = row.target_url
    await session.delete(row)
    await session.commit()

    # 删完要**再入库一次**，否则库里留着的还是改过的内容——
    # 用户以为撤销了，实际上答案没变
    try:
        await _reingest_one(session, target_url)
    except Exception:  # noqa: BLE001
        logger.exception("勘误已删除但重新入库失败：%s", target_url)
