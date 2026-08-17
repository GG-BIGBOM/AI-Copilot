"""带引用的问答。

防幻觉是这里的头等大事。ERP 实施场景下，一个编造出来的配置步骤可能让客户
的订单卡住——**答错比答"不知道"代价大得多**。所以设了两道闸门：

    第一道  检索层：一条够格的结果都没有 → 直接返回兜底话术，根本不调 LLM
    第二道  prompt：明确要求"材料里没有就说没有"，并禁止用常识补全

第二道是主闸门。第一道只滤掉明显无关的，因为重排分数的绝对值很低
（实测正确答案 0.02、无关内容 0.0001），靠绝对阈值卡不住。
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from copilot.providers.base import Embedder, Reranker
from copilot.providers.llm import ChatLLM
from copilot.retrieve import Citation, search

NO_ANSWER = "知识库暂无此内容。"

# 答案里的引用标记 `[1]`、`[2]`。用来区分「拒答」和「答了一部分、并说明另一部分没有」
_CITE_MARK_RE = re.compile(r"\[\d{1,2}\]")

SYSTEM_PROMPT = """你是一名旺店通旗舰版 ERP 的实施顾问助手，只依据下面提供的「参考材料」回答问题。

铁律：
1. 只用参考材料里的信息作答，**不得用你自己的常识补全或推测**。
2. 每一句结论后面标注来源编号，如 [1]、[2]。多个来源就写 [1][3]。
3. **先看材料里有没有能用的内容，再决定怎么答。这个顺序不能反**：
   - 材料里有能回答的内容——**哪怕只回答了问题的一部分**——就把那部分答出来，
     再明确写一句哪一部分材料里没有。**绝不能因为答不全就整个不答。**
     问题问了两件事而材料只写了一件，就答那一件、并说清另一件材料里没有。
     ⚠️ 说「材料里没有」只针对**问题问到的东西**，而且要先通读全部材料再说。
     不要顺手罗列问题没问到的方面、也不要凭印象断定某件事材料没写——
     说错一句「材料未提及 X」和编造一个 X 一样是错的。
   - 只有当材料**完全没有**与问题相关的内容时，才**只回复这一句**：知识库暂无此内容。
     不要解释、不要道歉、不要给建议、不要试着换个角度答。
4. 材料里写了具体的数字、上限、界面路径、字段名时，**照原文答**，不要概括成
   「有一定限制」「在设置里」这类说法——问的人要的就是那个具体值。
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

    判定分两种情形，缺一不可：

    1. **以那句话开头** —— prompt 要求的标准形态。
    2. **提到了那句话，且全文没有任何 `[n]` 引用标记。**
       M7 的 Agent 会先解释「我查到的是 X，不是你问的」再补一句
       「知识库暂无此内容」——只认开头的话，这种答案会被判成"有答案"，
       于是页面上出现「暂无此内容」下面挂着五条来源。**这是 M7 的评测
       撞出来的真 bug，不是理论风险。**

    为什么第 2 条要加「没有引用标记」这个条件：`partial` 类的答案长这样——
    「模板在【设置–策略设置–短信策略】里建 [1]。短信费用怎么收，知识库暂无此内容。」
    那是**答出来了**的，引用必须照常显示。带 `[n]` 就说明有据可依，
    不能因为末尾提了一句「某部分没有」就把整条来源清单丢掉。
    """
    body = text.strip()
    needle = NO_ANSWER.rstrip("。")
    if body.startswith(needle):
        return True
    return needle in body and not _CITE_MARK_RE.search(body)


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
    # 送进模型的上下文原文。调用方用它记 token 用量——
    # token 的大头在这里，不在答案里（5 块材料约 2500 字）
    context_text: str = ""


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
        stream=llm.stream(messages),
        citations=result.citations,
        images=context.images,
        context_text=context.text,
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
