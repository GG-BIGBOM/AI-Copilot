"""知识版本隔离（M14-A）。

这是隔离的**第二根轴**。第一根是 `owner_id`（谁的文档），已经在
`test_isolation.py` 里守着；这一根是「哪一版 ERP」——旗舰版、客户端企业版、
网页版企业版是三套不同的产品，同一个问题在三边有三套不同的答案。

⚠️ **这一组里最重要的不是「本空间能搜到」，而是两条边界**：

    1. 跨空间搜不到     混着答的表现是「界面路径全对不上」，而用户分辨不出
    2. 没有空间 = 搜不到（fail closed）
       缺空间的来路只有「老会话」和「调用方忘了传」两种，两种都不该退回
       全库搜——那种失败**没有任何报错**，只有用户觉得答案不对劲

第 2 条尤其要守死。`conftest.py` 里那个自动补 flagship 的填充器是为了让
二十几处夹具不必手写空间，它的代价是「生产代码忘了传空间，测试也不会红」。
所以这里有三条断言直接读库里的值，不经过填充器：ingest 写块、新建会话、
用户上传，各自验的是**真代码把哪个空间写了进去**。
"""

from __future__ import annotations

import uuid

import pytest
from chat_helpers import FakeEmbedder
from test_isolation import PassThroughReranker

from sqlalchemy import delete, select

from copilot import spaces
from copilot.db.models import Chunk, Document, KnowledgeSpace
from copilot.retrieve import search


async def _cleanup(maker, tag: str) -> None:
    """把这一条测试塞进真库的文档和块删掉，别污染别人的召回（见 `two_space_docs`）。"""
    async with maker() as s:
        docs = list(
            (
                await s.execute(select(Document.id).where(Document.title.like(f"%{tag}%")))
            ).scalars()
        )
        if docs:
            await s.execute(delete(Chunk).where(Chunk.document_id.in_(docs)))
            await s.execute(delete(Document).where(Document.id.in_(docs)))
            await s.commit()


@pytest.fixture
async def two_space_docs(maker, flagship_id, other_space):
    """两个空间各放一篇内容相近的公共文档，标题里带同一个 tag。"""
    tag = uuid.uuid4().hex[:8]
    # ⚠️ 两个空间的**正文一模一样**，只有标题不同。`FakeEmbedder` 是确定性的，
    # 拿这段原文去搜，两边的余弦距离都是 0——于是决定命中谁的**只剩空间过滤**
    # 这一件事。正文不同的话，测试可能只是在量"哪段文字更像问题"。
    body = f"电子面单在【设置-物流】里绑定物流账号-{tag}"
    async with maker() as s:
        for space_id, who in ((flagship_id, "旗舰版"), (other_space.id, "企业版")):
            doc = Document(
                owner_id=None,
                knowledge_space_id=space_id,
                source_type="yuque",
                title=f"{who}-电子面单设置-{tag}",
                content_hash=uuid.uuid4().hex,
                status="done",
                chunk_count=1,
            )
            s.add(doc)
            await s.flush()
            s.add(
                Chunk(
                    document_id=doc.id,
                    owner_id=None,
                    knowledge_space_id=space_id,
                    ordinal=0,
                    content=body,
                    embedding=FakeEmbedder().embed_query(body),
                    title=doc.title,
                )
            )
        await s.commit()

    yield maker, body

    # ⚠️ **一定要清理。** 这份夹具往真库里塞的是"和常见问句很像"的块，
    # 留着的话它们会在**别的测试**里被召回，把那些测试的目标块挤出 top-k。
    # 2026-08-23 当场踩到：`test_images_are_sent_before_the_text` 突然红了，
    # 原因是这里残留的「电子面单设置」块排到了它前面。
    async with maker() as s:
        docs = list(
            (
                await s.execute(
                    select(Document.id).where(Document.title.like(f"%{tag}%"))
                )
            ).scalars()
        )
        if docs:
            await s.execute(delete(Chunk).where(Chunk.document_id.in_(docs)))
            await s.execute(delete(Document).where(Document.id.in_(docs)))
            await s.commit()


async def _titles(maker, query, space_id, user_id=None):
    """拿**块的原文**去搜：`FakeEmbedder` 下距离是 0，稳稳压过库里几千条真向量。"""
    async with maker() as s:
        result = await search(
            s,
            query,
            FakeEmbedder(),
            PassThroughReranker(),
            user_id=user_id,
            space_id=space_id,
            top_k=20,
            rerank_k=20,
        )
    return [c.citation.title for c in result.chunks]


# ---------- 边界 1：跨空间搜不到 ----------


async def test_flagship_does_not_retrieve_enterprise(two_space_docs, flagship_id):
    maker, body = two_space_docs
    titles = await _titles(maker, body, flagship_id)
    assert any("旗舰版" in t for t in titles), "本空间的材料反而搜不到了"
    assert not any("企业版" in t for t in titles), f"跨空间泄漏：{titles}"


async def test_enterprise_does_not_retrieve_flagship(two_space_docs, other_space):
    maker, body = two_space_docs
    titles = await _titles(maker, body, other_space.id)
    assert any("企业版" in t for t in titles)
    assert not any("旗舰版" in t for t in titles), f"跨空间泄漏：{titles}"


# ---------- 边界 2：没有空间就什么都搜不到 ----------


async def test_no_space_retrieves_nothing(two_space_docs):
    """⭐⭐ 这一条是整组里最重要的。

    缺空间时**不许**退回「全库搜」。退回去的表现是：一个企业版的会话安安静静
    地拿旗舰版的材料作答，界面路径全对不上，而没有任何报错。
    """
    maker, body = two_space_docs
    assert await _titles(maker, body, None) == []


# ---------- 通用知识：任何空间都搜得到 ----------


async def test_common_space_is_visible_from_every_space(maker, flagship_id, other_space):
    tag = uuid.uuid4().hex[:8]
    body = f"快递单号是物流公司分配的唯一编号-{tag}"
    async with maker() as s:
        common = await spaces.by_code(s, spaces.COMMON)
        doc = Document(
            owner_id=None,
            knowledge_space_id=common.id,
            source_type="yuque",
            title=f"通用-快递单号规则-{tag}",
            content_hash=uuid.uuid4().hex,
            status="done",
            chunk_count=1,
        )
        s.add(doc)
        await s.flush()
        s.add(
            Chunk(
                document_id=doc.id,
                owner_id=None,
                knowledge_space_id=common.id,
                ordinal=0,
                content=body,
                embedding=FakeEmbedder().embed_query(body),
                title=doc.title,
            )
        )
        await s.commit()

    try:
        for space_id in (flagship_id, other_space.id):
            titles = await _titles(maker, body, space_id)
            assert any("通用" in t for t in titles), f"space={space_id} 看不到通用知识"
    finally:
        await _cleanup(maker, tag)


# ---------- 私有文档：两根轴同时生效 ----------


async def test_private_document_respects_space(maker, api_client, logged_in, flagship_id, other_space):
    """⭐ 自己的文档也不跨空间。

    两根轴各管各的：`owner_id` 管「谁的」，`knowledge_space_id` 管「哪一版」。
    只过滤 owner 会让企业版的会话读到自己传在旗舰版下的文档——那份文档讲的
    是另一个产品，混进来照样是错的。
    """
    alice_id = logged_in
    tag = uuid.uuid4().hex[:8]
    body = f"我们的电子面单只用中通-{tag}"
    async with maker() as s:
        doc = Document(
            owner_id=alice_id,
            knowledge_space_id=flagship_id,
            source_type="upload",
            title=f"爱丽丝的旗舰版约定-{tag}",
            content_hash=uuid.uuid4().hex,
            status="done",
            chunk_count=1,
        )
        s.add(doc)
        await s.flush()
        s.add(
            Chunk(
                document_id=doc.id,
                owner_id=alice_id,
                knowledge_space_id=flagship_id,
                ordinal=0,
                content=body,
                embedding=FakeEmbedder().embed_query(body),
                title=doc.title,
            )
        )
        await s.commit()

    try:
        assert any(
            "爱丽丝" in t for t in await _titles(maker, body, flagship_id, user_id=alice_id)
        ), "自己在本空间的文档搜不到了"
        assert not any(
            "爱丽丝" in t for t in await _titles(maker, body, other_space.id, user_id=alice_id)
        ), "自己的文档跨到了另一个空间"
    finally:
        await _cleanup(maker, tag)


# ---------- 真实写入路径（不经过 conftest 的填充器）----------


async def test_ingested_chunks_inherit_the_document_space(maker, other_space):
    """⭐ `chunks.knowledge_space_id` 必须等于所属 document 的那一个。

    ⚠️ 这条断言**故意不用默认空间**：conftest 的填充器补的是 flagship，
    所以如果 `write_chunks` 根本没写这一列，块会变成 flagship，
    和这里的 `enterprise_desktop` 对不上，测试就会红。
    """
    from copilot.ingest.pipeline import write_chunks

    tag = uuid.uuid4().hex[:8]
    async with maker() as s:
        doc = Document(
            owner_id=None,
            knowledge_space_id=other_space.id,
            source_type="upload",
            title=f"企业版手册-{tag}",
            content_hash=uuid.uuid4().hex,
            status="done",
        )
        s.add(doc)
        await s.flush()
        n = await write_chunks(s, doc, f"# 标题\n\n企业版的入库流程-{tag}", FakeEmbedder())
        await s.commit()

        assert n >= 1
        rows = list(
            (
                await s.execute(
                    Chunk.__table__.select().where(Chunk.document_id == doc.id)
                )
            ).all()
        )
        assert rows, "一块都没写进去"
        for row in rows:
            assert row.knowledge_space_id == other_space.id, (
                "块的知识版本和所属文档不一致——检索会把它算进错误的 ERP 版本"
            )
    await _cleanup(maker, tag)


async def test_a_new_conversation_is_pinned_to_a_space(api_client, logged_in, maker):
    """⭐ 新会话必须钉死一个知识版本，而且是默认那个。"""
    from chat_helpers import ask
    from sqlalchemy import select

    from copilot.db.models import Conversation

    r = await ask(api_client, "你好")
    assert r.status_code == 200

    async with maker() as s:
        conv = (
            await s.execute(select(Conversation).order_by(Conversation.created_at.desc()))
        ).scalars().first()
        assert conv is not None
        assert conv.knowledge_space_id is not None, "新会话没有知识版本，之后每一轮都会 fail closed"
        assert conv.knowledge_space_id == await spaces.default_id(s)


async def test_every_seeded_space_exists_and_common_is_not_selectable(maker):
    """种子齐全，且 `common` 不出现在用户可选列表里。"""
    from sqlalchemy import select

    async with maker() as s:
        codes = set((await s.execute(select(KnowledgeSpace.code))).scalars())
        assert {c for c, *_ in spaces.SEED} <= codes

        selectable = await spaces.selectable(s)
        assert [x.code for x in selectable] == [spaces.FLAGSHIP], (
            "企业版语料还没导入，不该出现在可选列表里"
        )
        assert spaces.COMMON not in {x.code for x in selectable}


async def test_an_unknown_code_fails_closed(maker):
    """拼错的 code 必须抛，不能退回默认空间。"""
    async with maker() as s:
        with pytest.raises(spaces.SpaceNotFound):
            await spaces.by_code(s, "flagshipp")
