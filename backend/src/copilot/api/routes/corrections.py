"""文档勘误：知识库里某一篇写错了，在网页上提一条修改。

**为什么需要这条路。** 勘误层本来只有一条路：编辑 `corrections/<slug>.md`
→ 重新 ingest → 部署。那要求你有仓库、会跑命令、还得等一次上线。
实施顾问在客户现场发现「原文写的上限是 100，实际是 300」时，这条路用不上。

四个必须记住的点：

1. **不能往服务器的 `corrections/` 目录写文件。**
   deploy.sh 第 4 步是 `rm -rf $APP_DIR/corrections` 再从仓库解包——
   写进去的下次上线就没了，而且**没有任何提示**。所以落库。

2. ⭐⭐ **提交 ≠ 生效。任何登录用户都可以提，但要过审才影响公共知识库。**

   这条路走过两个都不对的极端，值得把两次都写下来：

       ~2026-09-02   `CurrentUser` + 保存即重新入库
                     任何拿到邀请码的人都能把一篇语雀原文**整个换掉**，
                     当场对全站生效、无人审核，还能删掉别人写的勘误
       2026-09-03    收紧成 `CurrentAdmin`
                     安全了，但**把用户挡在了纠错之外**——而发现原文写错的
                     恰恰是天天在用的那些人，不是管理员

   两次错在同一个地方：把「提交修改」和「修改公共事实」当成了一种权限。
   现在拆开：**提交人人可做，生效只有管理员能给**。

       POST   /api/corrections            登录用户，落成 pending，**一个字都不进 RAG**
       GET    /api/corrections            已发布的（人人可见）+ 自己的（任何状态）
       GET    /api/corrections/mine       只看自己的
       PATCH  /api/corrections/{id}       改自己的 pending，或撤回
       DELETE /api/corrections/{id}       **管理员**：撤销一条已发布的（软撤销）

   审核和发布在管理台那一侧：`/api/admin/doc-corrections/*`。

3. **发布之后要立刻生效。** 勘误是在 ingest 时盖到原文上的，光改状态的话，
   管理员点完发布再问同一个问题，答案一个字都不会变——他只会以为发布是假的。
   所以**发布那一步**当场把那一篇重新入库（切分 + 向量化 + 换掉旧块）。
   ⚠️ 注意这件事**从 POST 挪到了 publish**：提交那一刻绝不碰公共知识库。

4. **撤销不做物理删除。** 已经生效过的东西，「谁发布的、什么时候、发过什么、
   什么时候撤的、为什么撤」都要留着。所以 DELETE 是软撤销
   （`status='retired'`，`published_at` **不清空**），并重新入库把那一篇
   还原成语雀原文。

状态图在 `corrections_flow.DOC_STATE_MACHINE`（和答案纠错共用同一个模块、
同一套审计字段口径，但各有一张迁移表——理由见那个文件的文件头）。
"""

from __future__ import annotations

import logging
import uuid as _uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import or_, select

from copilot import corrections_flow as flow
from copilot.api import providers
from copilot.api.schemas import (
    CorrectionIn,
    CorrectionOut,
    CorrectionPatch,
    CorrectionSaved,
)
from copilot.auth.deps import CurrentAdmin, CurrentUser, SessionDep
from copilot.config import get_settings
from copilot.db.models import Correction as CorrectionRow
from copilot.db.models import User

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/corrections", tags=["corrections"])

NOT_FOUND = HTTPException(status.HTTP_404_NOT_FOUND, "勘误不存在")


async def reingest_one(session, target_url: str) -> int:
    """把这一篇语雀文档按**当前已发布的**勘误重新入库，返回块数。

    ⭐ 走的是和 `copilot ingest` **完全一样**的那条链路（load → apply → ingest），
    只是把输入缩到一篇。另写一套简化版的话，两条路迟早在切分参数或
    隔离规则上分叉——而分叉的表现是「网页改的和命令行改的效果不一样」。

    ⚠️ 它读的是 `load_db_corrections`，而那个函数**只读 published**。
    所以撤销一条勘误之后调它，拿到的就是"没有这条勘误"的结果——
    那一篇自动还原成语雀原文，不需要第二条还原路径。

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


# 旧名字。`_reingest_one` 在测试里被 monkeypatch 了好几处，留一个别名比
# 让那些测试各自去改名安全——它们盯的是"重新入库有没有被调用"，和名字无关
_reingest_one = reingest_one


def _visible(row: CorrectionRow, user: User) -> bool:
    """这个人能不能看这一条。

    已发布的人人可见（谁改了公共知识大家都该看得见），别人还没审的提交
    只有作者和管理员看得到——一条 pending 是"某人的意见"，不是公共事实。
    """
    return row.status in flow.LIVE or row.author_id == user.id or user.is_admin


@router.get("", response_model=list[CorrectionOut])
async def list_corrections(user: CurrentUser, session: SessionDep) -> list[CorrectionRow]:
    """已发布的（人人可见）+ 自己提的（任何状态）。

    **已发布的不按作者过滤**：它们改的是同一个公共知识库，谁改了什么大家都
    该看得见。藏起来的话，两个人对同一篇的不同理解会在库里打架，而谁都看不出来。

    ⚠️ **别人还没审的提交不在里面。** 那是一条待审意见，不是公共事实；
    而且它随时可能被作者改掉或撤回，摆在公共列表上只会让人以为已经生效了。
    管理员要看全部走 `/api/admin/doc-corrections`。
    """
    stmt = (
        select(CorrectionRow)
        .where(
            or_(
                CorrectionRow.status.in_(flow.LIVE),
                CorrectionRow.author_id == user.id,
            )
        )
        .order_by(CorrectionRow.updated_at.desc())
    )
    if user.is_admin:
        stmt = select(CorrectionRow).order_by(CorrectionRow.updated_at.desc())
    return list((await session.execute(stmt)).scalars())


@router.get("/mine", response_model=list[CorrectionOut])
async def my_corrections(user: CurrentUser, session: SessionDep) -> list[CorrectionRow]:
    """我提过的，含被拒绝和已撤回的。和 `/api/answer-corrections/mine` 同形状。"""
    stmt = (
        select(CorrectionRow)
        .where(CorrectionRow.author_id == user.id)
        .order_by(CorrectionRow.created_at.desc())
    )
    return list((await session.execute(stmt)).scalars())


@router.post("", response_model=CorrectionSaved, status_code=status.HTTP_201_CREATED)
async def submit_correction(
    body: CorrectionIn, user: CurrentUser, session: SessionDep
) -> CorrectionSaved:
    """提一条勘误。**落成 `pending`，一个字都不进公共知识库。**

    ⚠️⚠️ **这里绝不调用 `reingest_one`。** 它原来就在这里，而那一句正是
    「提交即覆盖公共知识库」的全部实现。要改公共事实必须走完
    审核 → 发布，那两步在管理台那一侧。

    ⚠️ **同一篇可以有多条待审的提交，这是刻意的。** 原来 `target_url` 是
    全表唯一、接口做 upsert——在提交队列下那等于「后一个人**改写**前一个人
    还没审的意见」，而两个人对同一篇有不同看法是完全正常的事。
    唯一性现在收窄成「同一篇最多一条**已发布**」（部分唯一索引）。

    ⚠️ 但**同一个人对同一篇只留一条待审的**：他多半是在改自己刚写的那条，
    不是要提两份。这一条是更新，不是新增——否则他自己的列表里会堆一串草稿。
    """
    existing = (
        await session.execute(
            select(CorrectionRow).where(
                CorrectionRow.target_url == body.target_url,
                CorrectionRow.author_id == user.id,
                CorrectionRow.status == flow.PENDING,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = CorrectionRow(target_url=body.target_url, author_id=user.id)
        session.add(existing)
    else:
        existing.version += 1
        existing.updated_at = datetime.now(UTC)

    existing.title = body.title
    existing.reason = body.reason
    existing.body = body.body
    existing.status = flow.PENDING
    await session.commit()
    await session.refresh(existing)

    return CorrectionSaved(
        correction=CorrectionOut.model_validate(existing),
        chunks=0,
        applied=False,
        note="已提交，等待管理员审核。审核通过并发布之后才会影响知识库的答案。",
    )


@router.patch("/{correction_id}", response_model=CorrectionOut)
async def edit_correction(
    correction_id: _uuid.UUID,
    body: CorrectionPatch,
    user: CurrentUser,
    session: SessionDep,
) -> CorrectionRow:
    """改自己的 pending 勘误，或者撤回它。和 `/api/answer-corrections` 同形状。

    ⚠️ **只在 `pending` 时可做。** 审核通过之后还能改内容的话，
    管理员看过的和最终发布的就不是同一段文字——那等于没有审核。

    ⚠️ **别人的一律当不存在（404 而非 403）。** 403 等于告诉对方
    「这个 id 是真的」，而 id 会出现在列表接口的返回里。
    """
    row = await session.get(CorrectionRow, correction_id)
    if row is None or (row.author_id != user.id and not user.is_admin):
        raise NOT_FOUND
    if body.version is not None and body.version != row.version:
        raise HTTPException(status.HTTP_409_CONFLICT, "这条勘误刚被改过，请刷新后再试")

    if body.action == "withdraw":
        try:
            flow.check_transition(row.status, flow.WITHDRAWN, flow.DOC_STATE_MACHINE)
        except flow.TransitionError as e:
            raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e
        row.status = flow.WITHDRAWN
        row.version += 1
        await session.commit()
        await session.refresh(row)
        return row

    if row.status not in flow.EDITABLE:
        raise HTTPException(status.HTTP_409_CONFLICT, f"已经是「{row.status}」的勘误不能再改内容")

    if body.title is not None:
        row.title = body.title.strip()
    if body.reason is not None:
        if not (reason := body.reason.strip()):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "修改原因不能为空")
        row.reason = reason
    if body.body is not None:
        if not (text := body.body.strip()):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "正文不能为空")
        row.body = text

    row.version += 1
    row.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(row)
    return row


@router.get("/{correction_id}", response_model=CorrectionOut)
async def get_correction(
    correction_id: _uuid.UUID, user: CurrentUser, session: SessionDep
) -> CorrectionRow:
    """看一条：已发布的人人可看，待审的只有作者和管理员。"""
    row = await session.get(CorrectionRow, correction_id)
    if row is None or not _visible(row, user):
        raise NOT_FOUND
    return row


@router.delete("/{correction_id}", status_code=status.HTTP_204_NO_CONTENT)
async def retire_correction(
    correction_id: _uuid.UUID, user: CurrentAdmin, session: SessionDep
) -> None:
    """撤销一条**已发布**的勘误：那一篇立刻回到语雀原文。**管理员专用。**

    ⚠️⚠️ **软撤销，不是物理删除。** `status` 改成 `retired`，
    `published_at` **保留**——「它曾经生效过、从哪天开始」正是撤销这件事
    最该留下的东西。物理删掉的话，半年后没有任何办法回答
    「那阵子公共库里是什么内容、是谁放进去的」。

    ⚠️ 普通用户走不到这里：撤回自己**还没审**的提交是
    `PATCH {"action": "withdraw"}`。已经生效的东西不该由提交人单方面收回——
    那时它已经是公共事实了。

    还原不需要第二条路径：`reingest_one` 读的是**已发布**的勘误，
    这一条一旦不在 published 里，重新入库拿到的自然就是语雀原文。
    """
    row = await session.get(CorrectionRow, correction_id)
    if row is None:
        raise NOT_FOUND
    try:
        flow.check_transition(row.status, flow.RETIRED, flow.DOC_STATE_MACHINE)
    except flow.TransitionError as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e)) from e

    target_url = row.target_url
    row.status = flow.RETIRED
    row.version += 1
    row.reviewed_by = user.id
    row.reviewed_at = datetime.now(UTC)
    row.review_note = "管理员撤销"
    await session.commit()

    # 撤销之后要**再入库一次**，否则库里留着的还是改过的内容——
    # 管理员以为撤销了，实际上答案没变
    try:
        await reingest_one(session, target_url)
    except Exception:  # noqa: BLE001 - 状态已经改了，重新入库失败只是晚一点生效
        logger.exception("勘误已撤销但重新入库失败：%s", target_url)
