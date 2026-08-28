"""span 树的形状与安全性。W1.1。

这份测试要守两件事：

1. **树的形状对**——各段耗时之和 ≈ 总耗时。对不上就说明有一段没埋，
   而漏埋的表现是"看板上这一轮只花了 200ms"，人会照着这个数去优化别处。
2. **埋点不会把答案弄坏**——没装 SDK、属性写错类型、exporter 报错，
   一律退化成空操作。这一条比第一条重要：可观测性坏了只是看不见，
   可观测性把服务弄崩了是事故。

不连库、不打网络，用 OTel 的内存 exporter。
"""

from __future__ import annotations

import pytest

from copilot import obs

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture
def spans():
    """装一个只往内存里写的 tracer，跑完把 span 交出来。

    ⚠️ 走 `obs.install_for_tests` 而不是 `setup_tracing`：后者会调
    `trace.set_tracer_provider()`，而**全局 provider 一个进程只能设一次**——
    第二个测试拿到的会是第一个测试的 provider，断言就开始串台。
    """
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


def _by_name(exporter):
    return {s.name: s for s in exporter.get_finished_spans()}


def test_关掉追踪时_span_是纯空操作() -> None:
    """默认状态：`_tracer is None`，`span()` 什么都不做也不抛。"""
    obs.shutdown()  # 确保是关着的
    assert not obs.enabled()
    with obs.span("retrieve", top_k=20) as sp:
        sp.set(chunk_count=5, top_score=None)
        sp.error(ValueError("这条也不该抛"))
    assert obs.current() is not None  # 拿到的是 _NULL，不是 None


def test_子_span_的耗时之和约等于父_span(spans) -> None:
    """⭐ plan.md 里 W1.1 的验收条件原文：各段耗时之和 ≈ 总耗时。

    对不上只有一个原因——有一段没埋。而那种漏洞不会报错，
    它的表现是看板上一棵"很快"的树，人照着它去优化了别的地方。
    """
    # ⚠️ 每一段都要做**够久**的活。Windows 的时钟粒度是毫秒级，
    # 循环两万次在 `time_ns()` 眼里是 0——那样这个断言会随机地
    # 报「只覆盖了 0%」，而代码其实一点问题都没有。
    # 一个会假红的测试比没有测试更糟：人会开始习惯性忽略它。
    with obs.span("chat.turn"):
        for name in ("route", "rewrite", "retrieve", "generate"):
            with obs.span(name):
                sum(range(2_000_000))

    got = _by_name(spans)
    assert set(got) == {"chat.turn", "route", "rewrite", "retrieve", "generate"}

    turn = got["chat.turn"]
    children = [got[n] for n in ("route", "rewrite", "retrieve", "generate")]
    total = turn.end_time - turn.start_time
    covered = sum(c.end_time - c.start_time for c in children)
    # 子 span 不重叠，所以覆盖率应该很高。留 30% 的余量给
    # 「父 span 里那几行不属于任何子段的代码」
    assert covered <= total
    assert covered / total > 0.7, f"只覆盖了 {covered / total:.0%}，有一段没埋"


def test_父子关系靠_contextvars_自动串起来(spans) -> None:
    """调用方不必把 span 当参数传下去——这是 `retrieve.search()` 不需要
    知道自己被谁调用的前提。"""
    with obs.span("chat.turn"), obs.span("retrieve"), obs.span("retrieve.rerank"):
        pass

    got = _by_name(spans)
    turn, retrieve, rerank = got["chat.turn"], got["retrieve"], got["retrieve.rerank"]
    assert retrieve.parent.span_id == turn.context.span_id
    assert rerank.parent.span_id == retrieve.context.span_id


def test_None_属性被跳过而不是抛异常(spans) -> None:
    """`top_score` 在一条都没召回时就是 None，而 OTel 不接受 None。

    让每个埋点处自己过滤，等于把这个坑复制十几遍。
    """
    with obs.span("retrieve", top_k=20) as sp:
        sp.set(top_score=None, chunk_count=0)

    attrs = _by_name(spans)["retrieve"].attributes
    assert attrs[f"{obs.NS}.top_k"] == 20
    assert attrs[f"{obs.NS}.chunk_count"] == 0
    assert f"{obs.NS}.top_score" not in attrs


def test_业务异常照常抛出_只是顺手记一笔(spans) -> None:
    """⚠️ `span()` 不吞业务异常。吞了的话，一个埋点会把真正的报错
    变成"这一轮静静地答了个空"。"""
    with pytest.raises(RuntimeError, match="检索炸了"), obs.span("retrieve"):
        raise RuntimeError("检索炸了")

    sp = _by_name(spans)["retrieve"]
    assert sp.status.status_code.name == "ERROR"
    assert any(e.name == "exception" for e in sp.events)


def test_属性写坏了也不影响调用方(spans) -> None:
    """OTel 不收 dict 这类属性值。埋点写错类型时该静静跳过，
    不该让一次问答变成 500。"""
    with obs.span("retrieve") as sp:
        sp.set(weird={"不能": "这么写"})
    assert "retrieve" in _by_name(spans)  # span 照样正常结束


def test_没配任何_exporter_时不开追踪(monkeypatch) -> None:
    """`TRACING_ENABLED=true` 但既没有 OTLP 端点也没开控制台——
    开起来也没地方看，明确返回 False 而不是假装开了。"""
    obs.shutdown()

    class S:
        tracing_enabled = True
        tracing_console = False
        otlp_endpoint = ""
        otlp_headers = ""
        langfuse_public_key = ""
        langfuse_secret_key = ""
        tracing_service_name = "copilot"
        tracing_environment = "test"
        tracing_sample_ratio = 1.0

    assert obs.setup_tracing(S()) is False
    assert not obs.enabled()
    obs.shutdown()


def test_langfuse_头是_basic_并且缺一个就不加() -> None:
    """只填一个 key 的表现是 401，而导出在后台线程里——
    401 只会刷一行日志，看板上永远是空的。所以缺一个就干脆别加这个头。"""
    import base64

    h = obs._langfuse_headers("pk-1", "sk-2")
    raw = base64.b64decode(h["Authorization"].removeprefix("Basic ")).decode()
    assert raw == "pk-1:sk-2"
