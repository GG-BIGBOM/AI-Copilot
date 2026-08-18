"""LLM 封装（OpenAI 兼容接口：DeepSeek / 通义 / KIMI 一套代码通吃）。"""

from __future__ import annotations

from collections.abc import Iterator

import httpx

from copilot.config import get_settings
from copilot.providers.siliconflow import ProviderError


class ChatLLM:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        forced_temperature: float | None = None,
    ) -> None:
        """
        Args:
            forced_temperature: 这个模型只认某一个温度值，调用方传什么都按它来。
                ⚠️ **kimi-k2.5 / k2.6 / k3 全都只接受 temperature=1**，
                传 0.1 会直接 HTTP 400（`invalid temperature: only 1 is allowed
                for this model`）。而我们全局默认是 0.1——知识库问答要的是照着
                材料说，不是创作。所以这里得允许按模型锁死温度，
                否则换上 Kimi 的那一刻，每一次提问都是 400。
                （`moonshot-v1-*` 系列不受此限，两种温度都收。）
        """
        s = get_settings()
        key = api_key or s.llm_api_key
        if not key:
            raise ProviderError(
                "缺少 LLM_API_KEY。复制 backend/.env.example 成 .env 并填入 DeepSeek 密钥"
            )
        self._model = model or s.llm_model
        self._forced_temperature = forced_temperature
        self._client = httpx.Client(
            base_url=base_url or s.llm_base_url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(180.0, connect=15.0),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> ChatLLM:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def stream(self, messages: list[dict], temperature: float = 0.1) -> Iterator[str]:
        """流式生成。temperature 压得很低——知识库问答要的是照着材料说，不是创作。

        除非这个模型自己锁死了温度（见 `forced_temperature`）。
        """
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": (
                temperature if self._forced_temperature is None else self._forced_temperature
            ),
            "stream": True,
        }
        with self._client.stream("POST", "/chat/completions", json=payload) as resp:
            if resp.status_code != 200:
                resp.read()
                raise ProviderError(f"HTTP {resp.status_code}: {resp.text[:300]}")
            for line in resp.iter_lines():
                if not line or not line.startswith("data: "):
                    continue
                data = line[6:]
                if data == "[DONE]":
                    break
                try:
                    import json

                    delta = json.loads(data)["choices"][0].get("delta", {})
                except (KeyError, IndexError, ValueError):
                    continue
                if content := delta.get("content"):
                    yield content

    def complete(self, messages: list[dict], temperature: float = 0.1) -> str:
        return "".join(self.stream(messages, temperature))
