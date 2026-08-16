"""带引用的问答。

防幻觉是这里的头等大事。ERP 实施场景下，一个编造出来的配置步骤可能让客户
的订单卡住——**答错比答"不知道"代价大得多**。所以设了两道闸门：

    第一道  检索层：一条够格的结果都没有 → 直接返回兜底话术，根本不调 LLM
    第二道  prompt：明确要求"材料里没有就说没有"，并禁止用常识补全

第二道是主闸门。第一道只滤掉明显无关的，因为重排分数的绝对值很低
（实测正确答案 0.02、无关内容 0.0001），靠绝对阈值卡不住。
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from copilot.providers.base import Embedder, Reranker
from copilot.providers.llm import ChatLLM
from copilot.retrieve import Citation, search

NO_ANSWER = "知识库暂无此内容。"

SYSTEM_PROMPT = """你是一名旺店通旗舰版 ERP 的实施顾问助手，只依据下面提供的「参考材料」回答问题。

铁律：
1. 只用参考材料里的信息作答，**不得用你自己的常识补全或推测**。
2. 每一句结论后面标注来源编号，如 [1]、[2]。多个来源就写 [1][3]。
3. 如果参考材料回答不了这个问题，**只回复这一句**：知识库暂无此内容。
   不要解释、不要道歉、不要给建议、不要试着换个角度答。
4. 材料里只有部分信息时，答已有的部分，并明确指出哪一部分材料里没有。

写法要求：
- 操作步骤按 1. 2. 3. 分条列出，把界面路径原样保留（如「设置–策略设置–短信策略」）。
- 保留材料中的注意事项和限制条件，那往往是最容易踩坑的地方。
- 直接说事，不要"根据参考材料"之类的开场白。"""

USER_TEMPLATE = """参考材料：

{context}

---

问题：{question}"""


def is_no_answer(text: str) -> bool:
    """判断模型是否给出了「不知道」。

    ⚠️ **凡是展示引用的地方都必须先过这一关。** 一旦答案是「暂无此内容」，
    下面却挂着五条来源，用户会以为答案是有依据的——这比不做防幻觉更糟。
    """
    return text.strip().startswith(NO_ANSWER.rstrip("。"))


@dataclass(slots=True)
class Answer:
    text: str
    citations: list[Citation]

    @property
    def is_no_answer(self) -> bool:
        return is_no_answer(self.text)


async def ask_stream(
    session: AsyncSession,
    question: str,
    embedder: Embedder,
    reranker: Reranker | None,
    llm: ChatLLM,
    *,
    user_id: uuid.UUID | None = None,
) -> tuple[Iterator[str], list[Citation]]:
    """检索并流式作答。返回 (文本流, 引用列表)。

    引用先于文本返回，前端可以立刻开始渲染来源。

    ⚠️ **调用方的义务**：流消费完后，若 `is_no_answer(全文)` 为真，
    必须把这批引用丢掉不展示。否则会出现「知识库暂无此内容」下面挂着
    五条来源的情形——用户会误以为答案有依据。
    """
    result = await search(session, question, embedder, reranker, user_id=user_id)

    # 第一道闸门：一条都没召回，不必浪费一次 LLM 调用
    if result.is_empty:
        return iter([NO_ANSWER]), []

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": USER_TEMPLATE.format(context=result.to_context(), question=question),
        },
    ]
    return llm.stream(messages), result.citations


async def ask(
    session: AsyncSession,
    question: str,
    embedder: Embedder,
    reranker: Reranker | None,
    llm: ChatLLM,
    *,
    user_id: uuid.UUID | None = None,
) -> Answer:
    stream, citations = await ask_stream(
        session, question, embedder, reranker, llm, user_id=user_id
    )
    text = "".join(stream)
    answer = Answer(text=text, citations=citations)
    # 模型说了不知道，就别再挂一堆来源——那会让用户以为答案有依据
    if answer.is_no_answer:
        answer.citations = []
    return answer
