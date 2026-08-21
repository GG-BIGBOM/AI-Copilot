"""带引用的问答。

防幻觉是这里的头等大事。ERP 实施场景下，一个编造出来的配置步骤可能让客户
的订单卡住——**答错比答"不知道"代价大得多**。

⭐ **M12 把这条红线挪了一次位置，值得说清楚挪到了哪里。**

M1–M11 的红线是「不许用自己的知识」（铁律 1 原文：不得用你自己的常识补全）。
它挡住了幻觉，也挡住了一类正当问题——2026-08-20 线上实测：用户追问
「品牌方又是什么」，模型答了一段正确的行业概念解释，被硬防线整段换成
「知识库暂无此内容」；而事后查证，知识库里**确实没有**这个概念的定义
（最高分 0.35 那条讲的是一盘货库存），**怎么修检索都救不回来**。
一个连行业术语都要拒答的助手，用起来像坏的。

所以红线从「知识的来源」挪到了**「错了会不会伤到人」**：

    可以用自己的知识答     行业术语、概念解释、通用做法     错了是理解偏差
    绝不能凭记忆写         界面路径、菜单层级、字段名、
                          参数取值、数量上限               错了客户的订单卡住

`ALLOW_GENERAL_KNOWLEDGE=false` 一行退回 M11 的行为，两版 prompt 都留在
这个文件里（`_RULE1_STRICT` / `_RULE1_OPEN`），**其余铁律一字不差**。

三道闸门现在是这样：

    第一道  检索层：一条都没召回 → 放开时**让路**（那正是最需要问模型的时候），
            关闭时直接返回兜底话术、不调 LLM
    第二道  prompt：材料里有的以材料为准；没有的分三种情形处理（铁律 3）
    第三道  agent/guard.py：一个工具都没调却写出**操作步骤**的，一律拦下

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

from copilot.config import get_settings
from copilot.providers.base import Embedder, Reranker
from copilot.providers.llm import ChatLLM
from copilot.retrieve import Citation, has_private_chunks, search

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
    "你好", "您好", "你好呀", "你好啊", "哈喽", "哈啰", "嗨", "在", "在吗", "在么", "在不在",
    "早上好", "中午好", "下午好", "晚上好", "早安", "hi", "hello", "hey", "yo",
}
_THANKS = {
    "谢谢", "谢谢你", "谢了", "多谢", "感谢", "好的", "收到", "明白了", "懂了", "知道了",
    "thanks", "thank you", "thx",
}
_BYE = {"再见", "拜拜", "回见", "bye", "goodbye", "88"}
_UNCLEAR = {"?", "？"}
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

1. **回答操作与配置问题** —— 优先依据知识库作答，每句有依据的结论都标出处。
2. **带上操作截图** —— 原文档里有配图的步骤，会把截图一起给你。
3. **读你自己的文档** —— 在「知识库」页上传操作手册、FAQ、截图，
   之后提问就能引用到它，而且只有你自己能检索到。
4. **生成实施配置方案** —— 说一句「帮我出一份实施方案」，我会多轮问清楚需求，
   最后给一份可下载的 Excel。

知识库里没有的行业术语、通用概念，我会按通用理解说一说，**并且告诉你这一段
没有出处**。但**具体的界面路径、字段名、参数上限我绝不会凭记忆编** ——
ERP 里一个编出来的配置步骤，可能让客户的订单卡住。那种问题查不到我就说查不到。"""

_THANKS_REPLY = "不客气。还有别的问题随时问。"
_BYE_REPLY = "再见，需要的时候再来找我。"
_UNCLEAR_REPLY = "我在。请直接告诉我你想了解的旺店通功能或遇到的问题。"

# 去掉首尾空白和句末的标点再比对。「你好！」「你好~」都算招呼
_TRAILING_PUNCT = "?？.。!！~～,，;；、 \t\n"


# 顺序有意义：能力词排在道谢前面，「怎么用」这类先按问能力认
_SMALL_TALK_TABLE: tuple[tuple[str, set[str], str], ...] = (
    ("greeting", _GREETING, _GREETING_REPLY),
    ("capability", _CAPABILITY, _CAPABILITY_REPLY),
    ("thanks", _THANKS, _THANKS_REPLY),
    ("bye", _BYE, _BYE_REPLY),
    ("unclear", _UNCLEAR, _UNCLEAR_REPLY),
)


def small_talk_kind(question: str) -> str | None:
    """这句话属于哪一类寒暄。不是寒暄就返回 None。

    单独拆出来是给评测用的（`eval/routing.py` 要量路由准确率）——
    让评测量**真代码**，而不是它自己抄一份判定逻辑。抄一份的话，
    改了这边忘了改那边，评测会一直报告一个早就不存在的系统。
    """
    raw = question.strip().lower()
    if raw in _UNCLEAR:
        return "unclear"
    q = raw.strip(_TRAILING_PUNCT)
    if not q:
        return None
    for kind, table, _ in _SMALL_TALK_TABLE:
        if q in table:
            return kind
    return None


def canned_reply(kind: str) -> str | None:
    """按类别取那句固定回复。

    和 `small_talk_reply` 的区别是方向：那个是「这句话属于哪类 → 回复」，
    这个是「我就要这一类的回复」。Agent 的 `whoami` 工具用它——
    **一份数据两个入口，别再抄一份自我介绍**：抄了之后加了个新能力，
    改了一处忘了另一处，两个入口会给出不一样的自我介绍。
    """
    return next((reply for k, _, reply in _SMALL_TALK_TABLE if k == kind), None)


def small_talk_reply(question: str) -> str | None:
    """招呼 / 道谢 / 告别 / 问能力 —— 命中就直接给一句固定回复，不检索也不调模型。

    返回 None 表示这是一个正经问题，照常走检索。
    """
    kind = small_talk_kind(question)
    return None if kind is None else canned_reply(kind)


_HEAD_STRICT = "你是一名旺店通旗舰版 ERP 的实施顾问助手，只依据下面提供的「参考材料」回答问题。"
_HEAD_OPEN = "你是一名旺店通旗舰版 ERP 的实施顾问助手。回答优先依据下面提供的「参考材料」。"

# 铁律 1 和 3 的第二种情形，两个版本。**其余铁律两个版本一字不差。**
_RULE1_STRICT = """1. 只用参考材料里的信息作答，**不得用你自己的常识补全或推测**。"""

# ⚠️⚠️ **这几行看着啰嗦，一个字都别删。试过，破了线。**
#
# 2026-08-21 为了救配图带出率（放开后 83.3% → 33.3%），把这段从 7 行压到 3 行、
# 把下面铁律 3 的三分支压成两句。配图确实回到了 50%，代价是**幻觉率 0% → 50%**：
# 16 道 no_answer 题错了 8 道，其中两道是这个样子——
#
#     问：Lazada 店铺的订单怎么同步到 ERP？（知识库里没有 Lazada 的任何文档）
#     答：进入【设置】-【基本设置】-【店铺】，点击"添加"，店铺平台选择 "Lazada"…[5]
#
# 它把材料里通用的建店流程照抄下来，**把平台名换成了 Lazada，还挂上了 [5]**。
# 用户照着点进去，下拉框里根本没有那一项——而那句话是有出处的样子。
# 这正是这次改动唯一没有商量余地的那条线。
#
# 所以：**长度换的是这条线，不是啰嗦。** 配图那 2 道题另想办法（扩题集），
# 别再从这里省字。
_RULE1_OPEN = """1. **材料里有的，一律以材料为准**，并按第 2 条标出来源编号。
   材料里没有、而你确实知道的通用知识（行业术语、概念解释、通用做法），**可以答**。
   ⚠️ 但有一条绝不能破：**不要凭记忆写旺店通的界面路径、菜单层级、字段名、
   参数取值、数量上限。** 这一类只能来自材料。
   你记忆里的路径和真实系统对不上时，用户会照着去点——而他分辨不出
   这一句和有出处的那一句有什么区别。（**这不是保守，是这个产品唯一
   会真正伤到人的错误形态**：一个编出来的配置步骤能让客户的订单卡住。）"""

_RULE3_TAIL_STRICT = (
    "   - 只有当材料**完全没有**与问题相关的内容时，才**只回复这一句**："
    "知识库暂无此内容。\n"
    "     不要解释、不要道歉、不要给建议、不要试着换个角度答。"
)

# ⚠️ **三个分支要分开写，不能合并成两句。** 理由同 `_RULE1_OPEN` 上面那段：
# 合并版实测把幻觉率从 0% 打到 50%。真正起作用的是最后那句
# 「凭记忆编一个出来是这里唯一不可接受的答法」——它是这段里唯一一句
# 把「不知道」和「编一个」明确对立起来的话。
_RULE3_TAIL_OPEN = """   - 材料里**完全没有**与问题相关的内容时：
     · 这是个通用概念 / 术语 / 行业常识，而你确实知道 → **就答**，
       但要先说明一句这不是知识库里的内容（如「知识库里没有这一条，按通用理解：」），
       并且**不要给它标来源编号**——[n] 只属于材料里的内容。
     · 这是旺店通的具体配置、路径、取值、上限，而材料里没有 → **只回复这一句**：
       知识库暂无此内容。**凭记忆编一个出来是这里唯一不可接受的答法。**
     · 你也确实不知道 → 同样只回复：知识库暂无此内容。"""

_TEMPLATE = """{head}

铁律：
{rule1}
2. 每一句结论后面标注来源编号，如 [1]、[2]。多个来源就写 [1][3]。
3. **先看材料里有没有能用的内容，再决定怎么答。这个顺序不能反**：
   - 材料里有能回答的内容——**哪怕只回答了问题的一部分**——就把那部分答出来，
     再明确写一句哪一部分材料里没有。**绝不能因为答不全就整个不答。**
     问题问了两件事而材料只写了一件，就答那一件、并说清另一件材料里没有。
     ⚠️ 说「材料里没有」只针对**问题问到的东西**，而且要先通读全部材料再说。
     不要顺手罗列问题没问到的方面、也不要凭印象断定某件事材料没写——
     说错一句「材料未提及 X」和编造一个 X 一样是错的。
{rule3_tail}
4. 材料里写了具体的数字、上限、界面路径、字段名时，**照原文答**，不要概括成
   「有一定限制」「在设置里」这类说法——问的人要的就是那个具体值。
5. **材料里的 [图1]、[图2] 是原文档的操作截图，要带上。** 你引用哪段材料，
   那段材料里出现的图号就照抄到对应步骤那一行的末尾，
   如「1. 进入【设置】-【打印设置】[图1]」。ERP 的操作步骤，一张截图顶三句话。
   唯一限制：**只能用材料里真实出现过的图号**，材料里没有 [图9] 就绝不能写 [图9]。
6. 前面的对话记录**只用来理解这一轮在问什么**（比如「那不良品呢」指的是什么），
   **不是可以引用的材料**。回答的依据只能是本轮的参考材料——上一轮答过的话，
   这一轮材料里没有就还是没有。
7. 材料每一条都标了来源：**「你的文档《X》」是提问者自己上传的**（他自己那家
   公司/客户的专属约定），**「公共知识库」是产品的通用说明**。两者冲突时
   **以「你的文档」为准**。
   ⚠️ 「你的文档」可能有好几份，各讲一个主体（不同的客户）。**别把其中一份的
   约定说成"通用做法"**——那只是另一家的约定。要提通用做法，只能引公共知识库。
8. **材料讲的是另一个平台、另一个客户、另一种单据时，它不是这个问题的材料。**
   问的是 A 平台，而材料里只有 B 平台的做法——那属于铁律 3 的
   「材料完全没有」，**不是「有一部分」**。把 B 的步骤搬过来、把名字换成 A，
   是这里最危险的一种答法：它长着有出处的样子，用户分辨不出。
   只有材料**明确写了**「各平台通用」这类话时，才能拿通用流程回答某一个具体平台。

写法要求：
{style}"""

# ─────────────────────────────────────────────────────────
# 有条件的主体约束（M11 P3 第 3 步）
#
# ⚠️ **触发条件限定死，这是这条规则能不能活下来的关键。**
# M9 那次失败就是因为改的是**全局**铁律，和铁律 3「有一部分就答一部分」
# 正面撞车——而铁律 3 是花整整一轮才调对的，假阴性率是 55 题的既有指标。
#
# 两个条件都满足才追加：
#     1. 用户**确实有**私有文档（没传过东西的人永远不会触发）
#     2. 问题里带第一人称或公司名（他在问「我们家」的事，不是问产品通用能力）
# 于是公共库那 55 题（`user_id=None`）**结构上不可能**走到这里 ——
# 这就是它和 M9 那条全局规则的根本区别。
#
# ⭐ **原来还有第三个条件「这一轮一个私有块都没召回」，2026-08-20 实测删掉了。**
# 删的理由不是嫌它严，是它**和第 2 步互相拆台**：保底名额保证了至少有一个私有块
# 进 top-k，于是「一个都没召回」这件事对有私有文档的用户几乎**永远不成立**，
# 主体约束等于被自己的队友关掉了。实测两道题因此漏判：
#   priv-noanswer-not-in-fixture  召回里第 5 条正是客户A 的文档（但它不讲退货），
#                                 于是约束不触发，模型拿公共库的通用退货流程冒充
#                                 星辰电商的专属约定
#   priv-noanswer-invoice-a       同理，召回里有客户A 的两块（都不讲发票）
# 真正该管的从来不是「有没有私有块」，是**这些私有块讲不讲他问的这个主体的这件事**——
# 而那件事只有模型判得了。所以条件放宽、话说清楚，把判断交给它。
# ─────────────────────────────────────────────────────────

_SUBJECT_GUARD = """

⚠️ 本轮补充（只对这一轮生效）：提问者问的是**某一方自己的专属约定**
（他自己那家公司，或者他点名的某个客户），不是在问这个产品通常怎么用。所以：

- 只有标着「你的文档《…》」、**并且确实在讲他问的这个主体**的材料，
  才算是这个主体的约定。
- 「公共知识库」讲的是产品的通用能力和默认流程，**不是任何一家的约定**。
  不要拿它冒充某一家的专属约定。
- 「你的文档」里**讲的是别的主体**的那几篇，是**别人**的约定，
  同样不能拿来回答这一个——两家在同一个字段上给不同的值是常态。
- 如果没有任何一条材料在讲他问的这个主体的这件事，就按铁律 3 的第二种情形办：
  **只回复这一句**「知识库暂无此内容。」——不要罗列材料里都写了些什么，
  也不要解释为什么没有。

材料里确实讲到了这个主体的这件事时照常答，这一条不是让你少答。

⚠️ 如果他这一问其实**不针对某一方**（问的是这个产品本身怎么用、某个功能在哪配），
那这一整条不适用，照常按上面的铁律回答。
——判断触发条件的是一个关键词规则，它会认错；**认错时以你看到的问题为准**。"""

# 第一人称 + 公司名。**两类都要有**：
#   「我们的组合装要拆吗」      —— 第一人称，没提公司名
#   「星辰电商的对账以什么为准」 —— 提了公司名，没有第一人称
_FIRST_PERSON = ("我们", "我司", "我方", "咱们", "我的", "本公司", "本店", "我这边")
# 「星辰电商」「XX 公司」这类。**故意只认带后缀的**：
# 不带后缀的专有名词（「星辰」）和普通词分不开，认了就会把
# 「京东电子面单怎么配」里的「京东」也当成主体，那是产品支持的平台，不是客户。
#
# ⚠️ **这张后缀表天生是不全的，别指望补全它。**
# 2026-08-20 实测漏过一次：「远岸**家居**」不在表里，于是
# `priv-noanswer-picking-mode-b` 那一轮没加约束，模型给了一句
# 「材料里没有…的配置信息」——意思是对的，但不是那句兜底话术，
# 于是页面上会是「没有」底下挂着五条来源。
#
# 漏判的代价是**这一轮回到 M11 之前的行为**（不是出错），误判的代价才是
# 把假阴性率顶上去。所以这张表宁可长一点：多认一个后缀，最坏结果是给一道
# 本来就答得出的题加了一段用不上的约束。下面这些是按「实施顾问的客户名单
# 长什么样」列的，不是穷举。
#
# ⚠️⚠️ 加后缀前先问一句：**这个词会不会出现在一句正常的 ERP 产品问题里？**
# 「物流」「仓储」「品牌」都被我加过又删掉——「怎么设置物流单量限制」里
# 「怎么设置」+「物流」正好凑成一个假的公司名，而那类问题恰恰是最常问的。
# 留下的这些都是**只会出现在公司名里**的行业词。
_SUBJECT_SUFFIX_RE = re.compile(
    r"[一-龥A-Za-z0-9]{2,8}("
    r"电商|公司|集团|科技|商贸|实业|贸易|旗舰店|专营店|专卖店|供应链|"
    r"家居|家纺|家具|服饰|鞋业|箱包|珠宝|生鲜|医药|母婴|日化|美妆|建材"
    r")"
)


def asks_about_subject(question: str) -> bool:
    """这句话是在问「某一方自己的约定」吗。

    宁可漏判不可误判：漏判只是这一轮没加约束（回到 M11 之前的行为），
    误判则是给一道正常的公共库问题套上「没讲这个主体就说不知道」，
    那会直接把假阴性率顶上去。
    """
    return any(w in question for w in _FIRST_PERSON) or bool(
        _SUBJECT_SUFFIX_RE.search(question)
    )


async def needs_subject_guard(
    session: AsyncSession,
    question: str,
    user_id: uuid.UUID | None,
) -> bool:
    """这一轮要不要追加主体约束。两个条件都满足才要。

    ⭐ **单独拆出来是给评测用的**，同 `small_talk_kind`：
    `eval/run.py` 不走 `ask_stream`（它分两阶段跑，检索一次、生成一次），
    自己抄一份判定逻辑的话，改了这边忘了改那边，评测会一直报告一个
    早就不存在的系统——而私有库那组题量的恰恰就是这条规则。

    ⚠️ 顺序按代价排：正则免费，查库放后面。没有私有文档的用户（包括
    公共库评测那 55 题的 `user_id=None`）在第一个条件上就短路了。
    """
    return asks_about_subject(question) and await has_private_chunks(session, user_id)

# ─────────────────────────────────────────────────────────
# 两档回答风格
#
# ⚠️ **上面那段铁律两档共用，一个字都不改。** 会变的只有写法：
# 详解档是「同一份事实说得更透」，不是「可以多说一点材料里没有的」。
# 把防幻觉规则也做成两份，迟早会有一份先松掉。
# ─────────────────────────────────────────────────────────

# ⚠️ **这三行是调出来的，别再"优化"它。**
# 我把它改成过「能一句说清就不写三句 / 只保留真会踩坑的注意事项」，
# 评测立刻从 98.2% 掉到 96.4%：模型开始省略材料里的内容，
# hard-crossdoc-limit-100-both 那题直接把两个不同的上限混成了一个。
# 简答档是**默认档**，默认档的准确率不能拿来换一个新选项。
_STYLE_FAST = """
- 操作步骤按 1. 2. 3. 分条列出，把界面路径原样保留（如「设置–策略设置–短信策略」）。
- 保留材料中的注意事项和限制条件，那往往是最容易踩坑的地方。
- 直接说事，不要"根据参考材料"之类的开场白。""".lstrip("\n")

_STYLE_DEEP = """
- 操作步骤按 1. 2. 3. 分条列出，把界面路径原样保留（如「设置–策略设置–短信策略」）。
- 每一步写清楚**在哪个界面、点什么、填什么**，材料里有字段名和取值就照抄。
- 材料里提到的前置条件（要先开通什么、要什么权限、依赖哪个设置）单独写在步骤前面。
- 材料里提到的注意事项、限制、常见错误，逐条列出来，不要压缩成一句"注意相关限制"。
- 涉及多个平台、多种单据类型时，把差异分开说，别混成一段。
- 结尾可以补一句「材料里没有覆盖到的部分」，但只写问题真的问到、而材料确实没写的。
- 说得细 ≠ 可以多说。**每一句仍然必须来自材料**，展开的是材料里已有的信息。
- 不要"根据参考材料"之类的开场白。""".lstrip("\n")

ANSWER_STYLES = {"fast": _STYLE_FAST, "deep": _STYLE_DEEP}
DEFAULT_MODE = "fast"


def system_prompt_for(
    mode: str = DEFAULT_MODE, *, subject_guard: bool = False, general: bool | None = None
) -> str:
    """按档位拼出 system prompt。认不出来的档位一律退回简答档。

    `subject_guard` 只由 `ask_stream` 在两个条件同时成立时置 True，
    见 `_SUBJECT_GUARD` 上面那段注释。**调用方不要自己开这个开关**——
    它一旦变成常开，就又是 M9 那条和铁律 3 打架的全局规则了。

    `general` 是常识兜底（M12）。留 None 则读配置 `ALLOW_GENERAL_KNOWLEDGE`；
    传死值是给评测用的——A/B 两版 prompt 必须能在同一次运行里都拿到。
    """
    if general is None:
        general = get_settings().allow_general_knowledge
    prompt = _TEMPLATE.format(
        head=_HEAD_OPEN if general else _HEAD_STRICT,
        rule1=_RULE1_OPEN if general else _RULE1_STRICT,
        rule3_tail=_RULE3_TAIL_OPEN if general else _RULE3_TAIL_STRICT,
        style=ANSWER_STYLES.get(mode, _STYLE_FAST),
    )
    return prompt + _SUBJECT_GUARD if subject_guard else prompt


# 简答档的完整 prompt。评测脚本 import 的是这个名字，别删
SYSTEM_PROMPT = system_prompt_for(DEFAULT_MODE)

USER_TEMPLATE = """参考材料：

{context}

---

问题：{question}"""

# 一条都没召回时，`{context}` 会是空串。**得明说是空的**，不能留一片空白：
# 留白的话模型多半会当成「材料没给全」，然后开始猜材料里可能写了什么；
# 明说没有，它才会走铁律 3 的第二种情形（常识兜底或拒答）。
EMPTY_CONTEXT = "（这次检索一条都没命中，知识库里没有与这个问题相关的内容。）"

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

    # `(kind, text)`，kind 是 `reasoning`（模型的草稿）或 `content`（正文）。
    # ⚠️ **草稿不是答案**，落库和判「知识库暂无此内容」都只能看 content。
    # 它存在的唯一理由是详解档的正文首字要等几十秒，中间总得让人看见点什么
    stream: Iterator[tuple[str, str]]
    citations: list[Citation]
    images: list[dict] = field(default_factory=list)
    # 送进模型的上下文原文。调用方用它记 token 用量——
    # token 的大头在这里，不在答案里（5 块材料约 2500 字）
    context_text: str = ""
    # 这一轮召回的块里有几块来自用户自己的文档。
    # 给 `request_trace` 记一列（M11 P1）——**这一列是 P3 有没有生效的唯一证据**：
    # 私有题答错时，先看这里是 0（检索就没捞到）还是非 0（捞到了但模型没用），
    # 两种失败的修法完全不同，只看答案文本分不出来
    private_hits: int = 0


async def ask_stream(
    session: AsyncSession,
    question: str,
    embedder: Embedder,
    reranker: Reranker | None,
    llm: ChatLLM,
    *,
    user_id: uuid.UUID | None = None,
    history: list[tuple[str, str]] | None = None,
    mode: str = DEFAULT_MODE,
) -> StreamedAnswer:
    """检索并流式作答。

    Args:
        history: 这条会话之前的 (role, content)，**不含本轮提问**，从旧到新。
            有历史时会先把追问改写成独立问题再检索。
        mode: 回答档位，`fast` 简答 / `deep` 详解。只影响写法，
            防幻觉的铁律两档完全一样。

    ⚠️ **调用方的义务**：流消费完后，若 `is_no_answer(全文)` 为真，
    必须把这批引用丢掉不展示。否则会出现「知识库暂无此内容」下面挂着
    五条来源的情形——用户会误以为答案有依据。
    """
    # 招呼语在检索**之前**拦掉。放到后面就晚了：它一条都召不回，
    # 会被下面那道闸门变成一句「知识库暂无此内容」
    if (canned := small_talk_reply(question)) is not None:
        return StreamedAnswer(stream=iter([("content", canned)]), citations=[])

    # 只有检索词用改写后的版本；给模型看的问题仍是用户原话
    search_query = (
        await run_in_threadpool(rewrite_query, llm, question, history) if history else question
    )

    result = await search(session, search_query, embedder, reranker, user_id=user_id)

    # 第一道闸门：一条都没召回。
    #
    # ⚠️ **常识兜底打开后这道闸门必须让路**（M12）。它原来的作用是「省一次
    # LLM 调用」——反正材料是空的，模型只能答不知道。但允许用常识之后，
    # 材料为空恰恰是**最需要**问一次模型的时候：「品牌方是什么」在知识库里
    # 一条都没有，而它是个正当问题。
    #
    # 代价说清楚：这条路以前是 0 成本的，现在每一次「知识库里没有」都要花
    # 一次模型调用。挡在前面的仍然是寒暄短路（那个不花钱）。
    if result.is_empty and not get_settings().allow_general_knowledge:
        return StreamedAnswer(stream=iter([("content", NO_ANSWER)]), citations=[])

    context = result.build_context()

    # M11 P3 第 3 步。判据抽在 `needs_subject_guard` 里，评测走的是同一个函数
    # 追问可能只写「那对账呢」，主体只存在于改写后的独立问题里。
    # 用原句判定会把这类私有约定追问漏回公共知识库。
    guard = await needs_subject_guard(session, search_query, user_id)
    if guard:
        logger.info("本轮追加主体约束：q=%r", question[:60])
        # 没有任何当前用户的私有材料时，公共默认规则绝不能冒充这家公司的约定。
        # 这是能由数据结构直接保证的边界，不交给 Prompt 猜。
        if result.private_count == 0:
            return StreamedAnswer(stream=iter([("content", NO_ANSWER)]), citations=[])

    messages = [
        {"role": "system", "content": system_prompt_for(mode, subject_guard=guard)},
        *_history_messages(history),
        {
            "role": "user",
            "content": USER_TEMPLATE.format(
                context=context.text or EMPTY_CONTEXT, question=question
            ),
        },
    ]
    return StreamedAnswer(
        stream=llm.stream_parts(messages),
        citations=result.citations,
        images=context.images,
        context_text=context.text,
        private_hits=result.private_count,
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
    mode: str = DEFAULT_MODE,
) -> Answer:
    streamed = await ask_stream(
        session, question, embedder, reranker, llm, user_id=user_id, history=history, mode=mode
    )
    text = "".join(t for kind, t in streamed.stream if kind == "content")
    answer = Answer(text=text, citations=streamed.citations, images=streamed.images)
    # 模型说了不知道，就别再挂一堆来源——那会让用户以为答案有依据
    if answer.is_no_answer:
        answer.citations = []
        answer.images = []
    return answer
