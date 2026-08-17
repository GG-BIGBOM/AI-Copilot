"""Pydantic AI 用的模型对象。

**走通用的 OpenAI 兼容 provider，不用 `DeepSeekProvider`。** 后者把 base_url
写死在 DeepSeek，而本项目的 `llm_base_url` 是可配的（`plan.md` 的选型是
「DeepSeek / 通义 / KIMI 一套代码通吃」，见 providers/llm.py）。用
`OpenAIProvider(base_url=...)` 才能保持这个可换性——换供应商仍然只改 .env。

模型对象每次现建：它本身很轻（只包一个 AsyncOpenAI 客户端），而缓存成全局
会把 httpx 连接池的生命周期和 FastAPI 的 lifespan 绑在一起，多一处要收尾的东西。
真成为瓶颈再说（`plan.md` 七·5：不为假想需求先付工程费）。
"""

from __future__ import annotations

from copilot.config import get_settings
from copilot.providers.siliconflow import ProviderError


def build_model():
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    s = get_settings()
    if not s.llm_api_key:
        raise ProviderError("缺少 LLM_API_KEY，Agent 起不来")
    return OpenAIChatModel(
        s.llm_model,
        provider=OpenAIProvider(base_url=s.llm_base_url, api_key=s.llm_api_key),
    )
