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

    def stream_parts(
        self, messages: list[dict], temperature: float = 0.1
    ) -> Iterator[tuple[str, str]]:
        """流式生成，逐段吐出 `(kind, text)`，kind 是 `reasoning` 或 `content`。

        temperature 压得很低——知识库问答要的是照着材料说，不是创作。
        除非这个模型自己锁死了温度（见 `forced_temperature`）。

        ⭐ **为什么要把 `reasoning_content` 也吐出来。**
        详解档走的 kimi-k2.6 是推理模型。实测（8 段材料的真实上下文）：

            第一个 reasoning 字   0.8 ~ 1.5 秒
            第一个正文字          8 ~ 60 秒

        只取 content 的话，那中间几十秒**前端一个字都没有**——用户看到的就是
        「选了详解，它不回答」。而模型其实一直在说话，只是说的是草稿。
        把草稿也送出去，等待就从"死机"变成"看得见它在想"。

        试过的另一条路：`{"thinking": {"type": "disabled"}}` 是 Moonshot 认的
        参数（配 temperature=0.6），确实能把推理关掉。但关掉之后详解档就只是
        「换了个模型的简答档」，而这一档存在的理由恰恰是想得久一点。
        `reasoning_effort` 三档实测都没有量级差别，不值得引入一个新旋钮。
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
                # 顺序要紧：同一个 delta 里两样都有时，草稿先于正文
                if reasoning := delta.get("reasoning_content"):
                    yield "reasoning", reasoning
                if content := delta.get("content"):
                    yield "content", content

    def stream(self, messages: list[dict], temperature: float = 0.1) -> Iterator[str]:
        """只要正文。给不关心推理过程的调用方用（改写问题、评测、Agent）。"""
        for kind, text in self.stream_parts(messages, temperature):
            if kind == "content":
                yield text

    def complete(self, messages: list[dict], temperature: float = 0.1) -> str:
        return "".join(self.stream(messages, temperature))
