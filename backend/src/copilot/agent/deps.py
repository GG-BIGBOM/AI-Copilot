"""Agent 的依赖注入包。

Pydantic AI 把这个对象透传给每个工具（`ctx.deps`）。放进来的都是**工具自己
拿不到、也绝不能让模型指定**的东西：

    user_id   ⚠️ 隔离的命门。它只能从 cookie 里的登录态来。
              一旦让它变成工具的入参，模型就可以（被诱导着）填别人的 id，
              而那意味着一句 prompt injection 就能读到别人的私有文档。
    session   数据库会话
    embedder / reranker  检索用的 provider

`citations` 和 `images` 是**出参**：工具在跑的过程中往里累积，
一轮结束后由路由层一次性发给前端。不这么做的话，每次工具调用都得往流里插一次
引用，而模型完全可能在最后说「知识库暂无此内容」——那时页面上就是一句
「不知道」底下挂着五条来源（M1 踩过的坑，见 routes/chat.py 文件头第 1 条）。
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from copilot.agent.checklist import Checklist, Requirement
from copilot.providers.base import Embedder, Reranker


@dataclass
class AgentDeps:
    session: AsyncSession
    user_id: uuid.UUID
    conversation_id: uuid.UUID
    embedder: Embedder
    reranker: Reranker | None = None

    # 多轮收集的状态。进来时从 conversations 表读，出去时写回
    profile: Requirement = field(default_factory=Requirement)
    checklist: Checklist | None = None

    # 本轮累积的引用与配图，路由层在正文流完之后统一发
    citations: list[dict] = field(default_factory=list)
    images: list[dict] = field(default_factory=list)
    # 本轮检索到的材料原文。两个用处，都不是可选的：
    #   1. 记 token 账要算它——**上下文才是大头**，只算问题和答案会漏掉八成
    #   2. 评测的判分器要拿它当「参考材料」，否则没法判答案有没有依据
    retrieved: list[str] = field(default_factory=list)
    # 本轮生成的下载地址（xlsx）
    download_url: str | None = None

    @property
    def context_text(self) -> str:
        """本轮所有检索材料拼起来。给记账和判分用。"""
        return "\n\n".join(self.retrieved)

    def merge_citations(self, new: list[dict]) -> list[dict]:
        """把一次检索的引用并进本轮总表，返回**重新编号后**的那批。

        为什么要重编号：Agent 一轮里可能检索好几次，每次的引用都从 [1] 开始。
        直接拼起来的话会出现两个 [1]，答案里的 [1] 就指向不明——
        用户点开溯源看到的是另一篇文档。这里按「本轮全局」重新编号，
        同一篇（title+heading 相同）复用同一个号，不重复占位。
        """
        out: list[dict] = []
        for c in new:
            key = (c.get("title"), c.get("heading"))
            hit = next(
                (e for e in self.citations if (e.get("title"), e.get("heading")) == key), None
            )
            if hit is None:
                hit = {**c, "n": len(self.citations) + 1}
                self.citations.append(hit)
            out.append(hit)
        return out
