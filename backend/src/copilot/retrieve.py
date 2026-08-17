"""检索：向量召回 → 重排 → 拼成带编号引用的上下文。

⚠️ **本文件是数据隔离的唯一收敛点。**

`owner_id` 的过滤条件只允许出现在 `_visibility_filter()` 一处。任何调用方
都不得自己拼查询、不得绕过 `search()`。这不是洁癖——过滤条件散落在多处时，
只要有一个地方忘了加，就是把 A 用户的私有文档泄漏给 B，而且不会有任何报错。

检索为什么分两步：
    向量召回 top-20  负责「广」——把可能相关的都捞上来
    重排取 top-5     负责「准」——实测重排能把正确答案和无关内容拉开 200 倍分差，
                                 而向量相似度只拉开 1.3 倍
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from functools import partial

import anyio.to_thread
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from copilot.config import get_settings
from copilot.db.models import Chunk
from copilot.providers.base import Embedder, Reranker

# 与 ingest/chunker.py 的标记格式一致：正文里存 `[图:a3f9]`
_IMG_MARK_RE = re.compile(r"\[图:([0-9a-f]{4})\]")


@dataclass(slots=True)
class Citation:
    """一条引用来源，编号从 1 开始，对应答案里的 [1][2]。"""

    n: int
    title: str
    heading: str | None
    source_url: str | None
    score: float

    @property
    def label(self) -> str:
        return f"{self.title} · {self.heading}" if self.heading else self.title

    def to_dict(self) -> dict:
        return {
            "n": self.n,
            "title": self.title,
            "heading": self.heading,
            "url": self.source_url,
            "score": round(self.score, 4),
        }


@dataclass(slots=True)
class RetrievedChunk:
    content: str
    citation: Citation
    images: list[dict] = field(default_factory=list)


@dataclass(slots=True)
class ContextBundle:
    """喂给 LLM 的上下文，以及答案里 `[图N]` 该指向哪张图。

    两者必须一起产出——分成两个方法算的话，迟早会出现编号和图片对不上的情形，
    而那种错误的表现是「答案配了错误的截图」，没有任何报错。
    """

    text: str
    images: list[dict] = field(default_factory=list)  # [{"n":1,"url":...}, ...]


@dataclass(slots=True)
class RetrievalResult:
    chunks: list[RetrievedChunk]

    @property
    def is_empty(self) -> bool:
        """没有任何够格的结果——调用方据此走「知识库暂无此内容」分支。"""
        return not self.chunks

    @property
    def citations(self) -> list[Citation]:
        return [c.citation for c in self.chunks]

    def build_context(self) -> ContextBundle:
        """拼上下文，同时把配图标记重新编号。

        块正文里存的是 `[图:a3f9]`（id 随图走，与顺序无关）。这里按在上下文里
        第一次出现的先后，统一换成 `[图1]`、`[图2]`……模型只看得到这些编号，
        因此**只能引用真实存在的图**——它编不出一个 URL 来。
        """
        numbering: dict[str, int] = {}
        images: list[dict] = []
        parts: list[str] = []

        for rc in self.chunks:
            by_id = {img["id"]: img for img in rc.images if img.get("id")}

            def renumber(m: re.Match[str], by_id=by_id) -> str:
                ident = m.group(1)
                img = by_id.get(ident)
                if img is None:
                    return ""  # 块里没这张图的地址，标记留着只会误导模型
                if ident not in numbering:
                    numbering[ident] = len(numbering) + 1
                    images.append({"n": numbering[ident], "url": img["url"]})
                return f"[图{numbering[ident]}]"

            body = _IMG_MARK_RE.sub(renumber, rc.content)
            parts.append(f"[{rc.citation.n}] 来源：{rc.citation.label}\n{body}")

        return ContextBundle(text="\n\n".join(parts), images=images)

    def to_context(self) -> str:
        """只要文本的旧入口。"""
        return self.build_context().text


def _visibility_filter(user_id: uuid.UUID | None) -> ColumnElement[bool]:
    """可见性过滤——**全项目唯一一处**。

        owner_id IS NULL      公共库（语雀抓的），所有人可见
        owner_id = user_id    该用户自己上传的

    未登录（user_id 为 None）时只能看公共库。
    """
    public_only = Chunk.owner_id.is_(None)
    if user_id is None:
        return public_only
    return public_only | (Chunk.owner_id == user_id)


async def search(
    session: AsyncSession,
    query: str,
    embedder: Embedder,
    reranker: Reranker | None = None,
    *,
    user_id: uuid.UUID | None = None,
    top_k: int | None = None,
    rerank_k: int | None = None,
    score_threshold: float | None = None,
) -> RetrievalResult:
    """检索知识库。

    Args:
        user_id: 当前用户。None 表示只搜公共库
        top_k: 向量召回数量
        rerank_k: 重排后保留数量
        score_threshold: 低于此分丢弃。**只是滤掉明显垃圾的下限**，
            真正的防幻觉闸门在 prompt 里
    """
    s = get_settings()
    top_k = top_k or s.retrieve_top_k
    rerank_k = rerank_k or s.rerank_top_k
    threshold = s.rerank_score_threshold if score_threshold is None else score_threshold

    query = query.strip()
    if not query:
        return RetrievalResult(chunks=[])

    # Embedder / Reranker 是同步的（httpx.Client + time.sleep 限速），一次调用
    # 几百毫秒到数秒。直接在协程里调会**卡住整个事件循环**——单 worker 的
    # uvicorn 下，别人正在进行的 SSE 流会一起停住。丢线程池里跑。
    query_vec = await anyio.to_thread.run_sync(embedder.embed_query, query)

    # 向量召回。cosine_distance 越小越近
    stmt = (
        select(Chunk)
        .where(_visibility_filter(user_id))
        .order_by(Chunk.embedding.cosine_distance(query_vec))
        .limit(top_k)
    )
    candidates = list((await session.execute(stmt)).scalars())
    if not candidates:
        return RetrievalResult(chunks=[])

    # 重排
    if reranker is not None:
        ranked = await anyio.to_thread.run_sync(
            partial(reranker.rerank, query, [c.content for c in candidates], top_k=rerank_k)
        )
        picked = [(candidates[r.index], r.score) for r in ranked if r.score >= threshold]
    else:
        # 没有重排器时退回向量顺序，分数用 1/(1+序号) 占位
        picked = [(c, 1.0 / (i + 1)) for i, c in enumerate(candidates[:rerank_k])]

    return RetrievalResult(
        chunks=[
            RetrievedChunk(
                content=chunk.content,
                images=list(chunk.images or []),
                citation=Citation(
                    n=i,
                    title=chunk.title,
                    heading=chunk.heading,
                    source_url=chunk.source_url,
                    score=score,
                ),
            )
            for i, (chunk, score) in enumerate(picked, start=1)
        ]
    )
