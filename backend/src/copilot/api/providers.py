"""进程内共享的 provider 实例。

**不要每个请求各建一个。** `SiliconFlowClient` 的限速器是实例级的
（`self._last_at`），每请求一个新实例就等于每个请求各限各的速——
两个人同时提问就会一起打过去，免费额度直接吃 429。
共享一个实例还顺带复用 httpx 连接池，省掉每次的 TLS 握手。

生命周期挂在 FastAPI 的 lifespan 上，进程退出时统一 close。
"""

from __future__ import annotations

import logging
from functools import lru_cache

from copilot.providers.llm import ChatLLM
from copilot.providers.siliconflow import (
    SiliconFlowClient,
    SiliconFlowEmbedder,
    SiliconFlowReranker,
)

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_siliconflow_client() -> SiliconFlowClient:
    return SiliconFlowClient()


@lru_cache(maxsize=1)
def get_embedder() -> SiliconFlowEmbedder:
    return SiliconFlowEmbedder(client=get_siliconflow_client())


@lru_cache(maxsize=1)
def get_reranker() -> SiliconFlowReranker:
    # 和 embedder 共用同一个底座，限速才是全局的
    return SiliconFlowReranker(client=get_siliconflow_client())


@lru_cache(maxsize=1)
def get_llm() -> ChatLLM:
    """简答档（DeepSeek）。默认走这条。"""
    return ChatLLM()


@lru_cache(maxsize=1)
def get_deep_llm() -> ChatLLM:
    """详解档（Kimi）。

    key 留空就复用 vision 那把——本来就是同一家 Moonshot 的密钥，
    没必要让人在 .env 里为同一个账号填两遍。
    """
    from copilot.config import get_settings

    s = get_settings()
    return ChatLLM(
        api_key=s.llm_deep_api_key or s.vision_api_key,
        base_url=s.llm_deep_base_url,
        model=s.llm_deep_model,
        forced_temperature=s.llm_deep_temperature,
    )


def get_llm_for(mode: str) -> ChatLLM:
    """按档位取模型。**详解档没配 key 时静默退回简答档**。

    宁可答得简略一点，也不能让人点一下选择器就收到 500——
    对用户来说那不是"少了个高级功能"，那是"这个产品坏了"。
    """
    if mode != "deep":
        return get_llm()
    try:
        return get_deep_llm()
    except Exception:  # noqa: BLE001 - 没配 key / 建不起来，退回简答档
        logger.warning("详解档不可用（多半是没配 LLM_DEEP_API_KEY / VISION_API_KEY），已退回简答档")
        return get_llm()


def get_vision():
    """读图客户端。**没配 key 就返回 None，不抛异常。**

    这条路只有 worker 解析图片时才走，而 worker 是常驻进程：在这里抛
    异常会让它每轮循环都因为「没配视觉」而崩一次，连带把普通 docx 的
    解析也拖下水。返回 None，让 parsers 那边给出一句人话的失败原因。
    """
    from copilot.config import get_settings

    if not (get_settings().vision_api_key or get_settings().llm_api_key):
        return None
    try:
        return _build_vision()
    except Exception:  # noqa: BLE001 - 建不起来就是没有，理由由解析那边报给用户
        return None


@lru_cache(maxsize=1)
def _build_vision():
    from copilot.providers.vision import VisionLLM

    return VisionLLM()


def close_all() -> None:
    """lifespan 关闭时调用。没被创建过的就不去碰它，免得反而触发一次初始化。"""
    for factory in (get_llm, get_deep_llm, get_siliconflow_client, _build_vision):
        if factory.cache_info().currsize:
            factory().close()
    for factory in (
        get_llm,
        get_deep_llm,
        get_embedder,
        get_reranker,
        get_siliconflow_client,
        _build_vision,
    ):
        factory.cache_clear()
