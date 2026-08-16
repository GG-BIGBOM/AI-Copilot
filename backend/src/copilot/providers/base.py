"""Provider 接口。

定义成 Protocol 而不是基类，是为了换供应商时改配置而非改代码：
SiliconFlow 免费额度取消了就换通义/百炼，实现一个新类即可，
调用方（retrieve.py / pipeline.py）一行都不用动。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """把文本转成向量。"""

    @property
    def dim(self) -> int:
        """向量维度。必须与 chunks.embedding 的列定义一致。"""
        ...

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """批量向量化文档块（入库时用）。"""
        ...

    def embed_query(self, text: str) -> list[float]:
        """向量化查询（检索时用）。

        单列一个方法是因为有些模型对查询和文档用不同的前缀，
        bge-m3 不需要，但接口先留出来，换模型时不用改调用方。
        """
        ...


@dataclass(slots=True)
class RerankResult:
    index: int  # 在输入列表中的下标
    score: float


@runtime_checkable
class Reranker(Protocol):
    """对召回结果精排。"""

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[RerankResult]:
        ...
