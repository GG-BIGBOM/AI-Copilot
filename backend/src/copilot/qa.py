"""带引用的问答。

防幻觉是这里的头等大事。ERP 实施场景下，一个编造出来的配置步骤可能让客户
的订单卡住——**答错比答"不知道"代价大得多**。所以设了两道闸门：

    第一道  检索层：一条够格的结果都没有 → 直接返回兜底话术，根本不调 LLM
    第二道  prompt：明确要求"材料里没有就说没有"，并禁止用常识补全

第二道是主闸门。第一道只滤掉明显无关的，因为重排分数的绝对值很低
（实测正确答案 0.02、无关内容 0.0001），靠绝对阈值卡不住。
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession
from starlette.concurrency import run_in_threadpool

from copilot.providers.base import Embedder, Reranker
from copilot.providers.llm import ChatLLM
from copilot.retrieve import Citation, search

logger = logging.getLogger(__name__)

NO_ANSWER = "知识库暂无此内容。"

# 答案里的引用标记 `[1]`、`[2]`。用来区分「拒答」和「答了一部分、并说明另一部分没有」
_CITE_MARK_RE = re.compile(r"\[\d{1,2}\]")

# ─────────────────────────────────────────────────────────
# 招呼语 / 寒暄
#
# 「你好」检索不到任何东西，会撞上下面那道「一条都没召回」的闸门，
# 于是用户得到一句「知识库暂无此内容」——一个连招呼都不会回的助手，
# 谁也不会信任它后面的回答。
#
# ⚠️ **只认完全匹配，而且不调模型。** 用模型自由发挥就等于在防幻觉的墙上
# 开一个洞：它会开始"友好地"补全 ERP 知识。这里回的每一句都是写死的常量。
# 「你好，我想问下电子面单」不在这张表里 —— 它该走检索，也确实会走检索。
# ─────────────────────────────────────────────────────────

_GREETING = {
    "你好", "您好", "你好呀", "你好啊", "哈喽", "哈啰", "嗨", "在吗", "在么", "在不在",
    "早上好", "中午好", "下午好", "晚上好", "早安", "hi", "hello", "hey", "yo",
}
_THANKS = {
    "谢谢", "谢谢你", "谢了", "多谢", "感谢", "好的", "收到", "明白了", "懂了", "知道了",
    "thanks", "thank you", "thx",
}
_BYE = {"再见", "拜拜", "回见", "bye", "goodbye", "88"}
_CAPABILITY = {
    "你是谁", "你叫什么", "你是什么", "你能做什么", "你能干什么", "你能干嘛", "你会什么",
    "你会做什么", "有什么功能", "怎么用", "如何使用", "使用说明", "帮助", "help",
    "介绍一下你自己", "自我介绍",
}

_GREETING_REPLY = """你好，我是旺店通旗舰版 ERP 的知识库助手。

系统操作、参数配置、异常排查这类问题都可以问我，答案会标明出处。比如：

- 京东电子面单模板怎么设置？
- 退货入库的操作流程是什么？
- 对账单生成异常怎么排查？"""

_CAPABILITY_REPLY = """我是旺店通旗舰版 ERP 的知识库助手，能做四件事：

1. **回答操作与配置问题** —— 只依据知识库作答，每句结论都标出处；
   材料里没有的会直说没有，不猜。
2. **带上操作截图** —— 原文档里有配图的步骤，会把截图一起给你。
3. **读你自己的文档** —— 在「知识库」页上传操作手册、FAQ、截图，
   之后提问就能引用到它，而且只有你自己能检索到。
4. **生成实施配置方案** —— 说一句「帮我出一份实施方案」，我会多轮问清楚需求，
   最后给一份可下载的 Excel。

我不知道的事情会直接说不知道 —— ERP 里一个编出来的配置步骤，可能让客户的订单卡住。"""

_THANKS_REPLY = "不客气。还有别的问题随时问。"
_BYE_REPLY = "再见，需要的时候再来找我。"

# 去掉首尾空白和句末的标点再比对。「你好！」「你好~」都算招呼
_TRAILING_PUNCT = "?？.。!！~～,，;；、 \t\n"


def small_talk_reply(question: str) -> str | None:
    """招呼 / 道谢 / 告别 / 问能力 —— 命中就直接给一句固定回复，不检索也不调模型。

    返回 None 表示这是一个正经问题，照常走检索。
    """
    q = question.strip().strip(_TRAILING_PUNCT).lower()
    if not q:
        return None
    if q in _GREETING:
        return _GREETING_REPLY
    if q in _CAPABILITY:
        return _CAPABILITY_REPLY
    if q in _THANKS:
        return _THANKS_REPLY
    if q in _BYE:
        return _BYE_REPLY
    return None

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
6. 前面的对话记录**只用来理解这一轮在问什么**（比如「那不良品呢」指的是什么），
   **不是可以引用的材料**。回答的依据只能是本轮的参考材料——上一轮答过的话，
   这一轮材料里没有就还是没有。

写法要求：
- 操作步骤按 1. 2. 3. 分条列出，把界面路径原样保留（如「设置–策略设置–短信策略」）。
- 保留材料中的注意事项和限制条件，那往往是最容易踩坑的地方。
- 直接说事，不要"根据参考材料"之类的开场白。"""

USER_TEMPLATE = """参考材料：

{context}

---

问题：{question}"""

# ─────────────────────────────────────────────────────────
# 多轮改写
#
# 直路问答本来是**单轮**的：检索用最后一句话，送进模型的也只有最后一句话。
# 于是「退货入库怎么操作」之后追一句「那不良品呢？」，系统是拿这五个字去
# 检索——必然打偏，然后一本正经地答错，或者说知识库里没有。
#
# 标准解法：先把追问补全成一个独立问题，**只拿它去检索**；给模型的问题
# 仍然是用户原话（否则答非所问），历史另外作为对话轮次带上。
# ─────────────────────────────────────────────────────────

REWRITE_PROMPT = """把用户最后这句话改写成一个不依赖上文也能看懂的独立问题。

规则：
- 只补全指代（「它」「那个」「那呢」到底指什么），不要增加原问题没有的限定条件
- 本来就完整的问题，原样返回
- 只输出改写后的那一个问题，不要解释、不要引号、不要换行"""

# 改写结果的长度闸门。模型偶尔会不听话，返回一段解释而不是一个问题；
# 与其拿这种东西去检索，不如退回用户原话
_REWRITE_MAX_LEN = 120

# 带进上下文的历史轮数（user + assistant 各算一条）
HISTORY_TURNS = 6
# 单条历史的截断长度。助手的回答动辄上千字，整段塞进去会把参考材料挤出窗口
_HISTORY_CHAR_LIMIT = 600


def rewrite_query(llm: ChatLLM, question: str, history: list[tuple[str, str]]) -> str:
    """把追问补全成独立问题。任何异常都退回原问题——改写失败不该让提问失败。"""
    if not history:
        return question

    convo = "\n".join(
        f"{'用户' if role == 'user' else '助手'}：{content[:200]}"
        for role, content in history[-4:]
    )
    try:
        rewritten = llm.complete(
            [
                {"role": "system", "content": REWRITE_PROMPT},
                {"role": "user", "content": f"对话记录：\n{convo}\n\n最后这句话：{question}"},
            ],
            temperature=0.0,
        ).strip()
    except Exception:  # noqa: BLE001 - 改写是锦上添花，挂了就用原问题
        logger.warning("多轮改写失败，退回原问题：%r", question[:60], exc_info=True)
        return question

    if not rewritten or len(rewritten) > _REWRITE_MAX_LEN or "\n" in rewritten:
        return question
    return rewritten


def _history_messages(history: list[tuple[str, str]] | None) -> list[dict]:
    """历史轮次。只取最近几轮，且每条都截断。"""
    if not history:
        return []
    return [
        {"role": role, "content": content[:_HISTORY_CHAR_LIMIT]}
        for role, content in history[-HISTORY_TURNS:]
        if role in ("user", "assistant") and content.strip()
    ]


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
    history: list[tuple[str, str]] | None = None,
) -> StreamedAnswer:
    """检索并流式作答。

    Args:
        history: 这条会话之前的 (role, content)，**不含本轮提问**，从旧到新。
            有历史时会先把追问改写成独立问题再检索。

    ⚠️ **调用方的义务**：流消费完后，若 `is_no_answer(全文)` 为真，
    必须把这批引用丢掉不展示。否则会出现「知识库暂无此内容」下面挂着
    五条来源的情形——用户会误以为答案有依据。
    """
    # 招呼语在检索**之前**拦掉。放到后面就晚了：它一条都召不回，
    # 会被下面那道闸门变成一句「知识库暂无此内容」
    if (canned := small_talk_reply(question)) is not None:
        return StreamedAnswer(stream=iter([canned]), citations=[])

    # 只有检索词用改写后的版本；给模型看的问题仍是用户原话
    search_query = (
        await run_in_threadpool(rewrite_query, llm, question, history) if history else question
    )

    result = await search(session, search_query, embedder, reranker, user_id=user_id)

    # 第一道闸门：一条都没召回，不必浪费一次 LLM 调用
    if result.is_empty:
        return StreamedAnswer(stream=iter([NO_ANSWER]), citations=[])

    context = result.build_context()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *_history_messages(history),
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
    history: list[tuple[str, str]] | None = None,
) -> Answer:
    streamed = await ask_stream(
        session, question, embedder, reranker, llm, user_id=user_id, history=history
    )
    text = "".join(streamed.stream)
    answer = Answer(text=text, citations=streamed.citations, images=streamed.images)
    # 模型说了不知道，就别再挂一堆来源——那会让用户以为答案有依据
    if answer.is_no_answer:
        answer.citations = []
        answer.images = []
    return answer
