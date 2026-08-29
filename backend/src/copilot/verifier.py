"""校验 Agent（W3.2）——出稿之后再问一遍：这些具体说法，材料里找得到吗？

⭐⭐ **它是这个项目里唯一值得加的第二个 Agent，判据只有一条：
有一个指标非它不可。** 不为多而多——「多 Agent 编排」是 JD 高频词，
但一问「它让哪个指标动了几个点」就穿帮。这一个直接对着
`high_risk_hallucination_rate`（材料里没有的高风险问题却给了实质答案）。

⚠️⚠️ **先说清楚它今天量出来的结论，别让人以为它开着。**
现有的 47 题风险边界集上，那三条硬指标**已经是 0.0%**——
也就是说这个 Agent 的收益上限就是 0，它只可能把**对的答案降级成拒答**。
所以默认关，A/B 量的其实是它的**假阳性代价**。数字和取舍见 ADR-22。

⭐ 这本身是个好答案，比"我加了 verifier，指标涨了"更强：
**敢报负结果、并且写下"什么情况下我会打开它"的候选人极少。**

─────────────────────────────────────────────────────────
两个设计决定，都是这个项目的老规矩：

1. **能用规则判的先用规则判。** 核对哪几句话不交给模型来挑——
   界面路径和参数值是**能用正则抽出来**的。抽不出任何一条具体说法的答案
   （闲聊、概念解释、拒答）直接跳过，一次调用都不花。
   这既是成本控制，也是**目标锁定**：不需要一个"这道题高不高风险"的分类器，
   也就不会有分类器的误判。

2. **`annotate` 保住流式，`refuse` 保不住。** 答案是一个字一个字发出去的，
   发出去就收不回来。所以：
       annotate  流照常，末尾追加一段「以下说法未能核对到」
       refuse    **必须先攒完整段再发**，也就是这一轮没有流式
   两种都留着，因为它们的代价完全不同，而选哪种是产品决定。
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# ⚠️ 界面路径的写法在这份语料里有好几种，都要认：
#   【设置–策略设置–短信策略】   方头括号，全角横线
#   订单-标缺清点                 裸的短横线串
#   「打印机-打印选项-选择打印机」  引号
# 判据是「至少两级、用分隔符连起来」——一级的不算路径，那就是个功能名
# ⚠️⚠️ **`/` 和 `·` 不是路径分隔符，是中文里的"或者"。**
# 第一版把它们收进来了，结果 2026-08-29 那轮 A/B 的两条误报里有一条就是它：
# 一道**常识题**的答案里写着「复制/补发规则」，被当成一条界面路径送去核对，
# 而常识题按 M12 的铁律本来就不该有材料出处——**它注定核对不到**。
# 同类的还有「正/残品」「启用/停用」「客审/财审」，中文正文里到处都是。
# 误报的代价是给一条正确答案挂一句「未能核对到」，那比不标注更伤信任。
_SEP = r"[-–—>›》]"
_SEG = r"[一-龥A-Za-z0-9]{2,12}"
# ⚠️ **括号要跨得过去。** 这份语料里最常见的路径写法是
# `【设置】-【打印设置】-【集成打印】`，段与段之间隔着 `】-【` 三个字符。
# 第一版的分隔符只认那一个横线，于是**最常见的那种路径一条都抽不出来**——
# 而症状是"校验器很安静"，看起来像它认为一切都有据。
_KET = r"[】\]」』]?"
_BRA = r"[【\[「『]?"
_PATH_RE = re.compile(rf"{_SEG}(?:{_KET}\s*{_SEP}\s*{_BRA}{_SEG}){{1,5}}")
# 抽出来的两头可能挂着半个括号，去掉——送给校验器的应该是路径本身
_TRIM = "【】[]「」『』 　"

# 参数值：数字 + 单位。**不抓裸数字**——正文里的序号（「1. 登录…」）、
# 引用编号、版本号会把它淹掉，而淹掉的后果是每条答案都要花一次校验调用
_PARAM_RE = re.compile(
    r"\d+(?:\.\d+)?\s*(?:次|天|小时|分钟|秒|条|个|件|张|%|％|元|万|台|页|字符)"
)

# 引用与配图标记。抽取之前要剥掉，否则 `[图3]` 会被当成参数值
_MARK_RE = re.compile(r"\[(?:\d{1,2}|图\d+)\]")

# 一次最多核对多少条。⚠️ 不设上限的话，一段长答案会把整个上下文再塞一遍进去，
# 而校验这一步的价值集中在最前面那几条——它们是用户真的会照着点的
MAX_CLAIMS = 12


@dataclass
class Verdict:
    """一次校验的结论。"""

    claims: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)
    # ⚠️ **校验本身失败时是 None，不是 []。** 空列表的意思是「核对过了，全都有据」，
    # 而 None 的意思是「没核对成」——把后者当成前者，就等于模型一掉线，
    # 这道防线自动变成"全部放行"，且没有任何症状
    checked: bool = False

    @property
    def clean(self) -> bool:
        return self.checked and not self.unsupported


def extract_claims(answer: str) -> list[str]:
    """从答案里抽出「界面路径」和「参数值」。**纯函数，不调模型。**

    ⭐ 抽不出东西 = 这段答案里没有用户会照着点的具体说法（闲聊、概念解释、
    拒答），校验对它没有意义，直接跳过——**一次调用都不花**。

    ⚠️ 这不是一个"高风险问题分类器"。判的是**答案里有没有可核对的东西**，
    而不是"这道题高不高风险"。少一层分类器就少一层误判，
    而且这个判据和校验要做的事**是同一件事**：没有可核对的东西，就没什么可核对。
    """
    body = _MARK_RE.sub(" ", answer or "")
    seen: list[str] = []
    for m in list(_PATH_RE.finditer(body)) + list(_PARAM_RE.finditer(body)):
        text = m.group(0).strip().strip(_TRIM)
        if text and text not in seen:
            seen.append(text)
    return seen[:MAX_CLAIMS]


VERIFIER_PROMPT = """你是一个核对员。下面给你一段【参考材料】和一段【待核对的答案】，
以及从答案里抽出来的若干条**具体说法**（界面路径、参数值）。

你的任务只有一件：逐条判断这条说法在参考材料里**找不找得到**。

规则：
1. 只看参考材料。你自己知道的任何东西一律不算数。
2. 说法在材料里出现过、或者材料明确支持它，就算"找得到"。
   用词不完全一样但指的是同一件事（比如「订单-标缺清点」对应材料里的
   「订单 › 标缺清点」），算找得到。
3. 材料里根本没提，或者材料里是另一个值，算"找不到"。
4. **拿不准就算找得到。** 这道校验的假阳性代价是把一条正确答案标成可疑，
   而那比漏掉一条更常发生、也更伤用户对系统的信任。

只输出 JSON，不要任何别的字：
{"unsupported": ["找不到的说法原文", ...]}
找不到的一条都没有就输出 {"unsupported": []}。"""


def _parse(raw: str, claims: list[str]) -> list[str]:
    """把模型那段 JSON 读成"哪几条没找到"。

    ⚠️ **只认真的出现在 `claims` 里的条目。** 模型偶尔会把说法改写一遍再报回来，
    照单全收的话，标注里会出现一条用户在答案里根本找不到的句子——
    那比不标注更让人困惑。
    """
    text = (raw or "").strip()
    # 有些模型习惯裹一层 ```json
    if text.startswith("```"):
        text = text.strip("`")
        text = text[4:] if text.lower().startswith("json") else text
    try:
        data = json.loads(text[text.index("{") : text.rindex("}") + 1])
    except (ValueError, json.JSONDecodeError):
        raise
    got = data.get("unsupported") or []
    if not isinstance(got, list):
        raise ValueError("unsupported 不是列表")
    return [c for c in claims if any(str(g).strip() == c for g in got)]


def verify(llm, answer: str, context: str) -> Verdict:
    """核对一遍。**同步**，因为 `ChatLLM.complete` 是同步的（同 `rewrite_query`）。

    ⚠️ 任何异常都退回 `checked=False`，**不是"全都有据"**：
    校验器掉线的那一轮既不能算答案可信，也不能算答案不可信——
    它什么都不能算，和 `eval/gate.py` 的 UNRELIABLE 是同一条规矩。
    """
    claims = extract_claims(answer)
    if not claims:
        return Verdict(claims=[], unsupported=[], checked=True)
    try:
        raw = llm.complete(
            [
                {"role": "system", "content": VERIFIER_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"【参考材料】\n{context}\n\n"
                        f"【待核对的答案】\n{answer}\n\n"
                        f"【具体说法】\n" + "\n".join(f"- {c}" for c in claims)
                    ),
                },
            ],
            temperature=0.0,
        )
        return Verdict(claims=claims, unsupported=_parse(raw, claims), checked=True)
    except Exception as e:  # noqa: BLE001 - 校验挂了不该让这一轮答不出来
        logger.warning("校验失败：%s", e, exc_info=True)
        return Verdict(claims=claims, unsupported=[], checked=False)


UNVERIFIED_HEAD = "\n\n⚠️ 以下说法在参考材料里没能核对到，照着操作前请再确认一次："


def annotate(answer: str, verdict: Verdict) -> str:
    """`annotate` 模式：末尾追加一段。**流式照常**，这段最后到。

    ⚠️ 校验没跑成（`checked=False`）时**什么都不加**。加一句"本轮未能校验"
    会让用户对每一条答案都打个问号，而那不是这道防线想要的效果——
    校验掉线是运维问题，写进日志，不写进用户的屏幕。
    """
    if not verdict.unsupported:
        return answer
    return answer + UNVERIFIED_HEAD + "\n" + "\n".join(f"- {c}" for c in verdict.unsupported)
