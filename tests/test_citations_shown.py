"""来源清单只列**正文真的引用过**的那几条——直路和 Agent 两条路各验一遍。

⭐ **线上台账里的样子（2026-08-24 查生产库）：** 一条走方案流程的会话，
连「你好」「好的谢谢」都挂着 **21 条来源**（`chunk_count=21`,
`answer_source=tool`）。出方案那条路会大范围检索，而方案正文一个 `[n]`
都不写——于是整轮的召回全被当成"来源"挂在了一句寒暄下面。

来源清单是给人**溯源**用的，不是"这一轮检索到了什么"的日志。

⚠️ **为什么两条路各写一遍。** 挂来源这件事在 `chat.py` 里有两段各自独立的
代码（直路一段、Agent 一段），M13.1 的配图回归就是被这个形状咬过一次：
一段改了、另一段没改，用户看到的差别是"有的会话有、有的没有"，不报错。
"""

from __future__ import annotations

import json
import uuid

import pytest
from chat_helpers import FakeEmbedder, FakeLLM, ask, parts
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from sqlalchemy import delete, select

from copilot.db.models import Chunk, Conversation, Document, Message
from copilot.providers.base import RerankResult


class KeepAllReranker:
    """两篇都留下。

    ⚠️ **不能用 `chat_helpers.TopOneReranker`**：它只留第一名，于是召回永远
    只有一条来源——"只挂引用过的"这件事就没有可观察的差别，测试会在
    过滤器被删掉之后**照样全绿**（第一版就是这样，变异验证时才发现）。
    """

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[RerankResult]:
        return [RerankResult(index=i, score=0.9 - i * 0.1) for i in range(len(documents))]


def scripted(*turns) -> FunctionModel:
    """按顺序把这些回复喂给 Agent（同 `test_agent_images.py` 里那份）。"""
    it = iter(turns)

    async def f(messages, info):  # noqa: ARG001 - FunctionModel 的签名要求
        try:
            turn = next(it)
        except StopIteration:  # pragma: no cover - 只有测试写错了才会到这
            raise AssertionError("Agent 比预期多请求了一次模型") from None
        if isinstance(turn, str):
            for ch in turn:
                yield ch
        else:
            yield turn

    return FunctionModel(stream_function=f)


def call(name: str, **args) -> dict[int, DeltaToolCall]:
    return {
        0: DeltaToolCall(
            name=name, json_args=json.dumps(args, ensure_ascii=False), tool_call_id=f"call_{name}"
        )
    }


@pytest.fixture
async def two_public_chunks(maker):
    """两篇公共文档，检索会同时召回——这样"只挂引用过的"才有可观察的差别。"""
    tag = uuid.uuid4().hex[:8]
    made: list[uuid.UUID] = []
    body = f"电子面单打印设置与物流账号绑定-{tag}"
    async with maker() as s:
        for i in range(2):
            doc = Document(
                owner_id=None,
                source_type="yuque",
                title=f"面单设置第{i + 1}篇-{tag}",
                source_url=f"https://www.yuque.com/wdterpqjb/{tag}-{i}",
                content_hash=uuid.uuid4().hex,
                status="done",
                chunk_count=1,
            )
            s.add(doc)
            await s.flush()
            text = f"{body}｜第{i + 1}篇的正文"
            s.add(
                Chunk(
                    document_id=doc.id,
                    owner_id=None,
                    ordinal=0,
                    content=text,
                    embedding=FakeEmbedder().embed_query(text),
                    title=doc.title,
                    heading="设置",
                    source_url=doc.source_url,
                )
            )
            made.append(doc.id)
        await s.commit()

    yield body

    async with maker() as s:
        await s.execute(delete(Chunk).where(Chunk.document_id.in_(made)))
        await s.execute(delete(Document).where(Document.id.in_(made)))
        await s.commit()


@pytest.fixture
def wire(monkeypatch, maker):
    """把两条路都接到假 provider 上，并把路由钉死（路由本身在别处测）。"""

    def _wire(reply: str, *, agent: bool) -> FakeLLM:
        from copilot.agent import runner as runner_module
        from copilot.api import providers
        from copilot.api import trace as trace_module
        from copilot.api.routes import chat as chat_module

        llm = FakeLLM(reply)
        monkeypatch.setattr(providers, "get_embedder", FakeEmbedder)
        monkeypatch.setattr(providers, "get_reranker", KeepAllReranker)
        monkeypatch.setattr(providers, "get_llm", lambda: llm)
        monkeypatch.setattr(chat_module, "SessionLocal", maker)
        monkeypatch.setattr(trace_module, "SessionLocal", maker)

        async def _forced(*_a, **_kw) -> bool:
            return agent

        monkeypatch.setattr(chat_module, "_use_agent", _forced)

        if agent:
            real_build = runner_module.build_agent
            monkeypatch.setattr(
                runner_module,
                "build_agent",
                lambda model=None: real_build(scripted(call("answer_kb"), "完毕")),
            )
        return llm

    return _wire


def shown_citations(body: str) -> list[dict]:
    for part in parts(body):
        if part["type"] == "data-citations":
            return part["data"]["citations"]
    return []


async def stored_citations(maker, user_id):
    async with maker() as s:
        return list(
            (
                await s.execute(
                    select(Message.citations)
                    .join(Conversation, Message.conversation_id == Conversation.id)
                    .where(Conversation.user_id == user_id, Message.role == "assistant")
                )
            ).scalars()
        )


# ─────────────────────────────────────────────────────────
# 直路
# ─────────────────────────────────────────────────────────


async def test_direct_shows_only_the_cited_source(
    api_client, logged_in, two_public_chunks, wire, maker
):
    """召回两篇、正文只引了 [1] —— 清单里就只该有那一条。"""
    wire("按第一篇的说明操作即可。[1]", agent=False)
    r = await ask(api_client, two_public_chunks)

    shown = shown_citations(r.text)
    assert [c["n"] for c in shown] == [1], f"召回两篇、只引了一篇，却挂了 {len(shown)} 条"
    stored = await stored_citations(maker, logged_in)
    assert stored and [c["n"] for c in stored[0]] == [1]


async def test_direct_answer_without_any_citation_shows_none(
    api_client, logged_in, two_public_chunks, wire, maker
):
    """一个 `[n]` 都没写（方案 / 常识 / 寒暄的形状）→ 一条来源都不挂。"""
    wire("按你的情况，建议先接入店铺，再配置物流。", agent=False)
    r = await ask(api_client, two_public_chunks)

    assert shown_citations(r.text) == []
    assert await stored_citations(maker, logged_in) == [None]


# ─────────────────────────────────────────────────────────
# Agent
# ─────────────────────────────────────────────────────────


async def test_agent_shows_only_the_cited_source(
    api_client, logged_in, two_public_chunks, wire, maker
):
    """Agent 路同样只挂引用过的那条。**这一条守的是那段独立的转发代码。**"""
    wire("按第一篇的说明操作即可。[1]", agent=True)
    r = await ask(api_client, two_public_chunks)

    shown = shown_citations(r.text)
    assert [c["n"] for c in shown] == [1], f"召回两篇、只引了一篇，却挂了 {len(shown)} 条"
    stored = await stored_citations(maker, logged_in)
    assert stored and [c["n"] for c in stored[0]] == [1]


async def test_agent_answer_without_any_citation_shows_none(
    api_client, logged_in, two_public_chunks, wire, maker
):
    """⭐ 生产上那 21 条来源的形状：工具答完、正文不写 `[n]`。

    出方案、导出、寒暄都是这个形状——它们本来就没有可溯源的东西可挂。
    """
    wire("方案已生成：先接入店铺，再配置物流，最后开自动打印。", agent=True)
    r = await ask(api_client, two_public_chunks)

    assert shown_citations(r.text) == []
    assert await stored_citations(maker, logged_in) == [None]
