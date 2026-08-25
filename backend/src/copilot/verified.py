"""标准答案（VerifiedAnswer）的索引、修订与发布（M16）。

从 `api/routes/verified.py` 抽出来的，因为现在有**两个**调用方：那条路由，
和管理员发布纠错的那条路径。同一件事在两处各写一遍的下场是：某天有人在其中
一处加了「只有 active 才进索引」，另一处没加——于是退役的答案还在影响检索，
而站上任何地方都看不出来。

三条规矩：

1. **只有 `active` 的标准答案在索引里。** 退役/草稿要把块删干净，
   不是打个标记就算——留着块的表现是「我明明退役了它，答案却没变」。
2. **它进索引的那篇文档，空间跟着标准答案走。** 发布在旗舰版的答案不能
   出现在企业版的会话里（检索侧的过滤见 `retrieve._space_filter`）。
3. **改内容和写修订记录在同一个事务里。** 分开的话会出现「答案变了，
   但没有任何一版记录说它变过」——而这种缺口只有在事后追查时才发现。
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from copilot import assets
from copilot import corrections_flow as flow
from copilot.db.models import (
    AnswerCorrection,
    Chunk,
    Document,
    VerifiedAnswer,
    VerifiedAnswerRevision,
)
from copilot.ingest.pipeline import write_chunks

SOURCE_TYPE = "verified"

ACTIVE = "active"
RETIRED = "retired"

# 进索引的正文。**问题也要写进去**——检索是拿用户这次的问法去比对的，
# 只放答案的话，答案里未必出现问题里的词，相似问题就召不回它。
BODY_TEMPLATE = """问：{question}

答：{answer}"""

# 归一化用：首尾空白、全角/半角空格、句末的问号句号都不该影响「是不是同一个问题」
_SPACE_RE = re.compile(r"\s+")
_TAIL_PUNCT = "?？。.!！ 　"


def title_of(question: str) -> str:
    return f"已订正 · {' '.join(question.split())[:60]}"


def normalize_question(question: str) -> str:
    """把问法归一化到「同一个问题」的粒度。

    ⚠️ **只做确定性的清洗**（空白、大小写、句末标点），不做同义改写、
    不做分词、不做相似度。终结命中是一条**不经模型改写**就直接返回的路径，
    它宁可漏（退回正常检索，答案照样出得来），不可错——错了就是拿另一个
    问题的标准答案糊在用户脸上，而他分辨不出来。
    语义匹配的阈值要拿 `eval/verified_answers.yaml` 标定，那是 M19-A 的事。
    """
    return _SPACE_RE.sub(" ", question).strip().strip(_TAIL_PUNCT).casefold()


async def sync_index(session: AsyncSession, row: VerifiedAnswer, embedder) -> int:
    """把这条标准答案同步进检索索引。返回块数；退役/草稿返回 0。

    不提交事务，交给调用方——发布那条路径要和修订记录一起提交。
    """
    doc = (
        await session.execute(
            select(Document).where(
                Document.source_type == SOURCE_TYPE, Document.source_url == str(row.id)
            )
        )
    ).scalar_one_or_none()

    if row.status != ACTIVE:
        # ⭐ 退役就要把块删干净。只改状态不删块的表现是「我退役了它，
        # 答案却没变」——而那时你会去查检索、查 prompt，查不到这里
        if doc is not None:
            await session.execute(delete(Chunk).where(Chunk.document_id == doc.id))
            await session.delete(doc)
        return 0

    if doc is None:
        doc = Document(
            owner_id=None,  # 公共：它要盖住所有人的错误答案
            source_type=SOURCE_TYPE,
            title=title_of(row.question),
            # 没有真实外链，用它自己的 id 当对齐键——重复发布时能找回同一篇
            source_url=str(row.id),
            content_hash=uuid.uuid4().hex,
            status="done",
        )
        session.add(doc)
    else:
        doc.title = title_of(row.question)
        doc.content_hash = uuid.uuid4().hex

    # ⚠️ 空间跟着标准答案走。写错了不会报错，只会让旗舰版的修正出现在
    # 企业版的会话里（`write_chunks` 会把它冗余到每一块上）
    doc.knowledge_space_id = row.knowledge_space_id

    body = BODY_TEMPLATE.format(question=row.question, answer=row.answer)
    n = await write_chunks(session, doc, body, embedder)
    # ⭐ 打上 verified：检索靠它把这条排到语雀原文前面（`retrieve._verified_first`）。
    # 漏了这一步的表现最气人——发布说"已生效"，再问一遍答案却没变
    await session.execute(update(Chunk).where(Chunk.document_id == doc.id).values(verified=True))
    doc.chunk_count = n
    return n


def record_revision(
    session: AsyncSession,
    row: VerifiedAnswer,
    *,
    editor_id: uuid.UUID | None,
    note: str,
) -> None:
    """留一版修订。**必须和改动本身在同一个事务里。**"""
    session.add(
        VerifiedAnswerRevision(
            verified_answer_id=row.id,
            version=row.version,
            question=row.question,
            answer=row.answer,
            knowledge_space_id=row.knowledge_space_id,
            status=row.status,
            editor_id=editor_id,
            note=note,
        )
    )


async def publish_correction(
    session: AsyncSession,
    correction: AnswerCorrection,
    *,
    admin_id: uuid.UUID,
    embedder,
) -> tuple[VerifiedAnswer, int]:
    """把一条**已通过审核**的纠错发布成标准答案。返回（标准答案，块数）。

    ⚠️ 状态迁移、写标准答案、留修订、进索引**在同一个事务里**（调用方提交）。
    拆开的话会出现「纠错标成 published、标准答案却没建出来」，
    而那条纠错从此再也走不到发布——状态机不允许 published 再发布一次。

    同一个空间里同一个问题只能有一条标准答案（唯一键），所以已经存在时是
    **改它 + 加一版**，不是再插一条：两条互相打架的标准答案会让检索随机
    命中其中一条，表现是「答案时好时坏」，最难查。
    """
    flow.check_transition(correction.status, flow.PUBLISHED)

    # ⭐ 纠错里贴的截图在这里从私有变成公共（M17.1 P1），正文里的地址跟着换。
    # **不换的后果是无声的**：`/api/images/{id}` 不是 `assets.storage_path_of()`
    # 认识的形状，切块时那张图配不出资产行，检索层"换不成就丢掉"的规则会把它
    # 直接丢掉——发布说成功了，答案里却没有图，而没有任何一处报错。
    #
    # ⚠️⚠️ **必须在 `session.add(row)` 之前做完。** 它里面有 await，而在 ORM 里
    # **一次 await 不是无害的**：autoflush 会把一个 `answer` 还没填的半截
    # VerifiedAnswer 刷进库，报出来的是「answer 不能为空」——一个指不到真正
    # 原因的错误。2026-08-24 上传那条路刚踩过同一个坑（见「线上事故」那节），
    # 这里第一版又踩了一次。**先查完，再建行。**
    public_urls = await assets.publish_correction_images(session, correction.id)

    question = " ".join(correction.original_question.split())[:1024]
    row = (
        await session.execute(
            select(VerifiedAnswer).where(
                VerifiedAnswer.question == question,
                VerifiedAnswer.knowledge_space_id == correction.knowledge_space_id,
            )
        )
    ).scalar_one_or_none()

    if row is None:
        row = VerifiedAnswer(
            question=question,
            knowledge_space_id=correction.knowledge_space_id,
            version=0,  # 下面统一 +1，新旧两条路走同一段代码
        )
        session.add(row)

    answer = correction.corrected_answer_markdown.strip()
    for private_url, public_url in public_urls.items():
        answer = answer.replace(private_url, public_url)
    row.answer = answer
    row.author_id = correction.submitted_by  # 内容是提交人写的，署他的名
    row.status = ACTIVE
    row.source_correction_id = correction.id
    row.source_trace_id = correction.trace_id
    row.version = (row.version or 0) + 1
    await session.flush()  # 新建的行要先拿到 id，修订和索引都要用

    note = f"由纠错 {correction.id} 发布"
    if correction.review_note:
        note += f"；审核备注：{correction.review_note}"
    record_revision(session, row, editor_id=admin_id, note=note)
    n = await sync_index(session, row, embedder)

    correction.status = flow.PUBLISHED
    correction.version += 1
    correction.reviewed_by = admin_id
    correction.reviewed_at = datetime.now(UTC)
    return row, n


async def lookup(
    session: AsyncSession, question: str, space_id: uuid.UUID | None
) -> VerifiedAnswer | None:
    """终结命中：这个问题在这个空间有没有一条**人写定**的标准答案。

    ⚠️ **没有空间就不查**（fail closed，同 `retrieve._space_filter`）。
    跨空间命中就是拿旗舰版的答案回答企业版的问题，界面路径全对不上，
    而用户分辨不出来。

    ⚠️ 归一化精确匹配，不是相似度。见 `normalize_question` 的注释。
    """
    if space_id is None:
        return None
    key = normalize_question(question)
    if not key:
        return None

    rows = (
        await session.execute(
            select(VerifiedAnswer).where(
                VerifiedAnswer.knowledge_space_id == space_id,
                VerifiedAnswer.status == ACTIVE,
            )
        )
    ).scalars()
    for row in rows:
        if normalize_question(row.question) == key:
            return row
    return None


async def mark_hit(session: AsyncSession, row: VerifiedAnswer) -> None:
    """记一次终结命中。

    只统计**直接返回这条答案**的命中，不统计「作为材料参与了检索」——
    后者几乎每一轮都可能沾边，混在一起这个数就再也说明不了
    「这条订正到底有没有用」。
    """
    await session.execute(
        update(VerifiedAnswer)
        .where(VerifiedAnswer.id == row.id)
        .values(hit_count=VerifiedAnswer.hit_count + 1, last_hit_at=datetime.now(UTC))
    )
