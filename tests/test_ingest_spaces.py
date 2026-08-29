"""入库的判重必须按知识版本分开（M18 / W3.3 的前置）。

⭐⭐ **plan.md 的 M18 步骤里点名说「这是本步最值得先写测试的地方」，
写下来之后发现它猜对了一半、而漏掉的那一半更糟。**

`_ingest_one` 判"这篇是不是已经入过库"用的是

    source_url（没有就用 title） + owner_id

**没有空间这一维。** 于是导入企业版语料时，一篇和旗舰版同名（或同 URL）的
文档会撞上旗舰版那一行：

    content_hash 一样  →  判成「已入库、跳过」，企业版**静静少掉这一篇**
    content_hash 不同  →  ⚠️⚠️ **改的是旗舰版那一行**：内容换成企业版的，
                          而 `knowledge_space_id` 保持旗舰版不变
                          （那一行只在 `is None` 时才写）

第二种是**跨版本污染发生在入库层**，比"少几篇"糟得多：
`retrieve._space_filter` 是全项目唯一一处空间过滤，而它过滤得**完全正确**——
它只能保证"旗舰版空间里的块才会被旗舰版的提问召回"，
保证不了"旗舰版空间里的块讲的是旗舰版的事"。
⚠️ 而且没有任何症状：文档数不变、块数不变、门禁的跨空间污染率照样是 0
（那套题量的是召回，不是内容）。

⚠️ 这几道题在语料导入**之前**就必须是绿的——导入之后再发现，
要重新抓取、重新向量化几千次。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete, select

from copilot.db.models import Chunk, Document
from copilot.ingest.pipeline import SourceDoc, ingest_documents

DIM = 1024


class FakeEmbedder:
    dim = DIM

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * DIM
        for i, ch in enumerate(text[:64]):
            v[(ord(ch) * 7 + i) % DIM] += 1.0
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


@pytest.fixture
async def two_spaces(maker, flagship_id):
    """旗舰版 + 一个企业版空间。用种子里那个 `enterprise_desktop`，
    它本来就是为这件事预置的（`status='inactive'`，语料还没导入）。"""
    from copilot import spaces

    async with maker() as s:
        other = await spaces.by_code(s, spaces.ENTERPRISE_DESKTOP)
        other_id = other.id
    return flagship_id, other_id


@pytest.fixture
async def cleanup_docs(maker):
    """按标题前缀收尾。入库那条路自己建 Document，测试拿不到 id。"""
    marks: list[str] = []
    yield marks
    async with maker() as s:
        for mark in marks:
            ids = list(
                (
                    await s.execute(select(Document.id).where(Document.title.like(f"%{mark}%")))
                ).scalars()
            )
            if ids:
                await s.execute(delete(Chunk).where(Chunk.document_id.in_(ids)))
                await s.execute(delete(Document).where(Document.id.in_(ids)))
        await s.commit()


def _doc(title: str, body: str) -> SourceDoc:
    return SourceDoc(
        title=title,
        markdown=f"# {title}\n\n{body}",
        source_type="yuque",
        source_url=f"https://example.invalid/{title}",
    )


async def _count(maker, mark: str, space_id) -> int:
    async with maker() as s:
        return len(
            list(
                (
                    await s.execute(
                        select(Document.id).where(
                            Document.title.like(f"%{mark}%"),
                            Document.knowledge_space_id == space_id,
                        )
                    )
                ).scalars()
            )
        )


async def test_same_title_same_content_lands_in_both_spaces(maker, two_spaces, cleanup_docs):
    """⭐ **两个版本有同名同内容的文档，两边都要入库。**

    判重漏掉空间这一维的表现是：企业版那一次被判成「已入库、跳过」，
    于是**企业版静静少掉这一篇**，而 `stats.skipped` 会如实 +1——
    看报告的人只会觉得"哦有重复的"。
    """
    flagship, other = two_spaces
    mark = uuid.uuid4().hex[:8]
    cleanup_docs.append(mark)
    doc = _doc(f"发货流程-{mark}", "第一步：审核订单。")

    async with maker() as s:
        await ingest_documents(s, [doc], FakeEmbedder(), space_id=flagship)
    async with maker() as s:
        await ingest_documents(s, [doc], FakeEmbedder(), space_id=other)

    assert await _count(maker, mark, flagship) == 1
    assert await _count(maker, mark, other) == 1, "企业版这一篇被判成重复，静静丢了"


async def test_importing_another_space_never_rewrites_the_first(maker, two_spaces, cleanup_docs):
    """⚠️⚠️ **比"少几篇"糟得多的那一种：改的是旗舰版那一行。**

    同名不同内容时，判重会捞到旗舰版那一行，然后把正文换成企业版的，
    而 `knowledge_space_id` 保持旗舰版不变（那一行只在 `is None` 时才写）。

    结果是**旗舰版空间里躺着企业版的内容**——而 `_space_filter` 过滤得
    完全正确，它只能保证"旗舰版空间里的块才会被旗舰版的提问召回"，
    保证不了"旗舰版空间里的块讲的是旗舰版的事"。
    ⚠️ 没有任何症状：文档数不变、块数不变、跨空间污染率照样是 0。
    """
    flagship, other = two_spaces
    mark = uuid.uuid4().hex[:8]
    cleanup_docs.append(mark)

    async with maker() as s:
        await ingest_documents(
            s,
            [_doc(f"发货流程-{mark}", "旗舰版：先审核再出库。")],
            FakeEmbedder(),
            space_id=flagship,
        )
    async with maker() as s:
        await ingest_documents(
            s,
            [_doc(f"发货流程-{mark}", "企业版：先出库再审核。")],
            FakeEmbedder(),
            space_id=other,
        )

    async with maker() as s:
        rows = list(
            (
                await s.execute(
                    select(Document.knowledge_space_id, Chunk.content)
                    .join(Chunk, Chunk.document_id == Document.id)
                    .where(Document.title.like(f"%{mark}%"))
                )
            ).all()
        )

    assert len(rows) >= 2, "两个空间各自应该有自己的那一篇"
    by_space = {sid: content for sid, content in rows}
    assert "旗舰版" in by_space[flagship], f"旗舰版那一行被企业版内容改写了：{by_space[flagship]!r}"
    assert "企业版" in by_space[other]


async def test_reimporting_the_same_space_still_dedups(maker, two_spaces, cleanup_docs):
    """⚠️ 修判重不能把判重修没了。

    同一个空间里重复入同一篇，仍然要跳过——`content_hash` 判重省的是
    embedding 调用，是真金白银（`pipeline.py` 文件头第 2 条）。
    """
    flagship, _ = two_spaces
    mark = uuid.uuid4().hex[:8]
    cleanup_docs.append(mark)
    doc = _doc(f"退货流程-{mark}", "第一步：登记。")

    async with maker() as s:
        first = await ingest_documents(s, [doc], FakeEmbedder(), space_id=flagship)
    async with maker() as s:
        second = await ingest_documents(s, [doc], FakeEmbedder(), space_id=flagship)

    assert first.ingested == 1
    assert second.skipped == 1, "同一个空间里的重复入库应该被跳过"
    assert second.ingested == 0
    assert await _count(maker, mark, flagship) == 1


async def test_private_uploads_are_still_deduped_per_owner(maker, flagship_id, cleanup_docs):
    """加了空间这一维之后，owner 那一维必须原样还在。"""
    mark = uuid.uuid4().hex[:8]
    cleanup_docs.append(mark)
    doc = _doc(f"我的约定-{mark}", "不拆组合装。")
    a, b = uuid.uuid4(), uuid.uuid4()

    from copilot.db.models import User

    async with maker() as s:
        s.add_all(
            [
                User(id=a, email=f"ing-a-{mark}@t.local", password_hash="x"),
                User(id=b, email=f"ing-b-{mark}@t.local", password_hash="x"),
            ]
        )
        await s.commit()
    try:
        async with maker() as s:
            await ingest_documents(s, [doc], FakeEmbedder(), owner_id=a, space_id=flagship_id)
        async with maker() as s:
            stats = await ingest_documents(
                s, [doc], FakeEmbedder(), owner_id=b, space_id=flagship_id
            )
        assert stats.ingested == 1, "另一个人传同名文档不该被当成重复"
    finally:
        async with maker() as s:
            await s.execute(delete(User).where(User.id.in_([a, b])))
            await s.commit()
