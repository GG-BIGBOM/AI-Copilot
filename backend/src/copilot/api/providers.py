"""进程内共享的 provider 实例。

**不要每个请求各建一个。** `SiliconFlowClient` 的限速器是实例级的
（`self._last_at`），每请求一个新实例就等于每个请求各限各的速——
两个人同时提问就会一起打过去，免费额度直接吃 429。
共享一个实例还顺带复用 httpx 连接池，省掉每次的 TLS 握手。

生命周期挂在 FastAPI 的 lifespan 上，进程退出时统一 close。
"""

from __future__ import annotations

from functools import lru_cache

from copilot.providers.llm import ChatLLM
from copilot.providers.siliconflow import (
    SiliconFlowClient,
    SiliconFlowEmbedder,
    SiliconFlowReranker,
)


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
    return ChatLLM()


def close_all() -> None:
    """lifespan 关闭时调用。没被创建过的就不去碰它，免得反而触发一次初始化。"""
    for factory in (get_llm, get_siliconflow_client):
        if factory.cache_info().currsize:
            factory().close()
    for factory in (get_llm, get_embedder, get_reranker, get_siliconflow_client):
        factory.cache_clear()
