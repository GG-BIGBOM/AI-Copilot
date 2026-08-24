"""评测的空间契约（M19-A）：题集属于哪个知识版本，检索就必须限定在哪个版本。

在这一步之前，`eval/run.py` 的两条检索路径都写死 `spaces.default_id()`——
评测因此**只量得了旗舰版**，而 M18 要问的那个问题（"把企业版语料导进去，
会不会污染旗舰版的答案"）在导入之前一次都问不出来。

这一层守三件事：

1. 空间是**参数**，拼错了要当场退出，不能悄悄回落到旗舰版；
2. 结果档案里记得住「这一轮跑的是哪个空间、哪一份语料」；
3. 检索结果每一块都带着自己的 `knowledge_space_id`，跨空间的断言才能
   **逐块核对**，而不是"相信过滤器写对了"。
"""

from __future__ import annotations

import importlib.util
import sys
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, update

from copilot import spaces
from copilot.db.models import Chunk, Document
from copilot.retrieve import search

EVAL_DIR = Path(__file__).resolve().parents[1] / "eval"
DIM = 1024


def _load_run():
    """同 `test_eval_scoring.py`：按路径载入 `eval/run.py`，模块名只此一个。"""
    if "eval_run" in sys.modules:
        return sys.modules["eval_run"]
    spec = importlib.util.spec_from_file_location("eval_run", EVAL_DIR / "run.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eval_run"] = mod
    spec.loader.exec_module(mod)
    return mod


run = _load_run()


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


class PassThroughReranker:
    def rerank(self, query: str, documents: list[str], top_k: int):
        from copilot.providers.base import RerankResult

        return [RerankResult(index=i, score=1.0) for i in range(min(top_k, len(documents)))]


@pytest.fixture
async def chunk_in(maker):
    """往指定空间塞一块，用完删掉。"""
    docs: list[uuid.UUID] = []

    async def make(space_id: uuid.UUID, body: str) -> uuid.UUID:
        async with maker() as s:
            doc = Document(
                owner_id=None,
                source_type="yuque",
                title=f"评测空间夹具-{uuid.uuid4().hex[:8]}",
                source_url="https://www.yuque.com/wdterpqjb/eval-space",
                content_hash=uuid.uuid4().hex,
                status="done",
                chunk_count=1,
                knowledge_space_id=space_id,
            )
            s.add(doc)
            await s.flush()
            chunk = Chunk(
                document_id=doc.id,
                owner_id=None,
                knowledge_space_id=space_id,
                ordinal=0,
                content=body,
                embedding=FakeEmbedder().embed_query(body),
                title=doc.title,
                heading=None,
                source_url=doc.source_url,
            )
            s.add(chunk)
            await s.commit()
            docs.append(doc.id)
            return chunk.id

    yield make

    async with maker() as s:
        await s.execute(delete(Chunk).where(Chunk.document_id.in_(docs)))
        await s.execute(delete(Document).where(Document.id.in_(docs)))
        await s.commit()


# ─────────────────────────────────────────────────────────
# 1. 空间是参数，且 fail closed
# ─────────────────────────────────────────────────────────


def test_the_eval_default_space_is_the_app_default():
    """`eval/run.py` 里那个字面量必须和 `copilot.spaces.DEFAULT` 是同一个。

    评测那边写字面量是为了让纯函数测试不必连库；代价是**两处得有人核对**。
    这条测试就是那个人——没有它，改了 code 之后评测会继续量一个
    早就不存在的空间，而报告照常打印。
    """
    assert run.DEFAULT_SPACE == spaces.DEFAULT


async def test_a_typo_in_the_space_stops_the_run(maker):
    """`--space enterprise_desktp` 要当场退出，**不能回落到旗舰版**。

    回落的后果没有任何症状：评测又量了一遍旗舰版，而报告抬头写着企业版，
    门禁于是拿着一份"看起来通过了"的证据放行 M18 的导入。
    """
    async with maker() as s:
        with pytest.raises(SystemExit) as e:
            await run.resolve_space(s, "enterprise_desktp")
    assert "enterprise_desktop" in str(e.value)  # 报错里要给出可选值


async def test_an_inactive_space_can_still_be_evaluated(maker):
    """企业版现在是 `inactive`（语料还没导入），但评测必须能问它。

    M18 的门禁恰恰是「在**空的**企业版空间里问旗舰版的问题，一条都不该召回」——
    要是 inactive 的空间连跑都跑不了，这条门禁就无从跑起。
    """
    async with maker() as s:
        space = await run.resolve_space(s, spaces.ENTERPRISE_DESKTOP)
    assert space.code == spaces.ENTERPRISE_DESKTOP
    assert space.status == "inactive"


# ─────────────────────────────────────────────────────────
# 2. 语料指纹
# ─────────────────────────────────────────────────────────


async def test_corpus_fingerprint_notices_a_changed_chunk(maker, chunk_in, flagship_id):
    """块数没变、内容变了（勘误改了一句话）——指纹必须变。

    ⭐ 这正是 `chunk_count` 答不了的那个问题。两轮结果的配置栏一模一样，
    而其中一轮跑的语料已经不是另一轮那份了，**报告上完全看不出来**。
    """
    async with maker() as s:
        common = await spaces.common_id(s)
        chunk_id = await chunk_in(flagship_id, "自动审核失败后一共重试 48 次")
        before = await run.corpus_fingerprint(s, flagship_id, common)

    async with maker() as s:
        await s.execute(
            update(Chunk).where(Chunk.id == chunk_id).values(content="重试次数达到 10 次不再尝试")
        )
        await s.commit()
        after = await run.corpus_fingerprint(s, flagship_id, common)

    assert before["chunk_count"] == after["chunk_count"]
    assert before["corpus_sha"] != after["corpus_sha"]
    assert len(after["corpus_sha"]) == 12


async def test_corpus_fingerprint_is_scoped_to_the_space(
    maker, chunk_in, flagship_id, other_space
):
    """别的空间多一块，旗舰版的指纹不该动——否则它量的不是「这一轮看得见的语料」。"""
    async with maker() as s:
        common = await spaces.common_id(s)
        before = await run.corpus_fingerprint(s, flagship_id, common)

    await chunk_in(other_space.id, "客户端企业版专属的一段说明")

    async with maker() as s:
        after = await run.corpus_fingerprint(s, flagship_id, common)
        theirs = await run.corpus_fingerprint(s, other_space.id, common)

    assert after == before
    assert theirs["corpus_sha"] != before["corpus_sha"]


async def test_common_knowledge_counts_toward_every_space(maker, chunk_in, flagship_id):
    """`common` 的块要算进每个空间的指纹——它本来就会被任何空间召回。"""
    async with maker() as s:
        common = await spaces.common_id(s)
        assert common is not None
        before = await run.corpus_fingerprint(s, flagship_id, common)

    await chunk_in(common, "跨版本都适用的一段通用说明")

    async with maker() as s:
        after = await run.corpus_fingerprint(s, flagship_id, common)
    assert after["chunk_count"] == before["chunk_count"] + 1


# ─────────────────────────────────────────────────────────
# 3. 每一块都带着自己的空间
# ─────────────────────────────────────────────────────────


async def test_retrieved_chunks_carry_their_own_space(maker, chunk_in, flagship_id):
    """检索结果里每一块都要带 `space_id`。

    跨空间评测靠它**逐块核对**，而不是断言"过滤器应该是对的"。
    过滤器写错时，返回的块看起来和正确的一模一样——没有这一列，
    评测除了相信它没有别的办法。
    """
    body = f"逐块核对空间的测试语料-{uuid.uuid4().hex[:8]}"
    await chunk_in(flagship_id, body)
    async with maker() as s:
        res = await search(
            s,
            body,
            FakeEmbedder(),
            PassThroughReranker(),
            user_id=None,
            space_id=flagship_id,
            top_k=20,
            rerank_k=20,
        )
    assert res.chunks, "夹具塞的那一块没召回来，后面的断言就没意义了"
    assert {c.space_id for c in res.chunks} == {flagship_id}


async def test_a_query_in_an_empty_space_returns_nothing(
    maker, chunk_in, flagship_id, other_space
):
    """**M18 的门禁本体**：在还没导入语料的企业版空间里问旗舰版的问题，
    一条都不该召回。

    这条现在就该是绿的——它证明的是「隔离在导入之前已经成立」，
    而不是导入之后再来补证。
    """
    body = f"旗舰版独有的一段步骤说明-{uuid.uuid4().hex[:8]}"
    await chunk_in(flagship_id, body)
    async with maker() as s:
        res = await search(
            s,
            body,
            FakeEmbedder(),
            PassThroughReranker(),
            user_id=None,
            space_id=other_space.id,
            top_k=20,
            rerank_k=20,
        )
    assert [c.citation.title for c in res.chunks] == []
