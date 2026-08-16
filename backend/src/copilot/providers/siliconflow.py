"""SiliconFlow 上的 bge-m3（向量化）与 bge-reranker-v2-m3（精排）。

两个模型当前都在免费额度内，代价是限速比付费版严。所以这里的重点不是
"能调通"，而是**7000+ 个块能不能稳稳跑完**：批量分组、限速、429 退避重试，
一个都不能少。中途失败会浪费掉已经花掉的调用额度。
"""

from __future__ import annotations

import random
import time

import httpx

from copilot.config import get_settings
from copilot.providers.base import RerankResult


class ProviderError(RuntimeError):
    pass


class SiliconFlowClient:
    """带限速与重试的 HTTP 底座，Embedder 和 Reranker 共用。"""

    def __init__(self, api_key: str | None = None, base_url: str | None = None) -> None:
        s = get_settings()
        key = api_key or s.siliconflow_api_key
        if not key:
            raise ProviderError(
                "缺少 SILICONFLOW_API_KEY。复制 backend/.env.example 成 .env 并填入，"
                "免费注册：https://cloud.siliconflow.cn"
            )
        self._min_interval = 1.0 / s.embedding_rate_limit_per_sec
        self._max_retries = s.embedding_max_retries
        self._last_at = 0.0
        self._client = httpx.Client(
            base_url=base_url or s.siliconflow_base_url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            timeout=httpx.Timeout(60.0, connect=15.0),
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> SiliconFlowClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def post(self, path: str, payload: dict) -> dict:
        last: Exception | None = None
        for attempt in range(self._max_retries):
            # 限速
            wait = self._min_interval - (time.monotonic() - self._last_at)
            if wait > 0:
                time.sleep(wait)
            self._last_at = time.monotonic()

            try:
                resp = self._client.post(path, json=payload)
            except httpx.HTTPError as e:
                last = e
            else:
                if resp.status_code == 200:
                    return resp.json()
                # 401/403 是密钥问题，重试没意义，立刻报错省得白等
                if resp.status_code in (401, 403):
                    raise ProviderError(
                        f"密钥无效或无权限（HTTP {resp.status_code}）：{resp.text[:200]}"
                    )
                if resp.status_code == 429 or resp.status_code >= 500:
                    last = ProviderError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                else:
                    raise ProviderError(f"HTTP {resp.status_code}: {resp.text[:300]}")

            # 指数退避 + 抖动。429 时退避比平时更久，别火上浇油
            time.sleep(min(2**attempt + random.uniform(0, 1.5), 30))
        raise ProviderError(f"重试 {self._max_retries} 次仍失败：{path}") from last


class SiliconFlowEmbedder:
    """bge-m3 向量化。"""

    def __init__(self, client: SiliconFlowClient | None = None, model: str | None = None) -> None:
        s = get_settings()
        self._client = client or SiliconFlowClient()
        self._model = model or s.embedding_model
        self._dim = s.embedding_dim
        self._batch_size = s.embedding_batch_size

    @property
    def dim(self) -> int:
        return self._dim

    def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        data = self._client.post("/embeddings", {"model": self._model, "input": texts})
        items = data.get("data") or []
        if len(items) != len(texts):
            raise ProviderError(f"返回数量对不上：请求 {len(texts)} 条，收到 {len(items)} 条")
        # 接口不保证按序返回，按 index 排一遍——顺序错了会导致
        # 每个块配上别人的向量，检索结果全是错的，且极难发现
        items.sort(key=lambda x: x.get("index", 0))
        vectors = [x["embedding"] for x in items]
        for v in vectors:
            if len(v) != self._dim:
                raise ProviderError(
                    f"向量维度是 {len(v)}，配置写的是 {self._dim}。"
                    f"换模型要同步改 config.embedding_dim 和数据库迁移"
                )
        return vectors

    def embed_documents(self, texts: list[str], progress=None) -> list[list[float]]:
        """批量向量化。progress 是可选回调 (已完成数, 总数)。"""
        out: list[list[float]] = []
        total = len(texts)
        for i in range(0, total, self._batch_size):
            out.extend(self._embed_batch(texts[i : i + self._batch_size]))
            if progress:
                progress(len(out), total)
        return out

    def embed_query(self, text: str) -> list[float]:
        return self._embed_batch([text])[0]


class SiliconFlowReranker:
    """bge-reranker-v2-m3 精排。

    向量召回负责"广"，重排负责"准"。召回 top-20 再排出 top-5，
    比直接取向量 top-5 准得多——这是 RAG 里性价比最高的一步优化。
    """

    def __init__(self, client: SiliconFlowClient | None = None, model: str | None = None) -> None:
        s = get_settings()
        self._client = client or SiliconFlowClient()
        self._model = model or s.rerank_model

    def rerank(self, query: str, documents: list[str], top_k: int) -> list[RerankResult]:
        if not documents:
            return []
        data = self._client.post(
            "/rerank",
            {
                "model": self._model,
                "query": query,
                "documents": documents,
                "top_n": min(top_k, len(documents)),
            },
        )
        results = data.get("results") or []
        return [
            RerankResult(index=r["index"], score=float(r.get("relevance_score", 0.0)))
            for r in results
        ]
