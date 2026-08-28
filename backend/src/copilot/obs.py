"""可观测性：把一轮问答打成 span 树。W1.1。

    chat.turn
      ├─ route          寒暄短路 / 标准答案命中 —— 这两支根本不调模型
      ├─ rewrite        追问改写成独立问题（有历史才有这一段）
      ├─ retrieve       ├─ retrieve.dense   embedding + 向量召回 SQL
      │                 └─ retrieve.rerank  重排（要全部候选的分）
      ├─ tool:answer_kb Agent 路径才有
      └─ generate       从发出请求到最后一个正文字

**为什么要做这件事。** `request_trace` 一轮一行，有 `ttfb_ms` 和 `total_ms`，
但它答不了唯一要紧的那个问题：**p95 的 9771ms 花在哪一段**。是改写慢、
向量召回慢、重排慢，还是首 token 本来就慢？一行汇总看不到内部，
于是「优化 TTFB」只能靠猜——猜完改一版，指标动了也说不清是不是这个改动干的。

⭐ **三条不能破的规矩，和 trace.py 是同一套。**

1. **埋点绝不影响回答。** `span()` 在任何情况下都不因自身出错而抛：没装 SDK、
   没配置、导出失败、属性写错类型——全部退化成一个什么都不做的对象。
   一次问答因为埋点报错而变成 500，比完全没有可观测性糟得多。
2. **默认关。** `TRACING_ENABLED=false` 时 `span()` 返回同一个单例，
   不建对象、不取时间、不进 SDK。生产那台机器只有 1.6GB 内存
   （见 plan.md 七·约定 7），OTel SDK 的常驻占用要实测之后才谈开不开。
3. **SDK 是可选依赖。** 装在 `obs` 这个 extra 里，`uv sync` 默认不装。
   服务器上没装 SDK 时这个模块照样 import 得进来，只是永远返回空实现——
   把它写成硬依赖等于让 1.6GB 那台机器为一个默认关着的功能常驻几十兆。

⚠️ **span 的父子关系靠 contextvars 传递，不靠参数。**
所以 `retrieve.search()` 不需要知道自己被谁调用，它开的 span 会自动挂到
当轮的 `chat.turn` 下面。但这也意味着：**span 必须在同一个任务里开和关**。
丢进 `anyio.to_thread.run_sync` 的那段代码里不要再开 span——线程里
拿不到主任务的 context，开出来的会是一棵孤儿树，看起来像"这一段没被调用"。
所以 embedding / rerank 的计时都在**调用方**这一侧包住（见 `retrieve.py`）。
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

logger = logging.getLogger(__name__)

# OTel 的 Tracer。None = 没开或者装不上，这时全项目的 span 都是空实现
_tracer: Any = None
_setup_done = False

# 属性名前缀。Langfuse 之类的看板会把带点的名字自动分组，
# 混在 http.* / db.* 这些语义约定里会很难找
NS = "copilot"


class _NullSpan:
    """关掉追踪时返回的那个东西。**所有方法都是空操作。**

    它存在的理由是让调用方**不必写 if**：`with obs.span(...) as sp:` 这一行
    在开和关两种情况下长得一模一样。写成 `if tracing_on:` 的话，
    每个埋点处都会多出一个分支，而那些分支永远不会被测试覆盖到。
    """

    __slots__ = ()

    def set(self, **attrs: Any) -> None:
        return None

    def error(self, exc: BaseException) -> None:
        return None


_NULL = _NullSpan()


class _Span:
    """真 span 的薄包装。**把 OTel 的 API 挡在这一层后面。**

    直接把 otel 的 Span 对象交出去的话，埋点处就会开始出现
    `set_attribute` / `record_exception` / `Status(StatusCode.ERROR)`——
    于是关掉追踪时那些调用要么报 AttributeError，要么得在每处写 if。
    包一层之后，`_NullSpan` 只要长得一样就能顶替它。
    """

    __slots__ = ("_span",)

    def __init__(self, span: Any) -> None:
        self._span = span

    def set(self, **attrs: Any) -> None:
        """加属性。**None 直接跳过**——OTel 不接受 None，塞进去会抛。

        埋点处大量属性天然可空（`top_score` 在一条都没召回时就是 None），
        让每个调用方自己过滤等于把这个坑复制十几遍。
        """
        try:
            for k, v in attrs.items():
                if v is None:
                    continue
                self._span.set_attribute(f"{NS}.{k}", v)
        except Exception:  # noqa: BLE001 —— 见文件头第 1 条
            logger.debug("span 属性写失败", exc_info=True)

    def error(self, exc: BaseException) -> None:
        try:
            from opentelemetry.trace import Status, StatusCode

            self._span.record_exception(exc)
            self._span.set_status(Status(StatusCode.ERROR, str(exc)[:200]))
        except Exception:  # noqa: BLE001
            logger.debug("span 记异常失败", exc_info=True)


@contextmanager
def span(name: str, **attrs: Any) -> Iterator[Any]:
    """开一个 span。**永远 yield 一个能 `.set()` 的对象。**

    ⚠️ 业务异常照常往外抛——这里只负责在 span 上留个记号，不吞异常。
    吞了的话，一个埋点会把真正的报错变成"这一轮静静地答了个空"。
    """
    if _tracer is None:
        yield _NULL
        return
    try:
        cm = _tracer.start_as_current_span(name)
        raw = cm.__enter__()
    except Exception:  # noqa: BLE001 —— 见文件头第 1 条
        logger.debug("开 span 失败 name=%s", name, exc_info=True)
        yield _NULL
        return

    wrapped = _Span(raw)
    wrapped.set(**attrs)
    try:
        yield wrapped
    except BaseException as exc:
        wrapped.error(exc)
        try:
            cm.__exit__(type(exc), exc, exc.__traceback__)
        except Exception:  # noqa: BLE001
            logger.debug("关 span 失败 name=%s", name, exc_info=True)
        raise
    else:
        try:
            cm.__exit__(None, None, None)
        except Exception:  # noqa: BLE001
            logger.debug("关 span 失败 name=%s", name, exc_info=True)


def current() -> Any:
    """拿到当前 span，往上补属性用。

    ⭐ 有些属性要到很后面才知道：`answer_source` 得等答案写完、
    `ttfb_ms` 得等第一个正文字。它们属于 `chat.turn` 这个根 span，
    但产出它们的代码在好几层之下——一路把 span 对象当参数传下去
    会污染每一个函数签名，而 contextvars 本来就是为这件事存在的。
    """
    if _tracer is None:
        return _NULL
    try:
        from opentelemetry import trace

        sp = trace.get_current_span()
        # 没有活跃 span 时 OTel 返回一个 INVALID 的哨兵，往上写属性是无声丢弃。
        # 明确退回 _NULL，省得以后有人对着看板问"这个属性怎么没了"
        if not sp or not sp.get_span_context().is_valid:
            return _NULL
        return _Span(sp)
    except Exception:  # noqa: BLE001
        return _NULL


def enabled() -> bool:
    return _tracer is not None


def _langfuse_headers(public_key: str, secret_key: str) -> dict[str, str]:
    """Langfuse 的 OTLP 入口收 HTTP Basic。

    ⚠️ **两个 key 都得有。** 只填一个的表现是 401——而 OTLP 的导出是
    后台线程里异步做的，401 只会在 journal 里刷一行 warning，
    应用照常服务、看板上永远是空的。所以缺一个就干脆别开。
    """
    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def _parse_headers(raw: str) -> Iterator[tuple[str, str]]:
    """`k=v,k2=v2` → 键值对。OTel 官方环境变量就是这个格式。"""
    for part in (raw or "").split(","):
        if "=" in part:
            k, _, v = part.partition("=")
            if k.strip():
                yield k.strip(), v.strip()


def setup_tracing(settings: Any = None) -> bool:
    """装配追踪。返回是否真的开起来了。**幂等，且永远不抛。**

    调用点是 FastAPI 的 lifespan 和评测脚本。任何一步失败都只记一行日志、
    退回不追踪——一个可观测性组件把服务拉起不来，是所有失败模式里最蠢的一种。
    """
    global _tracer, _setup_done
    if _setup_done:
        return _tracer is not None
    _setup_done = True

    from copilot.config import get_settings

    s = settings or get_settings()
    if not s.tracing_enabled:
        return False

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
        from opentelemetry.sdk.trace.sampling import TraceIdRatioBased
    except ImportError:
        # SDK 没装（服务器上 `uv sync` 默认不装 obs 这个 extra）。
        # ⚠️ 这条要 warning 不能 debug：有人显式 `TRACING_ENABLED=true` 却
        # 什么都看不到时，日志里得有一句话告诉他缺的是什么
        logger.warning(
            "TRACING_ENABLED=true 但没装 OpenTelemetry SDK，本次不追踪。"
            "装法：cd backend && uv sync --extra obs"
        )
        return False

    try:
        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": s.tracing_service_name,
                    "deployment.environment": s.tracing_environment,
                }
            ),
            # 生产开采样。1.0 = 全采，本机和评测用这个
            sampler=TraceIdRatioBased(max(0.0, min(1.0, s.tracing_sample_ratio))),
        )

        exporters: list[Any] = []
        if s.tracing_console:
            exporters.append(ConsoleSpanExporter())
        if s.otlp_endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            headers = dict(_parse_headers(s.otlp_headers))
            if s.langfuse_public_key and s.langfuse_secret_key:
                headers.update(_langfuse_headers(s.langfuse_public_key, s.langfuse_secret_key))
            exporters.append(
                OTLPSpanExporter(endpoint=s.otlp_endpoint, headers=headers, timeout=10)
            )

        if not exporters:
            logger.warning("TRACING_ENABLED=true 但既没配 OTLP_ENDPOINT 也没开控制台导出")
            return False

        for exp in exporters:
            # ⚠️ **必须是 Batch 不是 Simple。** SimpleSpanProcessor 在每个 span
            # 结束时同步发一次 HTTP——那是在答题的热路径上加一次网络往返，
            # 而这个模块存在的理由恰恰是量延迟。量的东西自己制造延迟，
            # 得到的每一个数都是错的
            provider.add_span_processor(BatchSpanProcessor(exp))

        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer("copilot")
        logger.info(
            "追踪已开启 sample=%.2f endpoint=%s",
            s.tracing_sample_ratio,
            s.otlp_endpoint or "(仅控制台)",
        )
        return True
    except Exception:  # noqa: BLE001 —— 见文件头第 1 条
        logger.warning("装配追踪失败，本次不追踪", exc_info=True)
        _tracer = None
        return False


def shutdown() -> None:
    """关停时把还在队列里的 span 冲出去。

    ⚠️ **不冲的话最后一批 span 会丢。** BatchSpanProcessor 默认 5 秒一批，
    而一次 `Ctrl+C` 或 systemd restart 通常等不到那 5 秒——表现是
    "我刚问的那一轮在看板上找不到"，而那一轮往往正是要查的那一轮。
    """
    global _tracer, _setup_done
    if _tracer is None:
        return
    try:
        from opentelemetry import trace

        provider = trace.get_tracer_provider()
        if hasattr(provider, "shutdown"):
            provider.shutdown()
    except Exception:  # noqa: BLE001
        logger.debug("追踪关停失败", exc_info=True)
    finally:
        _tracer = None
        _setup_done = False


def install_for_tests(tracer: Any) -> None:
    """测试专用：直接塞一个 tracer 进来，不碰全局 TracerProvider。

    ⭐ 为什么不复用 `setup_tracing`：那个函数会调 `trace.set_tracer_provider()`，
    而 OTel 的全局 provider **一个进程只允许设一次**——第二次是一句
    warning 加静默忽略。测试里连着建几个内存 exporter 的话，
    第二个测试拿到的还是第一个的 provider，断言会莫名其妙地串台。
    """
    global _tracer, _setup_done
    _tracer = tracer
    _setup_done = True
