"""私有库主体纠偏（M11 P3）+ Agent 白名单（M11 P4）。

**P3 的三步是按代价从小到大排的**，这里也按同样的顺序量：

    第 1 步  上下文标注归属   最便宜。模型在此之前根本看不出哪块是谁的
    第 2 步  私有块保底名额   在重排层，绝不碰 `_visibility_filter`
    第 3 步  有条件的主体约束 触发条件限定死，公共库那 55 题结构上碰不到

⚠️ 这一组题里最重要的**不是**「私有块能被捞上来」，而是几条**边界**：
保底名额不许放行没过阈值的块、主体约束不许在没传过文档的用户身上触发。
M9 那次失败就是因为规则铺得太宽——加规则容易，加了不打架难。
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import delete

from copilot.db.models import Chunk, Document, User
from copilot.providers.base import RerankResult
from copilot.qa import asks_about_subject, system_prompt_for
from copilot.retrieve import (
    Citation,
    RetrievedChunk,
    has_private_chunks,
    search,
    source_label,
)

DIM = 1024


class FakeEmbedder:
    dim = DIM

    @staticmethod
    def _vec(text: str) -> list[float]:
        v = [0.0] * DIM
        for i, ch in enumerate(text[:64]):
            v[(ord(ch) * 7 + i) % DIM] += 1.0
        norm = sum(x * x for x in v) ** 0.5 or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


class RankByKeyword:
    """把含关键词的排前面，用来模拟「公共库整片挤掉私有块」那种真实形态。

    ⚠️ 分数要**按名次递减**，因为 `_private_floor` 是从最低分那头挤的。
    给一样的分数就等于把「挤谁」交给字典序，那道题也就白测了。
    """

    def __init__(self, keyword: str) -> None:
        self.keyword = keyword

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[RerankResult]:
        order = sorted(
            range(len(documents)),
            key=lambda i: (self.keyword not in documents[i], i),
        )
        return [
            RerankResult(index=idx, score=1.0 - rank * 0.01)
            for rank, idx in enumerate(order[:top_k])
        ]


class AllBelowThreshold:
    """全部低于阈值。用来验保底名额**不放行垃圾**。"""

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[RerankResult]:
        return [RerankResult(index=i, score=0.0001) for i in range(min(top_k, len(documents)))]


@pytest.fixture
async def customer_docs(maker):
    """一份私有约定 + 五块语义贴得极近的公共文档。

    这是 `priv-negation-combo-split` 那道题的最小复现：
    公共库《流程中拆分条件说明》整篇都在讲怎么拆组合装，
    而用户自己的文档里写着「不拆」。
    """
    tag = uuid.uuid4().hex[:8]
    emb = FakeEmbedder()

    async with maker() as s:
        owner = User(email=f"cust-{tag}@t.local", password_hash="x")
        s.add(owner)
        await s.flush()

        specs = [
            (
                owner.id,
                f"客户A-实施配置约定-{tag}",
                "对账规则",
                "组合装拆分：不启用，按整体发货不拆",
            ),
        ]
        specs += [
            (
                None,
                f"流程中拆分条件说明-{tag}",
                f"第{i}节",
                f"组合装拆分条件说明第{i}条：满足条件即拆分",
            )
            for i in range(1, 6)
        ]

        doc_ids = []
        for owner_id, title, heading, bodytext in specs:
            doc = Document(
                owner_id=owner_id,
                source_type="upload" if owner_id else "yuque",
                title=title,
                content_hash=uuid.uuid4().hex,
                status="done",
                chunk_count=1,
            )
            s.add(doc)
            await s.flush()
            doc_ids.append(doc.id)
            s.add(
                Chunk(
                    document_id=doc.id,
                    owner_id=owner_id,
                    ordinal=0,
                    content=bodytext,
                    embedding=emb.embed_query(bodytext),
                    title=title,
                    heading=heading,
                )
            )
        await s.commit()
        owner_id = owner.id

    yield maker, owner_id, tag

    async with maker() as s:
        await s.execute(delete(Chunk).where(Chunk.document_id.in_(doc_ids)))
        await s.execute(delete(Document).where(Document.id.in_(doc_ids)))
        await s.execute(delete(User).where(User.id == owner_id))
        await s.commit()


# ---------- 第 1 步：上下文标注归属 ----------


def _chunk(title: str, *, private: bool, heading: str | None = None) -> RetrievedChunk:
    return RetrievedChunk(
        content="正文",
        private=private,
        citation=Citation(n=1, title=title, heading=heading, source_url=None, score=0.9),
    )


def test_private_chunk_is_labelled_as_yours():
    """⭐ **P3 里最便宜的那六个字。**

    在此之前，私有块和公共块在上下文里长得一模一样——模型看不见的事，
    它当然判断不了。M9 加的那条「问题限定了主体时材料必须真的是讲这个主体的」
    铁律因此是一条**空规则**。
    """
    label = source_label(_chunk("客户A-实施配置约定", private=True, heading="对账规则"))
    assert label == "你的文档《客户A-实施配置约定》 · 对账规则"


def test_public_chunk_says_so():
    public = _chunk("流程中拆分条件说明", private=False)
    assert source_label(public) == "公共知识库 · 流程中拆分条件说明"


def test_context_text_carries_the_attribution(customer_docs):
    """拼出来的上下文里，两种来源必须写着不同的字。"""
    from copilot.retrieve import RetrievalResult

    ctx = RetrievalResult(
        chunks=[
            _chunk("客户A-实施配置约定", private=True),
            _chunk("流程中拆分条件说明", private=False),
        ]
    ).build_context()
    assert "你的文档《客户A-实施配置约定》" in ctx.text
    assert "公共知识库 · 流程中拆分条件说明" in ctx.text


# ---------- 第 2 步：私有块保底名额 ----------


async def test_private_chunk_survives_being_outranked(customer_docs, flagship_id):
    """⭐ 五块公共材料把私有那块整个挤出 top-5 —— 保底名额要把它捞回来。

    这就是 `priv-negation-combo-split` 的形态：用户问「我们的组合装要不要拆」，
    他自己的文档里白纸黑字写着「不拆」，但公共库有五个块在讲怎么拆，
    语义上贴得更近。没有保底名额，模型手上只剩通用流程，
    答出来的是一套和这家客户完全无关的说法——而且看着很专业。
    """
    maker, owner_id, tag = customer_docs
    async with maker() as s:
        result = await search(
            s,
            "组合装拆分条件说明",  # 故意用公共库那篇的原话，最严苛的情形
            FakeEmbedder(),
            RankByKeyword("条件说明"),
            user_id=owner_id,
            space_id=flagship_id,
            top_k=20,
            rerank_k=5,
        )

    titles = [c.citation.title for c in result.chunks]
    assert len(result.chunks) == 5, "名额总数不变，只是换了谁在里面"
    assert any("客户A" in t for t in titles), f"私有块被挤掉了：{titles}"
    assert result.private_count == 1


async def test_private_chunk_gets_recalled_even_when_vector_topk_is_full(
    customer_docs, maker, flagship_id
):
    """⭐⭐ **私有库的召回名额。这道题是实测逼出来的，不是设计出来的。**

    M11 定稿时的判断是「私有文档被 4 个公共块整个挤出 **top-5**」——以为发生在
    重排层，于是 P3 第 2 步（重排层的保底名额）应该能修。
    2026-08-20 真跑了一遍 `priv-negation-combo-split`，实测是：
    **混合池的 top-20 里私有块一条都没有**。挤掉它的是**向量召回**那一层，
    重排层根本没见过这块——保底名额有再多名额也无从捞起。

    所以这道题把候选池灌满：25 块和问题字面高度相似的公共块，
    保证私有那块进不了向量 top-20。它仍然必须出现在结果里。
    """
    maker_, owner_id, tag = customer_docs
    emb = FakeEmbedder()
    filler_ids = []
    async with maker_() as s:
        for i in range(25):
            body = f"组合装拆分条件说明补充第{i}条：满足条件即拆分"
            doc = Document(
                owner_id=None,
                source_type="yuque",
                title=f"拆分补充说明{i}-{tag}",
                content_hash=uuid.uuid4().hex,
                status="done",
                chunk_count=1,
            )
            s.add(doc)
            await s.flush()
            filler_ids.append(doc.id)
            s.add(
                Chunk(
                    document_id=doc.id,
                    owner_id=None,
                    ordinal=0,
                    content=body,
                    embedding=emb.embed_query(body),
                    title=doc.title,
                )
            )
        await s.commit()

    try:
        async with maker_() as s:
            result = await search(
                s,
                "组合装拆分条件说明补充第1条：满足条件即拆分",  # 和填充块字面几乎一致
                emb,
                RankByKeyword("不启用"),  # 重排会给私有那块最高分——只要它进得了候选池
                user_id=owner_id,
                space_id=flagship_id,
                top_k=20,
                rerank_k=5,
            )
        titles = [c.citation.title for c in result.chunks]
        assert any("客户A" in t for t in titles), (
            f"私有块连候选池都没进：{titles}。这正是 M11 P3 实测撞到的形态"
        )
    finally:
        async with maker_() as s:
            await s.execute(delete(Chunk).where(Chunk.document_id.in_(filler_ids)))
            await s.execute(delete(Document).where(Document.id.in_(filler_ids)))
            await s.commit()


async def test_private_recall_does_not_widen_visibility(customer_docs, flagship_id):
    """⚠️ 多一次召回**不等于**多一条可见性的路。

    `_visibility_filter(user_id, private_only=True)` 仍然是那**唯一一处**
    owner 过滤——换个参数，不是换个地方拼查询。别人一块都不该多看到。
    """
    maker_, owner_id, tag = customer_docs
    async with maker_() as s:
        result = await search(
            s,
            "组合装拆分：不启用，按整体发货不拆",  # 拿私有块原文去搜，最严苛
            FakeEmbedder(),
            RankByKeyword("不启用"),
            user_id=uuid.uuid4(),  # 一个和这些文档毫无关系的人
            top_k=20,
            rerank_k=5,
        )
    titles = [c.citation.title for c in result.chunks]
    assert not any("客户A" in t for t in titles), f"泄漏了：{titles}"


async def test_floor_never_lets_through_a_chunk_below_threshold(customer_docs, flagship_id):
    """⚠️ **保底只重排，不放行。**

    低于阈值的私有块一个都不能被捞回来——否则「用户传了什么就答什么」，
    防幻觉的第一道闸门等于对私有库单方面失效。
    """
    maker, owner_id, tag = customer_docs
    async with maker() as s:
        result = await search(
            s,
            "完全不相干的问题",
            FakeEmbedder(),
            AllBelowThreshold(),
            user_id=owner_id,
            space_id=flagship_id,
            top_k=20,
            rerank_k=5,
        )
    assert result.chunks == [], "全都不及格时该一块都不返回"


async def test_floor_does_nothing_for_a_user_without_documents(customer_docs, flagship_id):
    """没传过文档的人（以及评测公共库那 55 题走的 user_id=None）不受影响。"""
    maker, _owner_id, tag = customer_docs
    async with maker() as s:
        result = await search(
            s,
            "组合装拆分条件说明",
            FakeEmbedder(),
            RankByKeyword("条件说明"),
            user_id=None,
            space_id=flagship_id,
            top_k=20,
            rerank_k=5,
        )
    assert result.private_count == 0
    assert all(not c.private for c in result.chunks)


async def test_floor_does_not_reach_into_other_peoples_documents(customer_docs, maker, flagship_id):
    """⚠️ 保底名额**绝不能**变成一条绕过可见性的路。

    这是全项目唯一一条错了就不可挽回的规则，所以每加一处排序逻辑都要再验一次：
    别人的私有块不在候选里，也就永远不会被「保底」进来。
    """
    maker_, owner_id, tag = customer_docs
    stranger = uuid.uuid4()
    async with maker_() as s:
        result = await search(
            s,
            "组合装拆分条件说明",
            FakeEmbedder(),
            RankByKeyword("条件说明"),
            user_id=stranger,  # 一个和这些文档毫无关系的人
            top_k=20,
            rerank_k=5,
        )
    titles = [c.citation.title for c in result.chunks]
    assert not any("客户A" in t for t in titles), f"泄漏了：{titles}"


# ---------- 第 3 步：有条件的主体约束 ----------


@pytest.mark.parametrize(
    "question",
    [
        "我们的组合装订单需要拆分吗",
        "星辰电商给中通配的日单量上限是多少",
        "我司的对账以什么为准",
        "华远科技的安全库存线是多少",
        # 2026-08-20 实测漏过的那一个：「家居」当时不在后缀表里
        "远岸家居的拣货模式用的是边拣边分还是先拣后分",
    ],
)
def test_subject_questions_are_recognised(question):
    assert asks_about_subject(question)


@pytest.mark.parametrize(
    "question",
    [
        "京东电子面单模板怎么设置",
        "退货入库的操作流程是什么",
        "微信视频号物流映射里韵达的平台编码是什么",
        "组合装拆分的条件有哪些",
        # ⚠️ 这三句是**后缀表扩容时的照妖镜**。「物流」「仓储」「品牌」都被加过
        # 又删掉——它们太常出现在正常的 ERP 问题里，加上去就会让
        # 「怎么设置」+「物流」凑成一个假的公司名
        "怎么设置物流单量限制",
        "仓储绩效策略在哪里配置",
        "货品品牌怎么维护",
    ],
)
def test_plain_product_questions_are_not_subject_questions(question):
    """⚠️ **宁可漏判不可误判。**

    误判是给一道正常的公共库问题套上「没讲这个主体就说不知道」，
    那会直接把假阴性率顶上去——而假阴性率是 55 题的既有指标。
    「京东」在这里尤其关键：它是产品支持的平台，不是某家客户。
    """
    assert not asks_about_subject(question)


def test_guard_is_appended_only_when_asked_for():
    plain = system_prompt_for("fast")
    guarded = system_prompt_for("fast", subject_guard=True)
    assert guarded.startswith(plain), "约束是**追加**的，不能改动铁律本身"
    assert len(guarded) > len(plain)
    assert "本轮补充" in guarded
    assert "本轮补充" not in plain


def test_both_modes_share_the_same_guard():
    """两档的铁律一个字都不能差。把防幻觉规则做成两份，迟早有一份先松掉。"""
    fast = system_prompt_for("fast", subject_guard=True)
    deep = system_prompt_for("deep", subject_guard=True)
    tail = fast[fast.index("本轮补充") :]
    assert deep.endswith(tail)


async def test_guard_no_longer_depends_on_whether_a_private_chunk_was_recalled(
    customer_docs, flagship_id
):
    """⭐ **原来还有第三个条件「这一轮一个私有块都没召回」，实测删掉了。**

    删的理由不是嫌它严，是它**和第 2 步互相拆台**：保底名额保证了至少有一个
    私有块进 top-k，于是「一个都没召回」对有私有文档的用户几乎永远不成立，
    主体约束等于被自己的队友关掉了。实测有两道 no_answer 题因此漏判——
    召回里确实有客户A 的块，但那些块讲的是仓库和对账，
    和「退货审核节点」「发票形式」毫无关系。

    真正该管的从来不是「有没有私有块」，是**这些私有块讲不讲他问的这件事**，
    而那件事只有模型判得了。
    """
    from copilot.qa import needs_subject_guard

    maker, owner_id, tag = customer_docs
    async with maker() as s:
        # 有私有文档 + 问的是主体 → 追加约束，**不管这一轮召回了什么**
        assert await needs_subject_guard(s, "星辰电商的退货入库要走哪几个审核节点", owner_id)
        # 没传过文档的人：永远不触发
        assert not await needs_subject_guard(s, "星辰电商的退货入库要走哪几个审核节点", None)
        # 传过文档、但问的不是某一方的约定：也不触发
        assert not await needs_subject_guard(s, "京东电子面单模板怎么设置", owner_id)


async def test_has_private_chunks_is_the_hard_boundary(customer_docs, flagship_id):
    """⭐ 这个判断是主体约束**结构上碰不到公共库那 55 题**的保证。

    评测公共库走的是 `user_id=None`，这个函数恒为 False，
    于是那条约束根本没有被触发的可能。M9 的教训是改全局规则会和铁律 3
    正面撞车——这一次，凡是不涉及私有库的请求，一个字都不会变。
    """
    maker, owner_id, tag = customer_docs
    async with maker() as s:
        assert await has_private_chunks(s, None) is False
        assert await has_private_chunks(s, uuid.uuid4()) is False
        assert await has_private_chunks(s, owner_id) is True


async def test_subject_without_relevant_private_hit_refuses_before_generation(
    customer_docs, flagship_id
):
    """公共默认规则不能被模型改写成某家公司的专属约定。"""
    from chat_helpers import FakeLLM

    from copilot.qa import NO_ANSWER, ask_stream

    maker, owner_id, _tag = customer_docs
    llm = FakeLLM("星辰电商默认按金额对账[1]。")
    async with maker() as s:
        streamed = await ask_stream(
            s,
            "星辰电商的发票怎么开？",
            FakeEmbedder(),
            AllBelowThreshold(),
            llm,
            user_id=owner_id,
            space_id=flagship_id,
        )

    answer = "".join(piece for kind, piece in streamed.stream if kind == "content")
    assert answer == NO_ANSWER
    assert llm.calls == [], "没有私有依据时不该让模型拿公共材料自由发挥"


# ---------- 点名主体：公共材料整块拿掉（2026-08-23） ----------
#
# ⭐ 这一组守的是私有库那条 0% 红线上最后一个破口。
# 「星辰电商的退货入库要走哪几个审核节点？」——夹具里没有退货流程，
# 公共库里却一大把。三轮实测模型都这么答：
#     「星辰电商的退货入库流程，按公共知识库的标准流程…[1][3]
#      关于星辰电商是否有额外的审核节点约定，知识库中暂无此内容。」
# 私有库幻觉率 16.7%，而门槛是 0%。主体约束那段 prompt 里已经写了
# 「公共知识库不是任何一家的约定」，又补了一条「先按公共流程答一遍、
# 末尾再说这家没提，也算冒充」——**都没用**。材料在上下文里，模型就会用。
#
# 所以改成数据层保证：点名了公司的问题，只留他自己的文档。
# 第一人称（「我们的电子面单怎么配」）不走这道闸门，理由见 qa.py 那段注释。


@pytest.mark.parametrize(
    "question",
    ["星辰电商的退货入库要走哪几个审核节点？", "远岸家居每月几号做动碰盘点", "汇金商贸的对账口径"],
)
def test_named_subject_questions_are_recognised(question):
    from copilot.qa import asks_about_named_subject

    assert asks_about_named_subject(question) is True


@pytest.mark.parametrize(
    "question",
    [
        # 第一人称：是主体问题，但**不算点名**——它常常其实在问产品本身
        "我们的组合装要拆吗",
        "我们公司的电子面单怎么配",
        # 纯产品问题
        "电子面单模板在哪里设置",
    ],
)
def test_first_person_and_product_questions_are_not_named_subjects(question):
    from copilot.qa import asks_about_named_subject

    assert asks_about_named_subject(question) is False


async def test_named_subject_answer_never_sees_public_material(customer_docs, flagship_id):
    """点名了公司，公共材料就不进上下文——模型想用也没得用。"""
    from chat_helpers import FakeLLM

    from copilot.qa import ask_stream

    maker, owner_id, tag = customer_docs
    llm = FakeLLM("星辰电商的组合装不拆[1]。")
    async with maker() as s:
        streamed = await ask_stream(
            s,
            f"星辰电商的组合装拆分条件说明第1条-{tag}是什么规则？",
            FakeEmbedder(),
            RankByKeyword("拆分"),
            llm,
            user_id=owner_id,
            space_id=flagship_id,
        )
    "".join(piece for kind, piece in streamed.stream if kind == "content")

    assert llm.calls, "这一问有私有材料，不该在生成之前就兜底"
    context = llm.calls[0][-1]["content"]
    assert "流程中拆分条件说明" not in context, "公共材料仍然进了上下文"
    assert "客户A-实施配置约定" in context, "他自己的文档被一起滤掉了"
    assert all("流程中拆分条件说明" not in c.title for c in streamed.citations)
    # ⭐ 滤完要**重排编号**。留着旧号的话，页面上会出现「来源 · 2」下面列着
    # 1 和 4（2026-08-23 人工验收的原句：「星辰电商的对账以什么为准？」），
    # 上下文里的材料也标着跳号的 [1] [4]，模型照抄，正文引用跟着跳。
    assert [c.n for c in streamed.citations] == list(
        range(1, len(streamed.citations) + 1)
    ), "滤掉公共块之后编号留了窟窿"
    import re

    assert [int(n) for n in re.findall(r"^\[(\d+)\] 来源：", context, re.M)] == list(
        range(1, len(streamed.citations) + 1)
    ), "上下文里的编号也要连续"
    # ⚠️ 这道断言在这个夹具上是**结构性成立**的（私有块正好排在最前），
    # 真正盯住重排的是 test_images.py::test_citations_are_renumbered_after_chunks_are_filtered_out。
    # 这里留着是为了在整条链路上再确认一次，两处缺一不可。


async def test_first_person_question_keeps_public_material(customer_docs, flagship_id):
    """第一人称那一支不动：把公共材料拿掉会把答得出的题变成「暂无此内容」。"""
    from chat_helpers import FakeLLM

    from copilot.qa import ask_stream

    maker, owner_id, tag = customer_docs
    llm = FakeLLM("组合装按条件拆分[1]。")
    async with maker() as s:
        streamed = await ask_stream(
            s,
            f"我们的组合装拆分条件说明第1条-{tag}是什么规则？",
            FakeEmbedder(),
            RankByKeyword("拆分"),
            llm,
            user_id=owner_id,
            space_id=flagship_id,
        )
    "".join(piece for kind, piece in streamed.stream if kind == "content")

    assert llm.calls
    context = llm.calls[0][-1]["content"]
    assert "流程中拆分条件说明" in context


# ---------- P4：Agent 白名单 ----------


def test_allowlist_matches_case_insensitively(monkeypatch):
    """注册时邮箱是小写存的，白名单不跟着小写的话，`.env` 里写了大写字母
    就静默地一个人都匹配不上——而那种失败没有任何症状：
    灰度「开了」，但所有人还走直路。"""
    from copilot.api.routes.chat import in_agent_allowlist
    from copilot.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("AGENT_ALLOW_EMAILS", "Me@Example.com, friend@example.com")
    try:
        assert in_agent_allowlist("me@example.com")
        assert in_agent_allowlist("FRIEND@EXAMPLE.COM")
        assert not in_agent_allowlist("stranger@example.com")
        assert not in_agent_allowlist("")
    finally:
        get_settings.cache_clear()


def test_empty_allowlist_lets_nobody_in(monkeypatch):
    """默认是空的，且空的就是**谁都不放**——不能因为没配就退化成全放。"""
    from copilot.api.routes.chat import in_agent_allowlist
    from copilot.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("AGENT_ALLOW_EMAILS", "")
    try:
        assert not in_agent_allowlist("anyone@example.com")
    finally:
        get_settings.cache_clear()


async def test_allowlisted_user_takes_the_agent(maker, monkeypatch, flagship_id):
    """点名的那个人，普通问答也走 Agent——这就是白名单灰度的形态。"""
    from copilot.api.routes.chat import _use_agent
    from copilot.config import get_settings

    tag = uuid.uuid4().hex[:8]
    email = f"vip-{tag}@t.local"
    async with maker() as s:
        user = User(email=email, password_hash="x")
        s.add(user)
        await s.commit()
        uid = user.id

    get_settings.cache_clear()
    monkeypatch.setenv("AGENT_ALLOW_EMAILS", email)
    try:
        async with maker() as s:
            u = await s.get(User, uid)
            assert await _use_agent(s, u, "电子面单怎么设置", None) is True
    finally:
        get_settings.cache_clear()
        async with maker() as s:
            await s.execute(delete(User).where(User.id == uid))
            await s.commit()
