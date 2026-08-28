"""端到端的 span 树：真的打一次 `/api/chat`，看树长得对不对。W1.1。

⭐ **这一份和 `test_obs.py` 的分工不一样。**
那边验的是 `obs.span()` 这个原语本身（关掉时是空操作、异常照抛、属性写坏不影响调用方）；
这一份验的是**埋点埋在了正确的位置**——一次真实请求走完，
树上该有的段一个不少，`chat.turn` 上的汇总数字和台账那一行对得上。

plan.md 里 W1.1 的验收条件原文：「问一个方案题，看板上能看到完整 span 树，
且各段耗时之和 ≈ 总耗时（对不上说明有段没埋）」。前半句在这里，
后半句在 `test_obs.py::test_子_span_的耗时之和约等于父_span`。

不联网：provider 全是假的（`fake_providers`），追踪走内存 exporter。
"""

from __future__ import annotations

import pytest
from chat_helpers import ask
from sqlalchemy import delete, select

from copilot import obs
from copilot.db.models import RequestTrace


@pytest.fixture
def traced():
    """内存 exporter + 直接塞 tracer。理由见 `test_obs.py` 里那个同名夹具。"""
    sdk = pytest.importorskip("opentelemetry.sdk.trace")
    export = pytest.importorskip("opentelemetry.sdk.trace.export")
    memory = pytest.importorskip("opentelemetry.sdk.trace.export.in_memory_span_exporter")

    exporter = memory.InMemorySpanExporter()
    provider = sdk.TracerProvider()
    provider.add_span_processor(export.SimpleSpanProcessor(exporter))
    obs.install_for_tests(provider.get_tracer("test"))
    try:
        yield exporter
    finally:
        obs.shutdown()


@pytest.fixture(autouse=True)
async def clean_traces(maker, logged_in):
    yield
    async with maker() as s:
        await s.execute(delete(RequestTrace).where(RequestTrace.user_id == logged_in))
        await s.commit()


def _names(exporter) -> set[str]:
    return {s.name for s in exporter.get_finished_spans()}


def _by_name(exporter) -> dict:
    return {s.name: s for s in exporter.get_finished_spans()}


async def test_一次普通问答的_span_树是完整的(
    api_client, logged_in, public_chunk, fake_providers, traced
) -> None:
    """直路走完，树上该有这几段。

    少一段就说明埋点掉了，而掉了不会报错——只会让看板上那一轮"变快"，
    然后有人照着那个数去优化别处。
    """
    r = await ask(api_client, "电子面单怎么配置？")
    assert r.status_code == 200

    names = _names(traced)
    assert "chat.turn" in names, "根 span 没了，整棵树会散成一堆孤儿"
    assert "route" in names
    assert "retrieve" in names
    assert "retrieve.embed" in names
    assert "retrieve.dense" in names
    assert "generate" in names


async def test_span_树的父子关系接得上(
    api_client, logged_in, public_chunk, fake_providers, traced
) -> None:
    """`retrieve` 必须挂在 `chat.turn` 底下。

    ⚠️ 这一条钉的是 obs.py 文件头那句「span 必须在同一个任务里开和关」。
    `_traced` 是个异步生成器，`chat.turn` 在它内部 yield 期间一直开着——
    contextvars 要是没串上，`retrieve` 会变成一棵孤儿树，
    看板上表现为"这一轮只有 chat.turn，什么都没做"。
    """
    await ask(api_client, "电子面单怎么配置？")

    got = _by_name(traced)
    turn = got["chat.turn"]
    assert got["route"].parent.span_id == turn.context.span_id
    assert got["retrieve"].parent.span_id == turn.context.span_id
    assert got["generate"].parent.span_id == turn.context.span_id
    assert got["retrieve.dense"].parent.span_id == got["retrieve"].context.span_id


async def test_看板上的数字和台账那一行是同一份(
    api_client, logged_in, public_chunk, fake_providers, traced, maker
) -> None:
    """⭐ **每个数只有一个定义**（M13 立的那条规矩）。

    `chat.turn` 的属性来自 `TraceDraft.summary()`，`request_trace` 那一行
    也来自同一批字段。两边分开算的话，看板说 `answer_source=kb`、
    表里写着 `general_knowledge`，没有任何办法知道该信哪个。
    """
    await ask(api_client, "电子面单怎么配置？")

    async with maker() as s:
        row = (
            await s.execute(select(RequestTrace).where(RequestTrace.user_id == logged_in))
        ).scalar_one()

    attrs = _by_name(traced)["chat.turn"].attributes
    assert attrs[f"{obs.NS}.route"] == row.route
    assert attrs[f"{obs.NS}.answer_source"] == row.answer_source
    assert attrs[f"{obs.NS}.chunk_count"] == row.chunk_count
    assert attrs[f"{obs.NS}.tokens"] == row.tokens
    assert attrs[f"{obs.NS}.ok"] is True


async def test_寒暄短路那一轮不该有检索和生成(
    api_client, logged_in, public_chunk, fake_providers, traced
) -> None:
    """「你好」是写死的一句话，**一次模型调用都不花**。

    树上就该看得出这件事——这正是 `route` 这个 span 存在的理由：
    一眼分清"这一轮走没走完整条链路"。
    """
    r = await ask(api_client, "你好")
    assert r.status_code == 200

    names = _names(traced)
    assert "chat.turn" in names
    assert "retrieve" not in names, "寒暄不该走检索"
    assert "generate" not in names, "寒暄不该调模型"
    assert _by_name(traced)["chat.turn"].attributes[f"{obs.NS}.route"] == "canned"


async def test_关掉追踪时_问答一切照旧(
    api_client, logged_in, public_chunk, fake_providers, maker
) -> None:
    """没有 traced 夹具 = 追踪是关着的（默认状态）。

    这一条是 W1.1 最重要的保证：**埋点绝不影响回答**。
    答案照出、台账照写，一个字都不少。
    """
    obs.shutdown()
    assert not obs.enabled()

    r = await ask(api_client, "电子面单怎么配置？")
    assert r.status_code == 200

    async with maker() as s:
        row = (
            await s.execute(select(RequestTrace).where(RequestTrace.user_id == logged_in))
        ).scalar_one()
    assert row.ok is True
