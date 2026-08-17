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
from dataclasses import dataclass, field

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
5. **材料里的 [图1]、[图2] 是原文档的操作截图，要带上。** 你引用哪段材料，
   那段材料里出现的图号就照抄到对应步骤那一行的末尾，
   如「1. 进入【设置】-【打印设置】[图1]」。ERP 的操作步骤，一张截图顶三句话。
   唯一限制：**只能用材料里真实出现过的图号**，材料里没有 [图9] 就绝不能写 [图9]。

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
    images: list[dict] = field(default_factory=list)

    @property
    def is_no_answer(self) -> bool:
        return is_no_answer(self.text)


@dataclass(slots=True)
class StreamedAnswer:
    """`ask_stream` 的返回。

    `images` 是本轮上下文里 `[图N]` 的编号 → 地址对照表。它可以在正文之前
    就交给前端——与引用不同，图片本身不构成"答案有依据"的暗示：模型说
    「知识库暂无此内容」时，正文里根本不会出现任何 [图N]，也就什么都不会渲染。
    """

    stream: Iterator[str]
    citations: list[Citation]
    images: list[dict] = field(default_factory=list)


async def ask_stream(
    session: AsyncSession,
    question: str,
    embedder: Embedder,
    reranker: Reranker | None,
    llm: ChatLLM,
    *,
    user_id: uuid.UUID | None = None,
) -> StreamedAnswer:
    """检索并流式作答。

    ⚠️ **调用方的义务**：流消费完后，若 `is_no_answer(全文)` 为真，
    必须把这批引用丢掉不展示。否则会出现「知识库暂无此内容」下面挂着
    五条来源的情形——用户会误以为答案有依据。
    """
    result = await search(session, question, embedder, reranker, user_id=user_id)

    # 第一道闸门：一条都没召回，不必浪费一次 LLM 调用
    if result.is_empty:
        return StreamedAnswer(stream=iter([NO_ANSWER]), citations=[])

    context = result.build_context()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": USER_TEMPLATE.format(context=context.text, question=question),
        },
    ]
    return StreamedAnswer(
        stream=llm.stream(messages), citations=result.citations, images=context.images
    )


async def ask(
    session: AsyncSession,
    question: str,
    embedder: Embedder,
    reranker: Reranker | None,
    llm: ChatLLM,
    *,
    user_id: uuid.UUID | None = None,
) -> Answer:
    streamed = await ask_stream(session, question, embedder, reranker, llm, user_id=user_id)
    text = "".join(streamed.stream)
    answer = Answer(text=text, citations=streamed.citations, images=streamed.images)
    # 模型说了不知道，就别再挂一堆来源——那会让用户以为答案有依据
    if answer.is_no_answer:
        answer.citations = []
        answer.images = []
    return answer
