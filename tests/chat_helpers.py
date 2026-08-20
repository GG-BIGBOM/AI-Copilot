"""聊天测试共用的假 provider 和小工具。

⚠️ **不要把这些放进 conftest 再 import 回来。** `import conftest` 会让它被
加载两遍——一次作为 pytest 插件、一次作为普通模块——夹具于是注册两次，
报出来的是 `assert not self._finalizers`，和真正的原因隔着十万八千里。
夹具留在 conftest（靠名字自动收集），纯函数和类放这里。
"""

from __future__ import annotations

import json
from collections.abc import Iterator

from copilot.providers.base import RerankResult

PASSWORD = "test-password-2026"
DIM = 1024


# ---------- 假 provider ----------


class FakeEmbedder:
    """确定性向量。同样的文本永远得到同样的向量，所以拿原文去搜，
    余弦距离是 0，稳稳压过库里那 5000 个真实 bge-m3 向量。"""

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


class TopOneReranker:
    """只保留向量召回的第一名。测试里就是那条精确命中的 chunk。"""

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[RerankResult]:
        return [RerankResult(index=0, score=0.9)] if documents else []


class PartsFromStream:
    """把只会 `stream()` 的假模型补上 `stream_parts()`。

    真的 `ChatLLM` 反过来：`stream_parts()` 是本体，`stream()` 是它的正文过滤。
    假模型里没有推理草稿可吐，所以这里一律标成 `content`。
    想测草稿那一路的用例，自己实现 `stream_parts` 覆盖掉这个默认实现。
    """

    def stream_parts(
        self, messages: list[dict], temperature: float = 0.1
    ) -> Iterator[tuple[str, str]]:
        return (("content", piece) for piece in self.stream(messages, temperature))


class FakeLLM(PartsFromStream):
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[list[dict]] = []

    def stream(self, messages: list[dict], temperature: float = 0.1) -> Iterator[str]:
        self.calls.append(messages)
        # 一个字一个字地吐，逼出多个 text-delta 片段
        return iter(list(self.reply))

    def close(self) -> None:
        pass


# ---------- 夹具 ----------


def parts(body: str) -> list[dict]:
    """把 SSE 正文解成协议片段列表。`[DONE]` 不是 JSON，单独处理。"""
    out = []
    for line in body.split("\n\n"):
        line = line.strip()
        if line.startswith("data: ") and line != "data: [DONE]":
            out.append(json.loads(line[6:]))
    return out


async def ask(api_client, question: str, conv_id: str | None = None):
    payload = {"messages": [{"role": "user", "parts": [{"type": "text", "text": question}]}]}
    if conv_id:
        payload["id"] = conv_id
    return await api_client.post("/api/chat", json=payload)
