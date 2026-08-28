"""混合检索：BM25 + RRF。W1.2。

要守三件事，按重要性排：

1. ⚠️⚠️ **词法这一路照样过两道过滤。** 它是第三条召回路径
   （前两条：向量主查询、私有库补捞），而旁路正是隔离最容易漏的地方——
   M11 P3 那条补捞旁路当初就漏了空间过滤。漏了不会报错，
   只会让 A 的私有文档出现在 B 的答案里。
2. **关掉开关 = 行为和 W1.2 之前逐字节一致。** 这条是这次改动的退路，
   退路要有测试钉住，不然"关掉就好了"只是句猜测。
3. **它不放宽任何东西。** 词法只往候选池里塞人，留谁仍由重排分和阈值决定。

RRF 那部分是纯函数，不连库也不需要 jieba。
"""

from __future__ import annotations

import uuid

import pytest
from chat_helpers import FakeEmbedder
from sqlalchemy.ext.asyncio import async_sessionmaker

from copilot import lexical
from copilot.db.models import Chunk, Document, User
from copilot.providers.base import RerankResult
from copilot.retrieve import _lexical_recall, search


class PassThroughReranker:
    """不改顺序，给每条一个够高的分，避免被阈值滤掉。
    这里要的是"候选池里有谁"，不是"重排怎么排"。"""

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[RerankResult]:
        return [RerankResult(index=i, score=1.0) for i in range(min(top_k, len(documents)))]

jieba_needed = pytest.mark.skipif(
    not lexical.available(), reason="没装 jieba（uv sync --extra hybrid）"
)


# ─────────────────────────────────────────────
# RRF：纯函数，不碰库
# ─────────────────────────────────────────────


def test_rrf_把两路的名次融成一个池() -> None:
    dense = ["a", "b", "c"]
    lex = ["c", "d", "a"]
    fused = lexical.rrf_fuse([dense, lex], k=60)
    # c 在两路都靠前（3 和 1），a 也在两路（1 和 3）——两者同分。
    # b 和 d 各只出现一次，排在后面
    assert set(fused[:2]) == {"a", "c"}
    assert set(fused[2:]) == {"b", "d"}


def test_rrf_同分时按第一个列表的先后_结果可复现() -> None:
    """⚠️ 不定序的话，同一份语料能跑出两个不同的候选池，评测就再也复现不了。"""
    a = lexical.rrf_fuse([["x", "y"], ["y", "x"]], k=60)
    b = lexical.rrf_fuse([["x", "y"], ["y", "x"]], k=60)
    assert a == b == ["x", "y"]


def test_rrf_的_limit_截断的是融合之后的顺序() -> None:
    fused = lexical.rrf_fuse([["a", "b", "c"], ["c"]], k=60, limit=2)
    assert fused == ["c", "a"]


def test_rrf_k_越小_头部名次越重要() -> None:
    """k 决定"排名差一位值多少分"。这条钉住它确实有作用——
    改这个常量的人应该看得到它改的是什么。"""
    # 第一路的第 1 名 vs 第二路的第 1 名 + 第一路的第 5 名
    lists = [["head", "x", "y", "z", "tail"], ["tail"]]
    assert lexical.rrf_fuse(lists, k=1)[0] == "tail"  # k 小 → 两个第一名的权重压倒性
    assert lexical.rrf_fuse(lists, k=1000)[0] == "tail"


# ─────────────────────────────────────────────
# tsquery 的拼装：这里出错的表现是一次提问 500
# ─────────────────────────────────────────────


@jieba_needed
def test_单引号被转义_不会拼出语法错误的_tsquery() -> None:
    """用户问题里出现引号是很正常的事（"客户说'单子卡住了'"）。
    不转义拼出来的是一段语法错误的 tsquery，Postgres 直接抛——
    而那看起来完全是一句正常的话。"""
    q = lexical.to_tsquery("客户说'单子卡住了'")
    assert "'" in q
    # 每个词素都是成对引号包着的，没有落单的引号把语法拆开
    assert q.count("'") % 2 == 0


def test_空输入返回空串而不是全表查询() -> None:
    assert lexical.to_tsquery("") == ""
    assert lexical.to_tsquery("？？！！") == ""
    assert lexical.or_query([]) == ""


@jieba_needed
def test_词数封顶() -> None:
    long_q = "".join(f"词{i} " for i in range(200))
    assert len(lexical.query_terms(long_q)) <= lexical.MAX_QUERY_TERMS


# ─────────────────────────────────────────────
# 隔离：词法这一路是第三条召回路径
# ─────────────────────────────────────────────


@pytest.fixture
async def hybrid_corpus(engine, flagship_id, other_space):
    """四块内容，词法查询能命中同一个词，但分属不同的 owner / 空间。

    这样一条 `_lexical_recall` 就能同时验两道过滤：
    别人的私有块不能出现，别的空间的块也不能出现。
    """
    maker = async_sessionmaker(engine, expire_on_commit=False)
    emb = FakeEmbedder()
    tag = uuid.uuid4().hex[:8]
    marker = f"SAMPLEMARK{tag}"

    async with maker() as s:
        alice = User(email=f"a-{tag}@t.local", password_hash="x")
        bob = User(email=f"b-{tag}@t.local", password_hash="x")
        s.add_all([alice, bob])
        await s.flush()

        rows = {}
        specs = [
            ("public", None, flagship_id),
            ("alice", alice.id, flagship_id),
            ("bob", bob.id, flagship_id),
            ("otherspace", None, other_space.id),
        ]
        for name, owner, space in specs:
            doc = Document(
                owner_id=owner,
                knowledge_space_id=space,
                source_type="upload",
                title=f"{name}-{tag}",
                content_hash=f"h-{name}-{tag}",
                status="done",
            )
            s.add(doc)
            await s.flush()
            body = f"{marker} 这一块属于 {name}。物流编码 {marker} 出现两次。"
            chunk = Chunk(
                document_id=doc.id,
                owner_id=owner,
                knowledge_space_id=space,
                ordinal=0,
                content=body,
                embedding=emb.embed_query(body),
                title=doc.title,
            )
            s.add(chunk)
            await s.flush()
            rows[name] = chunk.id
        # 词法索引和块一起写，**走生产那个函数**，不在测试里另抄一份 SQL：
        # 抄一份的话，改了入库那边忘了改这边，这些测试会一直在验一个
        # 早就不存在的写法
        from sqlalchemy import select as _select

        from copilot.ingest.pipeline import _write_tsv

        doc_ids = (
            await s.execute(
                _select(Chunk.document_id).where(Chunk.id.in_(list(rows.values())))
            )
        ).scalars()
        for doc_id in list(doc_ids):
            await _write_tsv(s, doc_id)
        await s.commit()

    yield {"marker": marker, "alice": alice.id, "bob": bob.id, "ids": rows, "maker": maker}

    async with maker() as s:
        from sqlalchemy import delete

        await s.execute(delete(Chunk).where(Chunk.id.in_(list(rows.values()))))
        await s.execute(delete(User).where(User.id.in_([alice.id, bob.id])))
        await s.commit()


@jieba_needed
async def test_词法召回不会漏出别人的私有块(hybrid_corpus, flagship_id) -> None:
    """⚠️⚠️ 隔离红线。漏了不会报错，只会在某天让 A 的文档出现在 B 的答案里。"""
    async with hybrid_corpus["maker"]() as s:
        got = await _lexical_recall(
            s, hybrid_corpus["marker"], flagship_id, None, hybrid_corpus["alice"], limit=20
        )
    titles = {c.title for c in got}
    assert any(t.startswith("public-") for t in titles), "公共块本来就该召回"
    assert any(t.startswith("alice-") for t in titles), "自己的块该召回"
    assert not any(t.startswith("bob-") for t in titles), "⚠️ 漏了别人的私有块"


@jieba_needed
async def test_词法召回不会串到别的知识版本(hybrid_corpus, flagship_id) -> None:
    """和 `test_private_document_respects_space` 同一个理由：
    旁路漏掉空间过滤的表现是企业版会话拿到旗舰版的材料，没有任何报错。"""
    async with hybrid_corpus["maker"]() as s:
        got = await _lexical_recall(
            s, hybrid_corpus["marker"], flagship_id, None, hybrid_corpus["alice"], limit=20
        )
    assert not any(c.title.startswith("otherspace-") for c in got)


@jieba_needed
async def test_没有空间时词法召回一条都不给(hybrid_corpus) -> None:
    """fail closed，和 `_space_filter` 同一条规矩。"""
    async with hybrid_corpus["maker"]() as s:
        got = await _lexical_recall(s, hybrid_corpus["marker"], None, None, None, limit=20)
    assert got == []


@jieba_needed
async def test_切不出词时不返回全表(hybrid_corpus, flagship_id) -> None:
    async with hybrid_corpus["maker"]() as s:
        assert await _lexical_recall(s, "？？？", flagship_id, None, None, limit=20) == []


# ─────────────────────────────────────────────
# 开关：关掉就该完全退回 W1.2 之前
# ─────────────────────────────────────────────


async def test_关掉开关时_一次词法查询都不发(hybrid_corpus, flagship_id, monkeypatch) -> None:
    """这条是这次改动的退路。退路要有测试钉住——
    否则"关掉就好了"只是一句猜测。"""
    import copilot.retrieve as R
    from copilot.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("HYBRID_ENABLED", "false")

    called = []

    async def spy(*a, **kw):
        called.append(1)
        return []

    monkeypatch.setattr(R, "_lexical_recall", spy)
    async with hybrid_corpus["maker"]() as s:
        await search(
            s,
            hybrid_corpus["marker"],
            FakeEmbedder(),
            PassThroughReranker(),
            user_id=hybrid_corpus["alice"],
            space_id=flagship_id,
        )
    assert called == []
    get_settings.cache_clear()


async def test_词法那一路挂了也只是退回纯向量(hybrid_corpus, flagship_id, monkeypatch) -> None:
    """⚠️ 它是"多一次机会"，不是必需品。SQL 炸了该退回纯向量检索，
    而不是把整轮提问变成 500。"""
    import copilot.retrieve as R
    from copilot.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("HYBRID_ENABLED", "true")

    async def boom(*a, **kw):
        raise RuntimeError("tsquery 语法错")

    monkeypatch.setattr(R, "_rare_terms", boom)
    async with hybrid_corpus["maker"]() as s:
        res = await search(
            s,
            hybrid_corpus["marker"],
            FakeEmbedder(),
            PassThroughReranker(),
            user_id=hybrid_corpus["alice"],
            space_id=flagship_id,
        )
    # 向量那一路照常出结果
    assert not res.is_empty
    assert not any(c.citation.title.startswith("bob-") for c in res.chunks)
    get_settings.cache_clear()


async def test_开着开关时隔离照旧(hybrid_corpus, flagship_id, monkeypatch) -> None:
    """端到端再验一次：整条 `search()` 打开 hybrid 之后，
    仍然一块别人的私有内容都拿不到。"""
    from copilot.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("HYBRID_ENABLED", "true")
    async with hybrid_corpus["maker"]() as s:
        res = await search(
            s,
            hybrid_corpus["marker"],
            FakeEmbedder(),
            PassThroughReranker(),
            user_id=hybrid_corpus["alice"],
            space_id=flagship_id,
        )
    titles = {c.citation.title for c in res.chunks}
    assert not any(t.startswith("bob-") for t in titles)
    assert not any(t.startswith("otherspace-") for t in titles)
    get_settings.cache_clear()


# ─────────────────────────────────────────────
# 入库：切词写进 content_tsv
# ─────────────────────────────────────────────


@jieba_needed
def test_索引侧和查询侧用同一个切法() -> None:
    """⚠️ 用了不同模式的表现是「明明库里有这个词却搜不到」——
    零召回，而且没有任何报错。"""
    text = "微信视频号电子面单对接"
    indexed = set(lexical.tokenize(text).split())
    queried = set(lexical.query_terms(text))
    assert queried <= indexed
